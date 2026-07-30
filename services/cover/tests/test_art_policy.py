"""Tests for publisher_cover.art_policy -- catalogue loading, capability validation,
and tier resolution against the real pinned model_catalogue.yaml."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_cover.art_policy import (
    DispatchSpec,
    capability_index,
    load_catalogue,
    load_tiers,
    resolve_tier_panel,
    validate_dispatch,
)


class TestLoadCatalogue:
    def test_loads_pinned_catalogue(self):
        catalogue = load_catalogue()
        assert catalogue["catalogue_version"] == 1
        assert len(catalogue["models"]) > 0

    def test_seedream_present_as_cover_art(self):
        catalogue = load_catalogue()
        models = {m["id"]: m for m in catalogue["models"]}
        seedream = models["bytedance-seed/seedream-4.5"]
        assert seedream["role"] == "cover_art"
        assert seedream["native_2_3"] is True
        assert seedream["seed"] is True

    def test_recraft_scoped_to_ornament_not_cover_art(self):
        catalogue = load_catalogue()
        models = {m["id"]: m for m in catalogue["models"]}
        recraft = models["recraft/recraft-v4.1-vector"]
        assert recraft["role"] == "ornament_vector_svg"
        assert recraft["native_2_3"] is False

    def test_dynamic_routing_models_excluded(self):
        catalogue = load_catalogue()
        model_ids = {m["id"] for m in catalogue["models"]}
        assert "openrouter/auto" not in model_ids
        excluded_ids = {e["id"] for e in catalogue["exclude"]}
        assert "openrouter/auto" in excluded_ids
        assert "openrouter/auto-beta" in excluded_ids


class TestCapabilityIndex:
    def test_gemini_capability_varies_by_provider(self):
        """COVER_DESIGN.md §3: capability varies by PROVIDER within one model id."""
        catalogue = load_catalogue()
        index = capability_index(catalogue)
        vertex = index[("google/gemini-3-pro-image", "google-vertex/global")]
        studio = index[("google/gemini-3-pro-image", "google-ai-studio/global")]
        assert "4K" not in vertex.resolutions
        assert "4K" in studio.resolutions

    def test_flux_pro_has_no_resolution_parameter(self):
        catalogue = load_catalogue()
        index = capability_index(catalogue)
        cap = index[("black-forest-labs/flux.2-pro", "black-forest-labs")]
        assert cap.resolutions == ()
        assert cap.billing_unit == "megapixel"

    def test_seedream_endpoint_is_flat_rate(self):
        catalogue = load_catalogue()
        index = capability_index(catalogue)
        cap = index[("bytedance-seed/seedream-4.5", "seed")]
        assert cap.billing_unit == "image"
        assert cap.rate_usd == 0.04
        assert cap.seed is True


class TestValidateDispatch:
    def setup_method(self):
        self.catalogue = load_catalogue()
        self.index = capability_index(self.catalogue)

    def test_valid_seedream_dispatch(self):
        spec = DispatchSpec(
            model_id="bytedance-seed/seedream-4.5", provider_slug="seed",
            aspect_ratio="2:3", resolution="4K", n=3, seeds=(11, 22, 33),
        )
        result = validate_dispatch(self.index, spec)
        assert result.ok, result.errors

    def test_rejects_unsupported_resolution(self):
        spec = DispatchSpec(
            model_id="bytedance-seed/seedream-4.5", provider_slug="seed",
            aspect_ratio="2:3", resolution="8K", n=1,
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("resolution" in e for e in result.errors)

    def test_rejects_unsupported_aspect_ratio_on_recraft(self):
        """Recraft has no 2:3 -- this is exactly why it's ornament-only (COVER_DESIGN.md §3)."""
        spec = DispatchSpec(
            model_id="recraft/recraft-v4.1-vector", provider_slug="recraft",
            aspect_ratio="2:3", resolution=None, n=1,
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("aspect_ratio" in e for e in result.errors)

    def test_rejects_seed_on_non_seeded_model(self):
        spec = DispatchSpec(
            model_id="sourceful/riverflow-v2.5-pro", provider_slug="sourceful",
            aspect_ratio="2:3", resolution="4K", n=1, seeds=(11,),
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("seed" in e for e in result.errors)

    def test_rejects_n_over_max(self):
        spec = DispatchSpec(
            model_id="recraft/recraft-v4.1-vector", provider_slug="recraft",
            aspect_ratio="1:1", resolution=None, n=99,
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("n<=" in e for e in result.errors)

    def test_flux_pro_requires_no_resolution_field(self):
        """Sending a resolution to an endpoint with no resolution parameter is itself
        an error -- not silently ignored."""
        spec = DispatchSpec(
            model_id="black-forest-labs/flux.2-pro", provider_slug="black-forest-labs",
            aspect_ratio="2:3", resolution="4K", n=1,
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("no resolution parameter" in e for e in result.errors)

    def test_unknown_provider_pair_is_invalid(self):
        spec = DispatchSpec(
            model_id="bytedance-seed/seedream-4.5", provider_slug="nonexistent-vendor",
            aspect_ratio="2:3", resolution="4K", n=1,
        )
        result = validate_dispatch(self.index, spec)
        assert not result.ok
        assert any("no such (model, provider)" in e for e in result.errors)


class TestResolveTierPanel:
    def test_basic_tier_resolves_clean(self):
        panel = resolve_tier_panel("basic")
        assert panel.ok, panel.errors
        assert len(panel.dispatches) == 1
        assert panel.estimated_cost_usd > 0

    def test_pro_tier_resolves_clean_and_costs_more_than_basic(self):
        basic = resolve_tier_panel("basic")
        pro = resolve_tier_panel("pro")
        assert pro.ok, pro.errors
        assert len(pro.dispatches) == 4
        assert pro.estimated_cost_usd > basic.estimated_cost_usd

    def test_studio_tier_resolves_clean(self):
        panel = resolve_tier_panel("studio")
        assert panel.ok, panel.errors
        assert len(panel.dispatches) == 5

    def test_unknown_tier_reports_error_not_exception(self):
        panel = resolve_tier_panel("nonexistent-tier")
        assert not panel.ok
        assert panel.dispatches == ()
        assert "unknown tier" in panel.errors[0]

    def test_tiers_yaml_loads(self):
        tiers = load_tiers()
        assert set(tiers["tiers"].keys()) == {"basic", "pro", "studio"}
