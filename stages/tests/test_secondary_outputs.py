"""
`epub` and `onix` stages (E7.2, docs/ARCHITECTURE_SCORE_10_PLAN.md).

Unlike `idml`, neither is gated behind an opt-in import: both need no
external toolchain, so they register unconditionally and are reachable in
every ordinary build the moment `resolve` is. The reachability tests below
are what proves that -- not just that the stage functions exist.
"""

from __future__ import annotations
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stages  # noqa: F401 -- registers every stage, including epub/onix
from publisher_stages import StageCtx, StageError, ErrorKind, RegistryConfig, build_registry
from publisher_exec import plan
from stages.secondary_output_stages import epub, onix


def _selected_registry():
    """A properly SELECTED registry -- worker.py's own production shape. See
    stages/tests/test_structure_infer.py for why the raw get_registry() is
    the wrong tool here (ingest/acquire, design-compile/-typst are
    genuinely ambiguous without a selection)."""
    return build_registry(RegistryConfig(ingest_impl="ingest"))


def _ctx(tmp_path: Path) -> StageCtx:
    return StageCtx(
        build_id="test-secondary",
        deterministic_seed="test-secondary",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=128,
        work_dir=str(tmp_path),
        cas_root=str(tmp_path / "cas"),
    )


DOC = {
    "schema": "doc-effective/1",
    "metadata": {"title": "Test Book", "isbn": "9781234567890", "language": "en-US",
                 "contributors": [{"role": "author", "displayName": "Author Name"}]},
    "frontMatter": [],
    "body": [
        {"type": "chapter", "attrs": {"id": "ch1", "number": 1, "title": "One"},
         "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Words."}]}]},
        {"type": "chapter", "attrs": {"id": "ch2", "number": 2, "title": "Two"},
         "content": [{"type": "paragraph", "content": [{"type": "text", "text": "More words."}]}]},
    ],
    "backMatter": [],
}


def _doc_path(tmp_path: Path) -> Path:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(DOC), encoding="utf-8")
    return path


# ── reachability: both are always-on, unlike the gated idml/structure-infer ──

def test_epub_and_onix_are_reachable_in_an_ordinary_build():
    reachable = plan(_selected_registry(), {"ingest": {"docx_path": "/fake/manuscript.docx"}}).order
    assert "epub" in reachable
    assert "onix" in reachable
    # Reachable BECAUSE resolve is -- not vacuously true.
    assert "resolve" in reachable


# ── epub ──────────────────────────────────────────────────────────────────

def test_epub_stage_writes_a_valid_package(tmp_path):
    ctx = _ctx(tmp_path)
    result = epub(ctx, doc_path=str(_doc_path(tmp_path)))

    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art.kind == "epub"
    assert art.media_type == "application/epub+zip"

    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    with zipfile.ZipFile(stored) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert zf.read("mimetype").decode() == "application/epub+zip"
        sections = [n for n in names if n.startswith("OEBPS/s") and n.endswith(".xhtml")]
        assert len(sections) == 2

    assert result.metrics["chapters"] == 2.0


def _png(tmp_path: Path, fmt: str = "PNG") -> tuple[bytes, str]:
    """An image stored in the test CAS the way ingest stores one."""
    import hashlib
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(out, format=fmt)
    data = out.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    blob = tmp_path / "cas" / digest[:2] / digest[2:4] / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(data)
    return data, digest


def _text(text: str, *marks: str) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


