"""
Materialise CAS-stored images next to the HTML that references them.

`extract` emits `<img src="media/<sha256>.<ext>">` -- content-addressed rather
than a path, because the work directory the HTML was produced in is scratch and
is gone by the time anything renders it. Every renderer therefore pulls the
bytes back out of CAS into its own work directory first, and this is the one
place that knows how.

A referenced blob that is not in CAS raises. The alternative -- rendering a book
with a silently missing plate -- is the failure mode this whole path exists to
end, and a PDF with a blank rectangle where a figure should be looks finished.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches exactly what `extract._media_src` writes, so the two cannot drift
# apart without this stopping matching.
MEDIA_REF = re.compile(r"media/([0-9a-f]{64})\.([a-z0-9]+)")


def materialize_media(html: str, cas_root: str | Path, out_dir: str | Path) -> list[Path]:
    """Write every image the HTML references into `out_dir/media/`.

    Returns the files written. Paths are relative-compatible with the `src`
    attributes already in the HTML, so nothing has to rewrite the markup.
    """
    cas_root, out_dir = Path(cas_root), Path(out_dir)
    written: list[Path] = []

    for digest, ext in sorted(set(MEDIA_REF.findall(html))):
        blob = cas_root / digest[:2] / digest[2:4] / digest
        if not blob.is_file():
            raise FileNotFoundError(
                f"figure {digest[:12]}... is referenced by the HTML but is not in "
                f"CAS at {blob}. The manuscript's images did not survive ingest, "
                f"or this renderer is pointed at a different CAS root."
            )
        dest = out_dir / "media" / f"{digest}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob.read_bytes())
        written.append(dest)

    return written
