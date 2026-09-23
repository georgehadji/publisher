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


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_body_start_falls_back_to_first_heading_when_nothing_is_substantive():
    sections = [("", [_para("x")]), ("A", [_para("short")]), ("B", [_para("also short")])]
    assert _find_body_start(sections) == 0


def test_grouping_keeps_leading_matter_untitled():
    """Sections carry AST nodes, not strings: a block now reaches the AST with
    its marks, figures and footnotes attached, so grouping moves nodes."""
    from publisher_ingest.docx_to_ast import Block

    front, body = _para("front line"), _para("body line")
    blocks = [
        Block("front line", "Normal", False, (front,)),
        Block("TITLE", "Normal", True, heading_confidence=0.7),
        Block("body line", "Normal", False, (body,)),
    ]
    assert _group_headings(blocks) == [("", [front], None), ("TITLE", [body], 0.7)]


# ── Confidence: scored where the structural decision is made ────
#
# `rules.py` scores HTML blocks, but the real AST is built HERE, from a DOCX,
# by heuristics that never recorded how sure they were. Every downstream reader
# (review UI, agent tools, the API) then had nothing to read and reported the
# book as certain. These pin both halves: the score reflects the evidence the
# decision rested on, and the AST that carries it is still schema-valid.

from publisher_ingest.docx_to_ast import (  # noqa: E402
    BACK_MATTER_BY_PATTERN,
    BODY_SPLIT_BY_PROSE,
    BODY_SPLIT_FALLBACK,
    STYLED_HEADING,
    STYLED_UPPER_HEADING,
    UPPER_ONLY_HEADING,
)


def _chapter_confidence(tmp_path, title, style):
    path = _write(tmp_path, [(title, style), (PROSE, None)])
    (chapter,) = docx_to_ast(path)["body"]
    return chapter["confidence"]


@pytest.mark.parametrize("title,style,expected", [
    ("CHAPTER ONE", "Heading 1", STYLED_UPPER_HEADING),
    ("Chapter One", "Heading 1", STYLED_HEADING),
    ("CHAPTER ONE", None, UPPER_ONLY_HEADING),
])
def test_chapter_confidence_follows_the_evidence(tmp_path, title, style, expected):
    assert _chapter_confidence(tmp_path, title, style) == expected


def test_capitalisation_alone_escalates_for_review(tmp_path):
    """The unstyled manuscript is the common case, and upper case is also what a
    shouted line of dialogue looks like. Below 0.8 is where rules.py escalates."""
    assert _chapter_confidence(tmp_path, "NO!", None) < 0.8


def test_a_multi_line_title_is_as_sure_as_its_weakest_line(tmp_path):
    path = _write(tmp_path, [("PART ONE", "Heading 1"), ("THE ROAD", None), (PROSE, None)])
    (chapter,) = docx_to_ast(path)["body"]
    assert chapter["attrs"]["title"] == "PART ONE THE ROAD"
    assert chapter["confidence"] == UPPER_ONLY_HEADING


def test_front_matter_confidence_is_the_body_boundarys(tmp_path):
    found = docx_to_ast(_write(tmp_path, [
        ("DEDICATION", None), ("for mum", None), ("ONE", None), (PROSE, None),
    ]))
    assert [f["confidence"] for f in found["frontMatter"]] == [BODY_SPLIT_BY_PROSE]

    # No section clears the prose bar (a poetry collection looks like this), so
    # the boundary is the TOC marker by default -- a guess, and scored as one.
    (tmp_path / "guess").mkdir()
    guessed = docx_to_ast(_write(tmp_path / "guess", [
        ("DEDICATION", None), ("for mum", None),
        ("ΠΕΡΙΕΧΟΜΕΝΑ", None), ("ONE", None), ("short", None),
    ]))
    assert [f["confidence"] for f in guessed["frontMatter"]] == [BODY_SPLIT_FALLBACK]


def test_back_matter_confidence(tmp_path):
    ast = docx_to_ast(_write(tmp_path, [("ONE", None), (PROSE, None), ("COLOPHON", None), ("Set in Garamond", None)]))
    assert [b["confidence"] for b in ast["backMatter"]] == [BACK_MATTER_BY_PATTERN]


