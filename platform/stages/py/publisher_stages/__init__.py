"""
publisher_stages -- Stage Registry

Every stage in the build graph registers itself via the @stage decorator.
From this declaration five things are **derived** (ARCHITECTURE.md §2.8.1):
  - The DAG (match inputs/outputs between stages)
  - Contract tests (run over fixture sets, assert schema validation)
  - Cache key inputs (name + version + input hashes + params + toolchain)
  - Local dev harness (pub run-stage ...)
  - Admission control (memory budget, queue)
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional


class ErrorKind(str, Enum):
    """Error taxonomy from ARCHITECTURE.md §2.8."""
    BAD_INPUT = "bad_input"
    POLICY_VIOLATION = "policy_violation"
    ENGINE_BUG = "engine_bug"
    INFRA = "infra"
    EXTERNAL_LIMIT = "external_limit"


@dataclass(frozen=True)
class Diagnostic:
    """A structured diagnostic message."""
    code: str
    severity: str  # "error" | "warning" | "info"
    human_message: str
    suggested_fix: Optional[str] = None
    source_ref: Optional[str] = None


@dataclass(frozen=True)
class StageError(Exception):
    """Error returned by a stage."""
    kind: ErrorKind
    message: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    retry_count: int = 0
    retryable: bool = False

    def __post_init__(self):
        if self.retryable and self.kind not in (ErrorKind.INFRA, ErrorKind.EXTERNAL_LIMIT):
            raise ValueError(f"Only infra and external_limit errors are retryable, got {self.kind}")


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to an artifact in the content-addressed store."""
    kind: str
    hash: str  # sha256 hex
    media_type: str
    size: int


@dataclass(frozen=True)
class StageResult:
    """Result produced by a stage execution."""
    artifacts: list[ArtifactRef]
    metrics: dict[str, float] = field(default_factory=dict)
    warnings: list[Diagnostic] = field(default_factory=list)
    toolchain: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class StageCtx:
    """Context provided to a stage when it runs."""
    build_id: str
    cache_key: str
    deadline: datetime
    memory_budget_mb: int
    work_dir: str
    # A stub render/finish path may run instead of the real engine ONLY when this is
    # True. Default False so a real build (any executor other than the local tracer
    # bullet) fails loudly on a missing engine rather than silently certifying stub
    # output as "passed" (BUILD_PLAN.md D8: no silent quality downgrades). Only
    # tracer_bullet.py's own DagExecutor sets this — the API-triggered production
    # executor never does.
    allow_stub_engines: bool = False


@dataclass(frozen=True)
class StageDeclaration:
    """
    Immutable declaration of a stage -- the registry's core data type.
    ...
    """
    name: str
    version: int
    fn: Callable = field(repr=False, compare=False)
    inputs: dict[str, str] = field(default_factory=dict)  # param_name -> schema_id
    outputs: dict[str, str] = field(default_factory=dict)  # artifact_kind -> schema_id
    toolchain: list[str] = field(default_factory=list)  # names of required toolchain components
    fixtures: Optional[str] = None  # fixture set path or version
    root_inputs: Optional[list[str]] = None  # param names that are root (no producer)
    # Root inputs whose absence is a genuinely valid state, not a missing value --
    # e.g. `resolve`'s `overrides_path=None` legitimately means "zero overrides", vs
    # `cover`'s `page_count=0`, whose default is a placeholder the function rejects
    # outright (`if not page_count: raise BAD_INPUT`). A local executor building a
    # reachable-stage subset (tracer_bullet.py DagExecutor._reachable_stages) needs
    # this distinction explicitly declared -- inferring it from whether the Python
    # parameter merely HAS a default conflates the two cases.
    optional_root_inputs: Optional[list[str]] = None
    terminal: bool = False  # True if outputs are not expected to be consumed
    # Logical pipeline step this stage implements. Two stages may declare the same
    # outputs ONLY when they are alternative implementations of one step (e.g. the
    # Ghostscript vs pass-through finish path, or the two render engines of O1) and
    # both name that step here. Without it, two producers of one schema make the
    # derived DAG (§2.8.1) pick whichever ran first — a silent, nondeterministic edge.
    implements: Optional[str] = None
    placement: str = "on-demand"  # "arm-spot" | "on-demand" | "external" (F4.3)
    memory_budget_mb: int = 256
    queue: str = "q.default"
    description: str = ""

    def cache_key_inputs(self) -> list[str]:
        """Return the set of input schema IDs for cache key computation."""
        return sorted(set(self.inputs.values()))

    @property
    def schema_version(self) -> str:
        """Fully qualified schema version string."""
        return f"{self.name}/v{self.version}"


