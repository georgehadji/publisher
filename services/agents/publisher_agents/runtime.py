"""
Publisher Agent Runtime — task budgets, subagent caps, tool surfaces, context management.

From AGENT_DESIGN.md §0 and §1:
- Agent action space = override log + DesignSpec patches only. Never the AST, PDF, or preflight verdict.
- Every agent action is attributable and reversible: actor="agent:compositor@v7"
- Agents run at gate boundaries, never inside deterministic stages.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Protocol


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

    def call(self, name: str, role: Optional[AgentRole], **kwargs) -> Any:
        """
        Call a tool by name. `role` is REQUIRED — pass None only to mean "trusted
        non-agent caller", explicitly.

        It used to default to None, which made enforcement opt-in: `registry.call("x")`
        silently bypassed the scope check entirely, so the narrow tool surfaces existed
        only for callers who remembered to opt in. Making the parameter mandatory turns
        an omission into a TypeError at the call site instead of a silent escalation.

        Prefer `surface_for(role)` over calling this directly: a ToolSurface cannot
        reach outside its role at all, which is the capability-passing D7 asks for
        rather than a permission check every caller must remember.
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

    def surface_for(self, role: AgentRole) -> "ToolSurface":
        """
        The tool surface for one role — a capability object, not a permission check.

        BUILD_PLAN.md §3.17: "Seven narrow agents, each with its own tool surface";
        D7: "A module receives capabilities... never ambient access". A ToolSurface
        exposes no way to name a tool outside its role, so there is nothing to forget
        to check and no global registry to reach past it.
        """
        return ToolSurface(self, role)


class ToolSurface:
    """A role-scoped view of a ToolRegistry. Cannot reach tools outside its role."""

    def __init__(self, registry: "ToolRegistry", role: AgentRole):
        self._registry = registry
        self._role = role

    @property
    def role(self) -> AgentRole:
        return self._role

    def list_tools(self) -> list[ToolSpec]:
        return self._registry.list_tools(self._role)

    def names(self) -> list[str]:
        return sorted(s.name for s in self.list_tools())

    def call(self, name: str, **kwargs) -> Any:
        """Call a tool inside this surface. Out-of-surface names raise PermissionError."""
        return self._registry.call(name, self._role, **kwargs)


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

