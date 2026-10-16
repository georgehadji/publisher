"""
The CSS path's equations: weasyprint has no MathML (it prints an equation's
characters in a row, "a2" for a/2), so `paginate` has Typst draw each one.

The HTML's `<math>` elements (`rendering`, from `publisher_structure.mathml`)
go through the same pandoc the Typst path uses, into Typst math, and one Typst
document sets each on a page of its own, in the face the Typst path uses
(`rendering.MATH_FONT`): the two print paths draw equations alike. Each page
becomes an SVG, and the `<math>` an `<img>` of it.

- Size is in em, from a 10pt setting, so an equation scales with its text.
- An inline one is lowered by its depth below the baseline, which Typst measures
  (text edges at the baseline, then at the bounds; the difference is the depth).
- Fills are rewritten to `device-cmyk`: Typst writes RGB, and RGB black reaches
  the press as four-colour black (C72 M67 Y67 K88), which rich-black-text cannot
  see in vector art. A colour that is not a neutral grey is refused.

The integrity gate runs before this, on `extract`'s HTML, which still carries
the MathML: nothing here changes the book's text.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from stages.rendering import MATH_FONT, black_plate

EM_PT = 10.0
_OPEN = '<math xmlns="http://www.w3.org/1998/Math/MathML"'
MATH = re.compile(re.escape(_OPEN) + r'( display="block")?>.*?</math>', re.S)
_SPLIT = "EQUATIONSPLIT"
_HEX = re.compile(r'(fill|stroke)="(#[0-9A-Fa-f]{6})"')


def _typst_math(maths: list[str], pandoc: str, run) -> list[str]:
    """Each MathML as Typst math, through pandoc (as the Typst path converts it),
    without its `$` delimiters."""
    html = "".join(f"<p>{m}</p><p>{_SPLIT}</p>" for m in maths)
    out = run([pandoc, "--from=html", "--to=typst", "--wrap=none"], timeout=120,
              what="pandoc (equations)", stdin=html.encode("utf-8")).decode("utf-8")
    parts = [p.strip() for p in out.split(_SPLIT)][:len(maths)]
    if len(parts) != len(maths) or not all(len(p) > 1 and p[0] == p[-1] == "$" for p in parts):
        raise ValueError(f"pandoc did not return {len(maths)} equations")
    return [p[1:-1].strip() for p in parts]


def _document(equations: list[str]) -> str:
    edges = 'set text(top-edge: "bounds", bottom-edge: "{}")'
    pages = [
        f"#{{ {edges.format('bounds')}; {eq} }}\n"
        f"#context {{ let full = measure(block({{ {edges.format('bounds')}; {eq} }})); "
        f"let up = measure(block({{ {edges.format('baseline')}; {eq} }})); "
        f"[#metadata((i: {i}, h: full.height.pt(), up: up.height.pt()))<eqm>] }}"
        for i, eq in enumerate(equations)]
    return "\n".join([
        "#set page(width: auto, height: auto, margin: 0pt, fill: none)",
        f"#set text(size: {EM_PT:g}pt, fill: black)",
        f"#show math.equation: set text(font: {json.dumps(MATH_FONT)})",
        "\n#pagebreak()\n".join(pages), ""])


def _k_only(svg: str) -> str:
    def plate(match: re.Match) -> str:
        k = black_plate(match.group(2))
        if k is None:
            raise ValueError(f"an equation is drawn in a colour ({match.group(2)}), not black")
        return f'{match.group(1)}="device-cmyk(0 0 0 {k:g})"'
    return _HEX.sub(plate, svg)


def typeset_equations(html: str, work: Path, *, pandoc: str, typst: str, run) -> str:
    """`html` with every `<math>` replaced by an `<img>` of it, drawn by Typst.
    `run` is typst_stages._run, so a toolchain failure is an INFRA error."""
    found = list(dict.fromkeys(m.group(0) for m in MATH.finditer(html)))
    if not found:
        return html
    shown = [m.startswith(f'{_OPEN} display="block">') for m in found]
    # Typst sets `$ x $` (spaces inside) as a displayed equation, `$x$` inline.
    maths = [f"$ {m} $" if d else f"${m}$" for m, d in zip(_typst_math(found, pandoc, run), shown)]
    folder = work / "math"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "equations.typ"
    source.write_text(_document(maths), encoding="utf-8")
    run([typst, "compile", "--ignore-system-fonts", "--format", "svg", str(source),
         str(folder / "eq-{0p}.svg")], timeout=300, what="typst (equations)")
    metrics = sorted(json.loads(run([typst, "query", "--ignore-system-fonts", str(source), "<eqm>",
                                     "--field", "value"], timeout=300, what="typst query (equations)")),
                     key=lambda m: m["i"])
    pages = sorted(folder.glob("eq-*.svg"))
    if len(pages) != len(found) or len(metrics) != len(found):
        raise ValueError(f"typst drew {len(pages)} of {len(found)} equations")

    images: dict[str, str] = {}
    for mathml, page, metric, display in zip(found, pages, metrics, shown):
        svg = _k_only(page.read_text(encoding="utf-8")).encode("utf-8")
        name = f"math/{hashlib.sha256(svg).hexdigest()}.svg"
        (work / name).write_bytes(svg)
        style = f"height: {metric['h'] / EM_PT:.4f}em"
        if not display:
            style += f"; vertical-align: {-(metric['h'] - metric['up']) / EM_PT:.4f}em"
        cls = "math-display" if display else "math"
        images[mathml] = f'<img class="{cls}" src="{name}" style="{style}" alt=""/>'
    return MATH.sub(lambda m: images[m.group(0)], html)


__all__ = ["typeset_equations"]