class StageRegistry:
    """
    The global stage registry.

    Stages register themselves via the @stage decorator.
    The registry derives the DAG, contract tests, cache keys, and dev harnesses.
    """

    def __init__(self):
        self._stages: dict[str, StageDeclaration] = {}

    def register(self, decl: StageDeclaration) -> None:
        if decl.name in self._stages:
            raise ValueError(f"Stage '{decl.name}' is already registered")
        self._stages[decl.name] = decl

    def get(self, name: str) -> Optional[StageDeclaration]:
        return self._stages.get(name)

    def all(self) -> list[StageDeclaration]:
        return list(self._stages.values())

    def names(self) -> list[str]:
        return sorted(self._stages.keys())

    def derive_dag(self) -> dict[str, list[str]]:
        """
        Derive the DAG by matching each stage's declared inputs
        to other stages' declared outputs.

        Returns adjacency list: stage_name -> [dependency_stage_names].
        """
        # Build reverse map: schema_id -> [stage_names_that_produce_it]
        producers: dict[str, list[str]] = {}
        for name, decl in self._stages.items():
            for schema_id in decl.outputs.values():
                producers.setdefault(schema_id, []).append(name)

        dag: dict[str, list[str]] = {}
        for name, decl in self._stages.items():
            deps: set[str] = set()
            for schema_id in decl.inputs.values():
                if schema_id in producers:
                    deps.update(producers[schema_id])
            dag[name] = sorted(deps)

        return dag

    def derive_contract_tests(self) -> list[dict[str, Any]]:
        """
        Derive contract tests from the registry.
        Each test asserts that a stage's fixture outputs validate against
        its declared output schemas.
        """
        tests = []
        for name, decl in self._stages.items():
            if decl.fixtures:
                tests.append({
                    "stage": name,
                    "fixtures": decl.fixtures,
                    "outputs_to_validate": list(decl.outputs.keys()),
                })
        return tests

    def topological_sort(self) -> list[str]:
        """Return stages in topological (execution) order."""
        dag = self.derive_dag()
        visited: set[str] = set()
        result: list[str] = []

        def visit(node: str):
            if node in visited:
                return
            visited.add(node)
            for dep in dag.get(node, []):
                visit(dep)
            if node in self._stages:
                result.append(node)

        for name in self.names():
            visit(name)

        return result

    def check_integrity(self) -> list[dict[str, Any]]:
        """
        Check DAG integrity: find unsatisfiable inputs, orphan outputs,
        ambiguous producers, cycles, and non-schema inputs.
        
        Returns a list of violations (never raises — D3).
        Every violation has 'kind', 'stage', 'message', and 'severity'.
        """
        violations: list[dict[str, Any]] = []
        
        # Build producer map: schema_id -> [stage_names]
        producers: dict[str, list[str]] = {}
        for name, decl in self._stages.items():
            for schema_id in decl.outputs.values():
                producers.setdefault(schema_id, []).append(name)
        
        # Check 1: unsatisfiable inputs (declared input with no producer,
        # unless declared as a root input)
        for name, decl in self._stages.items():
            root_set = set(decl.root_inputs or [])
            for param_name, schema_id in decl.inputs.items():
                if param_name in root_set:
                    continue  # declared root input
                if schema_id not in producers:
                    violations.append({
                        "kind": "unsatisfiable_input",
                        "stage": name,
                        "message": f"Input '{param_name}' (schema '{schema_id}') "
                                   f"has no producing stage and is not declared as a root input",
                        "severity": "error",
                    })
        
        # Check 2: orphan outputs (declared output with no consumer,
        # unless the stage is declared terminal)
        consumed: set[str] = set()
        for name, decl in self._stages.items():
            for schema_id in decl.inputs.values():
                consumed.add(schema_id)
        
        for name, decl in self._stages.items():
            if decl.terminal:
                continue
            for out_key, schema_id in decl.outputs.items():
                if schema_id not in consumed:
                    violations.append({
                        "kind": "orphan_output",
                        "stage": name,
                        "message": f"Output '{out_key}' (schema '{schema_id}') "
                                   f"is not consumed by any stage and '{name}' is not terminal",
                        "severity": "warning",
                    })
        
        # Check 3: ambiguous producers (schema_id with >1 producer).
        #
        # This is an ERROR, not a warning, unless every producer declares the same
        # `implements` step. A schema with two undeclared producers makes derive_dag()
        # bind consumers to both, and the executor takes whichever ran first — a
        # nondeterministic edge in a DAG whose entire purpose is reproducibility
        # (ARCHITECTURE.md §2.15 lists DAG integrity as CI-blocking).
        for schema_id, prods in producers.items():
            if len(prods) <= 1:
                continue
            steps = {self._stages[p].implements for p in prods}
            declared_alternatives = len(steps) == 1 and None not in steps
            if declared_alternatives:
                continue
            violations.append({
                "kind": "ambiguous_producer",
                "stage": ",".join(prods),
                "message": (
                    f"Schema '{schema_id}' has {len(prods)} producers: {prods}. "
                    f"If these are alternative implementations of one step, declare the "
                    f"same implements=\"<step>\" on each; otherwise give them distinct "
                    f"output schema IDs."
                ),
                "severity": "error",
            })
        
        # Check 4: cycles (proper DFS with recursion stack)
        dag = self.derive_dag()
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._stages}
        
        def dfs(node: str, stack: list[str]):
            color[node] = GRAY
            stack.append(node)
            for dep in dag.get(node, []):
                if dep not in color:
                    continue  # not a registered stage
                if color[dep] == GRAY:
                    # Back-edge: cycle
                    cycle = stack[stack.index(dep):] + [dep]
                    violations.append({
                        "kind": "cycle",
                        "stage": node,
                        "message": f"DAG contains cycle: {' -> '.join(cycle)}",
                        "severity": "error",
                    })
                elif color[dep] == WHITE:
                    dfs(dep, stack)
            stack.pop()
            color[node] = BLACK
        
        for name in self.names():
            if color[name] == WHITE:
                dfs(name, [])
        
        # Check 5: non-schema inputs (input values that look like types, not schema IDs)
        for name, decl in self._stages.items():
            for param_name, schema_id in decl.inputs.items():
                # Schema IDs look like "type/version" e.g. "raw-source/1"
                # Non-schema inputs look like "integer", "image/*", etc.
                if "/" not in schema_id:
                    violations.append({
                        "kind": "non_schema_input",
                        "stage": name,
                        "message": f"Input '{param_name}' has value '{schema_id}' "
                                   f"which is not a valid schema ID (missing '/')",
                        "severity": "warning",
                    })
        
        return violations


