"""Tests for the agent runtime's tool loop (W5) and the agents built on it."""

import sys
from pathlib import Path

import pytest

from publisher_agents.runtime import (
    AgentRuntime, AgentRole, AgentResult, AgentTurn, TaskBudget, ToolCall,
    ToolRegistry, ToolSpec, get_tool_registry,
)

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from scripted_provider import ScriptedProvider, answer, call  # noqa: E402

AST = {"schema": "ast/1", "body": [
    {"type": "chapter", "attrs": {"number": 1, "id": "ch1", "title": "ONE"}, "confidence": 0.95,
     "sourceRef": {"docxId": "c1"}, "content": []},
    {"type": "chapter", "attrs": {"number": 2, "id": "ch2", "title": "NO!"}, "confidence": 0.7,
     "sourceRef": {"docxId": "c2"}, "content": []},
]}
ANY_ANSWER = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _run(provider, tools=("query_nodes",), schema=ANY_ANSWER, role=AgentRole.STRUCTURE_WRANGLER,
         runtime=None, **kw) -> AgentResult:
    runtime = runtime or AgentRuntime(provider)
    return runtime.execute(role, "1.0", {"task": "t"}, list(tools), schema, bound={"ast": AST}, **kw)


def test_budget_defaults():
    budget = AgentRuntime(ScriptedProvider()).get_budget(AgentRole.COMPOSITOR)
    assert budget.max_tokens >= 20000     # "minimum 20k for a repair session"
    assert TaskBudget().max_subagents == 3 and TaskBudget().effort == "medium"


# ── The loop ─────────────────────────────────────────────────────────────

def test_a_tool_call_runs_and_its_result_goes_back_to_the_model():
    provider = ScriptedProvider(call("query_nodes", confidence_below=0.8), answer({"ok": True}))
    result = _run(provider)
    assert not result.failed, result.error
    assert result.output == {"ok": True} and result.call.tools_used == ["query_nodes"]
    tool_reply = provider.seen[1]["messages"][-1]
    assert tool_reply["role"] == "tool" and tool_reply["tool_call_id"] == "t0"
    assert '"c2"' in tool_reply["content"] and '"c1"' not in tool_reply["content"]   # really filtered


def test_bound_artifacts_are_not_the_models_to_choose():
    provider = ScriptedProvider(call("query_nodes", ast={"body": []}), answer({"ok": True}))
    _run(provider)
    declared = provider.seen[0]["tools"][0]["function"]["parameters"]["properties"]
    assert "ast" not in declared                                   # never offered
    assert '"c1"' in provider.seen[1]["messages"][-1]["content"]   # the bound AST was read, not the model's


def test_a_tool_not_offered_is_refused_to_the_model_not_run():
    ran = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="secret", description="x"), lambda **k: ran.append(1))
    registry.register(ToolSpec(name="ok", description="y"), lambda **k: "fine")
    provider = ScriptedProvider(call("secret"), answer({"ok": True}))
    result = _run(provider, tools=["ok"], runtime=AgentRuntime(provider, registry))
    assert not result.failed and ran == []
    assert "no tool named 'secret'" in provider.seen[1]["messages"][-1]["content"]


def test_a_failing_tool_is_reported_and_the_loop_goes_on():
    provider = ScriptedProvider(call("crop", page=3), answer({"ok": True}))
    result = _run(provider, tools=["crop"], role=AgentRole.COMPOSITOR)
    assert not result.failed
    assert "ToolUnavailable" in provider.seen[1]["messages"][-1]["content"]


def test_offering_a_tool_outside_the_roles_surface_fails_before_any_turn():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="patch", description="x"), lambda **k: 1, roles=[AgentRole.COMPOSITOR])
    provider = ScriptedProvider()
    result = _run(provider, tools=["patch"], runtime=AgentRuntime(provider, registry))
    assert result.failed and "PermissionError" in result.error and provider.seen == []


# ── Budgets and the answer ───────────────────────────────────────────────

def test_the_turn_budget_stops_a_model_that_never_answers():
    provider = ScriptedProvider(*(call("query_nodes", i) for i in range(5)))
    runtime = AgentRuntime(provider)
    runtime.set_budget(AgentRole.STRUCTURE_WRANGLER, TaskBudget(max_turns=3))
    result = _run(provider, runtime=runtime)
    assert result.failed and "max_turns=3" in result.error and len(provider.seen) == 3


@pytest.mark.parametrize("budget, turn, field", [
    (TaskBudget(max_tokens=100), AgentTurn(answer={"ok": True}, tokens=101), "max_tokens"),
    (TaskBudget(max_cost_usd=0.01), AgentTurn(answer={"ok": True}, cost_usd=0.02), "max_cost_usd"),
])
def test_token_and_cost_budgets_stop_the_run(budget, turn, field):
    provider = ScriptedProvider(turn)
    runtime = AgentRuntime(provider)
    runtime.set_budget(AgentRole.STRUCTURE_WRANGLER, budget)
    result = _run(provider, runtime=runtime)
    assert result.failed and field in result.error


def test_the_subagent_cap_holds():
    runtime = AgentRuntime(ScriptedProvider())
    runtime.set_budget(AgentRole.COMPOSITOR, TaskBudget(max_subagents=3))
    result = _run(None, runtime=runtime, role=AgentRole.COMPOSITOR, tools=[], subagent_requests=5)
    assert result.failed and "max_subagents" in result.error


def test_an_answer_outside_its_schema_fails():
    result = _run(ScriptedProvider(answer({"ok": "yes, probably"})))
    assert result.failed and "AgentAnswerInvalid" in result.error


