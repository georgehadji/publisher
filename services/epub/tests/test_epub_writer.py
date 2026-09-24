"""
The EPUB writer packages rendered sections; these check the package itself.

What goes INTO the sections (and whether the book's text survives) is the
`epub` stage's test: stages/tests/test_secondary_outputs.py.
"""

from __future__ import annotations

import zipfile

import pytest

from publisher_epub import EPUB3Writer, EPUBError, Media, spine_text

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _sections(*bodies: str) -> list[dict]:
    return [{"id": f"s{i:03d}", "title": f"Part {i}", "matter": "bodymatter", "body": body}
            for i, body in enumerate(bodies, 1)]


def _write(tmp_path, sections, **kwargs):
    return EPUB3Writer(sections, **kwargs).write(tmp_path / "book.epub")


def test_mimetype_is_the_first_entry_and_stored(tmp_path):
    """Readers identify an EPUB by an uncompressed `mimetype` at offset 0; the
    old writer deflated it, which EPUBCheck rejects."""
    with zipfile.ZipFile(_write(tmp_path, _sections("<p>x</p>"))) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"


def test_the_package_declares_what_epub_requires(tmp_path):
    """The old OPF had no dc:identifier, dc:title or dc:language, all required."""
    path = _write(tmp_path, _sections("<p>x</p>"),
                  metadata={"title": "Βιβλίο", "language": "el", "isbn": "9781234567897"})
    with zipfile.ZipFile(path) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:identifier id=\"book-id\">urn:isbn:9781234567897</dc:identifier>" in opf
    assert "<dc:title>Βιβλίο</dc:title>" in opf
    assert "<dc:language>el</dc:language>" in opf
    assert "schema:accessMode" in opf


def test_the_same_input_writes_the_same_bytes(tmp_path):
    """Content-addressed output: zip entry mtimes must not come from the clock."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _write(tmp_path / "a", _sections("<p>x</p>"), metadata={"title": "T"})
    second = _write(tmp_path / "b", _sections("<p>x</p>"), metadata={"title": "T"})
    assert first.read_bytes() == second.read_bytes()


def test_a_section_that_is_not_well_formed_is_refused(tmp_path):
    with pytest.raises(EPUBError, match="s001"):
        _write(tmp_path, _sections("<p>unclosed"))


def test_an_image_that_was_not_supplied_is_refused(tmp_path):
    with pytest.raises(EPUBError, match="media/x.png"):
        _write(tmp_path, _sections('<figure><img src="media/x.png" alt=""/></figure>'))


def test_a_non_core_image_type_is_refused(tmp_path):
    with pytest.raises(EPUBError, match="image/tiff"):
        _write(tmp_path, _sections("<p>x</p>"), media={"media/x.tif": Media("image/tiff", b"II*")})


def test_images_are_packaged_and_listed(tmp_path):
    path = _write(tmp_path, _sections('<figure><img src="media/x.png" alt=""/></figure>'),
                  media={"media/x.png": Media("image/png", PNG)})
    with zipfile.ZipFile(path) as zf:
        assert zf.read("OEBPS/media/x.png") == PNG
        assert 'href="media/x.png" media-type="image/png"' in zf.read("OEBPS/content.opf").decode()


def test_spine_text_reads_in_order_and_skips_note_calls(tmp_path):
    body = ('<p>Before <em>marked</em> after<a epub:type="noteref" href="#fn-1">1</a>.</p>'
            '<aside epub:type="footnote" id="fn-1"><p>The note.</p></aside>')
    path = _write(tmp_path, _sections(body, "<p>Second part.</p>"), metadata={"title": "Head title"})
    text = " ".join(spine_text(path).split())
    assert text == "Before marked after. The note. Second part."
