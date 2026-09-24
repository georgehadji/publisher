"""
Chapters and front/back-matter sections start where the design says -- measured
on a real weasyprint render, not read off the stylesheet.

The stylesheet asked for `page-break-before: recto`, which is not CSS 2 (that
property takes left/right) and which weasyprint drops silently. Nothing looked:
chapters still opened on a fresh page because their named `@page` changes, so
every build looked right at a glance while no chapter was ever placed on a recto.
The first real manuscript showed it -- its contents page ran on from the
epigraph, mid-page.
"""

from __future__ import annotations

import pytest

from stages.rendering import ast_to_html, emit_css

weasyprint = pytest.importorskip("weasyprint")


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


BOOK = {
    "schema": "ast/1",
    "metadata": {"title": "Breaks"},
    "frontMatter": [
        {"type": "titlePage", "content": [_para("TITLE PAGE")]},
        {"type": "toc", "content": [_para("CONTENTS PAGE")]},
    ],
    "body": [
        {"type": "chapter", "attrs": {"number": n, "title": f"Chapter {n}", "id": f"ch{n}"},
         "content": [_para(f"CHAPTER {n} TEXT")]}
        for n in (1, 2, 3)
    ],
    "backMatter": [{"type": "bibliography", "content": [_para("BIBLIOGRAPHY PAGE")]}],
}


def _first_pages(starts_on: str) -> dict[str, int]:
    """1-based page on which each marker text first appears."""
    spec = {"chapterOpenings": {"startsOn": starts_on, "dropCap": False}}
    html = f"<style>{emit_css(spec)}</style>" + ast_to_html(BOOK)
    document = weasyprint.HTML(string=html).render()
    found: dict[str, int] = {}

    def texts(box):
        if getattr(box, "text", None):
            yield box.text
        for child in getattr(box, "children", []) or []:
            yield from texts(child)

    for number, page in enumerate(document.pages, start=1):
        page_text = " ".join(texts(page._page_box))
        for marker in ("TITLE PAGE", "CONTENTS PAGE", "CHAPTER 1 TEXT", "CHAPTER 2 TEXT",
                       "CHAPTER 3 TEXT", "BIBLIOGRAPHY PAGE"):
            if marker in page_text:
                found.setdefault(marker, number)
    return found


def test_every_section_starts_on_a_recto():
    pages = _first_pages("recto")
    assert len(pages) == 6, pages
    assert all(page % 2 == 1 for page in pages.values()), pages
    # And each on its own page: the contents never runs on from the title page.
    assert len(set(pages.values())) == 6, pages


def test_any_means_a_new_page_not_a_recto():
    pages = _first_pages("any")
    chapters = [pages[f"CHAPTER {n} TEXT"] for n in (1, 2, 3)]
    assert chapters == sorted(set(chapters)), pages
    assert any(page % 2 == 0 for page in chapters), "'any' should not force blank versos"
