"""Tests for DOCX -> ast/1 ingestion.

The rules exercised here are not stylistic: each one, if broken, produces a
build that fails the text-integrity gate three stages later with a diff offset
instead of a cause.
"""

from __future__ import annotations

import pytest

docx = pytest.importorskip("docx")

from publisher_ingest.docx_to_ast import (  # noqa: E402
    IngestError,
    _find_body_start,
    _group_headings,
    docx_to_ast,
)


def _write(tmp_path, paragraphs):
    """Build a .docx from (text, style) pairs and return its path."""
    document = docx.Document()
    for text, style in paragraphs:
        document.add_paragraph(text, style=style)
    path = tmp_path / "m.docx"
    document.save(str(path))
    return path


PROSE = "Καί " * 200  # comfortably over SUBSTANTIVE_PARAGRAPH_CHARS


def test_consecutive_upper_case_lines_form_one_title(tmp_path):
    path = _write(
        tmp_path,
        [
            ("ΠΡΟΛΟΓΟΣ", None),
            (PROSE, None),
            ("Β ΕΝΟΤΗΤΑ", None),
            ("ΜΗΧΑΝΙΚΗ ΨΥΧΗ", None),
            ("ΚΑΙ", None),
            (PROSE, None),
        ],
    )
    ast = docx_to_ast(path)
    titles = [c["attrs"]["title"] for c in ast["body"]]
    assert titles == ["ΠΡΟΛΟΓΟΣ", "Β ΕΝΟΤΗΤΑ ΜΗΧΑΝΙΚΗ ΨΥΧΗ ΚΑΙ"]


def test_table_of_contents_does_not_become_chapters(tmp_path):
    path = _write(
        tmp_path,
        [
            ("ΠΕΡΙΕΧΟΜΕΝΑ", None),
            ("ΠΡΟΛΟΓΟΣ", None),
            ("Α ΕΝΟΤΗΤΑ", None),
            ("ΠΡΟΛΟΓΟΣ", None),
            (PROSE, None),
        ],
    )
    ast = docx_to_ast(path)
    assert len(ast["body"]) == 1, "TOC entries were promoted to chapters"
    assert any(item["type"] == "toc" for item in ast["frontMatter"])


def test_long_epigraph_before_the_contents_page_is_not_chapter_one(tmp_path):
    """Front matter may contain real prose; only the TOC marker starts the scan.

    Note what is *not* asserted: that the chapter's title is exactly
    "ΠΡΟΛΟΓΟΣ". The contents page lists the same upper-case titles the
    chapters carry, so an entry and the heading it points at are the same
    string -- indistinguishable from a two-line display title. Both end up in
    the title here. No text is lost, and the boundary is what matters.
    """
    # Stripped: `read_blocks` strips each block, so a trailing space here would
    # make the needle unfindable in the haystack it is supposed to match.
    epigraph = ("Επίγραμμα " * 40).strip()
    chapter_prose = ("Κείμενο " * 60).strip()
    path = _write(
        tmp_path,
        [
            ("ΑΦΙΕΡΩΣΗ", None),
            (epigraph, None),
            ("ΠΕΡΙΕΧΟΜΕΝΑ", None),
            ("ΠΡΟΛΟΓΟΣ", None),
            (chapter_prose, None),
        ],
    )
    ast = docx_to_ast(path)

    assert len(ast["body"]) == 1, "the epigraph was promoted to a chapter"
    assert ast["body"][0]["attrs"]["title"] == "ΠΡΟΛΟΓΟΣ"
    assert epigraph in str(ast["frontMatter"])
    assert epigraph not in str(ast["body"])


def test_every_chapter_carries_a_title(tmp_path):
    """`extract` substitutes "Chapter N" for a missing title, which would put
    text in the HTML that no source text can match."""
    path = _write(tmp_path, [("ΠΡΟΛΟΓΟΣ", None), (PROSE, None)])
    ast = docx_to_ast(path)
    assert all(c["attrs"].get("title") for c in ast["body"])


