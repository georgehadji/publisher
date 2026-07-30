import pytest
"""Tests for agent runtime and tools."""

from publisher_agents.runtime import (
    AgentRuntime, AgentRole, AgentCall, AgentResult, TaskBudget,
    ToolRegistry, ToolSpec, register_tool, get_tool_registry,
)


def test_agent_runtime_creation():
    """`assert runtime is not None` cannot fail — a constructor never returns None.
    Assert the budget defaults the runtime exists to enforce (AGENT_DESIGN.md §1.5)."""
    runtime = AgentRuntime()
    budget = runtime.get_budget(AgentRole.COMPOSITOR)
    assert budget.max_tokens >= 20000     # "minimum 20k for a repair session"
    assert budget.max_subagents >= 0
    assert budget.max_cost_usd > 0


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
    
    result = registry.call("test-tool", None)   # None == trusted non-agent caller, explicit
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
    # `result is not None` cannot fail. Assert the agent actually ran and stayed
    # inside its declared action space (DesignSpec patches, never the AST/PDF).
    assert result.call.role is AgentRole.COMPOSITOR
    assert not result.failed, result.error
    assert isinstance(result.output, dict)


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


# ── Adversarial tests -- BUILD_PLAN.md §3.17, §7 DoD ───────────────────────

def test_zero_ast_write_tool_exists():
    """
    The agent action space is override ops and DesignSpec patches only -- NEVER
    the AST, PDF, or preflight verdict (AGENT_DESIGN.md §0, BUILD_PLAN.md D8's
    explicitly banned "an agent that writes to the AST instead of proposing
    override ops"). Provable as a structural absence: no tool in the default
    registry mutates an AST at all, so there is nothing an agent could call to do
    so even if it tried.
    """
    from publisher_agents.runtime import get_tool_registry
    registry = get_tool_registry()
    for spec in registry.list_tools():
        name = spec.name.lower()
        assert "write_ast" not in name and "set_ast" not in name and "mutate_ast" not in name, (
            f"tool '{spec.name}' looks like it could write the AST directly; "
            f"the agent action space must be override ops only"
        )
    # The one tool that changes build state is explicitly gated for human approval.
    propose = registry.spec("propose_override")
    assert propose is not None and propose.requires_human_gate is True


def test_agent_execute_halts_at_task_budget():
    """
    A task budget must "pace and wrap up gracefully" (AGENT_DESIGN.md §1.5), not
    be a dataclass nobody reads. Requesting more tool calls than max_turns must
    fail the call rather than run anyway.
    """
    runtime = AgentRuntime()
    runtime.set_budget(AgentRole.COMPOSITOR, TaskBudget(max_turns=2))
    result = runtime.execute(
        role=AgentRole.COMPOSITOR,
        agent_version="1.0",
        inputs={},
        tools=["scan_pagemap", "crop", "propose_override", "render_range"],  # 4 > 2
    )
    assert result.failed is True
    assert "max_turns" in result.call.error


def test_agent_execute_respects_subagent_cap():
    """
    AGENT_DESIGN.md §1.5: "explicit subagent cap (current Opus delegates readily;
    an uncapped Compositor spawns one per spread)". Requesting more subagents than
    the budget allows must fail the call, not silently proceed.
    """
    runtime = AgentRuntime()
    runtime.set_budget(AgentRole.COMPOSITOR, TaskBudget(max_subagents=3))
    result = runtime.execute(
        role=AgentRole.COMPOSITOR,
        agent_version="1.0",
        inputs={},
        tools=["scan_pagemap"],
        subagent_requests=5,  # 5 > 3
    )
    assert result.failed is True
    assert "max_subagents" in result.call.error


def test_agent_execute_within_budget_still_succeeds():
    """The caps above must not fire on a normal, within-budget call."""
    runtime = AgentRuntime()
    result = runtime.execute(
        role=AgentRole.PREFLIGHT_EXPLAINER,
        agent_version="1.0",
        inputs={"checks": []},
        tools=["read_preflight"],
        subagent_requests=0,
    )
    assert result.failed is False


# ── Tool surface scoping (D7: capabilities passed, never ambient) ──────────────

def test_tool_surface_hides_other_roles_tools():
    """A ToolSurface cannot name a tool outside its role — there is no check to forget."""
    reg = ToolRegistry()
    reg.register(ToolSpec(name="patch-designspec", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    reg.register(ToolSpec(name="read-preflight", description="y"), lambda **k: "ok")

    compositor = reg.surface_for(AgentRole.COMPOSITOR)
    explainer = reg.surface_for(AgentRole.PREFLIGHT_EXPLAINER)

    assert "patch-designspec" in compositor.names()
    assert "patch-designspec" not in explainer.names()   # scoped out entirely
    assert "read-preflight" in explainer.names()          # unscoped tool stays shared


def test_tool_surface_blocks_out_of_surface_call():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="patch-designspec", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    with pytest.raises(PermissionError):
        reg.surface_for(AgentRole.PREFLIGHT_EXPLAINER).call("patch-designspec")


def test_omitting_role_is_an_error_not_a_bypass():
    """`registry.call("x")` used to silently skip the scope check. Now it cannot compile
    away: role is a required parameter, so omission is a TypeError at the call site."""
    reg = ToolRegistry()
    reg.register(ToolSpec(name="scoped", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    with pytest.raises(TypeError):
        reg.call("scoped")                       # type: ignore[call-arg]
    assert reg.call("scoped", AgentRole.COMPOSITOR) == "ran"
