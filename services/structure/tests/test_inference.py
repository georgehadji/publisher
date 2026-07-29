"""Tests for inference gateway."""

import pytest

from publisher_structure.inference import (
    InferenceGateway, InferenceRequest, InferenceResult,
    ModelTier, RouteConfig, CostTracker, PromptCacheManager,
    InferenceGatewayConfig,
)


def test_gateway_creation():
    gateway = InferenceGateway()
    assert gateway is not None


def test_classify_basic():
    gateway = InferenceGateway()
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
    gateway = InferenceGateway()
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
    gateway = InferenceGateway(config)
    
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
    gateway = InferenceGateway()
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
