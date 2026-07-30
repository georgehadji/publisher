"""Tests for publisher_cover.image_gen_port -- the FakeImageGenAdapter (no network) and
the request-shaping logic that OpenRouterImageGenAdapter shares with it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_cover.image_gen_port import (
    FakeImageGenAdapter,
    ImageGenRequest,
    decode_image_bytes,
)


class TestFakeImageGenAdapter:
    def test_generate_returns_n_images(self):
        adapter = FakeImageGenAdapter()
        request = ImageGenRequest(
            model_id="bytedance-seed/seedream-4.5", provider_slug="seed",
            prompt="test prompt", aspect_ratio="2:3", resolution="4K", n=3,
        )
        result = adapter.generate(request)
        assert len(result.images) == 3

    def test_records_call_for_inspection(self):
        adapter = FakeImageGenAdapter()
        request = ImageGenRequest(
            model_id="bytedance-seed/seedream-4.5", provider_slug="seed",
            prompt="test prompt", aspect_ratio="2:3", n=1,
        )
        adapter.generate(request)
        assert adapter.calls == [request]

    def test_cost_scales_with_n(self):
        adapter = FakeImageGenAdapter(cost_per_image_usd=0.04)
        request = ImageGenRequest(
            model_id="x", provider_slug="y", prompt="p", aspect_ratio="2:3", n=5,
        )
        result = adapter.generate(request)
        assert result.cost_usd == 0.20

    def test_resolved_provider_matches_requested_no_silent_fallback(self):
        """allow_fallbacks is always false -- the fake mirrors that guarantee so tests
        exercising it catch a regression the same way a live call would."""
        adapter = FakeImageGenAdapter()
        request = ImageGenRequest(
            model_id="m", provider_slug="exact-provider", prompt="p", aspect_ratio="2:3",
        )
        result = adapter.generate(request)
        assert result.resolved_provider_slug == "exact-provider"

    def test_decode_image_bytes_produces_valid_png_signature(self):
        adapter = FakeImageGenAdapter()
        request = ImageGenRequest(model_id="m", provider_slug="p", prompt="x", aspect_ratio="2:3")
        result = adapter.generate(request)
        raw = decode_image_bytes(result.images[0])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_media_type_is_read_not_assumed(self):
        adapter = FakeImageGenAdapter()
        request = ImageGenRequest(model_id="m", provider_slug="p", prompt="x", aspect_ratio="2:3")
        result = adapter.generate(request)
        assert result.images[0].media_type == "image/png"
