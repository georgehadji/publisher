"""
What a structure-classification model is asked, and what it may answer (W3,
docs/WIRING_PLAN.md).

Three pieces, all derived from `schemas/classification/classification.schema.json`
so none can drift from it:

- `response_schema()` -- the JSON schema sent as OpenRouter's
  `response_format` (strict). A reply outside the closed label set can't come
  back, so it can't fail the parse at run time either. Strict structured output
  wants every property required and `additionalProperties: false`, which is
  why this is a reduced schema and not classification/1 itself.
- `system_prompt(route, version)` -- the instructions, one per route and
  prompt version. Changing the text means bumping the route's
  `prompt_version` in platform/routing/policy.yaml, which is in the cache key.
- `classification_document(reply, sent, ...)` -- the model's reply checked
  (schema, and only refs that were sent) and wrapped as classification/1.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

_SCHEMA_PATH = (Path(__file__).resolve().parents[3]
                / "schemas" / "classification" / "classification.schema.json")


class ClassificationReplyInvalid(ValueError):
    """A model reply that is not a valid classification of what was sent."""


@functools.lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def labels() -> list[str]:
    """The closed label set, as classification/1 declares it."""
    return list(_schema()["$defs"]["classifiedNode"]["properties"]["classification"]["enum"])


def response_schema() -> dict:
    node = _schema()["$defs"]["classifiedNode"]["properties"]
    return {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sourceRef": {"type": "string", "maxLength": node["sourceRef"]["maxLength"]},
                        "classification": {"type": "string", "enum": labels()},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["sourceRef", "classification", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }


def response_format() -> dict:
    """OpenRouter's structured-output parameter for a classification route."""
    return {"type": "json_schema",
            "json_schema": {"name": "classification", "strict": True, "schema": response_schema()}}


# One template per (route, prompt_version). A route with no entry here has no
# prompt and refuses to call a model, rather than sending a placeholder.
_PROMPTS = {
    ("structure-classify", "1.1"): """\
You classify the structure of a book manuscript. Each input node is one block
ingest was unsure of: `sourceRef` (its id), `current` (what ingest made it),
`text` (its text) and `context` (the words that follow it).

For every node, answer with exactly one label from the closed set the response
schema allows, and your confidence from 0 to 1. Judge structure only: whether a
line is a chapter title, a section heading, ordinary prose, a quotation and so on.
Never rewrite, summarise or translate text, and never answer for a sourceRef you
were not given. When the evidence does not decide it, answer `uncertain`.""",
}
_PROMPTS[("structure-classify-deep", "1.1")] = _PROMPTS[("structure-classify", "1.1")]


def system_prompt(route: str, prompt_version: str) -> str:
    try:
        return _PROMPTS[(route, prompt_version)]
    except KeyError:
        raise ValueError(f"no prompt template for route {route!r} at prompt version "
                         f"{prompt_version!r}; add one before routing calls to it") from None


def classification_document(reply: Any, sent: list[str], *, model_id: str, prompt_version: str,
                            cost_usd: float) -> dict:
    """The reply as classification/1, or ClassificationReplyInvalid.

    Structured output makes a reply outside the schema unlikely, not
    impossible (a provider can ignore the parameter), so the reply is checked
    here as well -- and no schema can say "only the sourceRefs you were sent"."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import best_match

    error = best_match(Draft202012Validator(response_schema()).iter_errors(reply))
    if error is not None:
        raise ClassificationReplyInvalid(f"reply does not match the response schema: {error.message[:200]}")
    unknown = sorted({n["sourceRef"] for n in reply["nodes"]} - set(sent))
    if unknown:
        raise ClassificationReplyInvalid(f"reply classifies nodes that were never sent: {unknown[:5]}")
    return {
        "schema": "classification/1",
        "nodes": reply["nodes"],
        "modelInfo": {"modelId": model_id, "promptVersion": prompt_version,
                      "schemaVersion": "classification/1", "cacheHit": False,
                      "costUsd": cost_usd},
    }
