"""
Ghostscript PDF/X conversion -- the guards that make a false "press-ready"
claim impossible.

These exist because a real build shipped a PDF that `finish` reported as
`"profileApplied": "pdfx-1a"` and that carried no OutputIntent at all.
Ghostscript had printed "reverting to normal PDF output" and exited 0; the
old code read only the exit code and the `%PDF` magic bytes, both of which
say nothing about PDF/X conformance.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from publisher_prepress.ghostscript import (
    PDFX_DEF_TEMPLATE,
    GhostscriptError,
    _assert_no_pdfx_downgrade,
    _assert_pdf,
    _assert_pdfx,
    _run,
    to_pdfx,
)

# The exact line Ghostscript 10.05.1 emitted on the build that shipped a
# non-PDF/X file as press-ready.
REAL_GS_DOWNGRADE = (
    "GPL Ghostscript 10.05.1: TrimBox does not fit inside BleedBox, "
    "not permitted in PDF/X-3, reverting to normal PDF output\n"
)

PDFX_BYTES = b"%PDF-1.4\n/OutputIntents [<</S /GTS_PDFX /OutputCondition (x)>>]\n%%EOF"
PLAIN_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<</Type /Catalog>>\n%%EOF"


def test_downgrade_warning_is_an_error_even_though_gs_exited_zero():
    with pytest.raises(GhostscriptError) as exc:
        _assert_no_pdfx_downgrade(REAL_GS_DOWNGRADE)
    # The operator needs gs's own reason, not just "it failed".
    assert "TrimBox does not fit inside BleedBox" in str(exc.value)


@pytest.mark.parametrize(
    "message",
    [
        "reverting to normal PDF output",
        "some construct is not permitted in PDF/X",
        "colour cannot be represented in PDF/X",
    ],
)
def test_every_known_downgrade_phrase_is_caught(message):
    with pytest.raises(GhostscriptError):
        _assert_no_pdfx_downgrade(message)


def test_clean_gs_output_passes():
    _assert_no_pdfx_downgrade("Processing pages 1 through 5.\nPage 1\n")


def test_plain_pdf_is_rejected_as_not_pdfx(tmp_path: Path):
    """
    The regression itself: a file that is a perfectly valid PDF, and would pass
    a `%PDF` magic-byte check, must still be refused when it carries no
    OutputIntent -- that is exactly the unconverted-input substitution.
    """
    out = tmp_path / "press.pdf"
    out.write_bytes(PLAIN_PDF_BYTES)

    _assert_pdf(out, "press")  # the old check: passes

    with pytest.raises(GhostscriptError) as exc:
        _assert_pdfx(out)
    assert "/GTS_PDFX" in str(exc.value)


def test_real_pdfx_output_passes(tmp_path: Path):
    out = tmp_path / "press.pdf"
    out.write_bytes(PDFX_BYTES)
    _assert_pdfx(out)


def test_run_returns_output_so_callers_can_inspect_it(monkeypatch):
    """`_run` must not swallow gs's chatter -- the downgrade notice lives there."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "stdout-line", "stderr-line"),
    )
    combined = _run(["gs", "--version"])
    assert "stdout-line" in combined
    assert "stderr-line" in combined


def test_nonzero_exit_still_raises(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )
    with pytest.raises(GhostscriptError) as exc:
        _run(["gs", "--version"])
    assert "boom" in str(exc.value)


def test_to_pdfx_refuses_without_a_binary(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary", lambda: None)
    with pytest.raises(GhostscriptError, match="not found"):
        to_pdfx(tmp_path / "in.pdf", tmp_path / "out.pdf", tmp_path)


def test_icc_is_found_next_to_the_gs_install(tmp_path: Path, monkeypatch):
    """The search list was Debian-only, so every Windows host failed finish-gs
    with "no CMYK ICC profile found" while the profile sat in the install tree.
    Deriving it from the binary is what makes an unusual prefix work at all."""
    from publisher_prepress.ghostscript import find_cmyk_icc

    binary = tmp_path / "bin" / "gswin64c.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    profile = tmp_path / "iccprofiles" / "default_cmyk.icc"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"icc")

    monkeypatch.delenv("PUBLISHER_CMYK_ICC", raising=False)
    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary",
                        lambda: str(binary))
    assert find_cmyk_icc() == profile


def test_an_explicit_icc_override_wins(tmp_path: Path, monkeypatch):
    from publisher_prepress.ghostscript import find_cmyk_icc

    vendor = tmp_path / "LightningSource.icc"
    vendor.write_bytes(b"icc")
    monkeypatch.setenv("PUBLISHER_CMYK_ICC", str(vendor))
    assert find_cmyk_icc() == vendor


