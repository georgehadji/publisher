"""
What a classification call sends, and what it accepts back (W3, docs/WIRING_PLAN.md).

No network: `OpenRouterProvider._call` is replaced by a function that records
the body and returns a canned payload, so these pin the request shape and the
reply checks, not what a real model would answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from publisher_structure.classify_contract import (
    SECTION_LABELS, ClassificationReplyInvalid, classification_document, labels, response_schema,
    system_prompt,
)
from publisher_structure.inference import (
    InferenceRequest, ModelTier, OpenRouterProvider, PromptCacheManager, load_routes_from_policy,
)

SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                     / "schemas/classification/classification.schema.json").read_text(encoding="utf-8"))
ROUTE = load_routes_from_policy(service="structure")["structure-classify"]
NODES = [{"sourceRef": "c2", "current": "chapter-title", "text": "NO!", "context": "…"}]


def _request() -> InferenceRequest:
    return InferenceRequest(request_id="b1", route=ROUTE.route, inputs={"nodes": NODES},
                            prompt_version=ROUTE.prompt_version, schema_version=ROUTE.schema_version)


def _provider(reply, usage=None, **choice):
    provider = OpenRouterProvider(api_key="sk-test")
    sent = []

    def call(body):
        sent.append(body)
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {"choices": [{"message": {"content": content}, **choice}], "usage": usage or {"cost": 0.001}}

    provider._call = call
    return provider, sent


def test_the_label_set_is_classification_1s_own():
    assert labels() == SCHEMA["$defs"]["classifiedNode"]["properties"]["classification"]["enum"]


def test_every_front_and_back_matter_section_type_has_its_label():
    """Pinned to both schemas: a section type with no label could not be sent,
    and a label outside the set would be refused by strict output."""
    defs = json.loads((Path(__file__).resolve().parents[3] / "schemas/ast/ast.schema.json")
                      .read_text(encoding="utf-8"))["$defs"]
    section_types = {t for union in ("frontMatterNode", "backMatterNode")
                     for branch in defs[union]["allOf"]
                     if "sourceRef" in ((branch.get("then") or {}).get("properties") or {})
                     for t in branch["then"]["properties"]["type"]["enum"]}
    assert set(SECTION_LABELS) == section_types
    assert set(SECTION_LABELS.values()) <= set(labels())


def test_the_request_pins_schema_provider_reasoning_and_prompt():
    provider, sent = _provider({"nodes": [{"sourceRef": "c2", "classification": "paragraph", "confidence": 0.9}]})
    provider.complete(_request(), ROUTE, ModelTier.FAST)
    body = sent[0]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == response_schema()
    assert body["provider"] == {"only": ["amazon-bedrock"], "allow_fallbacks": False, "require_parameters": True,
                                "data_collection": "deny", "zdr": True}
    assert body["reasoning"] == {"effort": "none"}
    assert body["messages"][0]["content"] == system_prompt(ROUTE.route, ROUTE.prompt_version)
    assert "Route:" not in body["messages"][0]["content"], "the placeholder prompt is gone"


def test_a_valid_reply_becomes_classification_1():
    reply = {"nodes": [{"sourceRef": "c2", "classification": "paragraph", "confidence": 0.6}]}
    provider, _ = _provider(reply)
    result = provider.complete(_request(), ROUTE, ModelTier.FAST)
    assert result.output["schema"] == "classification/1" and result.output["nodes"] == reply["nodes"]
    assert result.confidence == 0.6
    from jsonschema import validate
    validate(result.output, SCHEMA)


@pytest.mark.parametrize("reply, reason", [
    ({"nodes": [{"sourceRef": "c2", "classification": "a nice heading", "confidence": 0.9}]}, "response schema"),
    ({"nodes": [{"sourceRef": "invented", "classification": "paragraph", "confidence": 0.9}]}, "never sent"),
    ({"answer": "It is a chapter."}, "response schema"),
])
def test_a_reply_that_is_not_a_classification_of_what_was_sent_fails(reply, reason):
    provider, _ = _provider(reply)
    with pytest.raises(ClassificationReplyInvalid, match=reason):
        provider.complete(_request(), ROUTE, ModelTier.FAST)


def test_a_reply_that_reasoned_on_a_no_reasoning_route_fails():
    reply = {"nodes": [{"sourceRef": "c2", "classification": "paragraph", "confidence": 0.9}]}
    provider, _ = _provider(reply, usage={"completion_tokens_details": {"reasoning_tokens": 40}})
    with pytest.raises(RuntimeError, match="reasoning tokens"):
        provider.complete(_request(), ROUTE, ModelTier.FAST)


def test_a_refusal_is_not_read_as_content():
    provider, _ = _provider("{}", finish_reason="refusal")
    with pytest.raises(RuntimeError, match="refused"):
        provider.complete(_request(), ROUTE, ModelTier.FAST)


def test_a_route_with_no_prompt_refuses_rather_than_sending_a_placeholder():
    with pytest.raises(ValueError, match="no prompt template"):
        system_prompt("genre-suggest", "1.0")


def test_the_cacheable_prefix_is_the_prompt_the_provider_sends():
    assert PromptCacheManager().get_prefix(ROUTE.route, ROUTE.prompt_version) == \
        system_prompt(ROUTE.route, ROUTE.prompt_version)


def test_classification_document_needs_nothing_sent_to_accept_nothing():
    doc = classification_document({"nodes": []}, [], model_id="m", prompt_version="1.1", cost_usd=0)
    assert doc["nodes"] == []
