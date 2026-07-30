"""
Publisher Agent Runtime — task budgets, subagent caps, tool surfaces, context management.

From AGENT_DESIGN.md §0 and §1:
- Agent action space = override log + DesignSpec patches only. Never the AST, PDF, or preflight verdict.
- Every agent action is attributable and reversible: actor="agent:compositor@v7"
- Agents run at gate boundaries, never inside deterministic stages.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional


# ── Core types ──────────────────────────────────────────────────

class AgentRole(str, Enum):
    """Agent roles from AGENT_DESIGN.md §1.3."""
    STRUCTURE_WRANGLER = "structure-wrangler"
    COMPOSITOR = "compositor"
    PREFLIGHT_EXPLAINER = "preflight-explainer"
    MANUSCRIPT_DOCTOR = "manuscript-doctor"
    VENDOR_WATCHER = "vendor-watcher"
    SUPPORT_TRIAGE = "support-triage"


@dataclass
class ToolSpec:
    """A tool available to an agent."""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_human_gate: bool = False  # Proposals that change the build


@dataclass
class TaskBudget:
    """Agent task budget (AGENT_DESIGN.md §1.5)."""
    max_tokens: int = 20000      # minimum 20k for a repair session
    max_turns: int = 50           # cap on tool-call rounds
    max_subagents: int = 3       # subagent cap
    max_cost_usd: float = 0.05   # per-session cost ceiling
    effort: str = "medium"       # "low" for scanning, "high" for planning


@dataclass
class AgentCall:
    """A single agent invocation."""
    role: AgentRole
    agent_version: str
    inputs: dict[str, Any]
    tools_used: list[str] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    tokens_used: int = 0
    error: Optional[str] = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class AgentResult:
    """Result from an agent invocation."""
    role: AgentRole
    call: AgentCall
    output: dict[str, Any]
    accepted: bool = False
    accepted_by: Optional[str] = None  # "user" or "auto"
    rejected_reason: Optional[str] = None
    # Explicit failure flag. `execute()` catches exceptions and returns a result either
    # way; without this the caller cannot distinguish "the agent ran and proposed
    # nothing" from "the agent crashed", because both surface as an empty
    # `output.get("proposals", [])`. D3 requires errors to be carried, not swallowed.
    failed: bool = False

    @property
    def error(self) -> Optional[str]:
        """The failure message, if this invocation failed."""
        return self.call.error


# ── Tool registry ───────────────────────────────────────────────

class ToolRegistry:
    """
    Registry of tools available to agents.

    BUILD_PLAN.md §3.17 specifies "Seven narrow agents, **each with its own tool
    surface**", and D7 requires that a module receive capabilities explicitly rather
    than having ambient access. `list_tools(role=...)` previously accepted a role and
    then returned `list(self._specs.values())` unconditionally, and `call()` performed
    no role check at all — so every agent could reach every tool and the narrow surfaces
    existed only in the documentation.
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._roles: dict[str, frozenset[AgentRole]] = {}

    def register(self, spec: ToolSpec, fn: Callable,
                 roles: Optional[Iterable[AgentRole]] = None):
        """
        Register a tool, optionally scoping it to specific agent roles.

        `roles=None` means unscoped — available to any role. Scoping is opt-in so
        existing registrations keep working, but a tool that changes the build should
        always name its roles.
        """
        self._tools[spec.name] = fn
        self._specs[spec.name] = spec
        self._roles[spec.name] = frozenset(roles) if roles else frozenset()

    def get(self, name: str) -> Optional[Callable]:
        return self._tools.get(name)

    def spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def allowed_for(self, name: str, role: AgentRole) -> bool:
        """True if `role` may call `name`. An unscoped tool is allowed for every role."""
        scoped = self._roles.get(name) or frozenset()
        return not scoped or role in scoped

    def list_tools(self, role: Optional[AgentRole] = None) -> list[ToolSpec]:
        """List available tools. With `role`, returns only that role's tool surface."""
        if role is None:
            return list(self._specs.values())
        return [s for n, s in self._specs.items() if self.allowed_for(n, role)]

    def call(self, name: str, role: Optional[AgentRole] = None, **kwargs) -> Any:
        """
        Call a tool by name.

        When `role` is supplied it is ENFORCED, not merely recorded — a role outside the
        tool's surface raises rather than silently succeeding.
        """
        fn = self._tools.get(name)
        if fn is None:
            raise ValueError(f"Unknown tool: {name}")
        if role is not None and not self.allowed_for(name, role):
            raise PermissionError(
                f"agent role '{role.value}' may not call tool '{name}'; "
                f"its surface is {sorted(r.value for r in self._roles[name])}"
            )
        return fn(**kwargs)


# Global tool registry
_GLOBAL_TOOLS = ToolRegistry()


def register_tool(spec: ToolSpec):
    """Decorator to register a tool."""
    def decorator(fn):
        _GLOBAL_TOOLS.register(spec, fn)
        return fn
    return decorator


def get_tool_registry() -> ToolRegistry:
    return _GLOBAL_TOOLS


# ── Agent Runtime ───────────────────────────────────────────────

