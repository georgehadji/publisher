"""DOCX manuscript ingestion -- the front door of the pipeline.

`docx_to_ast` is the missing first step the rest of the pipeline assumed
existed: it turns a real `.docx` into an `ast/1` document, which is what
`acquire`/`extract` have always actually consumed (the `raw-source/1` schema
is AST JSON, not DOCX, despite the name).
"""

from __future__ import annotations

from .docx_to_ast import (
    docx_to_ast,
    Block,
    read_blocks,
    IngestError,
)

__all__ = ["docx_to_ast", "read_blocks", "Block", "IngestError"]
