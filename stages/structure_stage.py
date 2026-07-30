"""
Structure stage -- `ast-assemble` (ARCHITECTURE.md §1.2 stage 8).

Compares the HTML produced by `extract` against the original source it was
derived from, and only on a match does it emit the canonical `ast/1` artifact
that every downstream stage (via `resolve` and `paginate`) depends on.

WHY THIS REPLACED THE OLD `structure` STAGE
The previous version read a single `source` input, computed a hash of its own
text, compared that hash against an `integrityHash` field embedded in the SAME
file, and treated an absent hash as a pass. It never touched a second,
independently-derived text stream, so it could not detect a real integrity
violation -- there was nothing on the other side of the "==" to disagree with.
It also declared `ast/1` as an output but never actually emitted an `ast`
artifact, which is why the DAG integrity checker's `orphan_output` warning for
"ast" was really masking a `never-produced` bug, not just an unconsumed one.

This stage now takes BOTH `html` (from `extract`) and `source` (the original,
from `acquire`) as required, non-root inputs -- there is no path through this
function that can run with only one side of the comparison.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.rules import extract_text_from_html, normalize_text


def _extract_all_text(ast: dict) -> str:
    """Extract and concatenate all text content from a canonical AST (schemas/ast),
    walking frontMatter/body/backMatter.

    Chapter and section titles live in `attrs.title`, not as a `type: "text"` node
    inside `content` -- the original version of this function only walked `content`
    arrays, so it silently dropped every chapter title from the integrity check.
    `extract`'s `_ast_to_html` DOES render titles into the HTML (as an `<h1>`), so
    omitting them here made the two sides of the comparison structurally unequal
    even for a perfectly faithful conversion.

    Key order below is NOT arbitrary: it must match `_ast_to_html`'s render order
    (frontMatter, then body, then backMatter). The root AST dict has all three keys
    simultaneously, so walking them in a different order -- the original code used
    ("content", "frontMatter", "backMatter", "body"), putting backMatter before
    body -- silently reorders the concatenated text relative to what the HTML
    actually rendered, and the comparison fails on ANY manuscript with non-empty
    back matter even when no text was lost or altered.
    """
    texts: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            title = (node.get("attrs") or {}).get("title")
            if title:
                texts.append(title)
            for key in ("frontMatter", "body", "backMatter", "content"):
                val = node.get(key)
                if isinstance(val, list):
                    for item in val:
                        _walk(item)
                elif isinstance(val, dict):
                    _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(ast)
    return " ".join(t for t in texts if t)


@stage(
    name="ast-assemble",
    version=2,
    inputs={"html": "typescript-html/1", "source": "raw-source/1"},
    outputs={"ast": "ast/1", "integrity-report": "integrity-report/1"},
    toolchain=[],
    fixtures="fixtures/structure/v1",
    memory_budget_mb=128,
    queue="q.structure",
    description="Verify text-integrity between extract's HTML and the source AST, "
                 "then emit the canonical AST",
)
def ast_assemble(ctx: StageCtx, html: str | None = None, source: str | None = None) -> StageResult:
    """
    ast-assemble -- the text-integrity gate (ARCHITECTURE.md §3.1):

        normalize(text_stream(html)) == normalize(text_stream(source))

    Hard fail, no flag, no override, no config (BUILD_PLAN.md §3.6). A missing or
    unreadable input is `bad_input`; a genuine text mismatch is `engine_bug` -- the
    pipeline's fault, not the user's, since `extract` is supposed to be lossless.
    """
    if html is None or source is None:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="ast-assemble requires both 'html' (from extract) and 'source' "
                    "(the original AST) -- the integrity gate has nothing to compare "
                    "against with only one side.",
        )

    html_path, source_path = Path(html), Path(source)
    if not html_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"HTML input not found: {html}")
    if not source_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Source input not found: {source}")

    html_text = html_path.read_text(encoding="utf-8")
    source_ast = json.loads(source_path.read_bytes())

    html_side = normalize_text(extract_text_from_html(html_text))
    source_side = normalize_text(_extract_all_text(source_ast))

    if html_side != source_side:
        i = 0
        limit = min(len(html_side), len(source_side))
        while i < limit and html_side[i] == source_side[i]:
            i += 1
        raise StageError(
            kind=ErrorKind.ENGINE_BUG,
            message="Text integrity violation -- extract's HTML text does not match "
                    "the source AST's text after normalization.",
            diagnostics=[Diagnostic(
                code="integrity_mismatch",
                severity="error",
                human_message=(
                    f"Text streams diverge at normalized offset {i}. "
                    f"HTML side: ...{html_side[max(0, i - 40):i + 40]!r}... "
                    f"Source side: ...{source_side[max(0, i - 40):i + 40]!r}..."
                ),
                suggested_fix="Check extract's HTML rendering for a node type it "
                              "does not know how to render (it drops unknown node "
                              "text silently) or a genuinely lossy transformation.",
            )],
        )

    integrity_hash = hashlib.sha256(source_side.encode("utf-8")).hexdigest()
    print(f"  [ast-assemble] Text integrity verified: {integrity_hash[:16]}... "
          f"({len(source_side)} normalized chars)")

    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    # The canonical AST for this build. Since the comparison above proved the
    # source's text round-trips losslessly through extract's HTML, the source AST
    # itself -- stamped with the freshly computed hash -- IS the canonical ast/1.
    # A real structure-rules inference pass (services/structure/rules.py
    # build_ast_draft, for manuscripts that don't already start as a structured
    # AST) plugs in here without changing this stage's contract.
    ast_out = dict(source_ast)
    ast_out["integrityHash"] = f"sha256:{integrity_hash}"
    ast_bytes = json.dumps(ast_out, indent=2).encode("utf-8")
    ast_ref = cas.put(ast_bytes, media_type=MediaType("application/json"))

    report = {
        "integrityHash": integrity_hash,
        "htmlTextLength": len(html_side),
        "sourceTextLength": len(source_side),
        "passed": True,
    }
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    report_ref = cas.put(report_bytes, media_type=MediaType("application/json"))

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="ast",
                hash=str(ast_ref.hash),
                media_type="application/json",
                size=len(ast_bytes),
            ),
            StageArtifactRef(
                kind="integrity-report",
                hash=str(report_ref.hash),
                media_type="application/json",
                size=len(report_bytes),
            ),
        ],
        metrics={"text_length": len(source_side), "integrity_ok": 1.0},
    )