class AgentRuntime:
    """
    Runtime for executing agent tasks.
    
    Manages:
    - Task budgets (tokens, turns, subagents, cost)
    - Tool surfaces (which tools are available per role)
    - Context editing / compaction (clearing stale results)
    - Subagent caps
    """
    
    def __init__(self, registry: Optional[ToolRegistry] = None):
        self._registry = registry or _GLOBAL_TOOLS
        self._budgets: dict[str, TaskBudget] = {}
        self._active_calls: dict[str, AgentCall] = {}
    
    def set_budget(self, role: AgentRole, budget: TaskBudget):
        self._budgets[role.value] = budget
    
    def get_budget(self, role: AgentRole) -> TaskBudget:
        return self._budgets.get(role.value, TaskBudget())
    
    def execute(
        self,
        role: AgentRole,
        agent_version: str,
        inputs: dict[str, Any],
        tools: list[str],
        subagent_requests: int = 0,
    ) -> AgentResult:
        """
        Execute an agent task.

        In production, this calls the LLM with tools.
        In the tracer bullet, runs the rule-based fallback.

        Enforces the two hard caps BUILD_PLAN.md §3.17 requires and that AGENT_DESIGN.md
        §1.5 names but which nothing previously checked: a task budget that "paces and
        wraps up gracefully" and an "explicit subagent cap (current Opus delegates
        readily; an uncapped Compositor spawns one per spread)". `TaskBudget` existed as
        a dataclass with `max_turns`/`max_subagents` fields that were recorded on
        `AgentCall` and never compared against anything -- an agent could exceed either
        with no error, no flag, nothing.
        """
        budget = self.get_budget(role)
        call = AgentCall(
            role=role,
            agent_version=agent_version,
            inputs=inputs,
            tools_used=tools,
        )

        start = time.monotonic()
        failed = False

        if len(tools) > budget.max_turns:
            call.error = (
                f"AgentBudgetExceeded: {len(tools)} tool calls requested exceeds "
                f"max_turns={budget.max_turns} for role '{role.value}'"
            )
            call.latency_ms = int((time.monotonic() - start) * 1000)
            self._active_calls[call.started_at] = call
            return AgentResult(role=role, call=call, output={"error": call.error, "proposals": []}, failed=True)

        if subagent_requests > budget.max_subagents:
            call.error = (
                f"AgentBudgetExceeded: {subagent_requests} subagent(s) requested exceeds "
                f"max_subagents={budget.max_subagents} for role '{role.value}'"
            )
            call.latency_ms = int((time.monotonic() - start) * 1000)
            self._active_calls[call.started_at] = call
            return AgentResult(role=role, call=call, output={"error": call.error, "proposals": []}, failed=True)

        try:
            output = self._run_agent_logic(role, inputs, tools)
            call.latency_ms = int((time.monotonic() - start) * 1000)
            call.tokens_used = len(json.dumps(output))
        except Exception as e:
            # Record the failure and FLAG it. Returning an error dict alone made a crash
            # indistinguishable from an empty-but-successful run at every call site that
            # reads `result.output.get("proposals", [])` — see compositor.evaluate.
            call.error = f"{type(e).__name__}: {e}"
            call.latency_ms = int((time.monotonic() - start) * 1000)
            output = {"error": call.error, "proposals": []}
            failed = True

        self._active_calls[call.started_at] = call

        return AgentResult(
            role=role,
            call=call,
            output=output,
            failed=failed,
        )
    
    def _run_agent_logic(
        self,
        role: AgentRole,
        inputs: dict[str, Any],
        tools: list[str],
    ) -> dict[str, Any]:
        """Run the agent's logic.
        
        In the tracer bullet, this simulates agent output.
        In production, this calls the LLM with tools and structured output.
        """
        if role == AgentRole.STRUCTURE_WRANGLER:
            return self._run_structure_wrangler(inputs)
        elif role == AgentRole.COMPOSITOR:
            return self._run_compositor(inputs)
        elif role == AgentRole.PREFLIGHT_EXPLAINER:
            return self._run_preflight_explainer(inputs)
        else:
            return {"status": "simulated", "role": role.value}
    
    def _run_structure_wrangler(self, inputs: dict) -> dict:
        """Structure Wrangler logic."""
        low_conf_nodes = inputs.get("low_confidence_nodes", [])
        proposals = []
        
        for node in low_conf_nodes:
            alternatives = node.get("alternatives", [])
            if alternatives:
                proposals.append({
                    "id": f"pr-{node.get('block_index', '?')}",
                    "type": "reclassify",
                    "sourceRef": {"docxId": f"node-{node.get('block_index', '?')}"},
                    "from": node.get("suggested", "uncertain"),
                    "to": alternatives[0],
                    "rationale": f"Rules engine confidence {node.get('confidence', 0):.2f} is below threshold; best alternative is '{alternatives[0]}' based on context.",
                    "confidence": 0.85,
                    "evidence": [
                        f"detected: {node.get('suggested', '?')}",
                        f"alternatives: {', '.join(alternatives)}",
                    ],
                })
        
        return {
            "schema": "agent-proposal/1",
            "agentId": "structure-wrangler",
            "agentVersion": inputs.get("agent_version", "1.0"),
            "proposals": proposals,
            "costUsd": len(proposals) * 0.003,
        }
    
    def _run_compositor(self, inputs: dict) -> dict:
        """Compositor agent logic."""
        defects = inputs.get("defects", [])
        proposals = []
        
        for defect in defects:
            if defect.get("severity") in ("error", "warning"):
                proposals.append({
                    "id": f"adj-{defect.get('page_number', '?')}",
                    "type": "adjust_layout",
                    "sourceRef": {"docxId": f"page-{defect.get('page_number', '?')}"},
                    "rationale": f"Detected {defect.get('defect_type', 'issue')} on page {defect.get('page_number', '?')}: {defect.get('description', '')}",
                    "confidence": 0.7,
                    "adjustment": {
                        "type": "tracking",
                        "target_page": defect.get("page_number"),
                        "value": -0.005,
                        "unit": "em",
                    },
                })
        
        return {
            "schema": "agent-proposal/1",
            "agentId": "compositor",
            "agentVersion": inputs.get("agent_version", "1.0"),
            "proposals": proposals,
            "costUsd": len(proposals) * 0.01,
        }
    
    def _run_preflight_explainer(self, inputs: dict) -> dict:
        """Preflight Explainer — read-only, no proposals."""
        checks = inputs.get("checks", [])
        explanations = []
        
        for check in checks:
            if check.get("status") == "fail":
                explanations.append({
                    "code": check.get("code", "?"),
                    "explanation": check.get("humanMessage", ""),
                    "fix": check.get("suggestedFix", ""),
                    "severity": "blocking",
                })
            elif check.get("status") == "warn":
                explanations.append({
                    "code": check.get("code", "?"),
                    "explanation": check.get("humanMessage", ""),
                    "fix": check.get("suggestedFix", ""),
                    "severity": "advisory",
                })
        
        return {
            "schema": "preflight-explanation/1",
            "agentId": "preflight-explainer",
            "totalFindings": len(explanations),
            "blockingCount": sum(1 for e in explanations if e["severity"] == "blocking"),
            "explanations": explanations,
        }


