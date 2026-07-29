"""
Structure Wrangler agent — pre-populates the review UI with proposals.

From AGENT_DESIGN.md §1.3:
Trigger: rules confidence low across the doc
Tools: query_nodes, sample_text, preview_structure, propose_override
Action space: OverrideSet ops
Gate: Human review UI (proposals pre-populate it)
"""

from __future__ import annotations

from .runtime import AgentRuntime, AgentRole, AgentCall, AgentResult, TaskBudget


class StructureWrangler:
    """
    Structure Wrangler agent.
    
    Analyzes low-confidence nodes from the rules engine and proposes
    classifications. Proposals pre-populate the review UI so the human
    just clicks "accept" instead of making every decision from scratch.
    
    Target: ≥ 70% of proposals accepted unedited (BUILD_PLAN.md P4 gate).
    """
    
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        runtime.set_budget(AgentRole.STRUCTURE_WRANGLER, TaskBudget(
            max_tokens=15000,
            max_turns=30,
            max_subagents=1,
            max_cost_usd=0.03,
            effort="medium",
        ))
    
    def analyze(
        self,
        low_confidence_nodes: list[dict],
        corpus_context: dict | None = None,
        agent_version: str = "1.0",
    ) -> AgentResult:
        """
        Analyze low-confidence nodes and propose classifications.
        
        Args:
            low_confidence_nodes: Nodes from rules engine with confidence < 0.8
            corpus_context: Optional corpus statistics for context
            agent_version: Agent version string
            
        Returns:
            AgentResult with proposals
        """
        return self._runtime.execute(
            role=AgentRole.STRUCTURE_WRANGLER,
            agent_version=agent_version,
            inputs={
                "low_confidence_nodes": low_confidence_nodes,
                "corpus_context": corpus_context or {},
                "agent_version": agent_version,
            },
            tools=["query_nodes", "propose_override"],
        )
    
    def acceptance_rate(self, results: list[AgentResult]) -> float:
        """Compute acceptance rate across results."""
        if not results:
            return 0.0
        accepted = sum(1 for r in results if r.accepted)
        return accepted / len(results)


# ── Evaluation helpers ──────────────────────────────────────────

def evaluate_structure_wrangler(
    wrangler: StructureWrangler,
    test_cases: list[dict],
    golden_labels: list[list[str]],
) -> dict:
    """
    Evaluate the Structure Wrangler against labeled test cases.
    
    Returns precision, recall, and acceptance-rate metrics.
    """
    from publisher_structure.rules import parse_html, classify_blocks, find_low_confidence
    
    total_proposals = 0
    correct_proposals = 0
    total_accepted = 0
    
    for i, case in enumerate(test_cases):
        html = case.get("html", "")
        blocks = parse_html(html)
        low_conf_indices = find_low_confidence(blocks, threshold=0.8)
        
        low_conf_nodes = [
            {
                "block_index": idx,
                "text": blocks[idx].text[:100] if idx < len(blocks) else "",
                "suggested": "unknown",
                "confidence": 0.0,
                "alternatives": ["paragraph", "heading"],
            }
            for idx in low_conf_indices
        ]
        
        result = wrangler.analyze(low_conf_nodes)
        proposals = result.output.get("proposals", [])
        total_proposals += len(proposals)
        
        # Compare against golden labels
        if i < len(golden_labels):
            expected = golden_labels[i]
            for prop in proposals:
                if prop.get("to") in expected:
                    correct_proposals += 1
    
    precision = correct_proposals / total_proposals if total_proposals > 0 else 0.0
    recall = correct_proposals / max(len(golden_labels), 1) if golden_labels else 0.0
    
    return {
        "precision": precision,
        "recall": recall,
        "total_proposals": total_proposals,
        "correct_proposals": correct_proposals,
        "acceptance_rate": 0.0,  # Requires human eval
    }