def _rich_doc(digest: str, media_type: str = "image/png") -> dict:
    """Everything the old writer dropped: marks, footnotes, a table, a figure,
    a sidebar, front and back matter -- and text after an inline element."""
    para = lambda *runs: {"type": "paragraph", "content": list(runs)}  # noqa: E731
    return {
        "schema": "doc-effective/1",
        "metadata": {"title": "Βιβλίο", "language": "el"},
        "frontMatter": [{"type": "toc", "content": [para(_text("Contents page."))]}],
        "body": [{"type": "chapter", "attrs": {"id": "ch1", "number": 1, "title": "Πρώτο"},
                  "content": [
                      para(_text("Before "), _text("ἐν ἀρχῇ", "emphasis"), _text(" after.")),
                      {"type": "footnote", "attrs": {"number": 1}, "content": [_text("First note.")]},
                      {"type": "footnote", "attrs": {"number": 2}, "content": [_text("Second note.")]},
                      {"type": "table", "content": [{"type": "tableRow", "content": [
                          {"type": "tableCell", "content": [para(_text("Cell text."))]}]}]},
                      {"type": "figure", "attrs": {"caption": "A plate.", "altText": "",
                                                   "mediaRef": {"hash": digest, "mediaType": media_type}}},
                      {"type": "sidebar", "content": [para(_text("Boxed aside."))]},
                  ]}],
        "backMatter": [{"type": "bibliography", "content": [para(_text("A source, 2008."))]}],
    }


def _epub_files(ctx, result) -> dict[str, bytes]:
    art = result.artifacts[0]
    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    with zipfile.ZipFile(stored) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_the_epub_carries_everything_the_print_book_does(tmp_path):
    ctx = _ctx(tmp_path)
    data, digest = _png(tmp_path)
    doc = tmp_path / "rich.json"
    doc.write_text(json.dumps(_rich_doc(digest)), encoding="utf-8")

    result = epub(ctx, doc_path=str(doc))
    files = _epub_files(ctx, result)
    xhtml = "".join(v.decode("utf-8") for k, v in files.items() if k.endswith(".xhtml") and "/s" in k)

    assert "Before <em>ἐν ἀρχῇ</em> after." in xhtml          # marks, and word order
    assert xhtml.count('epub:type="noteref"') == 2
    assert '<aside epub:type="footnote" id="fn-2"><p>Second note.</p></aside>' in xhtml
    assert "Cell text." in xhtml and "Boxed aside." in xhtml
    assert "Contents page." in xhtml and "A source, 2008." in xhtml
    assert files[f"OEBPS/media/{digest}.png"] == data
    assert result.metrics["sections"] == 3.0
    assert result.metrics["footnotes"] == 2.0
    assert result.metrics["images"] == 1.0
    assert result.metrics["integrity_ok"] == 1.0


def test_an_epub_that_loses_text_is_not_stored(tmp_path, monkeypatch):
    """The check reads the written package back, so a renderer that drops a
    block -- as the old writer dropped every footnote -- fails the stage."""
    import stages.secondary_output_stages as module

    real = module.ast_to_epub_sections

    def lossy(doc):
        sections = real(doc)
        sections[1]["body"] = sections[1]["body"].replace("<p>First note.</p>", "<p></p>")
        return sections

    monkeypatch.setattr(module, "ast_to_epub_sections", lossy)
    ctx = _ctx(tmp_path)
    _, digest = _png(tmp_path)
    doc = tmp_path / "rich.json"
    doc.write_text(json.dumps(_rich_doc(digest)), encoding="utf-8")

    with pytest.raises(StageError) as exc:
        epub(ctx, doc_path=str(doc))
    assert exc.value.kind == ErrorKind.ENGINE_BUG
    assert exc.value.diagnostics[0].code == "integrity_mismatch"
    stored = [p for p in Path(ctx.cas_root).rglob("*") if p.is_file()]
    assert not any(p.read_bytes()[:2] == b"PK" for p in stored)   # no EPUB reached CAS


