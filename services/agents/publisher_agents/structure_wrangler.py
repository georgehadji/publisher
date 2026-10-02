"""
Structure Wrangler agent -- reviews structure-propose's proposals (W5).

From AGENT_DESIGN.md §1.3:
Trigger: rules confidence low across the doc
Tools: query_nodes (read-only)
Action space: which proposals to show, and in what order -- never a new one
Gate: Human review UI (a proposal reaches the override log only when accepted)

`structure-propose` makes proposals deterministically from `classification/1`.
The Wrangler may drop or re-rank them after reading the AST; its answer schema
lists only the ids it was given, so it cannot add a proposal or change one.
"""

from __future__ import annotations

from typing import Optional

from .runtime import AgentResult, AgentRole, AgentRuntime, TaskBudget

PROMPT_VERSION = "1.0"
SYSTEM_PROMPT = """\
You review proposed structural edits to a book manuscript before a human editor
sees them. Each proposal names a chapter (by docxId), the edit (merge it into the
chapter before, make it a section of that chapter, or flag it), and why a
classifier suggested it. Use query_nodes to read the chapters involved. Answer
with `keep`: the ids of the proposals worth the editor's time, most useful first.
Leave out a proposal the text clearly contradicts. You cannot add or change one."""


def keep_schema(proposal_ids: list[str]) -> dict:
    """The answer: a reordered subset of `proposal_ids`, nothing else."""
    return {
        "type": "object",
        "properties": {"keep": {"type": "array", "items": {"type": "string", "enum": proposal_ids},
                                "uniqueItems": True}},
        "required": ["keep"],
        "additionalProperties": False,
    }


class StructureWrangler:
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        runtime.set_budget(AgentRole.STRUCTURE_WRANGLER, TaskBudget(
            max_tokens=15000,
            max_turns=30,
            max_subagents=0,
            max_cost_usd=0.03,
            effort="medium",
        ))

    def review(self, proposals: list[dict], ast: dict,
               agent_version: str = PROMPT_VERSION) -> tuple[list[dict], Optional[AgentResult]]:
        """The proposals to keep, in the Wrangler's order, and the run (None
        when there was nothing to review). A failed run keeps every proposal
        unchanged: the review is advisory, and the human gate still sees each one."""
        if not proposals:
            return [], None
        by_id = {p["id"]: p for p in proposals}
        result = self._runtime.execute(
            role=AgentRole.STRUCTURE_WRANGLER,
            agent_version=agent_version,
            inputs={"proposals": [{"id": p["id"], "type": p["type"], "docxId": p["sourceRef"]["docxId"],
                                   "rationale": p["rationale"]} for p in proposals]},
            tools=["query_nodes"],
            output_schema=keep_schema(list(by_id)),
            system_prompt=SYSTEM_PROMPT,
            bound={"ast": ast},
        )
        if result.failed:
            return proposals, result
        return [by_id[i] for i in result.output["keep"]], result
