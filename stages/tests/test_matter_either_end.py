"""
`alsoBy` and `aboutTheAuthor` sit at either end of a book (B4,
docs/STRUCTURE_REPAIR_PLAN.md): `ast/1` accepts them in the front matter, and
both renderers set a front-matter one as front matter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from stages.rendering import ast_to_epub_sections, ast_to_html

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schemas/ast/ast.schema.json")
                    .read_text(encoding="utf-8"))


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _book(kind: str) -> dict:
    return {
        "schema": "ast/1",
        "integrityHash": "sha256:" + "0" * 64,
        "sourceRef": {"manuscriptId": "t", "inferenceVersion": 1, "createdAt": "2026-01-01T00:00:00Z"},
        "frontMatter": [{"type": kind, "content": [_para("ALSO BY THE AUTHOR"), _para("The First Book")]}],
        "body": [{"type": "chapter", "attrs": {"number": 1, "id": "ch1", "title": "One"},
                  "content": [_para("prose")]}],
    }


@pytest.mark.parametrize("kind", ["alsoBy", "aboutTheAuthor"])
def test_ast_accepts_it_in_the_front_matter(kind):
    errors = list(Draft202012Validator(SCHEMA).iter_errors(_book(kind)))
    assert not errors, errors[0].message


def test_a_back_matter_only_type_is_still_refused_in_the_front_matter():
    assert list(Draft202012Validator(SCHEMA).iter_errors(_book("colophon")))


def test_a_front_also_by_renders_as_front_matter_in_print_and_epub():
    book = _book("alsoBy")
    html = ast_to_html(book)
    assert '<div class="front-matter alsoBy">' in html
    assert html.index('class="front-matter alsoBy"') < html.index('class="chapter-title"')
    first = ast_to_epub_sections(book)[0]
    assert (first["matter"], first["title"]) == ("frontmatter", "Also By")
    assert "ALSO BY" in first["body"]