def test_a_tiff_figure_is_packaged_as_png(tmp_path):
    ctx = _ctx(tmp_path)
    _, digest = _png(tmp_path, fmt="TIFF")
    doc = tmp_path / "rich.json"
    doc.write_text(json.dumps(_rich_doc(digest, "image/tiff")), encoding="utf-8")

    files = _epub_files(ctx, epub(ctx, doc_path=str(doc)))
    assert files[f"OEBPS/media/{digest}.png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert not any(name.endswith(".tif") for name in files)
    assert f'src="media/{digest}.png"' in files["OEBPS/s002.xhtml"].decode("utf-8")


def test_a_figure_missing_from_cas_fails_the_stage(tmp_path):
    ctx = _ctx(tmp_path)
    doc = tmp_path / "rich.json"
    doc.write_text(json.dumps(_rich_doc("ab" * 32)), encoding="utf-8")
    with pytest.raises(StageError) as exc:
        epub(ctx, doc_path=str(doc))
    assert exc.value.kind == ErrorKind.INFRA


def test_epub_stage_refuses_a_document_that_never_reached_resolve(tmp_path):
    with pytest.raises(StageError) as exc:
        epub(_ctx(tmp_path), doc_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT


# ── onix ──────────────────────────────────────────────────────────────────

def test_onix_stage_writes_valid_metadata(tmp_path):
    ctx = _ctx(tmp_path)
    result = onix(ctx, doc_path=str(_doc_path(tmp_path)))

    assert len(result.artifacts) == 1
    art = result.artifacts[0]
    assert art.kind == "onix"
    assert art.media_type == "application/xml"

    stored = Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash
    content = stored.read_text(encoding="utf-8")
    assert "ONIXMessage" in content
    assert "3.0" in content
    assert "Test Book" in content


def test_onix_stage_refuses_a_document_that_never_reached_resolve(tmp_path):
    with pytest.raises(StageError) as exc:
        onix(_ctx(tmp_path), doc_path=None)
    assert exc.value.kind == ErrorKind.BAD_INPUT


def test_link_targets_with_characters_urls_forbid_are_encoded():
    """The first real book links into Perseus with raw backslashes and `|` in the query;
    EPUBCheck rejected all twelve (RSC-020). Non-ASCII stays: HTML takes IRIs."""
    from stages.rendering import ast_to_html

    href = r"http://www.perseus.tufts.edu/hopper/morph?l=o%29%5Cn&prior=a)gaqo\n|"
    doc = json.loads(json.dumps(DOC))
    doc["body"][0]["content"] = [{"type": "paragraph", "content": [
        {"type": "text", "text": "link", "marks": [{"type": "link", "attrs": {"href": href}}]},
        {"type": "text", "text": "greek", "marks": [{"type": "link", "attrs": {"href": "https://έ.gr/Σ"}}]}]}]
    html = ast_to_html(doc)
    assert 'href="http://www.perseus.tufts.edu/hopper/morph?l=o%29%5Cn&amp;prior=a)gaqo%5Cn%7C"' in html
    assert 'href="https://έ.gr/Σ"' in html


def test_epubcheck_accepts_the_epub(tmp_path):
    """BUILD_PLAN.md §3.12's gate. EPUBCheck is Java and not on the worker
    toolchain, so this runs where PUBLISHER_EPUBCHECK_JAR names its jar
    (w3c/epubcheck releases). The first real book validated clean with 5.4.0."""
    import os
    import shutil
    import subprocess

    jar = os.environ.get("PUBLISHER_EPUBCHECK_JAR")
    if not jar or not Path(jar).is_file() or not shutil.which("java"):
        pytest.skip("set PUBLISHER_EPUBCHECK_JAR to an epubcheck.jar (and have java) to run")
    ctx = _ctx(tmp_path)
    _, digest = _png(tmp_path)
    doc = tmp_path / "rich.json"
    doc.write_text(json.dumps(_rich_doc(digest)), encoding="utf-8")
    art = epub(ctx, doc_path=str(doc)).artifacts[0]
    book = tmp_path / "book.epub"
    book.write_bytes((Path(ctx.cas_root) / art.hash[:2] / art.hash[2:4] / art.hash).read_bytes())
    run = subprocess.run(["java", "-jar", jar, str(book)], capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
