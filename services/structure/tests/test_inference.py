"""Tests for inference gateway."""

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
    prefix = manager.get_prefix("structure-classify", "1.0")
    assert "structure-classify" in prefix
    
    # Second call should be a hit
    prefix2 = manager.get_prefix("structure-classify", "1.0")
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
