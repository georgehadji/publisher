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
        dest.write_bytes(opaque(blob.read_bytes()))
        written.append(dest)

    return written


# Pillow formats whose files can carry an alpha channel, by the name
# `Image.format` reports. JPEG cannot, and SVG is not a raster Pillow opens.
_ALPHA_CAPABLE = {"PNG", "GIF", "TIFF", "WEBP", "BMP"}


def opaque(data: bytes) -> bytes:
    """The image composited onto white paper, in its own format, if it has
    transparency; otherwise the bytes unchanged.

    Print has no transparency, and one image with an alpha channel took a whole
    book's text with it. weasyprint shares one resource dictionary across every
    page, so the figure's soft mask appears on all 805 pages of the first real
    book. PDF/X-1a (PDF 1.3) cannot express transparency, so Ghostscript
    flattened every page to a 300 dpi picture: no text, 1.6 MB and 6 s a page,
    and a press file that could no longer be searched, copied or sharply printed.
    Taking the alpha out here, where the print renderers get their figures, keeps
    the soft mask out of the PDF. (The EPUB takes its figures straight from CAS
    and keeps theirs; screens show transparency.)
    """
    from io import BytesIO

    from PIL import Image

    try:
        image = Image.open(BytesIO(data))
    except Exception:
        return data   # not a raster Pillow reads (SVG): nothing to flatten here
    with image:
        fmt, mode, info = image.format, image.mode, dict(image.info)
        if fmt not in _ALPHA_CAPABLE:
            return data
        keyed = mode == "P" and "transparency" in info
        if not keyed and mode not in ("RGBA", "LA", "PA", "La", "RGBa"):
            return data
        rgba = image.convert("RGBA")
    paper = Image.new("RGB", rgba.size, "white")
    paper.paste(rgba, mask=rgba.getchannel("A"))
    if mode in ("LA", "La"):
        paper = paper.convert("L")
    out = BytesIO()
    paper.save(out, format=fmt, **({"dpi": info["dpi"]} if "dpi" in info else {}))
    return out.getvalue()
