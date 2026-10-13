"""
A paragraph's `fit` (K2): the lever the Compositor's proposals pull. It must
reach both print renderers, stay out of the EPUB, and -- the test that matters --
actually move a line in a real render. Without that last one the lever is
decorative.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stages.rendering import FIT_TRACKING, _render_content, ast_to_epub_sections, emit_css

ROOT = Path(__file__).resolve().parents[2]


def _para(text: str, fit: str | None = None, ref: str = "long") -> dict:
    return {"type": "paragraph", "sourceRef": {"docxId": ref}, **({"attrs": {"fit": fit}} if fit else {}),
            "content": [{"type": "text", "text": text}]}


def test_the_schema_and_the_renderer_name_the_same_steps():
    ast = json.loads((ROOT / "schemas/ast/ast.schema.json").read_text(encoding="utf-8"))
    assert ast["$defs"]["paragraph"]["properties"]["attrs"]["properties"]["fit"]["enum"] == list(FIT_TRACKING)


def test_print_sets_the_text_in_a_fit_span_the_stylesheet_tracks_and_the_epub_does_not():
    html = _render_content([_para("one two three", "tight"), _para("as designed")])
    assert '<span class="fit-tight">one <span class="keep">two three</span></span></p>' in html
    assert html.count("fit-") == 1
    css = emit_css({})
    assert all(f".fit-{name} {{ letter-spacing: {em:g}em; }}" in css for name, em in FIT_TRACKING.items())
    book = {"schema": "ast/1", "body": [{"type": "chapter", "attrs": {"number": 1, "id": "c1", "title": "One"},
                                         "content": [_para("one two three", "tight")]}]}
    body = ast_to_epub_sections(book)[0]["body"]
    assert "one two three" in body and "fit-" not in body


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc on PATH")
def test_the_typst_path_honours_fit_rather_than_dropping_it(tmp_path):
    from stages.typst_stages import FOOTNOTE_FILTER_LUA
    lua = tmp_path / "f.lua"
    lua.write_text(FOOTNOTE_FILTER_LUA, encoding="utf-8")
    html = _render_content([_para("one [two] three", "tighter")])
    out = subprocess.run(["pandoc", "--from=html", "--to=typst", "--lua-filter", str(lua)],
                         input=html.encode("utf-8"), capture_output=True, check=True).stdout.decode("utf-8")
    assert out.strip() == r"#text(tracking: -0.02em)[one \[two\] three]"


# The K1 measurement page: about nine lines of 9pt, widows allowed so one can be made.
CSS = "@page{size:200pt 120pt;margin:8pt} p{font-size:9pt;margin:0;orphans:1;widows:1}"
WORDS = "the quick brown fox jumps over a lazy dog while seven ample wizards quietly hum".split()


def test_tightening_a_widowed_paragraph_pulls_its_last_line_back():
    """Searched for, not hard-coded (see test_pagemap_measurement): where a line
    breaks depends on the host's fonts. What is asserted once found holds anywhere:
    the same paragraph, rendered by the same renderer and stylesheet, widows as
    designed and does not once its `fit` is `tight`."""
    weasyprint = pytest.importorskip("weasyprint")
    from stages.paginate_stage import _measure_pages
    fit_css = "".join(line for line in emit_css({}).splitlines() if line.startswith(".fit-"))

    def widows(words: int, fit: str | None) -> list[list[str]]:
        text = " ".join(WORDS[i % len(WORDS)] for i in range(words))
        html = _render_content([_para(text, fit), _para("tail here", ref="tail")])
        pages = _measure_pages(weasyprint.HTML(string=f"<style>{CSS}{fit_css}</style>{html}").render().pages)
        return [page.get("widowRefs", []) for page in pages.values() if page["hasWidows"]]

    for words in range(40, 400):
        designed = widows(words, None)
        if designed and widows(words, "tight") == []:
            assert designed == [["long"]]
            return
    pytest.fail("no widow that `fit: tight` pulls back across the swept paragraph lengths")
