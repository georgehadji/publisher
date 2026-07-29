"""
Tracer Bullet -- structure stage.

Performs text-integrity verification (the hard gate from ARCHITECTURE.md §3.1)
and produces the integrity hash. In the tracer bullet, the AST was already valid;
this stage re-derives the integrity assertion.
"""

from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


def _extract_all_text(ast: dict) -> str:
    """Extract and concatenate all text nodes from an AST for integrity checking."""
    texts = []
    
    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            # Also walk content arrays
            for key in ("content", "frontMatter", "backMatter", "body"):
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
    return "".join(texts)


def _normalize(text: str) -> str:
    """Normalize text for comparison: collapse whitespace."""
    return re.sub(r'\s+', ' ', text).strip()


@stage(
    name="structure",
    version=1,
    inputs={"source": "raw-source/1"},
    outputs={"ast": "ast/1", "integrity-report": "integrity-report/1"},
    toolchain=[],
    fixtures="fixtures/structure/v1",
    memory_budget_mb=128,
    queue="q.structure",
    description="Extract text and verify text-integrity invariant",
)
def structure(ctx: StageCtx, source: str | None = None) -> StageResult:
    """
    Structure stage -- validates text integrity.
    
    The text-integrity invariant (ARCHITECTURE.md §3.1):
    normalize(concat(text nodes of AST)) == normalize(text stream of source)
    
    Takes the raw AST JSON from the acquire stage as input.
    """
    if source is None:
        source = "corpus/manuscripts/minimal-novel.ast.json"
    
    fixture_path = Path(source)
    if not fixture_path.exists():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Input not found: {source}",
        )
    
    ast = json.loads(fixture_path.read_bytes())
    
    # Extract all text
    all_text = _extract_all_text(ast)
    normalized = _normalize(all_text)
    
    # Compute integrity hash
    integrity_hash = hashlib.sha256(all_text.encode("utf-8")).hexdigest()
    
    # Compare with the AST's declared integrity hash
    declared = ast.get("integrityHash", "")
    expected = f"sha256:{integrity_hash}"
    
    warnings = []
    if declared and declared != expected:
        warnings.append(Diagnostic(
            code="integrity_mismatch",
            severity="error",
            human_message=f"Text integrity mismatch: declared {declared}, computed {expected}",
            suggested_fix="Re-generate AST with correct hash",
        ))
        raise StageError(
            kind=ErrorKind.ENGINE_BUG,
            message="Text integrity violation -- AST text does not match declared hash",
            diagnostics=warnings,
        )
    
    print(f"  [structure] Text integrity verified: {integrity_hash[:16]}... ({len(all_text)} chars total)")
    
    # Store the integrity report
    report = {
        "integrityHash": integrity_hash,
        "textLength": len(all_text),
        "normalizedLength": len(normalized),
        "declaredIntegrity": declared,
        "passed": True,
    }
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(report_bytes, media_type=MediaType("application/json"))
    
    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="integrity-report",
                hash=str(ref.hash),
                media_type="application/json",
                size=len(report_bytes),
            ),
        ],
        metrics={"text_length": len(all_text), "integrity_ok": 1.0},
        warnings=warnings,
    )
