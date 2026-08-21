#!/usr/bin/env python3
"""
Local build harness -- a real DOCX in, a real press PDF out.

WHY THIS EXISTS, NEXT TO tracer_bullet.py AND worker.py
`tracer_bullet.py` selects the fixture loader `acquire` and allows stub engines:
it proves the DAG is wired, not that a manuscript renders. `worker.py` is the
production path but reaches its manuscript through Postgres
(manuscripts.source_sha256 -> CAS), so it cannot be pointed at a file on disk.
Neither runs a DOCX sitting in a directory. This does: the SAME executor and the
SAME registry as the worker, with the real `ingest` implementation selected and
`allow_stub_engines=False`, differing from the worker only in where the
manuscript bytes and the DesignSpec come from (argv, not the database).

`allow_stub_engines` stays False deliberately. A missing weasyprint or a missing
Ghostscript must fail this build the way it fails a tenant's, rather than emit a
stub PDF the preflight gate would then bless as press-ready (BUILD_PLAN.md D8).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
for _sub in (
    "platform/stages/py", "platform/cas/py", "platform/cache/py",
    "services/ingest", "services/structure", "services/prepress",
    "services/agents", "services/cover", "services/epub", "services/idml",
    "services/onix", "services/alttext",
):
    sys.path.insert(0, str(REPO_ROOT / _sub))

import stages  # noqa: E402,F401 -- import for its registration side effect
from publisher_stages import ErrorKind, StageError, get_registry  # noqa: E402
from tracer_bullet import DagExecutor  # noqa: E402

# The artifact kinds worth handing back to a human, and the filename each gets in
# the output directory. Keyed by (stage, artifact kind) because `kind` alone is
# ambiguous -- both `paginate` and `finish-gs` emit a "pdf".
DELIVERABLES: dict[tuple[str, str], str] = {
    ("finish-gs", "pdf"): "press.pdf",
    ("finish-gs", "proof"): "proof.pdf",
    ("finish-gs", "report"): "finish-report.json",
    ("finish", "pdf"): "press.pdf",
    ("finish", "proof"): "proof.pdf",
    ("finish", "report"): "finish-report.json",
    ("paginate", "pdf"): "interior-raw.pdf",
    ("paginate", "pagemap"): "pagemap.json",
    ("preflight", "report"): "preflight.json",
    ("package", "build-report"): "build-report.json",
}


def _cas_path(cas_root: Path, digest: str) -> Path:
    """Where the CAS shards a blob: h[:2]/h[2:4]/h."""
    return cas_root / digest[:2] / digest[2:4] / digest


def build(docx: Path, profile: str, designspec: Path | None, out_dir: Path,
          cas_root: Path, build_id: str | None = None) -> int:
    if not docx.is_file():
        print(f"FAILED: manuscript not found: {docx}")
        return 2
    if designspec is not None and not designspec.is_file():
        print(f"FAILED: designspec not found: {designspec}")
        return 2

    registry = get_registry()
    # NOT `acquire`. stages/__init__.py already default-selects the real `ingest`;
    # this asserts it rather than assuming, because selecting the fixture loader
    # here would silently build a synthetic novel instead of the user's manuscript.
    registry.select_implementation("ingest", "ingest")
    finish_stage = registry.selected_implementation("finish")

    executor = DagExecutor(registry, allow_stub_engines=False)

    print("=" * 60)
    print("  PUBLISHER -- LOCAL BUILD")
    print("=" * 60)
    print(f"  manuscript : {docx}")
    print(f"  profile    : {profile}")
    print(f"  designspec : {designspec or '(built-in default)'}")
    print(f"  cas root   : {cas_root}")
    print(f"  output     : {out_dir}")
    print()

    initial_inputs = {
        "ingest": {"docx_path": str(docx)},
        # One profile name to all three stages with a say in page geometry:
        # design-compile grows the page box by its bleed, finish insets the
        # TrimBox by the same amount, preflight measures the result.
        "design-compile": {
            "designspec_path": str(designspec) if designspec else None,
            "profile_name": profile,
        },
        finish_stage: {"profile_name": profile},
        "preflight": {"profile_name": profile},
    }

    build_id = build_id or f"local-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    try:
        results = executor.execute(
            build_id=build_id,
            initial_inputs=initial_inputs,
            cas_root=cas_root,
        )
    except StageError as e:
        print(f"\nFAILED: [{e.kind}] {e.message}")
        for d in getattr(e, "diagnostics", None) or []:
            print(f"  - {d.code}: {d.human_message}")
        return 1

    # Same two-gate assertion tracer_bullet.py makes: "completed" must mean the
    # gates ran, not merely that nothing raised.
    for gate in ("preflight", "package"):
        if gate not in results:
            print(f"\nFAILED: '{gate}' never executed -- that is not a passed build.")
            return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    print("\n--- deliverables ---")
    for stage_name, result in results.items():
        for art in result.artifacts:
            name = DELIVERABLES.get((stage_name, art.kind))
            if name is None:
                continue
            src = _cas_path(cas_root, art.hash)
            if not src.is_file():
                print(f"  !! {name}: blob {art.hash[:12]} missing from CAS")
                continue
            shutil.copyfile(src, out_dir / name)
            print(f"  {name:<20} {art.size:>12,} bytes  sha256:{art.hash[:12]}")

    verdict = json.loads((out_dir / "preflight.json").read_bytes()) if \
        (out_dir / "preflight.json").is_file() else {}
    print(f"\nOK build {build_id} complete -- preflight: {verdict.get('status', '?')} "
          f"({(verdict.get('summary') or {}).get('failed', '?')} failed, "
          f"{(verdict.get('summary') or {}).get('warnings', '?')} warnings)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a book PDF from a DOCX, locally.")
    ap.add_argument("docx", type=Path, help="Path to the .docx manuscript")
    ap.add_argument("--profile", default="Greek 17x24",
                    help="Vendor profile name from profiles/*/*.yaml (owns trim + bleed)")
    ap.add_argument("--designspec", type=Path, default=None,
                    help="Path to a designspec/1 JSON (owns typography + margins)")
    ap.add_argument("--out", type=Path, default=Path("./.publisher/out"),
                    help="Directory to copy the finished artifacts into")
    ap.add_argument("--cas-root", type=Path, default=Path("./.publisher/cas"))
    ap.add_argument("--build-id", default=None)
    args = ap.parse_args()
    return build(args.docx, args.profile, args.designspec, args.out,
                 args.cas_root, args.build_id)


if __name__ == "__main__":
    sys.exit(main())
