"""
Open a generated IDML in Scribus, headless, and report what it found.

WHY SCRIBUS. `validate_idml` checks the package against the IDML format --
mimetype, container, part references, style resolution, well-formed XML. What it
cannot check is whether an independent implementation agrees, and that is the
only claim anyone actually cares about: *does a page-layout application open
this file*. Scribus is the one such application that is free, scriptable and
runs without a display, so it is the closest available stand-in for InDesign.

WHAT THIS IS NOT. Scribus's IDML importer is not InDesign's. A file Scribus
opens may still surprise InDesign, and a construct Scribus fails on may be
perfectly legal. Treat a pass as evidence, not proof, and a failure as a lead to
investigate rather than a verdict.

Scribus is not part of the toolchain and is not installed by the worker image --
this is a development check, so callers skip when the binary is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# `-g` runs without the GUI, `-ns` suppresses the splash screen. `-py` must be
# the LAST option -- everything after the script path is passed to the script --
# so no `--` separator and no flags may follow it.
SCRIBUS_ARGS = ("-g", "-ns")
SCRIBUS_BINARIES = ("scribus", "scribus-ng", "scribus-1.6", "scribus-1.5")
TIMEOUT_S = 180

# Runs *inside* Scribus, which supplies the `scribus` module.
#
# Results go to a FILE, not stdout: Scribus's scripter rebinds the interpreter's
# stdout to its own console window, so a `print` here reaches the parent process
# as nothing at all -- an empty result that reads exactly like a refused
# document. The file makes success and silence distinguishable.
PROBE = """
import sys
import scribus

result, document = [], sys.argv[1]
try:
    scribus.openDoc(document)
    result.append("PAGES=%d" % scribus.pageCount())
    result.append("OBJECTS=%d" % len(scribus.getAllObjects()))
    result.append("OK")
except Exception as exc:
    result.append("FAILED=%s: %s" % (type(exc).__name__, exc))

with open(sys.argv[2], "w") as fh:
    fh.write("\\n".join(result))
"""


@dataclass(frozen=True)
class ScribusVerdict:
    opened: bool
    pages: int
    objects: int
    output: str


def find_scribus() -> str | None:
    """Path to a Scribus binary, or None. `PUBLISHER_SCRIBUS_BIN` overrides."""
    override = os.environ.get("PUBLISHER_SCRIBUS_BIN")
    if override:
        return override if Path(override).is_file() else None
    for name in SCRIBUS_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    # Windows installs land outside PATH more often than not.
    for base in (r"C:\Program Files\Scribus 1.6.1", r"C:\Program Files\Scribus"):
        candidate = Path(base) / "Scribus.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def scribus_opens(idml_path: str | Path) -> ScribusVerdict:
    """Try to open `idml_path` in headless Scribus.

    Raises `RuntimeError` if Scribus is not installed -- callers are expected to
    check `find_scribus()` first and skip, rather than have a missing dev tool
    quietly turn into a passing check.
    """
    binary = find_scribus()
    if binary is None:
        raise RuntimeError(
            "Scribus is not installed. Install it (winget install Scribus.Scribus, "
            "or apt install scribus) or set PUBLISHER_SCRIBUS_BIN."
        )

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        report = Path(tmp) / "verdict.txt"
        # Scribus aborts with "Preferences directory ... does not exist" rather
        # than creating it, so `-pr` needs a directory that is already there.
        prefs = Path(tmp) / "prefs"
        prefs.mkdir()
        try:
            proc = subprocess.run(
                # `-pr` gives this run its own preferences directory. Without it
                # every Scribus shares one profile, and a second launch while
                # another instance is up fails in a way that reads exactly like
                # a rejected document -- which is how this check first appeared
                # to fail intermittently under the full test suite.
                [binary, *SCRIBUS_ARGS, "-pr", str(prefs),
                 "-py", str(probe),
                 str(Path(idml_path).resolve()), str(report)],
                capture_output=True, text=True, timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return ScribusVerdict(False, 0, 0, f"Scribus timed out after {TIMEOUT_S}s")

        # Scribus exits 0 whether or not the script succeeded, so the report
        # file -- not the exit code -- is the verdict. An absent file means the
        # script never ran, which is a different failure from a rejected
        # document and must not be reported as one.
        if not report.is_file():
            streams = ((proc.stdout or "") + (proc.stderr or "")).strip()
            return ScribusVerdict(
                False, 0, 0,
                f"Scribus ran but the probe wrote no verdict (exit {proc.returncode}). "
                f"Console output: {streams or '<none>'}",
            )
        out = report.read_text(encoding="utf-8").strip()

    values = dict(
        line.split("=", 1) for line in out.splitlines()
        if "=" in line and line.split("=", 1)[0] in ("PAGES", "OBJECTS")
    )
    return ScribusVerdict(
        opened="OK" in out.splitlines(),
        pages=int(values.get("PAGES", 0) or 0),
        objects=int(values.get("OBJECTS", 0) or 0),
        output=out,
    )
