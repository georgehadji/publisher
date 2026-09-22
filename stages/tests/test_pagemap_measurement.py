"""
`paginate`'s composition measurement -- the data `pagemap/1` carries so that a
defect scanner has something real to read.

These tests deliberately SEARCH for a fixture that produces each defect instead
of hard-coding the paragraph length that produced one on the author's machine.
Where a line breaks depends on the fonts actually installed, so a hard-coded
length is a test that passes here and fails on a host with different metrics.
What gets asserted once a defect is found is the structural precondition, which
no amount of font variation changes: a widow's paragraph really does have one
line on that page and lines on an earlier one.
"""

from __future__ import annotations

import pytest

from stages.paginate_stage import _build_pagemap, _measure_pages

weasyprint = pytest.importorskip("weasyprint")

# A page that fits roughly nine lines, so a paragraph can be made to straddle it
# without needing a wall of text. `orphans`/`widows: 1` disables CSS's own
# suppression -- the point here is to MEASURE a stranded line, which means the
# renderer has to be allowed to produce one.
CSS = "@page{size:200pt 120pt;margin:8pt} p{font-size:9pt;margin:0;orphans:1;widows:1}"


def _measure(body: str) -> dict[int, dict]:
    document = weasyprint.HTML(string=f"<style>{CSS}</style>{body}").render()
    return _measure_pages(document.pages)


def _lines_on(measured: dict, page: int, para: int) -> int:
    for entry in measured[page].get("paraRanges", []):
        if entry["paraIndex"] == para:
            return entry["linesOnPage"]
    return 0


def _pages_of(measured: dict, para: int) -> list[int]:
    return sorted(
        page
        for page, fields in measured.items()
        if any(r["paraIndex"] == para for r in fields.get("paraRanges", []))
    )


def test_widow_is_flagged_where_a_last_line_is_stranded():
    """A paragraph's final line, alone at the top of a page."""
    for words in range(108, 140, 2):
        measured = _measure(f"<p>{'wd ' * words}</p><p>tail here</p>")
        flagged = [p for p, f in measured.items() if f["hasWidows"]]
        if not flagged:
            continue
        page = flagged[0]
        para = next(
            r["paraIndex"]
            for r in measured[page]["paraRanges"]
            if r["linesOnPage"] == 1
        )
        spans = _pages_of(measured, para)
        assert _lines_on(measured, page, para) == 1, "widow page must hold one line"
        assert spans[0] < page, "a widow's paragraph must have begun earlier"
        assert page == spans[-1], "a widow falls on the paragraph's LAST page"
        return
    pytest.fail("no widow produced across the swept paragraph lengths")


def test_orphan_is_flagged_where_a_first_line_is_stranded():
    """A paragraph's opening line, alone at the bottom of a page."""
    for filler in range(120, 164, 2):
        measured = _measure(f"<p>{'ff ' * filler}</p><p>{'gg ' * 160}</p>")
        flagged = [p for p, f in measured.items() if f["hasOrphans"]]
        if not flagged:
            continue
        page = flagged[0]
        para = next(
            r["paraIndex"]
            for r in measured[page]["paraRanges"]
            if r["linesOnPage"] == 1
        )
        spans = _pages_of(measured, para)
        assert _lines_on(measured, page, para) == 1, "orphan page must hold one line"
        assert page == spans[0], "an orphan falls on the paragraph's FIRST page"
        assert spans[-1] > page, "an orphan's paragraph must continue past it"
        return
    pytest.fail("no orphan produced across the swept filler lengths")


def test_paragraph_indices_are_document_order_and_complete():
    measured = _measure("<p>one two three</p><p>four five six</p><p>seven eight</p>")
    seen = {
        r["paraIndex"]
        for fields in measured.values()
        for r in fields.get("paraRanges", [])
    }
    assert seen == {0, 1, 2}
    assert all(fields["wordCount"] > 0 for fields in measured.values())


def test_unmeasurable_pages_leave_the_fields_absent_rather_than_false():
    """The honesty property the measurement exists for.

    Absent means "not measured"; `false` means "measured, none found". Collapse
    the two and an unmeasured book reports as clean -- the exact defect that
    made the pagescan path decorative, so it is asserted rather than assumed.
    """

    class _NoBoxTree:
        """The shape stages/typst_stages.py hands `_build_pagemap`."""

        anchors: dict = {}
        width = 432.0
        height = 648.0

    assert _measure_pages([]) == {}
    assert _measure_pages(None) == {}
    assert _measure_pages([_NoBoxTree()]) == {}

    pagemap = _build_pagemap(
        [{"chapterId": "ch1", "number": 1, "textLength": 10}],
        page_count=2,
        rendered_pages=None,
    )
    for page in pagemap["pages"]:
        assert "hasWidows" not in page
        assert "hasOrphans" not in page
        assert "hasRunts" not in page
        assert "wordCount" not in page
