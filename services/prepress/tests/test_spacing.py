"""
Hyphen stacks and rivers, read off `pdftotext -bbox-layout` word boxes (§2.1).

The input here is poppler's own shape (page > flow > block > line > word, in
pt); `test_spacing_against_poppler` reads a real one where poppler is installed.
"""

from __future__ import annotations

from publisher_prepress.preflight import check_hyphen_stacks, check_rivers
from publisher_prepress.spacing import scan_spacing


def _line(*words: tuple[float, float, str], y: float) -> str:
    inner = "".join(f'<word xMin="{x0}" yMin="{y}" xMax="{x1}" yMax="{y + 10}">{t}</word>'
                    for x0, x1, t in words)
    return f'<line xMin="0" yMin="{y}" xMax="300" yMax="{y + 10}">{inner}</line>'


def _doc(*blocks: list[str]) -> str:
    body = "".join(f'<block xMin="0" yMin="0" xMax="300" yMax="400">{"".join(b)}</block>' for b in blocks)
    return f'<doc><page width="400" height="600"><flow>{body}</flow></page></doc>'


def _prose(n: int, ends: list[str], gap_at: float | None = None, wide: float = 12) -> list[str]:
    """n lines of four words, 4 pt gaps, last word from `ends`; one gap of
    `wide` pt starting at `gap_at` on every line when given."""
    lines = []
    for i in range(n):
        if gap_at is None:
            words = [(0, 60, "w"), (64, 124, "w"), (128, 188, "w"), (192, 252, ends[i % len(ends)])]
        else:
            words = [(0, gap_at, "w"), (gap_at + wide, gap_at + wide + 60, "w"),
                     (gap_at + wide + 64, gap_at + wide + 124, "w"),
                     (gap_at + wide + 128, gap_at + wide + 188, ends[i % len(ends)])]
        lines.append(_line(*words, y=12.0 * i))
    return lines


def test_three_hyphenated_line_ends_in_a_row_are_a_stack():
    found = scan_spacing(_doc(_prose(5, ["end", "hy-", "phen-", "a‐", "end"])))
    assert found == {"pages": 1, "hyphenStacks": [1], "rivers": []}


def test_two_in_a_row_and_a_stack_split_by_blocks_are_not():
    assert scan_spacing(_doc(_prose(4, ["hy-", "phen-", "end"])))["hyphenStacks"] == []
    two_blocks = _doc(_prose(2, ["hy-"]), _prose(2, ["hy-"]))
    assert scan_spacing(two_blocks)["hyphenStacks"] == []


def test_a_wide_gap_aligned_through_four_lines_is_a_river():
    found = scan_spacing(_doc(_prose(4, ["end"], gap_at=100) + _prose(3, ["end"])))
    assert found["rivers"] == [1] and found["hyphenStacks"] == []


def test_three_lines_or_ordinary_gaps_are_not_a_river():
    assert scan_spacing(_doc(_prose(3, ["end"], gap_at=100) + _prose(4, ["end"])))["rivers"] == []
    # Ordinary 4 pt gaps line up through every line of justified-looking prose.
    assert scan_spacing(_doc(_prose(8, ["end"])))["rivers"] == []
    # Wide gaps that do not overlap from line to line are not a channel.
    drifting = [_line((0, 50 + 30 * i, "w"), (62 + 30 * i, 120 + 30 * i, "w"),
                      (124 + 30 * i, 180 + 30 * i, "w"), (184 + 30 * i, 240 + 30 * i, "end"), y=12.0 * i)
                for i in range(5)]
    assert scan_spacing(_doc(drifting))["rivers"] == []


def test_entities_are_unescaped_before_the_hyphen_is_read():
    lines = [_line((0, 60, "a&amp;-"), y=12.0 * i) for i in range(3)]
    assert scan_spacing(_doc(lines))["hyphenStacks"] == [1]


def test_found_warns_and_names_the_pages():
    info = {"spacing": {"scan": {"pages": 9, "hyphenStacks": [4, 4, 7], "rivers": []}}}
    stacks = check_hyphen_stacks(info, {})
    assert stacks.status == "warn" and stacks.value == 3 and "pages 4, 7" in stacks.humanMessage
    assert stacks.sourceRef == "pdf#page=4"
    assert check_rivers(info, {}).status == "pass"


def test_unmeasured_warns_and_is_not_clean():
    info = {"spacing": {"unmeasured": "pdftotext (poppler-utils) is not installed"}}
    for check in (check_hyphen_stacks, check_rivers):
        c = check(info, {})
        assert c.status == "warn" and "not measured" in c.humanMessage


def test_spacing_against_poppler(tmp_path):
    """A real weasyprint PDF read by real poppler (CI's python job installs
    both): a stack, a river and a clean page. Skips where only xpdf's
    pdftotext is installed, which has no -bbox-layout."""
    import pytest
    weasyprint = pytest.importorskip("weasyprint")
    from publisher_prepress.spacing import measure_spacing

    css = "@page{size:120mm 180mm;margin:15mm} p{font:11pt serif;margin:0 0 6pt}"
    pages = {
        "stack": "<p>" + "<br>".join(["Typesetting is an old craft-", "and one that repays atten-",
                                      "tion paid to the line end-", "ings, which here stack up."]) + "</p>",
        # Monospace: the three-space gap is a column through four lines. poppler
        # cuts each of those lines in two at the gap; spacing.py joins them back.
        "river": "<p style='font-family:monospace;white-space:pre'>" + "\n".join(
            ["alpha beta   gamma delta", "eta theta   iota kappa", "mu nu xii   omicron pi",
             "rho sigma   tau upsilon", "phi chi psi omega end"]) + "</p>",
        "clean": "<p>A paragraph of ordinary prose with nothing remarkable about its spacing, set ragged.</p>",
    }
    found = {}
    for name, body in pages.items():
        pdf = tmp_path / f"{name}.pdf"
        weasyprint.HTML(string=f"<style>{css}</style>{body}").write_pdf(pdf)
        measured = measure_spacing(pdf)
        if "scan" not in measured:
            pytest.skip(measured["unmeasured"])
        found[name] = measured["scan"]
    assert found["stack"]["hyphenStacks"] == [1] and found["stack"]["rivers"] == []
    assert found["river"]["rivers"] == [1] and found["river"]["hyphenStacks"] == []
    assert found["clean"]["hyphenStacks"] == [] and found["clean"]["rivers"] == []