# ── Default tool set ────────────────────────────────────────────

def _init_default_tools():
    """Register the default set of agent tools."""
    
    @register_tool(ToolSpec(
        name="query_nodes",
        description="Query AST nodes by type, role, or confidence threshold",
        parameters={"types": {"type": "array", "items": {"type": "string"}}},
    ))
    def query_nodes(types: list[str] = None, confidence_below: float = None) -> list[dict]:
        """Query nodes from the AST."""
        # Stub — in production queries the AST
        return []
    
    @register_tool(ToolSpec(
        name="propose_override",
        description="Propose an override operation for human approval",
        requires_human_gate=True,
    ))
    def propose_override(source_ref: str, op: str, from_val: str = "", to_val: str = "", rationale: str = "") -> dict:
        """Propose an override op."""
        return {
            # Stable across processes. Python's builtin hash() is salted by
            # PYTHONHASHSEED, so the same proposal for the same sourceRef produced a
            # DIFFERENT id on every interpreter run. That id flows into the append-only
            # override log, which is exactly where a stable identity is required for
            # audit and undo (D5: no nondeterminism inside stage logic).
            "id": f"ov-auto-{hashlib.sha256(source_ref.encode('utf-8')).hexdigest()[:16]}",
            "sourceRef": source_ref,
            "op": op,
            "from": from_val,
            "to": to_val,
            "rationale": rationale,
            "actor": "agent",
        }
    
    @register_tool(ToolSpec(
        name="crop",
        description="Crop a region of a page for visual inspection",
        parameters={"page": {"type": "integer"}, "bbox": {"type": "object"}},
    ))
    def crop(page: int, bbox: dict = None) -> str:
        """Crop a page region. Returns a data URL or ref."""
        # Stub — in production renders via CAS raster
        return f"crop://page-{page}"
    
    @register_tool(ToolSpec(
        name="scan_pagemap",
        description="Scan the pagemap for defects (code exec wrapper)",
        parameters={"filter": {"type": "string"}},
    ))
    def scan_pagemap(filter: str = "") -> dict:
        """Run pagescan over the current pagemap."""
        # Stub — in production calls the Rust pagescan via PyO3
        return {"defects": []}
    
    @register_tool(ToolSpec(
        name="render_range",
        description="Re-render a page range with adjustments applied",
        parameters={"start_page": {"type": "integer"}, "end_page": {"type": "integer"}},
    ))
    def render_range(start_page: int, end_page: int) -> str:
        """Re-render a range of pages."""
        # Stub
        return f"render://pages-{start_page}-to-{end_page}"
    
    @register_tool(ToolSpec(
        name="read_preflight",
        description="Read the preflight report for a build",
        parameters={"build_id": {"type": "string"}},
    ))
    def read_preflight(build_id: str) -> dict:
        """Read the preflight report."""
        # Stub
        return {"checks": []}


_init_default_tools()
