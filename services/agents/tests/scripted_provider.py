"""
A scripted AgentProvider for tests (W5): replays a fixed list of turns and
records what the runtime sent each time. Test-only, like
services/structure/tests/fabricating_provider.py -- every tests/ directory is
excluded from the Docker build contexts (.dockerignore).
"""

from __future__ import annotations

import copy

from publisher_agents.runtime import AgentRole, AgentTurn, ToolCall


class ScriptedProvider:
    def __init__(self, *turns: AgentTurn):
        self._turns = list(turns)
        self.seen: list[dict] = []   # {"messages", "tools", "schema"} per call

    def respond(self, role: AgentRole, messages, tools, output_schema) -> AgentTurn:
        self.seen.append({"messages": copy.deepcopy(messages), "tools": tools, "schema": output_schema})
        if not self._turns:
            raise AssertionError("the runtime asked for more turns than the script has")
        return self._turns.pop(0)


def call(name: str, i: int = 0, **arguments) -> AgentTurn:
    """One turn asking for one tool call."""
    return AgentTurn(tool_calls=[ToolCall(id=f"t{i}", name=name, arguments=arguments)], tokens=10)


def answer(value) -> AgentTurn:
    return AgentTurn(answer=value, tokens=10)
