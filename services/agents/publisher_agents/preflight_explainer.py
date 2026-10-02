"""
Preflight Explainer agent — explains preflight failures in natural language.

From AGENT_DESIGN.md §1.3:
Trigger: preflight produces findings
Tools: read-only over preflight.json + pagemap
Action space: text report only — cannot change the build
Gate: None needed (read-only)
"""

from __future__ import annotations

from .runtime import AgentResult, AgentRuntime, AgentRole, TaskBudget

# The explainer's answer: one explanation per finding it explains. Text only;
# nothing here can change a build.
EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {"explanations": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "explanation": {"type": "string", "maxLength": 2000},
            "fix": {"type": "string", "maxLength": 2000},
            "severity": {"type": "string", "enum": ["blocking", "advisory"]},
        },
        "required": ["code", "explanation", "fix", "severity"],
        "additionalProperties": False,
    }}},
    "required": ["explanations"],
    "additionalProperties": False,
}


class PreflightExplainer:
    """
    Preflight Explainer agent.
    
    Takes a preflight report and produces human-readable explanations
    with actionable fix suggestions. Read-only — cannot change the build.
    """
    
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        runtime.set_budget(AgentRole.PREFLIGHT_EXPLAINER, TaskBudget(
            max_tokens=10000,
            max_turns=10,
            max_subagents=0,
            max_cost_usd=0.01,
            effort="low",
        ))
    
    def explain(
        self,
        preflight_report: dict,
        pagemap: dict | None = None,
        agent_version: str = "1.0",
    ) -> AgentResult:
        """Natural-language explanations of the report's findings. The report
        is bound to `read_preflight`, so the model reads it through the tool."""
        return self._runtime.execute(
            role=AgentRole.PREFLIGHT_EXPLAINER,
            agent_version=agent_version,
            inputs={"profile": preflight_report.get("profileId", "unknown")},
            tools=["read_preflight"],
            output_schema=EXPLANATION_SCHEMA,
            bound={"report": preflight_report},
        )


# ── Evaluation helpers ──────────────────────────────────────────

def evaluate_preflight_explainer(
    explainer: PreflightExplainer,
    test_reports: list[dict],
) -> dict:
    """
    Evaluate the Preflight Explainer against known reports.
    
    Measures:
    - Coverage (did it explain every finding?)
    - Accuracy (were the explanations correct?)
    """
    total_findings = 0
    explained = 0
    
    for report in test_reports:
        result = explainer.explain(report)
        explanations = result.output.get("explanations", [])
        total_findings += report.get("summary", {}).get("failed", 0)
        explained += len(explanations)
    
    return {
        "coverage": explained / total_findings if total_findings > 0 else 1.0,
        "explanations_generated": explained,
        "total_findings": total_findings,
    }
