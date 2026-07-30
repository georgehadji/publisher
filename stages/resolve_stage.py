"""
Resolve stage -- `resolve` (ARCHITECTURE.md §1.2 stage 9).

Applies the override log to the canonical `ast/1` to produce `doc-effective/1`,
the artifact `paginate` actually consumes. Never mutates `ast/1` itself
(BUILD_PLAN.md §3.7: AST is derived and immutable; human/agent decisions live in
a separate rebasable override layer).

No override log is wired to a stage yet -- `overrides_path` is an optional root
input, and its absence means zero overrides, not a stub. Calling
`apply_overrides(ast, ops=[])` through the same code path production will use
(rather than a bare passthrough) means: (a) the empty-ops case is exercised by
every build today, and (b) wiring a real override log later is a change to
where `ops` comes from, not to this stage's body.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.overrides import apply_overrides, OverrideOp


@stage(
    name="resolve",
    version=1,
    inputs={"ast": "ast/1", "overrides_path": "overrides/1"},
    root_inputs=["overrides_path"],
    optional_root_inputs=["overrides_path"],  # absence means zero overrides, not missing
    outputs={"doc": "doc-effective/1"},
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    queue="q.structure",
    description="Apply the override log to the canonical AST, producing doc.effective.json",
)
def resolve(ctx: StageCtx, ast: str | None = None, overrides_path: str | None = None) -> StageResult:
    """Resolve stage -- fold overrides onto the canonical AST."""
    if ast is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="resolve requires 'ast' (from ast-assemble)")

    ast_path = Path(ast)
    if not ast_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"AST input not found: {ast}")

    ast_doc = json.loads(ast_path.read_bytes())

    ops: list[OverrideOp] = []
    if overrides_path:
        ops_path = Path(overrides_path)
        if not ops_path.exists():
            raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Overrides input not found: {overrides_path}")
        raw_ops = json.loads(ops_path.read_bytes()).get("ops", [])
        ops = [OverrideOp(**op) for op in raw_ops]

    effective = apply_overrides(ast_doc, ops)

    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    doc_bytes = json.dumps(effective, indent=2).encode("utf-8")
    ref = cas.put(doc_bytes, media_type=MediaType("application/json"))

    print(f"  [resolve] Applied {len(ops)} override(s) -> {ref.hash}")

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="doc",
                hash=str(ref.hash),
                media_type="application/json",
                size=len(doc_bytes),
            ),
        ],
        metrics={"overrides_applied": len(ops)},
    )
