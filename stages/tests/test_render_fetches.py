"""
paginate's renderer loads its own files and nothing else.

weasyprint 62.3 carries three advisories (pip-audit): its default fetcher follows
HTTP redirects unchecked (CVE-2025-68616), presentational hints allow CSS
injection (CVE-2026-49452, no fix released), and write_pdf's `xmp_metadata` /
`stylesheets` bypass a custom fetcher (CVE-2026-55073). CI accepts them for this
pin because none is reachable from how paginate calls weasyprint. These tests
are what keep that true: if one fails, the acceptance in ci.yml is void.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import stages.paginate_stage as paginate_stage
from stages.paginate_stage import local_only_fetcher

pytest.importorskip("weasyprint")


def _setup(tmp_path: Path):
    work = tmp_path / "work"
    (work / "media").mkdir(parents=True)
    figure = work / "media" / "fig.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\n")
    font = tmp_path / "fonts" / "Face.ttf"
    font.parent.mkdir()
    font.write_bytes(b"\x00\x01\x00\x00")
    other = tmp_path / "secret.txt"
    other.write_text("not for the renderer", encoding="utf-8")
    faces = f'@font-face {{ font-family: "Face"; src: url("{font.resolve().as_uri()}"); }}'
    return work, figure, font, other, local_only_fetcher(work, faces)


def test_the_render_s_own_media_and_pinned_fonts_load(tmp_path):
    work, figure, font, _, fetch = _setup(tmp_path)
    assert fetch(figure.resolve().as_uri())
    assert fetch(font.resolve().as_uri())
    assert fetch("data:text/plain,ok")


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "https://example.com/pixel.png",
    "ftp://example.com/x",
])
def test_nothing_is_fetched_over_a_network(tmp_path, url):
    *_, fetch = _setup(tmp_path)
    with pytest.raises(ValueError, match="refused"):
        fetch(url)


def test_no_other_file_on_the_worker_loads(tmp_path):
    work, _, _, other, fetch = _setup(tmp_path)
    with pytest.raises(ValueError, match="refused"):
        fetch(other.resolve().as_uri())
    with pytest.raises(ValueError, match="refused"):
        fetch((work / ".." / "secret.txt").resolve().as_uri())


def test_paginate_calls_weasyprint_the_way_the_audit_acceptance_assumes():
    source = inspect.getsource(paginate_stage.paginate)
    assert "url_fetcher=local_only_fetcher(" in source
    assert "presentational_hints" not in source
    assert "xmp_metadata" not in source
    assert "stylesheets=" not in source