# ── The agents ───────────────────────────────────────────────────────────

PROPOSALS = [
    {"id": "pr-a", "type": "merge_chapters", "sourceRef": {"docxId": "c2"}, "rationale": "prose"},
    {"id": "pr-b", "type": "flag_ambiguity", "sourceRef": {"docxId": "c1"}, "rationale": "odd"},
]


def test_the_wrangler_drops_and_reorders_but_cannot_add():
    from publisher_agents.structure_wrangler import StructureWrangler
    provider = ScriptedProvider(call("query_nodes", types=["chapter"]), answer({"keep": ["pr-b"]}))
    kept, result = StructureWrangler(AgentRuntime(provider)).review(PROPOSALS, AST)
    assert [p["id"] for p in kept] == ["pr-b"] and not result.failed
    invented = ScriptedProvider(answer({"keep": ["pr-a", "pr-new"]}))
    kept, result = StructureWrangler(AgentRuntime(invented)).review(PROPOSALS, AST)
    assert result.failed and kept == PROPOSALS        # a failed review keeps every proposal


def test_the_wrangler_asks_nothing_about_no_proposals():
    from publisher_agents.structure_wrangler import StructureWrangler
    provider = ScriptedProvider()
    assert StructureWrangler(AgentRuntime(provider)).review([], AST) == ([], None)
    assert provider.seen == []


def test_the_explainer_reads_its_report_through_the_tool():
    from publisher_agents.preflight_explainer import PreflightExplainer
    report = {"status": "fail", "checks": [{"code": "bleed", "status": "fail"}]}
    explained = {"explanations": [{"code": "bleed", "explanation": "x", "fix": "y", "severity": "blocking"}]}
    provider = ScriptedProvider(call("read_preflight", status="fail"), answer(explained))
    result = PreflightExplainer(AgentRuntime(provider)).explain(report)
    assert not result.failed and result.output == explained
    assert '"bleed"' in provider.seen[1]["messages"][-1]["content"]


def test_the_compositor_answers_in_agent_proposal_1():
    from publisher_agents.compositor import Compositor
    bad = ScriptedProvider(answer({"proposals": []}))      # no schema/agentId: not agent-proposal/1
    assert Compositor(AgentRuntime(bad)).scan_and_fix([]).failed
    good = ScriptedProvider(answer({"schema": "agent-proposal/1", "agentId": "compositor", "proposals": []}))
    assert not Compositor(AgentRuntime(good)).scan_and_fix([]).failed


def test_evaluate_compositor_with_no_sets():
    from publisher_agents.compositor import evaluate_compositor, Compositor
    assert "fix_success_rate" in evaluate_compositor(Compositor(AgentRuntime(ScriptedProvider())), [])


def test_the_openrouter_provider_sends_tools_schema_and_pins():
    from publisher_agents.openrouter import OpenRouterAgentProvider
    from publisher_structure.inference import load_routes_from_policy
    route = load_routes_from_policy(service="agents")["structure-wrangle"]
    sent = []

    class Http:
        def chat(self, body, route):
            sent.append(body)
            return ({"content": None, "tool_calls": [
                {"id": "x", "function": {"name": "query_nodes", "arguments": '{"types": ["chapter"]}'}}]},
                {"total_tokens": 50, "cost": 0.001})

    turn = OpenRouterAgentProvider("k", route, http=Http()).respond(
        AgentRole.STRUCTURE_WRANGLER, [{"role": "user", "content": "{}"}], [{"type": "function"}], ANY_ANSWER)
    assert turn.tool_calls == [ToolCall(id="x", name="query_nodes", arguments={"types": ["chapter"]})]
    body = sent[0]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"]["allow_fallbacks"] is False and body["tools"] == [{"type": "function"}]


# ── Tool registry and surfaces (D7) ──────────────────────────────────────

def test_tool_registry():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="test-tool", description="A test tool"), lambda: "done")
    assert registry.spec("test-tool").name == "test-tool"
    assert registry.call("test-tool", None) == "done"   # None == trusted non-agent caller, explicit


def test_zero_ast_write_tool_exists():
    """The agent action space is override ops and DesignSpec patches only --
    never the AST (AGENT_DESIGN.md §0, BUILD_PLAN.md D8)."""
    registry = get_tool_registry()
    for spec in registry.list_tools():
        name = spec.name.lower()
        assert "write_ast" not in name and "set_ast" not in name and "mutate_ast" not in name, spec.name
    propose = registry.spec("propose_override")
    assert propose is not None and propose.requires_human_gate is True


def test_tool_surface_hides_other_roles_tools():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="patch-designspec", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    reg.register(ToolSpec(name="read-preflight", description="y"), lambda **k: "ok")
    assert "patch-designspec" in reg.surface_for(AgentRole.COMPOSITOR).names()
    assert "patch-designspec" not in reg.surface_for(AgentRole.PREFLIGHT_EXPLAINER).names()
    assert "read-preflight" in reg.surface_for(AgentRole.PREFLIGHT_EXPLAINER).names()


def test_tool_surface_blocks_out_of_surface_call():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="patch-designspec", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    with pytest.raises(PermissionError):
        reg.surface_for(AgentRole.PREFLIGHT_EXPLAINER).call("patch-designspec")


def test_omitting_role_is_an_error_not_a_bypass():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="scoped", description="x"),
                 lambda **k: "ran", roles=[AgentRole.COMPOSITOR])
    with pytest.raises(TypeError):
        reg.call("scoped")                       # type: ignore[call-arg]
    assert reg.call("scoped", AgentRole.COMPOSITOR) == "ran"
