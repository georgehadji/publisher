"""
Tracer Bullet -- extract stage.

Converts a manuscript fixture (AST JSON) into a "typescript HTML" representation.
In the tracer bullet, this reads the synthetic AST and produces HTML fragments.
For real production, this would use Saxon/XSweet on actual DOCX files.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType, ArtifactRef

# E1.3: ast_to_html moved to stages/rendering.py -- it is a public shared
# module now, not a private cross-sibling import (paginate_stage.py,
# idml_stage.py and typst_stages.py all call this SAME function object; see
# rendering.py's module docstring for why that identity matters).
from stages.rendering import ast_to_html


@stage(
    name="extract",
    version=6,   # v6: notes carry over in document order (data-seq, paginate_stage.notes_in_document_order); no footnote-policy: line (it stranded lines: 27 widows, 23 one-line pages). v5: keep span: a paragraph's last two words never split in print (no runts). v2: footnotes render inside the paragraph that cites them (a lone call number no longer gets a line).
                 # v3: link hrefs are percent-encoded (rendering._safe_href).
                 # v4: long footnotes set in pieces, own note numbers (rendering.PrintNotes), footnote area capped.
    inputs={"source": "raw-source/1"},
    outputs={"html": "typescript-html/1"},
    toolchain=[],
    fixtures="fixtures/extract/v1",
    memory_budget_mb=128,
    queue="q.ingest",
    description="Convert manuscript fixture to typescript HTML",
)
def extract(ctx: StageCtx, source: str | None = None) -> StageResult:
    """
    Extract stage: loads AST JSON from CAS, converts to flat HTML.
    
    In the tracer bullet, source is a path to an AST JSON file,
    or a fixture path.
    """
    if source is None:
        # Default fixture for tracer bullet
        source = "corpus/manuscripts/minimal-novel.ast.json"
    
    fixture_path = Path(source)
    if not fixture_path.exists():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Source not found: {source}",
        )
    
    ast = json.loads(fixture_path.read_bytes())
    html = ast_to_html(ast)
    
    # Store in CAS
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(html.encode("utf-8"), media_type=MediaType("text/html"))
    
    print(f"  [extract] Generated HTML from {fixture_path.name} -> {ref.hash} ({len(html)} chars)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="html",   # must exactly equal the declared output key "html"
            hash=str(ref.hash),
            media_type="text/html",
            size=len(html),
        )],
        metrics={"html_size_chars": len(html), "paragraph_count": html.count("<p ")},
    )