def test_scored_ast_is_schema_valid(tmp_path):
    """The schema is `additionalProperties: false` on every node; before
    `confidence` was declared there, emitting it would have failed validation."""
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[3] / "schemas" / "ast" / "ast.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    ast = docx_to_ast(_write(tmp_path, [
        ("DEDICATION", None), ("for mum", None), ("CHAPTER ONE", "Heading 1"), (PROSE, None),
        ("COLOPHON", None), ("Set in Garamond", None),
    ]))
    sections = ast["frontMatter"] + ast["body"] + ast["backMatter"]
    assert len(ast["frontMatter"]) == len(ast["body"]) == len(ast["backMatter"]) == 1
    assert all("confidence" in n for n in sections)
    jsonschema.validate(ast, schema)


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


# ── E3.4 (docs/ARCHITECTURE_SCORE_10_PLAN.md): audit the OOXML XML path ──
#
# The XXE test above exercises document.xml, which python-docx's own
# Document() constructor parses. word/footnotes.xml is a SEPARATE parse call
# (docx_rich.read_footnotes(), via a bare lxml etree.fromstring() -- python-docx
# has no footnote API) that the XXE test above never touches. These two verify
# it independently rather than assuming it inherits document.xml's protection
# just because it is "the same library".

def _docx_with_footnotes(tmp_path: "Path", footnotes_xml: bytes) -> "Path":
    """A real DOCX, built via python-docx, with a properly-declared
    word/footnotes.xml part added after the fact -- valid content-type
    Override and relationship, so read_footnotes()'s `iter_parts()` walk
    actually discovers it, the same as a real document with footnotes would."""
    import zipfile
    from pathlib import Path

    base = tmp_path / "base.docx"
    docx.Document().save(str(base))

    content_types_override = (
        b'<Override PartName="/word/footnotes.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
    )
    footnotes_rel = (
        b'<Relationship Id="rIdFootnotesTest" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" '
        b'Target="footnotes.xml"/>'
    )

    out = tmp_path / "with-footnotes.docx"
    with zipfile.ZipFile(base) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", content_types_override + b"</Types>")
            elif item.filename == "word/_rels/document.xml.rels":
                data = data.replace(b"</Relationships>", footnotes_rel + b"</Relationships>")
            dst.writestr(item, data)
        dst.writestr("word/footnotes.xml", footnotes_xml)
    return out


def test_external_entity_in_footnotes_xml_is_not_resolved(tmp_path):
    from pathlib import Path

    marker = "PUBLISHER_XXE_FOOTNOTE_MARKER_9c1b"
    target_url = Path(__file__).resolve().as_uri()
    footnotes_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE r [<!ENTITY xxe SYSTEM "{target_url}">]>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:id="1" w:type="normal">'
        '<w:p><w:r><w:t>&xxe;</w:t></w:r></w:p>'
        "</w:footnote>"
        "</w:footnotes>"
    ).encode("utf-8")
    docx_path = _docx_with_footnotes(tmp_path, footnotes_xml)

    try:
        ast = docx_to_ast(docx_path)
    except Exception:
        return  # refused loudly -- equally acceptable: no silent expansion

    import json

    assert marker not in json.dumps(ast, ensure_ascii=False), (
        "external entity in footnotes.xml was resolved -- the AST contains "
        "the entity target's content (XXE)."
    )


def test_billion_laughs_in_footnotes_xml_is_refused_as_bad_input(tmp_path):
    # Verified empirically (not assumed) that lxml's built-in entity-
    # amplification guard already refuses this, raising XMLSyntaxError --
    # this asserts docx_to_ast reclassifies that as IngestError/BAD_INPUT
    # instead of letting it leak as an unclassified crash.
    footnotes_xml = (
        '<?xml version="1.0"?>'
        "<!DOCTYPE lolz ["
        '<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        '<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">'
        '<!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">'
        '<!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">'
        "]>"
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:id="1" w:type="normal">'
        "<w:p><w:r><w:t>&lol6;</w:t></w:r></w:p>"
        "</w:footnote>"
        "</w:footnotes>"
    ).encode("utf-8")
    docx_path = _docx_with_footnotes(tmp_path, footnotes_xml)

    with pytest.raises(IngestError):
        docx_to_ast(docx_path)
