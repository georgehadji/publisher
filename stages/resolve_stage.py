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

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.overrides import (
    apply_overrides, parse_overrides, MalformedOverrideOp, OverrideOp, UnsupportedOverrideOp,
)


@stage(
    name="resolve",
    version=3,   # v2: an override op with no transform now fails the build instead of
                 # being skipped -- see publisher_structure.overrides.UnsupportedOverrideOp.
                 # v3: reads the overrides/1 shape (`from`/`to`/`at`, object sourceRef)
                 # via parse_overrides; v2's OverrideOp(**op) raised TypeError on it.
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
        try:
            ops = parse_overrides(json.loads(ops_path.read_bytes()))
        except (MalformedOverrideOp, json.JSONDecodeError) as exc:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"override log is not a valid overrides/1 document: {exc}",
                diagnostics=[Diagnostic(
                    code="override-log-malformed",
                    severity="error",
                    human_message=str(exc),
                    suggested_fix="Override ops enter through PATCH /v1/documents/:id/overrides, "
                                  "which validates them against overrides/1; a log written "
                                  "any other way must match that schema exactly.",
                )],
            ) from exc

    try:
        effective = apply_overrides(ast_doc, ops)
    except UnsupportedOverrideOp as exc:
        # BAD_INPUT, not an internal error: the override log is a valid `overrides/1`
        # document asking for something this layer cannot do. Surfaced as a diagnostic
        # rather than a traceback, because a reviewer made this decision and has to be
        # told it did not take effect (§3.15).
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=str(exc),
            diagnostics=[Diagnostic(
                code="override-op-unsupported",
                severity="error",
                human_message=str(exc),
                suggested_fix="Remove the override, or express the same intent with an "
                              "implemented op (reclassify, retitle, delete, flag_ambiguity).",
            )],
        ) from exc

    cas_root = Path(ctx.cas_root)
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