def test_a_broken_icc_override_raises_rather_than_substituting(tmp_path, monkeypatch):
    """The OutputIntent names the printing condition the book is proofed for.
    Quietly using a different profile misstates it on every page."""
    from publisher_prepress.ghostscript import find_cmyk_icc

    monkeypatch.setenv("PUBLISHER_CMYK_ICC", str(tmp_path / "absent.icc"))
    with pytest.raises(GhostscriptError, match="not a file"):
        find_cmyk_icc()


def test_to_pdfx_refuses_without_an_icc_profile(tmp_path: Path, monkeypatch):
    """PDF/X-1a requires an OutputIntent destination profile; no profile, no claim."""
    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary", lambda: "/usr/bin/gs")
    monkeypatch.setattr("publisher_prepress.ghostscript.find_cmyk_icc", lambda: None)
    with pytest.raises(GhostscriptError, match="ICC"):
        to_pdfx(tmp_path / "in.pdf", tmp_path / "out.pdf", tmp_path)


def test_to_pdfx_fails_when_gs_downgrades(tmp_path: Path, monkeypatch):
    """
    End-to-end shape of the bug: gs exits 0, writes a valid non-PDF/X file, and
    announces the downgrade. to_pdfx must raise rather than return.
    """
    icc = tmp_path / "cmyk.icc"
    icc.write_bytes(b"icc")
    out = tmp_path / "out.pdf"

    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary", lambda: "/usr/bin/gs")
    monkeypatch.setattr("publisher_prepress.ghostscript.find_cmyk_icc", lambda: icc)

    def fake_run(cmd, **kwargs):
        out.write_bytes(PLAIN_PDF_BYTES)
        return subprocess.CompletedProcess(cmd, 0, "", REAL_GS_DOWNGRADE)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GhostscriptError, match="declined to produce PDF/X"):
        to_pdfx(tmp_path / "in.pdf", out, tmp_path)


def test_to_pdfx_fails_when_output_lacks_outputintent(tmp_path: Path, monkeypatch):
    """Belt and braces: gs says nothing, but the bytes are not PDF/X."""
    icc = tmp_path / "cmyk.icc"
    icc.write_bytes(b"icc")
    out = tmp_path / "out.pdf"

    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary", lambda: "/usr/bin/gs")
    monkeypatch.setattr("publisher_prepress.ghostscript.find_cmyk_icc", lambda: icc)

    def fake_run(cmd, **kwargs):
        out.write_bytes(PLAIN_PDF_BYTES)
        return subprocess.CompletedProcess(cmd, 0, "Processing pages 1 through 1.", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GhostscriptError, match="is not PDF/X"):
        to_pdfx(tmp_path / "in.pdf", out, tmp_path)


def test_title_parens_cannot_break_the_postscript_prologue(tmp_path: Path, monkeypatch):
    """
    A book title is user content and lands in a PostScript string literal.
    An unescaped `)` would terminate the literal early and corrupt the prologue.
    """
    icc = tmp_path / "cmyk.icc"
    icc.write_bytes(b"icc")
    out = tmp_path / "out.pdf"

    monkeypatch.setattr("publisher_prepress.ghostscript.find_binary", lambda: "/usr/bin/gs")
    monkeypatch.setattr("publisher_prepress.ghostscript.find_cmyk_icc", lambda: icc)

    def fake_run(cmd, **kwargs):
        out.write_bytes(PDFX_BYTES)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    to_pdfx(tmp_path / "in.pdf", out, tmp_path, title="Ends) with (parens")

    prologue = (tmp_path / "PDFX_def.ps").read_text(encoding="utf-8")
    assert r"Ends\) with \(parens" in prologue


# --- PDF/X box geometry -------------------------------------------------

def test_bleed_offset_matches_the_trim_offset():
    """BleedBox must reach the MediaBox, not sit on the TrimBox.

    Writing [0 0 0 0] for BleedBoxToTrimBoxOffset makes gs compute
    BleedBox == TrimBox, and it then fails its own "TrimBox does not fit
    inside BleedBox" test on two identical non-integral rectangles and
    silently reverts to ordinary PDF. Both offsets carry the same figure so
    that no two boxes come out exactly equal.
    """
    prologue = PDFX_DEF_TEMPLATE.format(
        icc_profile="/tmp/x.icc",
        trim_offset="8.50394 8.50394 8.50394 8.50394",
        bleed_offset="8.50394 8.50394 8.50394 8.50394",
        title="t", output_condition="c", condition_id="i",
    )
    trim = re.search(r"/PDFXTrimBoxToMediaBoxOffset \[([^\]]+)\]", prologue)
    bleed = re.search(r"/PDFXBleedBoxToTrimBoxOffset \[([^\]]+)\]", prologue)
    assert trim and bleed
    assert trim.group(1) == bleed.group(1)
    assert set(bleed.group(1).split()) != {"0"}
