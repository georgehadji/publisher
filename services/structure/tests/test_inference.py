"""Tests for inference gateway."""

import os
import sys
from pathlib import Path

import pytest

from publisher_structure.inference import (
    InferenceGateway, InferenceRequest, InferenceResult,
    ModelTier, RouteConfig, CostTracker, PromptCacheManager,
    InferenceGatewayConfig,
)

# fabricating_provider.py is a sibling in this same (non-package, no
# __init__.py) tests/ directory -- same sys.path-insert pattern
# services/idml/tests/test_outputs.py already uses for alttext/epub/onix.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from fabricating_provider import FabricatingProvider  # noqa: E402


def test_gateway_requires_a_provider():
    """E6.1: the gateway's constructor takes an InferenceProvider by
    injection and has no default -- there is no `simulate` flag left to
    accidentally leave at a fabricating default."""
    with pytest.raises(TypeError):
        InferenceGateway()


def test_fabricating_provider_is_not_shipped_to_the_worker_image():
    """E6.1 acceptance: the fabricating provider is not importable from the
    worker image's PYTHONPATH.

    Dockerfile.worker COPYs services/ wholesale and puts each service's ROOT
    (e.g. /app/services/structure) directly on PYTHONPATH -- needed so
    `publisher_structure` itself imports. Without .dockerignore excluding
    test directories, Python 3's implicit namespace packages (PEP 420, no
    __init__.py required) would make `import tests.fabricating_provider`
    succeed inside the container purely because the file sits next to
    `publisher_structure/` on that PYTHONPATH entry -- regardless of the
    fact that tests/ is never `pip install`ed (see ../pyproject.toml).
    """
    repo_root = Path(__file__).resolve().parents[3]
    dockerignore = repo_root / ".dockerignore"
    assert dockerignore.is_file(), (
        f"{dockerignore} does not exist -- nothing prevents Dockerfile.worker's "
        "wholesale `COPY services/ services/` from shipping every tests/ "
        "directory (fabricating_provider.py included) into the image."
    )
    patterns = [
        line.strip() for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    target = Path("services") / "structure" / "tests" / "fabricating_provider.py"
    excluded = any(pattern.strip("/").endswith("tests") for pattern in patterns)
    assert excluded, (
        f".dockerignore has no pattern excluding a `tests` directory, so "
        f"{target} is not actually kept out of the build context: {patterns}"
    )


def test_simulated_classifications_are_marked():
    """Any classification produced by the fabricating provider carries
    `simulated: true` in its artifact, so a fabricated confidence is visible in
    the build record instead of being inferred from source reading."""
    gateway = InferenceGateway(provider=FabricatingProvider())
    request = InferenceRequest(
        request_id="sim-marked",
        route="structure-classify",
        inputs={"nodes": [{"sourceRef": "p1", "text": "Hello"}]},
        prompt_version="1.0",
        schema_version="classification/1",
    )
    result = gateway.classify(request)
    assert result.output["modelInfo"]["simulated"] is True


def test_gateway_creation():
    """A constructor cannot return None. Assert the gateway loaded its routes from
    versioned YAML (LLM_STRATEGY.md §4) rather than from hardcoded literals."""
    gateway = InferenceGateway(provider=FabricatingProvider())
    routes = gateway._config.routes
    assert "structure-classify" in routes
    # Routing policy is data: concrete slugs only, never a moving `-latest` alias,
    # because model_id is part of the cache key.
    for name, cfg in routes.items():
        assert not cfg.model_id.endswith("-latest"), f"{name} pins a moving alias"
        assert "/" in cfg.model_id, f"{name} model_id {cfg.model_id!r} is not a full slug"
    # §3.16 keeps alt-text out of the classification gateway.
    assert "alttext" not in routes


def test_classify_basic():
    gateway = InferenceGateway(provider=FabricatingProvider())
    request = InferenceRequest(
        request_id="test-001",
        route="structure-classify",
        inputs={"nodes": [{"sourceRef": "p1", "text": "Hello"}]},
        prompt_version="1.0",
        schema_version="classification/1",
    )
    result = gateway.classify(request)
    assert result.route == "structure-classify"
    assert result.cost_usd >= 0
    assert "nodes" in result.output


def test_classify_no_external_llm():
    gateway = InferenceGateway(provider=FabricatingProvider())
    request = InferenceRequest(
        request_id="test-002",
        route="structure-classify",
        inputs={},
        prompt_version="1.0",
        schema_version="classification/1",
        no_external_llm=True,
    )
    result = gateway.classify(request)
    assert result.tier_used == ModelTier.RULES
    assert result.cost_usd == 0.0


def test_cost_tracking():
    tracker = CostTracker(tenant_id="tenant-1")
    assert tracker.total_cost == 0.0
    
    tracker.record_call("structure-classify", 0.003)
    assert tracker.total_cost == 0.003
    
    tracker.record_call("alttext", 0.01)
    assert tracker.total_cost == pytest.approx(0.013)


def test_cost_ceiling():
    config = InferenceGatewayConfig(cost_ceiling_usd=0.01)
    gateway = InferenceGateway(config, provider=FabricatingProvider())
    
    # Mock a tracker that's exceeded ceiling
    gateway._cost_trackers["test"] = CostTracker(tenant_id="test", total_cost=999.0)
    
    request = InferenceRequest(
        request_id="test-ceiling",
        route="structure-classify",
        inputs={},
        prompt_version="1.0",
        schema_version="classification/1",
        tenant_id="test",
    )
    result = gateway.classify(request)
    assert result.refusal == "cost_ceiling"


def test_prompt_cache():
    manager = PromptCacheManager()
    prefix = manager.get_prefix("structure-classify", "1.1")
    assert prefix.startswith("You classify the structure")
    
    # Second call should be a hit
    prefix2 = manager.get_prefix("structure-classify", "1.1")
    assert prefix == prefix2
    assert manager.cache_read_ratio > 0


def test_route_config():
    config = RouteConfig(
        route="test",
        prompt_version="1.0",
        schema_version="test/1",
        model_id="test-model",
        tier=ModelTier.FAST,
        cost_per_call=0.001,
    )
    assert config.route == "test"
    assert config.cost_per_call == 0.001


class _CountingProvider:
    """A real-shaped (not simulated) answer, counting the calls that cost money."""

    def __init__(self):
        self.calls = 0

    def complete(self, request, route, tier):
        self.calls += 1
        return InferenceResult(
            request_id=request.request_id, route=request.route, tier_used=tier,
            output={"schema": "classification/1",
                    "nodes": [{"sourceRef": "p1", "classification": "chapter-title", "confidence": 0.9}],
                    "modelInfo": {"modelId": route.model_id, "cacheHit": False, "costUsd": 0.004}},
            confidence=0.9, model_id=route.model_id, prompt_version=route.prompt_version, cost_usd=0.004,
        )


def _request(text="Hello", rid="r1"):
    return InferenceRequest(request_id=rid, route="structure-classify",
                            inputs={"nodes": [{"sourceRef": "p1", "text": text}]},
                            prompt_version="1.0", schema_version="classification/1")


def test_the_same_question_is_paid_for_once(tmp_path):
    """The stage cache keys on the whole AST, so a typo fixed anywhere in the book
    used to buy the same classification again. The gateway keys on what it sends."""
    provider = _CountingProvider()
    first = InferenceGateway(provider=provider, cache_dir=tmp_path).classify(_request(rid="b1"))
    # A new gateway, as the next build constructs: the cache is on disk, not in memory.
    again = InferenceGateway(provider=provider, cache_dir=tmp_path).classify(_request(rid="b2"))

    assert provider.calls == 1
    assert (first.cache_hit, again.cache_hit) == (False, True)
    assert again.cost_usd == 0.0 and again.request_id == "b2"
    assert again.output["nodes"] == first.output["nodes"]
    assert again.output["modelInfo"]["cacheHit"] is True and again.output["modelInfo"]["costUsd"] == 0.0
    assert first.output["modelInfo"]["cacheHit"] is False   # the stored copy is not rewritten


def test_a_different_question_or_an_expired_answer_is_asked_again(tmp_path):
    provider = _CountingProvider()
    gateway = InferenceGateway(provider=provider, cache_dir=tmp_path)
    gateway.classify(_request())
    gateway.classify(_request(text="Another chapter"))
    assert provider.calls == 2

    for entry in tmp_path.glob("*.json"):   # older than the route's TTL (168 h)
        os.utime(entry, (0, 0))
    gateway.classify(_request())
    assert provider.calls == 3


def test_simulated_and_refused_answers_are_never_cached(tmp_path):
    gateway = InferenceGateway(provider=FabricatingProvider(), cache_dir=tmp_path)
    gateway.classify(_request())
    assert not gateway.classify(_request()).cache_hit
    assert not list(tmp_path.glob("*.json"))


def test_no_cache_dir_means_no_cache(tmp_path):
    provider = _CountingProvider()
    gateway = InferenceGateway(provider=provider)
    gateway.classify(_request())
    gateway.classify(_request())
    assert provider.calls == 2


def test_cost_summary():
    gateway = InferenceGateway(provider=FabricatingProvider())
    request = InferenceRequest(
        request_id="test-summary",
        route="structure-classify",
        inputs={},
        prompt_version="1.0",
        schema_version="classification/1",
        tenant_id="tenant-summary",
    )
    gateway.classify(request)
    summary = gateway.cost_summary("tenant-summary")
    assert "tenant-summary" in summary
    assert summary["tenant-summary"]["total_cost"] > 0