# ── Global registry instance ─────────────────────────────────────

_REGISTRY = StageRegistry()


def get_registry() -> StageRegistry:
    """Return the global stage registry instance."""
    return _REGISTRY


def stage(
    name: str,
    version: int = 1,
    inputs: Optional[dict[str, str]] = None,
    outputs: Optional[dict[str, str]] = None,
    toolchain: Optional[list[str]] = None,
    fixtures: Optional[str] = None,
    root_inputs: Optional[list[str]] = None,
    optional_root_inputs: Optional[list[str]] = None,
    terminal: bool = False,
    implements: Optional[str] = None,
    placement: str = "on-demand",
    memory_budget_mb: int = 256,
    queue: str = "q.default",
    description: str = "",
) -> Callable:
    """
    Decorator that registers a function as a build stage.

    Example:
        @stage(name="finish", version=9,
               inputs={"pdf": "raw-pdf/1", "profile": "profile/1"},
               outputs={"pdf": "pdfx/1", "report": "finish-report/1"},
               toolchain=["ghostscript", "icc"],
               fixtures="fixtures/finish/v3",
               memory_budget_mb=512,
               queue="q.prepress")
        def finish(ctx: StageCtx, pdf: ArtifactRef, profile: OutputProfile) -> StageResult:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        decl = StageDeclaration(
            name=name,
            version=version,
            inputs=inputs or {},
            outputs=outputs or {},
            toolchain=toolchain or [],
            fixtures=fixtures,
            root_inputs=root_inputs,
            optional_root_inputs=optional_root_inputs,
            terminal=terminal,
            implements=implements,
            placement=placement,
            memory_budget_mb=memory_budget_mb,
            queue=queue,
            description=description or fn.__doc__ or "",
            fn=fn,
        )
        _REGISTRY.register(decl)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def run_stage(name: str, ctx: StageCtx, **inputs: Any) -> StageResult:
    """Execute a registered stage by name."""
    decl = _REGISTRY.get(name)
    if decl is None:
        raise ValueError(f"Unknown stage: '{name}'. Registered stages: {_REGISTRY.names()}")
    return decl.fn(ctx, **inputs)