def test_front_and_back_matter_carry_no_title_attribute(tmp_path):
    """`extract` never renders those titles, but the integrity checker counts
    any `attrs.title` it finds -- so one here is text on only one side."""
    path = _write(
        tmp_path,
        [
            ("ΤΙΤΛΟΣ", None),
            ("Ενα", None),
            ("ΠΡΟΛΟΓΟΣ", None),
            (PROSE, None),
            ("ΕΞΩ ΜΕΡΟΣ-ΟΠΙΣΘΟΦΥΛΛΟ", None),
            (PROSE, None),
        ],
    )
    ast = docx_to_ast(path)
    for item in [*ast["frontMatter"], *ast["backMatter"]]:
        assert "title" not in (item.get("attrs") or {})


def test_no_docx_text_is_dropped(tmp_path):
    path = _write(
        tmp_path,
        [
            ("ΤΙΤΛΟΣ", None),
            ("μια γραμμή", None),
            ("ΠΡΟΛΟΓΟΣ", None),
            (PROSE, None),
            ("ΕΞΩ ΜΕΡΟΣ-ΟΠΙΣΘΟΦΥΛΛΟ", None),
            ("οπισθόφυλλο", None),
        ],
    )
    ast = docx_to_ast(path)

    emitted: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                emitted.append(node["text"])
            if (node.get("attrs") or {}).get("title"):
                emitted.append(node["attrs"]["title"])
            for key in ("frontMatter", "body", "backMatter", "content"):
                walk(node.get(key))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(ast)
    haystack = " ".join(emitted)
    for expected in ("ΤΙΤΛΟΣ", "μια γραμμή", "ΠΡΟΛΟΓΟΣ", "ΕΞΩ ΜΕΡΟΣ-ΟΠΙΣΘΟΦΥΛΛΟ", "οπισθόφυλλο"):
        assert expected in haystack


def test_a_document_with_no_prose_is_an_error_not_an_empty_book(tmp_path):
    path = _write(tmp_path, [("ΜΟΝΟ", None), ("ΤΙΤΛΟΙ", None)])
    with pytest.raises(IngestError):
        docx_to_ast(path)


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(IngestError):
        docx_to_ast(tmp_path / "nope.docx")


def test_body_start_falls_back_to_first_heading_when_nothing_is_substantive():
    # Sections carry Blocks, not bare strings: a section body has to keep each
    # block's heading depth and footnote ids for the AST builder to read.
    from publisher_ingest.docx_to_ast import Block

    def section(title, *texts):
        return (title, [Block(t, "Normal", False) for t in texts])

    sections = [section("", "x"), section("A", "short"), section("B", "also short")]
    assert _find_body_start(sections) == 0


def test_grouping_keeps_leading_matter_untitled():
    from publisher_ingest.docx_to_ast import Block

    blocks = [
        Block("front line", "Normal", False),
        Block("TITLE", "Normal", True),
        Block("body line", "Normal", False),
    ]
    assert _group_headings(blocks) == [("", ["front line"]), ("TITLE", ["body line"])]


# ── U5/S9: XXE verification ────────────────────────────────────
#
# The plan says "verify, do not assume" that python-docx's lxml parser resolves
# no external entities. This crafts a DOCX whose document.xml declares an
# external entity pointing at THIS test file, then asserts the entity's target
# content never appears in the AST -- either the parse refuses loudly, or it
# succeeds with the entity unresolved. Either way: no leak.

def _xxe_docx(path: Path, entity_url: str) -> Path:
    """Hand-build a DOCX whose document.xml expands an external entity."""
    import zipfile

    content_types = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<!DOCTYPE r [<!ENTITY xxe SYSTEM "{entity_url}">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    return path


def test_external_entity_in_docx_is_not_resolved(tmp_path):
    from pathlib import Path

    marker = "PUBLISHER_XXE_MARKER_7f3a"
    # The entity points at this very test file, which contains `marker` above.
    target_url = Path(__file__).resolve().as_uri()
    docx_path = _xxe_docx(tmp_path / "xxe.docx", target_url)

    try:
        ast = docx_to_ast(docx_path)
    except Exception:
        # Refused loudly -- equally acceptable: no silent expansion.
        return

    import json

    assert marker not in json.dumps(ast, ensure_ascii=False), (
        "external entity was resolved -- the AST contains the entity target's "
        "content (XXE). lxml must not load external entities."
    )
