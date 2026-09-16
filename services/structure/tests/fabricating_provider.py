"""
FabricatingProvider -- the quarantined simulation (E6.1, closing L7).

Implements InferenceProvider by returning the same paragraph/0.85
classification for every input, exactly as the pre-E6.1 gateway's internal
fabricating path did. Every result is marked `simulated: true` so a
fabricated confidence is visible in the build record rather than inferred
from source reading.

This file lives under tests/, NOT publisher_structure/: it is excluded from
the `publisher-structure` distribution (see ../pyproject.toml's
packages.find, which only includes `publisher_structure*`) and from every
Docker build context by .dockerignore's `**/tests/` rule. A production
process cannot import this -- not because of a runtime env-var gate (the
pre-E6.1 PUBLISHER_ALLOW_SIMULATED_INFERENCE check), but because the file
is not there to import.
"""

from __future__ import annotations

import time

from publisher_structure.inference import (
    InferenceRequest,
    InferenceResult,
    ModelTier,
    RouteConfig,
)


class FabricatingProvider:
    """Test-only InferenceProvider. FABRICATES a model result -- construct
    this in tests only, never in application code."""

    def complete(self, request: InferenceRequest, route: RouteConfig, tier: ModelTier) -> InferenceResult:
        start = time.monotonic()
        nodes = request.inputs.get("nodes", [])
        classifications = [
            {
                "sourceRef": n.get("sourceRef", f"node-{i}"),
                "classification": "paragraph",  # Default classification
                "confidence": 0.85,
            }
            for i, n in enumerate(nodes)
        ]
        latency_ms = int((time.monotonic() - start) * 1000)

        return InferenceResult(
            request_id=request.request_id,
            route=request.route,
            tier_used=tier,
            output={
                "schema": "classification/1",
                "nodes": classifications,
                "modelInfo": {
                    "modelId": route.model_id,
                    "promptVersion": route.prompt_version,
                    "schemaVersion": route.schema_version,
                    "cacheHit": False,
                    "costUsd": route.cost_per_call,
                    # This classification was FABRICATED, not inferred. The
                    # flag travels with the artifact so a simulated
                    # confidence is visible in the build record.
                    "simulated": True,
                },
            },
            confidence=0.85,
            model_id=route.model_id,
            prompt_version=route.prompt_version,
            cache_hit=False,
            cost_usd=route.cost_per_call,
            latency_ms=latency_ms,
        )
