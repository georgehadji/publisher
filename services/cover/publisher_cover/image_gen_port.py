"""
ImageGenPort -- the adapter boundary between cover-art and whichever provider generates
pixels. From COVER_DESIGN.md §10: route -> provider is data (art_policy_tiers.yaml),
never a runtime decision, and the port is the seam that makes swapping providers a
config change instead of an architectural one.

OpenRouterImageGenAdapter calls the dedicated `POST /api/v1/images` endpoint verified in
COVER_DESIGN.md §4 -- NOT `chat/completions` with a `modalities` field. Key invariants
enforced here, not left to caller discipline:
  - `provider.allow_fallbacks` is always sent as `false`. A silent failover would return
    different pixels under a cache key that didn't change.
  - `media_type` is read from the response, never assumed to be PNG (Recraft vector
    output returns image/svg+xml).
  - Billing is all-or-nothing (COVER_DESIGN.md §4) -- a raised exception means nothing
    was billed; there is no partial-credit path to reconcile.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

IMAGES_ENDPOINT = "https://openrouter.ai/api/v1/images"
TIMEOUT_S = 120
RETRIES = 3


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """Parse a numeric Retry-After (seconds form only -- the HTTP-date form
    exists but no provider this adapter targets sends it). None if absent or
    unparseable, so the caller falls back to exponential backoff."""
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), 60.0))
    except ValueError:
        return None


@dataclass(frozen=True)
class ImageGenRequest:
    model_id: str
    provider_slug: str
    prompt: str
    aspect_ratio: str
    resolution: Optional[str] = None
    n: int = 1
    seed: Optional[int] = None
    input_references: tuple[str, ...] = ()  # image URLs or data URIs


@dataclass(frozen=True)
class GeneratedImage:
    b64_json: str
    media_type: str  # read from the response -- never assumed


@dataclass(frozen=True)
class ImageGenResult:
    images: tuple[GeneratedImage, ...]
    cost_usd: float
    resolved_provider_slug: str
    model_id_requested: str
    raw_usage: dict[str, Any] = field(default_factory=dict)


class ImageGenPort(Protocol):
    """From COVER_DESIGN.md §10 -- the boundary every image provider adapter implements."""

    def generate(self, request: ImageGenRequest) -> ImageGenResult: ...


class OpenRouterImageGenAdapter:
    """Real adapter: POST /api/v1/images, all-or-nothing billing, no silent fallback."""

    def __init__(self, api_key: str, *, base_url: str = IMAGES_ENDPOINT, timeout_s: int = TIMEOUT_S):
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_s = timeout_s

    def generate(self, request: ImageGenRequest) -> ImageGenResult:
        body: dict[str, Any] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "n": request.n,
            "provider": {
                "only": [request.provider_slug],
                "allow_fallbacks": False,  # never negotiable -- see module docstring
            },
        }
        if request.resolution is not None:
            body["resolution"] = request.resolution
        if request.seed is not None:
            body["seed"] = request.seed
        if request.input_references:
            body["input_references"] = [
                {"type": "image_url", "image_url": {"url": ref}}
                for ref in request.input_references
            ]

        payload = self._call(body)

        images = tuple(
            GeneratedImage(b64_json=d["b64_json"], media_type=d.get("media_type", "image/png"))
            for d in payload.get("data", [])
        )
        usage = payload.get("usage", {}) or {}
        return ImageGenResult(
            images=images,
            cost_usd=float(usage.get("cost", 0.0)),
            resolved_provider_slug=request.provider_slug,  # allow_fallbacks=false pins this
            model_id_requested=request.model_id,
            raw_usage=usage,
        )

    def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._base_url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        last: Optional[Exception] = None
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # All-or-nothing billing (COVER_DESIGN.md §4): a non-2xx means nothing
                # was charged. Retry infra-class errors only; a 4xx is BAD_INPUT-shaped
                # and retrying it wastes time without changing the outcome -- EXCEPT
                # 429, which is retryable by definition (ARCHITECTURE_UPLIFT_PLAN.md
                # U8): pre-existing code fell into `code < 500: raise` and never
                # retried a rate limit at all. Honor Retry-After when the provider
                # sends one; blind exponential backoff into an active limit just
                # provokes a longer one.
                if exc.code == 429:
                    last = exc
                    time.sleep(_retry_after_seconds(exc.headers) or 2**attempt)
                    continue
                if exc.code < 500:
                    raise
                last = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
            time.sleep(2**attempt)
        raise RuntimeError(f"POST {self._base_url} failed after {RETRIES} attempts: {last}")


class FakeImageGenAdapter:
    """
    Deterministic, network-free adapter for tests. Returns a 1x1 PNG's bytes as the
    b64_json payload so callers can exercise the full ImageGenResult shape without a
    live OpenRouter call.
    """

    # A valid 1x1 transparent PNG, base64-encoded -- small, real, decodable.
    _TRANSPARENT_PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def __init__(self, cost_per_image_usd: float = 0.04):
        self.calls: list[ImageGenRequest] = []
        self._cost_per_image_usd = cost_per_image_usd

    def generate(self, request: ImageGenRequest) -> ImageGenResult:
        self.calls.append(request)
        n = max(request.n, 1)
        images = tuple(
            GeneratedImage(b64_json=self._TRANSPARENT_PNG_B64, media_type="image/png")
            for _ in range(n)
        )
        cost = round(self._cost_per_image_usd * n, 4)
        return ImageGenResult(
            images=images,
            cost_usd=cost,
            resolved_provider_slug=request.provider_slug,
            model_id_requested=request.model_id,
            raw_usage={"cost": cost, "prompt_tokens": 0, "completion_tokens": 0},
        )


def decode_image_bytes(image: GeneratedImage) -> bytes:
    """Decode one generated image's base64 payload to raw bytes."""
    return base64.b64decode(image.b64_json)