@dataclass
class ToolCall:
    """One tool call a model asked for."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentTurn:
    """One model reply: tool calls to run, or (with none) the final answer."""
    tool_calls: list[ToolCall] = field(default_factory=list)
    answer: Any = None
    tokens: int = 0
    cost_usd: float = 0.0


class AgentProvider(Protocol):
    """The model behind an agent. Injected, as InferenceGateway's provider is (E6.1):
    production passes OpenRouterAgentProvider; tests pass a scripted one."""

    def respond(self, role: AgentRole, messages: list[dict], tools: list[dict],
                output_schema: dict) -> AgentTurn: ...


class AgentBudgetExceeded(RuntimeError):
    """A run reached its TaskBudget before giving a final answer."""


class AgentAnswerInvalid(ValueError):
    """A final answer outside the output schema the caller asked for."""


# A tool result goes back into the conversation; an unbounded one (a whole
# chapter's nodes) would spend the token budget on one call.
# ponytail: plain truncation; summarise per tool if agents start needing the tail.
MAX_TOOL_RESULT_CHARS = 20_000


class AgentRuntime:
    """
    Runs one agent task as a tool loop (W5, docs/WIRING_PLAN.md): the model
    replies with tool calls, the runtime runs them through the role's
    ToolSurface and sends the results back, until the model gives a final
    answer -- checked against the caller's schema -- or the TaskBudget runs out.

    What an agent may do is bounded twice: `tools` (offered on this call) inside
    the role's surface (D7), and the answer schema the caller supplies. Artifacts
    a tool reads (`bound`) are injected by the runtime, never passed by the model,
    so a model cannot point a tool at a document it was not given.
    """

    def __init__(self, provider: AgentProvider, registry: Optional[ToolRegistry] = None):
        self._provider = provider
        self._registry = registry or _GLOBAL_TOOLS
        self._budgets: dict[str, TaskBudget] = {}

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
        output_schema: dict,
        *,
        system_prompt: str = "",
        bound: Optional[dict[str, Any]] = None,
        subagent_requests: int = 0,
    ) -> AgentResult:
        """Run the loop. Never raises: a failure is `AgentResult.failed` with
        the reason on `call.error` (D3), so "proposed nothing" and "crashed"
        stay distinguishable."""
        budget = self.get_budget(role)
        call = AgentCall(role=role, agent_version=agent_version, inputs=inputs)
        start = time.monotonic()
        try:
            if subagent_requests > budget.max_subagents:
                raise AgentBudgetExceeded(
                    f"{subagent_requests} subagent(s) requested exceeds "
                    f"max_subagents={budget.max_subagents} for role '{role.value}'")
            output = self._loop(role, call, budget, inputs, tools, output_schema,
                                system_prompt, bound or {})
            failed = False
        except Exception as e:
            call.error = f"{type(e).__name__}: {e}"
            output, failed = {"error": call.error, "proposals": []}, True
        call.latency_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(role=role, call=call, output=output, failed=failed)

    def _loop(self, role, call, budget, inputs, tools, output_schema, system_prompt, bound):
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import best_match

        surface = self._registry.surface_for(role)
        offered = {s.name: s for s in surface.list_tools() if s.name in tools}
        outside = sorted(set(tools) - set(offered))
        if outside:
            raise PermissionError(f"role '{role.value}' has no tool(s) {outside}")
        schemas = [_tool_schema(spec, bound) for spec in offered.values()]
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, default=str)},
        ]
        for _ in range(budget.max_turns):
            turn = self._provider.respond(role, messages, schemas, output_schema)
            call.tokens_used += turn.tokens
            call.cost_usd += turn.cost_usd
            if call.tokens_used > budget.max_tokens:
                raise AgentBudgetExceeded(f"{call.tokens_used} tokens exceeds max_tokens={budget.max_tokens}")
            if call.cost_usd > budget.max_cost_usd:
                raise AgentBudgetExceeded(f"${call.cost_usd:.4f} exceeds max_cost_usd={budget.max_cost_usd}")
            if not turn.tool_calls:
                error = best_match(Draft202012Validator(output_schema).iter_errors(turn.answer))
                if error is not None:
                    raise AgentAnswerInvalid(f"final answer breaks its schema: {error.message[:200]}")
                return turn.answer
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in turn.tool_calls]})
            for c in turn.tool_calls:
                call.tools_used.append(c.name)
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": self._dispatch(surface, offered, c, bound)})
        raise AgentBudgetExceeded(f"no final answer within max_turns={budget.max_turns} for role '{role.value}'")

    def _dispatch(self, surface: "ToolSurface", offered: dict, c: ToolCall, bound: dict) -> str:
        """Run one call and return what the model sees. A tool the call did not
        offer, or one that fails, comes back as an error the model can read --
        it is never run, and it does not end the loop."""
        if c.name not in offered:
            return json.dumps({"error": f"no tool named {c.name!r} on this call"})
        fn = self._registry.get(c.name)
        accepted = inspect.signature(fn).parameters
        # Bound artifacts win over anything the model sent under the same name.
        kwargs = {**c.arguments, **{k: v for k, v in bound.items() if k in accepted}}
        try:
            result = surface.call(c.name, **kwargs)
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + f"... [truncated at {MAX_TOOL_RESULT_CHARS} chars]"
        return text


def _tool_schema(spec: ToolSpec, bound: dict) -> dict:
    """The chat-tools declaration of one tool, without the parameters the
    runtime binds: the model is not asked for what it may not choose."""
    properties = {k: v for k, v in spec.parameters.items() if k not in bound}
    return {"type": "function", "function": {
        "name": spec.name, "description": spec.description,
        "parameters": {"type": "object", "properties": properties}}}


# ── Default tool set ────────────────────────────────────────────

class ToolUnavailable(RuntimeError):
    """A declared tool whose underlying capability this deployment does not have.

    Raised instead of returning a plausible-looking value. A tool that answers
    "no defects", "no nodes", or a `crop://page-3` reference to an image it never
    rendered is worse than a missing one: the agent reasons on the answer, the
    proposal it then does or does not make looks considered, and nothing in the
    run records that the question was never actually asked.
    """


# The node types `ast/1` lets carry a `confidence`: chapters and the front/back
# matter sections. Mirrored from the schema rather than read from it at runtime;
# test_agent_tools asserts the two stay equal.
SCORED_TYPES = frozenset({
    "chapter",
    "halfTitle", "titlePage", "copyrightPage", "dedication", "toc",
    "foreword", "preface", "acknowledgments", "prologue",
    "epilogue", "afterword", "appendix", "notes", "bibliography", "index",
    "aboutTheAuthor", "alsoBy", "colophon",
    "heading", "paragraph",
})
# Of those, the types ingest scores only where it doubts one (B6,
# docs/STRUCTURE_REPAIR_PLAN.md): an unscored heading or paragraph raised no
# doubt, so a confidence query skips it rather than returning the whole book.
DOUBTED_ONLY_TYPES = frozenset({"heading", "paragraph"})


def _walk_nodes(ast: dict):
    """Every node of an `ast/1` document, depth-first, in document order.

    `frontMatter`, `body` and `backMatter` are the three roots; any node may
    carry its own `content` list (a chapter's blocks, a list's items).
    """
    stack: list = []
    for root in ("backMatter", "body", "frontMatter"):
        stack.extend(reversed(ast.get(root) or []))
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("content")
        if isinstance(children, list):
            stack.extend(reversed(children))


def _init_default_tools():
    """Register the default set of agent tools.

    Every tool here is PURE: it takes the artifact it reads as an argument and
    returns a value. None of them reaches into an ambient build, because none of
    them can — there is no session, no CAS handle and no database in this layer,
    and tools that pretended otherwise are how this file came to hold five
    functions that answered questions they had no way to ask (D7: a module
    receives capabilities explicitly, never ambient access).
    """

    @register_tool(ToolSpec(
        name="query_nodes",
        description="Query the nodes of a given AST by type and/or confidence threshold",
        parameters={
            "ast": {"type": "object", "description": "an ast/1 document"},
            "types": {"type": "array", "items": {"type": "string"}},
            "confidence_below": {"type": "number"},
        },
    ))
    def query_nodes(ast: dict, types: list[str] | None = None,
                    confidence_below: float | None = None) -> list[dict]:
        """Nodes of `ast` matching every filter given, in document order.

        Read-only by construction: nodes are returned as they are, and an agent's
        only way to act on one is `propose_override`. Nothing here can write an
        AST, which is the structural form of AGENT_DESIGN.md §0's "never the AST".
        """
        if not isinstance(ast, dict):
            raise TypeError(
                f"query_nodes needs an ast/1 document, got {type(ast).__name__}"
            )
        wanted = set(types) if types else None
        found = []
        for node in _walk_nodes(ast):
            if wanted is not None and node.get("type") not in wanted:
                continue
            if confidence_below is not None:
                # Only types whose structure a heuristic DECIDED can carry a score
                # (ast.schema.json's `confidence`). A text run has no decision to
                # doubt, and an unscored block raised none; counting either as
                # unscored would return the whole book.
                if node.get("type") not in SCORED_TYPES:
                    continue
                confidence = node.get("confidence")
                if confidence is None and node.get("type") in DOUBTED_ONLY_TYPES:
                    continue
                # A scorable node carrying no score is UNSCORED, not perfectly
                # confident. Reading a missing score as 1.0 would hide exactly the
                # nodes a confidence query exists to surface.
                if confidence is not None and confidence >= confidence_below:
                    continue
            found.append(node)
        return found
    
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
        description="Crop a region of a page for visual inspection "
                    "(UNAVAILABLE: needs a page-raster pipeline)",
        parameters={"page": {"type": "integer"}, "bbox": {"type": "object"}},
    ))
    def crop(page: int, bbox: dict | None = None) -> str:
        """Unavailable: cropping needs a rasterised page, and nothing rasterises one.

        This used to return `crop://page-3` — a well-formed reference to an image
        that was never rendered and that no resolver anywhere knows how to fetch.
        The Compositor's entire loop is crop → look → propose, so a fabricated
        crop does not degrade that loop, it invents the evidence it runs on.
        """
        raise ToolUnavailable(
            f"crop(page={page}) is unavailable: this deployment has no page-raster "
            f"pipeline. The render path produces a PDF, not per-page images."
        )
    
    @register_tool(ToolSpec(
        name="scan_pagemap",
        description="Scan a pagemap for composition defects (widows, orphans, runts)",
        parameters={
            "pagemap": {"type": "object", "description": "a pagemap/1 document"},
            "filter": {"type": "string", "description": "defect-type substring"},
        },
    ))
    def scan_pagemap(pagemap: dict, filter: str = "") -> dict:
        """Composition defects in `pagemap`, plus how much of it was measured.

        Delegates "what counts as a defect, and how bad is it" to
        `publisher_prepress.preflight.scan_composition` — the same function the
        delivery gate itself runs — rather than carrying a second opinion that
        could drift from the gate the agent is trying to help a book pass.

        `measuredPages` rides along deliberately. Zero defects across zero
        measured pages is not a clean book, and an agent handed only the defect
        list has no way to tell those two apart.
        """
        from publisher_prepress.preflight import scan_composition

        found = scan_composition(pagemap)
        # Severities follow platform/pagescan/src/lib.rs, as the preflight gate does.
        kinds = (
            ("orphans", "orphan", "error",
             "first line of a paragraph stranded at the foot of the page"),
            ("widows", "widow", "warning",
             "last line of a paragraph stranded at the top of the page"),
            ("runts", "runt", "warning",
             "a multi-line paragraph ending in a single word"),
        )
        defects = [
            {
                "defect_type": defect_type,
                "page_number": page,
                "severity": severity,
                "description": f"Page {page}: {description}",
            }
            for bucket, defect_type, severity, description in kinds
            if not filter or filter in defect_type
            for page in found[bucket]
        ]
        defects.sort(key=lambda d: (d["page_number"], d["defect_type"]))
        return {
            "defects": defects,
            "pages": found["pages"],
            "measuredPages": found["measured"],
        }
    
    @register_tool(ToolSpec(
        name="render_range",
        description="Re-render a page range with adjustments applied "
                    "(UNAVAILABLE: re-rendering is a stage, not a tool)",
        parameters={"start_page": {"type": "integer"}, "end_page": {"type": "integer"}},
    ))
    def render_range(start_page: int, end_page: int) -> str:
        """Unavailable: a re-render is a build, and agents do not run builds.

        Rendering happens in `paginate`, from a DesignSpec, inside the DAG —
        which is what makes a rendered page reproducible from its cache key. An
        agent reaches a new render by proposing a DesignSpec patch and letting
        the build re-run, never by rendering privately (AGENT_DESIGN.md §0:
        agents run at gate boundaries, never inside deterministic stages).
        """
        raise ToolUnavailable(
            f"render_range({start_page}, {end_page}) is unavailable: re-rendering runs "
            f"as the `paginate` stage inside the DAG. Propose a DesignSpec patch instead."
        )
    
    @register_tool(ToolSpec(
        name="read_preflight",
        description="Read a preflight report, optionally filtered by check status",
        parameters={
            "report": {"type": "object", "description": "a preflight/1 document"},
            "status": {"type": "string", "enum": ["pass", "fail", "warn", "skip"]},
        },
    ))
    def read_preflight(report: dict, status: str | None = None) -> dict:
        """The checks of `report`, optionally only those with one status.

        Takes the report rather than a `build_id`: this layer holds no database
        handle and no CAS root, so a `build_id` was a parameter it could not
        honour — it accepted the id and returned `{"checks": []}`, which reads as
        a book that passed everything.
        """
        if not isinstance(report, dict):
            raise TypeError(
                f"read_preflight needs a preflight/1 document, got {type(report).__name__}"
            )
        checks = [c for c in (report.get("checks") or []) if isinstance(c, dict)]
        if status is not None:
            checks = [c for c in checks if c.get("status") == status]
        return {
            "status": report.get("status"),
            "profileId": report.get("profileId"),
            "checks": checks,
            "summary": report.get("summary") or {},
        }


_init_default_tools()
