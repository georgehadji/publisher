"""
The production AgentProvider: OpenRouter chat/completions with tools (W5).

The HTTP client and the reply guard (refusal; the route's reasoning pin) are
publisher_structure's `OpenRouterProvider.chat`, so a classification call and
an agent turn cannot enforce different rules. Calls go out through the same
egress proxy (infra/llm-egress/) as every other OpenRouter call.

Not verified against the live API from this repo: no call has been made with
a real key. Tests drive the runtime with a scripted provider instead.
"""

from __future__ import annotations

import json
from typing import Any

from publisher_structure.inference import OpenRouterProvider, RouteConfig

from .runtime import AgentRole, AgentTurn, ToolCall


class OpenRouterAgentProvider:
    def __init__(self, api_key: str, route: RouteConfig, http: Any = None):
        self._http = http or OpenRouterProvider(api_key=api_key)
        self._route = route

    def respond(self, role: AgentRole, messages: list[dict], tools: list[dict],
                output_schema: dict) -> AgentTurn:
        route = self._route
        body: dict[str, Any] = {
            "model": route.model_id,
            "messages": messages,
            # Strict: the answer schema the caller passed is a closed shape
            # (the Structure Wrangler's lists only the ids it was given).
            "response_format": {"type": "json_schema", "json_schema": {
                "name": f"{role.value}-answer", "strict": True, "schema": output_schema}},
        }
        if tools:
            body["tools"] = tools
        if route.provider:
            body["provider"] = route.provider
        if route.reasoning is not None:
            body["reasoning"] = route.reasoning
        message, usage = self._http.chat(body, route)
        calls = [ToolCall(id=c["id"], name=c["function"]["name"],
                          arguments=json.loads(c["function"].get("arguments") or "{}"))
                 for c in message.get("tool_calls") or []]
        content = message.get("content")
        answer = None if calls else (json.loads(content) if isinstance(content, str) else content)
        return AgentTurn(tool_calls=calls, answer=answer,
                         tokens=int(usage.get("total_tokens") or 0),
                         cost_usd=float(usage.get("cost") or 0.0))
