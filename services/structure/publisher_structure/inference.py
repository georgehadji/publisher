"""
Inference gateway — LLM routing and classification service.

From BUILD_PLAN.md §3.16 and LLM_STRATEGY.md:
Routes classification requests through a cascade: rules → cheap model → expensive model.
Every LLM call writes its result into a CAS artifact, frozen as data.
No LLM output ever enters a deterministic pipeline stage (D9).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


# ── Routing types ───────────────────────────────────────────────


class ModelTier(str, Enum):
    """Model tier for routing decisions."""
    RULES = "rules"  # No LLM, deterministic only
    FAST = "fast"     # Cheap/fast model (e.g., Claude Haiku)
    GOOD = "good"     # Good quality (e.g., Claude Sonnet)
    BEST = "best"     # Best quality (e.g., Claude Opus)


class RouteReason(str, Enum):
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    FORCE_LLM = "force_llm"
    RETRY_FAST = "retry_fast"
    RETRY_GOOD = "retry_good"
    LOW_CONFIDENCE = "low_confidence"


@dataclass
class InferenceRequest:
    """A request to the inference gateway."""
    request_id: str
    route: str  # e.g. "structure-classify", "alttext", "genre-suggest"
    inputs: dict[str, Any]
    prompt_version: str
    schema_version: str
    tenant_id: Optional[str] = None
    
    # Routing constraints
    force_tier: Optional[ModelTier] = None
    no_external_llm: bool = False  # tenant opt-out flag


@dataclass
class InferenceResult:
    """Result from an inference call."""
    request_id: str
    route: str
    tier_used: ModelTier
    output: dict[str, Any]
    confidence: float = 1.0
    model_id: Optional[str] = None
    prompt_version: Optional[str] = None
    cache_hit: bool = False
    cost_usd: float = 0.0
    latency_ms: int = 0
    refusal: Optional[str] = None  # Set if the model refused


@dataclass
class RouteConfig:
    """Configuration for one inference route."""
    route: str
    prompt_version: str
    schema_version: str
    model_id: str  # concrete slug, e.g. "anthropic/claude-haiku-4-5" — never a `-latest` alias
    tier: ModelTier
    max_retries: int = 2
    timeout_s: int = 30
    cost_per_call: float = 0.0
    cache_ttl_hours: int = 168  # 7 days
    fallback_route: Optional[str] = None  # Route to try on failure


@dataclass
class InferenceGatewayConfig:
    """Configuration for the full inference gateway."""
    routes: dict[str, RouteConfig] = field(default_factory=dict)
    default_tier: ModelTier = ModelTier.FAST
    cost_ceiling_usd: float = 0.50  # Per-title ceiling
    no_external_model_id: str = "deterministic-rules"
    prompt_cache_prefix: str = "publisher-inference-v1"
    log_dir: Optional[str] = None


# ── Prompt cache manager ────────────────────────────────────────

class PromptCacheManager:
    """Manages prompt caching for inference routes.
    
    From BUILD_PLAN.md §3.16 and O7:
    The classification prompt prefix is designed for ≥90% cache-read ratio.
    """
    
    def __init__(self):
        self._cache: dict[str, str] = {}
        self._prefix_hits = 0
        self._prefix_misses = 0
    
    def get_prefix(self, route: str, prompt_version: str) -> str:
        """Get or build the cacheable prefix for a route."""
        key = f"{route}/{prompt_version}"
        if key in self._cache:
            self._prefix_hits += 1
            return self._cache[key]
        
        # Build the prefix from the route's prompt template
        prefix = self._build_prefix(route, prompt_version)
        self._cache[key] = prefix
        self._prefix_misses += 1
        return prefix
    
    def _build_prefix(self, route: str, prompt_version: str) -> str:
        """Build the shared prompt prefix (system instructions + schema)."""
        # In production, loads from prompt templates directory
        return f"Route: {route} v{prompt_version}\nSchema: classification/1\n"
    
    @property
    def cache_read_ratio(self) -> float:
        total = self._prefix_hits + self._prefix_misses
        return self._prefix_hits / total if total > 0 else 0.0


# ── Cost tracking ───────────────────────────────────────────────

@dataclass
class CostTracker:
    """Per-tenant, per-title cost tracking."""
    tenant_id: str
    title_id: Optional[str] = None
    total_cost: float = 0.0
    calls_by_route: dict[str, int] = field(default_factory=dict)
    ceiling_exceeded: bool = False
    
    def record_call(self, route: str, cost: float):
        self.total_cost += cost
        self.calls_by_route[route] = self.calls_by_route.get(route, 0) + 1
    
    def is_within_budget(self, ceiling: float) -> bool:
        return self.total_cost <= ceiling


# ── Routing policy loading (LLM_STRATEGY.md §4 — data, not code) ─

# Repo-root-relative location of the versioned routing policy.
DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[3] / "platform" / "routing" / "policy.yaml"
)

_TIER_BY_NAME = {
    "rules": ModelTier.RULES,
    "fast": ModelTier.FAST,
    "good": ModelTier.GOOD,
    "best": ModelTier.BEST,
}


def load_routes_from_policy(
    policy_path: Optional[str] = None,
    service: Optional[str] = None,
) -> dict[str, RouteConfig]:
    """
    Load inference routes from `platform/routing/policy.yaml`.

    `service` filters to routes owned by one gateway. The structure classification
    gateway passes "structure" so it never serves the alt-text route — LLM_STRATEGY.md
    §3.16 keeps alt-text in its own service precisely so the no-free-text invariant
    stays absolute on the classification surface.

    Returns an empty dict if the policy file is absent, so the gateway degrades to
    whatever routes the caller supplied rather than crashing — but a route that is
    genuinely missing still fails loudly at `classify()` with "Unknown route".

    Routes carrying no `model` (the cover-art panel, for example) are skipped: they are
    not chat/completions routes and have no RouteConfig representation.
    """
    path = Path(policy_path) if policy_path else DEFAULT_POLICY_PATH
    if not path.exists():
        return {}

    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a declared dependency
        return {}

    with open(path, encoding="utf-8") as f:
        policy = yaml.safe_load(f) or {}

    routes: dict[str, RouteConfig] = {}
    for name, spec in (policy.get("routes") or {}).items():
        if not isinstance(spec, dict):
            continue
        model_id = spec.get("model")
        if not model_id:
            continue
        if service is not None and spec.get("service") != service:
            continue

        # A moving alias inside a cache key silently changes which concrete model
        # produced a frozen artifact. policy.yaml documents this rule; enforce it here
        # so a bad edit fails at load time rather than at the 2029 rebuild.
        if model_id.endswith("-latest") or model_id.startswith("~"):
            raise ValueError(
                f"route {name!r} pins a moving model alias {model_id!r}. "
                f"Cache keys embed model_id, so an alias silently swaps the model "
                f"under a frozen artifact. Use a concrete slug."
            )

        routes[name] = RouteConfig(
            route=name,
            prompt_version=str(spec.get("prompt_version", "1.0")),
            schema_version=str(spec.get("schema_version", "classification/1")),
            model_id=model_id,
            tier=_TIER_BY_NAME.get(str(spec.get("tier", "fast")).lower(), ModelTier.FAST),
            cost_per_call=float(spec.get("cost_per_call", 0.0)),
            fallback_route=spec.get("fallback_route"),
        )
    return routes


# ── Inference gateway ───────────────────────────────────────────

class InferenceGateway:
    """
    Central gateway for all LLM calls.
    
    From D9 (BUILD_PLAN.md §1):
    - Every LLM and agent call runs at a gate boundary
    - Writes its result into a CAS artifact
    - model_id + prompt_version + schema_version are part of the cache key
    
    From LLM_STRATEGY.md:
    - Rules first, LLM second (cascade)
    - Model output is frozen data, never a live decision
    - No prose output — only closed-enum classifications
    """
    
    def __init__(self, config: Optional[InferenceGatewayConfig] = None,
                 policy_path: Optional[str] = None):
        self._config = config or InferenceGatewayConfig()
        self._prompt_cache = PromptCacheManager()
        self._cost_trackers: dict[str, CostTracker] = {}

        # Routes come from versioned YAML, not code (LLM_STRATEGY.md §4: "Routing
        # policy is data, not code. A table the ops team can change without a deploy.")
        #
        # `setdefault` so caller-supplied routes WIN. The previous version called
        # _register_default_routes() unconditionally at the end of __init__, which
        # silently discarded any routes passed in via InferenceGatewayConfig.
        for route_name, route_config in load_routes_from_policy(
            policy_path, service="structure"
        ).items():
            self._config.routes.setdefault(route_name, route_config)
    
    def classify(self, request: InferenceRequest) -> InferenceResult:
        """
        Run a classification request through the inference cascade.
        
        Cascade: rules → fast model → good model → best model.
        Stops at the first tier that produces confidence above threshold.
        """
        route_config = self._config.routes.get(request.route)
        if not route_config:
            return InferenceResult(
                request_id=request.request_id,
                route=request.route,
                tier_used=ModelTier.RULES,
                output={"error": f"Unknown route: {request.route}"},
                confidence=0.0,
            )
        
        # Check tenant opt-out
        if request.no_external_llm:
            return self._run_deterministic(request)
        
        # Check cost ceiling
        tracker = self._get_cost_tracker(request)
        if not tracker.is_within_budget(self._config.cost_ceiling_usd):
            # LLM_STRATEGY.md §5: "at the ceiling, drop to rules-only and *tell the
            # user*". Previously this returned only an error dict and no
            # classifications, which is a silent downgrade rather than a degraded-but-
            # working result.
            return self._run_deterministic(request, refusal="cost_ceiling")
        
        # Determine starting tier
        start_tier = request.force_tier or route_config.tier
        
        # Cache hit check (simulated — real impl checks CAS)
        cache_key = self._cache_key(request, route_config)
        cached = self._check_cache(cache_key)
        if cached:
            cached["cache_hit"] = True
            return InferenceResult(
                request_id=request.request_id,
                route=request.route,
                tier_used=start_tier,
                output=cached["output"],
                confidence=cached.get("confidence", 1.0),
                model_id=route_config.model_id,
                prompt_version=route_config.prompt_version,
                cache_hit=True,
                cost_usd=0.0,
            )
        
        # Run the model call (simulated)
        result = self._call_model(request, route_config, start_tier)
        
        # Record cost
        tracker.record_call(request.route, result.cost_usd)
        
        # Write to CAS (simulated)
        self._write_to_cache(cache_key, result)
        
        return result
    
    def _run_deterministic(self, request: InferenceRequest,
                           refusal: Optional[str] = None) -> InferenceResult:
        """
        Rules-only classification — no external LLM call.

        Used for the `no_external_llm` privacy tier (LLM_STRATEGY.md §4.3) and for the
        spend-ceiling path (§5). Both are documented as routing to *rules-only*, not to
        nothing: "a `no_external_llm` tenant flag that routes to rules-only (with an
        honest confidence banner in the UI)".

        This used to return a hardcoded `{"status": "rules_only", "confidence": 0.5}`
        without calling the rules engine at all, so a privacy-tier tenant received no
        classifications whatsoever — labelled as a 0.5-confidence success. That is a
        silent quality downgrade, which D8 bans outright.

        Every result carries `degraded: True` and a human-readable `banner` so the UI
        cannot present rules-only output as if it were the full cascade.
        """
        nodes = request.inputs.get("nodes", []) or []
        classified = [
            {
                "sourceRef": n.get("sourceRef", f"node-{i}"),
                # The rules engine's own verdict, carried through verbatim. Falling back
                # to "uncertain" is honest: it marks the node for human review rather
                # than inventing a label.
                "classification": n.get("classification") or n.get("suggested") or "uncertain",
                "confidence": float(n.get("confidence", 0.0)),
            }
            for i, n in enumerate(nodes)
        ]
        mean_confidence = (
            sum(c["confidence"] for c in classified) / len(classified) if classified else 0.0
        )

        return InferenceResult(
            request_id=request.request_id,
            route=request.route,
            tier_used=ModelTier.RULES,
            output={
                "schema": "classification/1",
                "nodes": classified,
                "degraded": True,
                "banner": (
                    "Structure confidence lowered — classified by rules only, with no "
                    "model escalation. Please review chapter detection."
                ),
                "reason": refusal or "no_external_llm",
                "modelInfo": {
                    "modelId": self._config.no_external_model_id,
                    "promptVersion": "0.0",
                    "schemaVersion": "classification/1",
                    "cacheHit": False,
                    "costUsd": 0.0,
                },
            },
            confidence=mean_confidence,
            model_id=self._config.no_external_model_id,
            prompt_version="0.0",
            cost_usd=0.0,
            refusal=refusal,
        )
    
    def _call_model(self, request: InferenceRequest, config: RouteConfig,
                    tier: ModelTier) -> InferenceResult:
        """
        Call the LLM.
        
        In production, this calls the OpenRouter API or Bedrock.
        In the tracer bullet, returns a simulated result.
        """
        # Simulate model call
        import time
        start = time.monotonic()
        
        # Build classifications for low-confidence nodes
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
                    "modelId": config.model_id,
                    "promptVersion": config.prompt_version,
                    "schemaVersion": config.schema_version,
                    "cacheHit": False,
                    "costUsd": config.cost_per_call,
                },
            },
            confidence=0.85,
            model_id=config.model_id,
            prompt_version=config.prompt_version,
            cache_hit=False,
            cost_usd=config.cost_per_call,
            latency_ms=latency_ms,
        )
    
    def _cache_key(self, request: InferenceRequest, config: RouteConfig) -> str:
        """Compute cache key for an inference call."""
        payload = {
            "route": request.route,
            "prompt_version": config.prompt_version,
            "schema_version": config.schema_version,
            "model_id": config.model_id,
            "inputs": request.inputs,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
    
    def _check_cache(self, cache_key: str) -> Optional[dict]:
        """Check if a cached inference result exists.
        
        In production, checks CAS + Postgres cache index.
        """
        return None  # Miss for tracer bullet
    
    def _write_to_cache(self, cache_key: str, result: InferenceResult):
        """Store inference result in CAS.
        
        In production, writes to CAS and indexes in Postgres.
        """
        pass
    
    def _get_cost_tracker(self, request: InferenceRequest) -> CostTracker:
        """Get or create cost tracker for a tenant."""
        key = request.tenant_id or "default"
        if key not in self._cost_trackers:
            self._cost_trackers[key] = CostTracker(tenant_id=key)
        return self._cost_trackers[key]
    
    @property
    def prompt_cache_ratio(self) -> float:
        """Report the prompt cache read ratio (≥90% target)."""
        return self._prompt_cache.cache_read_ratio
    
    def cost_summary(self, tenant_id: Optional[str] = None) -> dict:
        """Report cost summary for a tenant or all tenants."""
        if tenant_id:
            trackers = {tenant_id: self._cost_trackers.get(tenant_id)}
        else:
            trackers = self._cost_trackers
        
        return {
            tid: {
                "total_cost": t.total_cost,
                "calls_by_route": t.calls_by_route,
                "within_budget": t.is_within_budget(self._config.cost_ceiling_usd),
            }
            for tid, t in trackers.items()
            if t
        }


# ── Convenience factory ─────────────────────────────────────────

def create_gateway(config: Optional[InferenceGatewayConfig] = None) -> InferenceGateway:
    """Create a configured inference gateway."""
    return InferenceGateway(config=config or InferenceGatewayConfig(
        routes={},
        cost_ceiling_usd=0.50,
    ))
