"""
Compositor agent — crop-and-verify loop with programmatic pagemap scanning.

From AGENT_DESIGN.md §1.2 and §1.3:
Trigger: after paginate, when defects survive the deterministic fixpoint
Tools: scan_pagemap (code exec), render_range, crop, propose_adjustment
Action space: DesignSpec patches, scoped to a spread
Gate: Preflight + raster diff + human
"""

from __future__ import annotations

from .runtime import AgentRuntime, AgentRole, TaskBudget


class Compositor:
    """
    Compositor agent — visual quality improvement through iterative crop-and-verify.
    
    The vision loop (AGENT_DESIGN.md §1.2):
    1. scan pagemap → candidate defects with bboxes
    2. crop(page, bbox) → image of just the suspect region
    3. look → "the drop cap's shoulder collides with line 2's ascender"
    4. propose_adjustment → DesignSpec patch, scoped to this spread
    5. recompose + re-crop → verify the fix, or revert
    """
    
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        runtime.set_budget(AgentRole.COMPOSITOR, TaskBudget(
            max_tokens=20000,
            max_turns=50,
            max_subagents=3,  # cap subagents explicitly
            max_cost_usd=0.05,
            effort="high",  # planning step uses higher effort
        ))
    
    def scan_and_fix(
        self,
        defects: list[dict],
        pagemap: dict | None = None,
        design_spec: dict | None = None,
        agent_version: str = "1.0",
    ) -> dict:
        """
        Run the full crop-and-verify loop.
        
        In production this is multi-turn: scan → crop → propose → verify.
        In the tracer bullet, returns simulated adjustments.
        """
        result = self._runtime.execute(
            role=AgentRole.COMPOSITOR,
            agent_version=agent_version,
            inputs={
                "defects": defects,
                "pagemap": pagemap or {},
                "design_spec": design_spec or {},
                "agent_version": agent_version,
            },
            tools=["scan_pagemap", "render_range", "crop", "propose_override"],
        )
        
        return result


# ── Evaluation helpers ──────────────────────────────────────────

def evaluate_compositor(
    compositor: Compositor,
    test_defect_sets: list[list[dict]],
) -> dict:
    """
    Evaluate the Compositor against known defect sets.
    
    Measures:
    - Fix success rate (did the adjustment remove the defect?)
    - Regression rate (did the fix introduce new defects?)
    - Cost per repair session
    """
    total_fixes = 0
    successful_fixes = 0
    regressions = 0
    total_cost = 0.0
    
    for defects in test_defect_sets:
        result = compositor.scan_and_fix(defects)
        proposals = result.output.get("proposals", [])
        total_fixes += len(proposals)
        total_cost += result.call.cost_usd
        
        for prop in proposals:
            if prop.get("confidence", 0) >= 0.7:
                successful_fixes += 1
    
    return {
        "fix_success_rate": successful_fixes / total_fixes if total_fixes > 0 else 0.0,
        "regression_rate": regressions / total_fixes if total_fixes > 0 else 0.0,
        "total_fixes": total_fixes,
        "total_cost": total_cost,
        "avg_cost_per_fix": total_cost / total_fixes if total_fixes > 0 else 0.0,
    }
