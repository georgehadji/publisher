"""
Compositor agent -- looks at preflight's page-level warnings on the press PDF.

From AGENT_DESIGN.md §1.2 and §1.3, as built (W11, docs/WIRING_PLAN.md):
Trigger: `layout-review`, after preflight passes with page-level warnings
Tools: crop (the build's own press PDF, bound by the caller)
Action space: one closed verdict per finding -- confirmed, not-a-defect,
  needs-human -- never a change to the book
Gate: the verdicts are advisory (layout-review/1 is terminal); a human reads them

AGENT_DESIGN.md's fuller loop -- propose a DesignSpec patch, recompose,
re-crop -- needs an accept path for patches and a per-build re-render that
neither exists yet. `render_range` stays unavailable by design: a new layout
is a build. Until then the Compositor answers the question a reviewer asks
first: is this warning a real defect on the page?
"""

from __future__ import annotations

from typing import Optional

from .runtime import AgentResult, AgentRole, AgentRuntime, TaskBudget

PROMPT_VERSION = "1.0"
VERDICTS = ("confirmed", "not-a-defect", "needs-human")
SYSTEM_PROMPT = """\
You check a printed book's pages for the layout defects an automatic scan
reported. Each finding names a page and a kind:
- widow: the last line of a paragraph stranded alone at the top of the page
- runt: a paragraph whose last line is a single short word
- hyphen-stack: three or more consecutive lines ending in a hyphen
- river: a channel of wide word gaps running down several lines
Use crop to look at each page (request several crops in one turn). Answer with
one verdict per finding: confirmed (the defect is there and a reader would
notice), not-a-defect (the scan was wrong, or it is intended, e.g. verse or a
list), or needs-human (you cannot tell from the page)."""


def verdict_schema(finding_ids: list[str]) -> dict:
    """The answer: verdicts on the findings it was given, nothing else."""
    return {
        "type": "object",
        "properties": {"verdicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"finding": {"type": "string", "enum": finding_ids},
                           "verdict": {"type": "string", "enum": list(VERDICTS)}},
            "required": ["finding", "verdict"],
            "additionalProperties": False,
        }}},
        "required": ["verdicts"],
        "additionalProperties": False,
    }


class Compositor:
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        runtime.set_budget(AgentRole.COMPOSITOR, TaskBudget(
            max_tokens=30000,   # a whole page at CROP_DPI is ~1.5k tokens; layout-review caps the pages
            max_turns=6,
            max_subagents=0,
            max_cost_usd=0.15,
            effort="medium",
        ))

    def verify(self, findings: list[dict], press_pdf: str,
               agent_version: str = PROMPT_VERSION) -> tuple[dict[str, str], Optional[AgentResult]]:
        """{finding id: verdict} for `findings` ({"id", "kind", "page"}), and the
        run (None when there was nothing to look at). A finding the answer
        leaves out, or every finding of a failed run, has no verdict."""
        if not findings:
            return {}, None
        result = self._runtime.execute(
            role=AgentRole.COMPOSITOR,
            agent_version=agent_version,
            inputs={"findings": [{"id": f["id"], "kind": f["kind"], "page": f["page"]} for f in findings]},
            tools=["crop"],
            output_schema=verdict_schema([f["id"] for f in findings]),
            system_prompt=SYSTEM_PROMPT,
            bound={"press_pdf": press_pdf},
        )
        if result.failed:
            return {}, result
        verdicts: dict[str, str] = {}
        for v in result.output["verdicts"]:
            verdicts.setdefault(v["finding"], v["verdict"])   # a repeated finding: its first verdict
        return verdicts, result
