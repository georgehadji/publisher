"""
publisher_cover -- cover design: brief, multi-model art generation, cross-vendor judging.

From COVER_DESIGN.md: cover art is page-count-free (runs parallel to the interior build);
only geometry and assembly depend on the interior's final page count. No image model ever
renders title/author/series text -- that is composed deterministically downstream in
cover-compose, through the same DesignSpec emitter path as the interior.
"""

from __future__ import annotations

from .art_policy import (
    DispatchSpec,
    ProviderCapability,
    ResolvedPanel,
    ValidationResult,
    capability_index,
    estimate_cost_usd,
    load_catalogue,
    load_tiers,
    resolve_tier_panel,
    validate_dispatch,
)
from .brief import ArtBrief, render_prompt
from .image_gen_port import (
    FakeImageGenAdapter,
    ImageGenPort,
    ImageGenRequest,
    ImageGenResult,
    OpenRouterImageGenAdapter,
)
from .judge import (
    PairwiseVerdict,
    aggregate_panel,
    thumbnail_legibility_gate,
    type_zone_contrast_gate,
)

__all__ = [
    "ArtBrief",
    "render_prompt",
    "DispatchSpec",
    "ProviderCapability",
    "ResolvedPanel",
    "ValidationResult",
    "capability_index",
    "estimate_cost_usd",
    "load_catalogue",
    "load_tiers",
    "resolve_tier_panel",
    "validate_dispatch",
    "ImageGenPort",
    "ImageGenRequest",
    "ImageGenResult",
    "OpenRouterImageGenAdapter",
    "FakeImageGenAdapter",
    "PairwiseVerdict",
    "aggregate_panel",
    "thumbnail_legibility_gate",
    "type_zone_contrast_gate",
]
