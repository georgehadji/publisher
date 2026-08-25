"""Tests for publisher_cover.image_gen_port -- the FakeImageGenAdapter (no network) and
the request-shaping logic that OpenRouterImageGenAdapter shares with it."""

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_cover.image_gen_port import (
    FakeImageGenAdapter,
    ImageGenRequest,
    OpenRouterImageGenAdapter,
    decode_image_bytes,
)


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://openrouter.ai/api/v1/images", code=code, msg="err",
        hdrs=headers, fp=None,
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


class TestOpenRouterRateLimitRetry:
    """U8 (ARCHITECTURE_UPLIFT_PLAN.md) -- a 429 must be retried, honoring
    Retry-After when present. Before this fix, `_call`'s `if exc.code < 500:
    raise` treated 429 exactly like a real 4xx and never retried it at all."""

    def _adapter(self):
        return OpenRouterImageGenAdapter(api_key="test-key")

    def _request(self):
        return ImageGenRequest(model_id="m", provider_slug="p", prompt="x", aspect_ratio="2:3")

    def test_429_is_retried_not_raised(self):
        success_body = json.dumps({"data": [], "usage": {}}).encode("utf-8")
        responses = [_http_error(429, {"Retry-After": "0"})]

        def fake_urlopen(*args, **kwargs):
            if responses:
                raise responses.pop()
            return _FakeResponse(success_body)

        with patch("publisher_cover.image_gen_port.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("publisher_cover.image_gen_port.time.sleep"):
            result = self._adapter().generate(self._request())
        assert result.images == ()

    def test_429_honors_retry_after_over_exponential_backoff(self):
        responses = [_http_error(429, {"Retry-After": "7"})]

        def fake_urlopen(*args, **kwargs):
            if responses:
                raise responses.pop()
            return _FakeResponse(json.dumps({"data": [], "usage": {}}).encode("utf-8"))

        with patch("publisher_cover.image_gen_port.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("publisher_cover.image_gen_port.time.sleep") as sleep:
            self._adapter().generate(self._request())
        sleep.assert_called_once_with(7.0)

    def test_non_429_4xx_still_raises_immediately(self):
        def fake_urlopen(*args, **kwargs):
            raise _http_error(400)

        with patch("publisher_cover.image_gen_port.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("publisher_cover.image_gen_port.time.sleep") as sleep:
            try:
                self._adapter().generate(self._request())
                assert False, "expected HTTPError to propagate"
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        sleep.assert_not_called()


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
