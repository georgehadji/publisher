"""
The composition gate — preflight's verdict on widows, orphans and runts.

The measurement these read comes from `stages.paginate_stage._measure_pages`,
which walks the renderer's box tree. Here only the POLICY is under test: which
counts block delivery, which merely warn, and — the case that matters most —
that pages nobody measured are reported as unknown rather than clean.
"""

from publisher_prepress.preflight import check_composition, scan_composition


def _page(number, **flags):
    page = {"pageNumber": number, "folio": number, "chapterId": "ch1"}
    page.update(flags)
    return page


def _measured(number, orphans=False, widows=False, runts=False):
    return _page(number, hasOrphans=orphans, hasWidows=widows, hasRunts=runts,
                 wordCount=250)


def _pagemap(pages):
    return {"schema": "pagemap/1", "pages": pages, "chapters": []}


def test_unmeasured_pages_warn_rather_than_pass():
    """The honesty property, at the gate this time.

    A page carrying no flags was never measured. Reporting that as a pass is
    how the pagescan path certified every book clean before any of this
    existed, so it must not read as one.
    """
    result = check_composition({"pagemap": _pagemap([_page(1), _page(2)])}, {})
    assert result.status == "warn"
    assert "UNKNOWN" in result.humanMessage


def test_a_missing_pagemap_warns_too():
    assert check_composition({}, {}).status == "warn"
    assert check_composition({"pagemap": None}, {}).status == "warn"


def test_orphans_block_delivery():
    pagemap = _pagemap([_measured(1), _measured(2, orphans=True), _measured(3)])
    result = check_composition({"pagemap": pagemap}, {})
    assert result.status == "fail"
    assert result.severity == "error"
    assert result.sourceRef == "pagemap/1#page=2"


def test_a_profile_may_allow_some_orphans():
    """The fail threshold is house style, so the profile owns it."""
    pagemap = _pagemap([_measured(1, orphans=True), _measured(2)])
    profile = {"composition": {"maxOrphanPages": 1}}
    assert check_composition({"pagemap": pagemap}, profile).status == "pass"
    assert check_composition({"pagemap": pagemap}, {}).status == "fail"


def test_widows_and_runts_warn_but_do_not_block():
    """Routine in set text. Failing on these would block every book."""
    pagemap = _pagemap([_measured(1, widows=True), _measured(2, runts=True)])
    result = check_composition({"pagemap": pagemap}, {})
    assert result.status == "warn"
    assert result.value == {"widows": 1, "runts": 1}


def test_clean_measured_pages_pass():
    pagemap = _pagemap([_measured(n) for n in range(1, 6)])
    result = check_composition({"pagemap": pagemap}, {})
    assert result.status == "pass"
    assert "5 measured page(s)" in result.humanMessage


def test_partially_measured_books_count_only_what_was_measured():
    """The Typst path can contribute unmeasured pages to the same pagemap."""
    found = scan_composition(_pagemap([
        _measured(1, widows=True), _page(2), _page(3), _measured(4),
    ]))
    assert found == {"pages": 4, "measured": 2,
                     "orphans": [], "widows": [1], "runts": []}
