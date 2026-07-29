"""Tests for agent runtime and tools."""

from publisher_agents.runtime import (
    AgentRuntime, AgentRole, AgentCall, AgentResult, TaskBudget,
    ToolRegistry, ToolSpec, register_tool, get_tool_registry,
)


def test_agent_runtime_creation():
    runtime = AgentRuntime()
    assert runtime is not None


def test_agent_execute_structure_wrangler():
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.STRUCTURE_WRANGLER,
        agent_version="1.0",
        inputs={
            "low_confidence_nodes": [
                {"block_index": 1, "suggested": "paragraph", "confidence": 0.6,
                 "alternatives": ["chapter-title", "heading-2"]},
            ],
        },
        tools=["query_nodes", "propose_override"],
    )
    assert result.role == AgentRole.STRUCTURE_WRANGLER
    assert len(result.output.get("proposals", [])) >= 1
    assert result.call.cost_usd >= 0


def test_agent_execute_compositor():
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.COMPOSITOR,
        agent_version="1.0",
        inputs={
            "defects": [
                {"page_number": 1, "defect_type": "Orphan",
                 "severity": "error", "description": "Orphan at page 1"},
            ],
        },
        tools=["scan_pagemap", "crop", "propose_override"],
    )
    assert result.role == AgentRole.COMPOSITOR
    assert len(result.output.get("proposals", [])) >= 1


def test_agent_execute_preflight_explainer():
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.PREFLIGHT_EXPLAINER,
        agent_version="1.0",
        inputs={
            "checks": [
                {"code": "trim-size", "status": "fail",
                 "humanMessage": "Trim size mismatch",
                 "suggestedFix": "Adjust PDF to 152x229mm"},
            ],
        },
        tools=["read_preflight"],
    )
    assert result.role == AgentRole.PREFLIGHT_EXPLAINER
    explanations = result.output.get("explanations", [])
    assert len(explanations) >= 1
    assert explanations[0]["severity"] == "blocking"


def test_tool_registry():
    registry = ToolRegistry()
    
    registry.register(ToolSpec(name="test-tool", description="A test tool"), lambda: "done")
    
    tool = registry.spec("test-tool")
    assert tool is not None
    assert tool.name == "test-tool"
    
    result = registry.call("test-tool")
    assert result == "done"


def test_task_budget_defaults():
    budget = TaskBudget()
    assert budget.max_tokens == 20000
    assert budget.max_subagents == 3
    assert budget.effort == "medium"


def test_agent_result():
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.STRUCTURE_WRANGLER,
        agent_version="1.0",
        inputs={"low_confidence_nodes": []},
        tools=[],
    )
    assert isinstance(result, AgentResult)
    assert result.call.started_at is not None
    assert result.call.latency_ms >= 0


def test_agent_call_tracks_tools():
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.COMPOSITOR,
        agent_version="1.0",
        inputs={"defects": []},
        tools=["scan_pagemap", "crop"],
    )
    assert "scan_pagemap" in result.call.tools_used
    assert "crop" in result.call.tools_used


def test_structure_wrangler_from_runtime():
    from publisher_agents.structure_wrangler import StructureWrangler
    runtime = AgentRuntime()
    wrangler = StructureWrangler(runtime)
    
    result = wrangler.analyze([
        {"block_index": 0, "text": "Hello", "suggested": "paragraph",
         "confidence": 0.6, "alternatives": ["heading"]},
    ])
    assert len(result.output.get("proposals", [])) >= 1


def test_compositor_from_runtime():
    from publisher_agents.compositor import Compositor
    runtime = AgentRuntime()
    compositor = Compositor(runtime)
    
    result = compositor.scan_and_fix([
        {"page_number": 1, "defect_type": "Widow", "severity": "warning",
         "description": "Widow detected"},
    ])
    assert result is not None


def test_preflight_explainer_from_runtime():
    from publisher_agents.preflight_explainer import PreflightExplainer
    runtime = AgentRuntime()
    explainer = PreflightExplainer(runtime)
    
    result = explainer.explain({
        "checks": [
            {"code": "bleed", "status": "fail",
             "humanMessage": "Bleed too small",
             "suggestedFix": "Increase bleed to 3mm"},
        ],
        "profileId": "KDP 6x9",
    })
    assert result.output.get("totalFindings", 0) >= 1


def test_evaluate_structure_wrangler():
    from publisher_agents.structure_wrangler import evaluate_structure_wrangler, StructureWrangler
    runtime = AgentRuntime()
    wrangler = StructureWrangler(runtime)
    
    result = evaluate_structure_wrangler(
        wrangler,
        test_cases=[],
        golden_labels=[],
    )
    assert "precision" in result
    assert "recall" in result


def test_evaluate_compositor():
    from publisher_agents.compositor import evaluate_compositor, Compositor
    runtime = AgentRuntime()
    compositor = Compositor(runtime)
    
    result = evaluate_compositor(compositor, test_defect_sets=[])
    assert "fix_success_rate" in result


def test_proposal_requires_human_gate():
    spec = ToolSpec(name="propose_override", description="Test", requires_human_gate=True)
    assert spec.requires_human_gate is True
