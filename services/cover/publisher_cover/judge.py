"""
cover-judge -- deterministic gates first, cross-vendor vision panel second, ranking only.

From COVER_DESIGN.md §8: the thumbnail-legibility check is the highest-value check in
the whole feature (it's how covers are actually discovered at retail) and it is pure
arithmetic -- it never touches a model. The vision panel only ranks candidates that
already cleared every deterministic gate.

Gate functions take pre-computed raster metrics rather than raw pixel bytes: real
luminance/contrast measurement belongs to a real image library (libvips/pdfium per
ARCHITECTURE.md §1.2's `rasterize` stage), which this service does not depend on. What's
implemented and tested here is the GATE POLICY -- the thresholds and pass/fail logic --
which is the part that's actually load-bearing and reviewable independent of which
library eventually produces the metrics.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Thresholds -- named constants per coding-style: no magic numbers.
MIN_TYPE_ZONE_CONTRAST_L = 35.0   # CIE L* delta between type-zone bg and worst-case text
MIN_THUMBNAIL_EDGE_PX = 160       # retail thumbnail reference size, COVER_DESIGN.md §8
MAX_TYPE_ZONE_LUMINANCE_STDDEV = 18.0  # busy backgrounds fail even with good mean contrast


@dataclass(frozen=True)
class RasterMetrics:
    """Pre-computed measurements of one candidate cover raster."""
    long_edge_px: int
    type_zone_mean_luminance_l: float       # CIE L*, 0-100
    type_zone_luminance_stddev: float
    assumed_text_luminance_l: float = 95.0  # white type, the common case; caller may vary


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str = ""


def thumbnail_legibility_gate(metrics: RasterMetrics) -> GateResult:
    """
    Blocking. A candidate that isn't legible at retail thumbnail size never reaches the
    vision panel, regardless of how well it scores on composition.
    """
    if metrics.long_edge_px < MIN_THUMBNAIL_EDGE_PX:
        return GateResult(
            False,
            f"raster long edge {metrics.long_edge_px}px < minimum {MIN_THUMBNAIL_EDGE_PX}px",
        )
    return GateResult(True)


def type_zone_contrast_gate(metrics: RasterMetrics) -> GateResult:
    """
    Blocking. Verifies the ArtBrief.typeZone region actually has room for legible type --
    COVER_DESIGN.md §5: cover-compose sets type deterministically, but only if a
    candidate's type zone clears contrast first.
    """
    delta_l = abs(metrics.assumed_text_luminance_l - metrics.type_zone_mean_luminance_l)
    if delta_l < MIN_TYPE_ZONE_CONTRAST_L:
        return GateResult(
            False,
            f"type-zone contrast delta L*={delta_l:.1f} < minimum {MIN_TYPE_ZONE_CONTRAST_L}",
        )
    if metrics.type_zone_luminance_stddev > MAX_TYPE_ZONE_LUMINANCE_STDDEV:
        return GateResult(
            False,
            f"type-zone luminance stddev={metrics.type_zone_luminance_stddev:.1f} "
            f"> maximum {MAX_TYPE_ZONE_LUMINANCE_STDDEV} (too busy for legible type)",
        )
    return GateResult(True)


def run_deterministic_gates(metrics: RasterMetrics) -> GateResult:
    """All blocking gates, in order. First failure wins -- matches StageError's
    fail-fast convention rather than accumulating every violation."""
    for gate in (thumbnail_legibility_gate, type_zone_contrast_gate):
        result = gate(metrics)
        if not result.passed:
            return result
    return GateResult(True)


# ── Vision panel aggregation (COVER_DESIGN.md §8) ───────────────────────────────


@dataclass(frozen=True)
class PairwiseVerdict:
    """One judge's vote. Mirrors schemas/cover/cover-verdict.schema.json exactly."""
    winner: str  # "a" | "b" | "tie"
    model_id: str
    provider_slug: str

    def __post_init__(self) -> None:
        if self.winner not in ("a", "b", "tie"):
            raise ValueError(f"winner must be 'a', 'b', or 'tie', got {self.winner!r}")


@dataclass(frozen=True)
class PanelResult:
    winner: str  # "a" | "b" | "tie" | "no_quorum"
    votes: tuple[PairwiseVerdict, ...]
    quorum_met: bool


def aggregate_panel(verdicts: list[PairwiseVerdict], *, quorum: int = 3) -> PanelResult:
    """
    Aggregate a panel's pairwise votes into one verdict. Below quorum, the pairing is
    invalid and must not proceed to a ranking -- COVER_DESIGN.md §8: "verdict invalid
    below this many voters -- never proceed short."

    Quorum counts DISTINCT (model, provider) judges, not raw votes. Counting votes would
    let three samples from one model satisfy `quorum=3`, which is precisely the
    single-model-consensus failure the cross-vendor panel exists to prevent
    (BUILD_PLAN.md §3.19: "not three copies of one prompt"). A duplicated judge also has
    its vote counted once, so a repeated voter cannot outweigh a distinct one.
    """
    by_judge: dict[tuple[str, str], PairwiseVerdict] = {}
    for v in verdicts:
        by_judge.setdefault((v.model_id, v.provider_slug), v)
    distinct = list(by_judge.values())

    if len(distinct) < quorum:
        return PanelResult(winner="no_quorum", votes=tuple(verdicts), quorum_met=False)
    verdicts = distinct

    counts = Counter(v.winner for v in verdicts)
    top = counts.most_common()
    # A tie between the top two options (including an explicit "tie" vote count) is
    # reported as "tie" rather than arbitrarily picking one -- ranking-only, per §8.
    if len(top) > 1 and top[0][1] == top[1][1]:
        winner = "tie"
    else:
        winner = top[0][0]

    return PanelResult(winner=winner, votes=tuple(verdicts), quorum_met=True)
