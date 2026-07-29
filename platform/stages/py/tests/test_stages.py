"""Tests for publisher_stages."""

from publisher_stages import (
    ErrorKind, Diagnostic, StageError, StageResult, StageCtx,
    ArtifactRef, StageDeclaration, StageRegistry,
    stage, get_registry, run_stage,
)
from datetime import datetime


def test_stage_declaration():
    decl = StageDeclaration(
        name="test-stage",
        version=1,
        inputs={"input1": "schema/1"},
        outputs={"output1": "schema/2"},
        toolchain=["tool-a"],
        fixtures="fixtures/test/v1",
        memory_budget_mb=256,
        queue="q.test",
        description="A test stage",
        fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
    )
    assert decl.name == "test-stage"
    assert decl.version == 1
    assert decl.cache_key_inputs() == ["schema/1"]


def test_stage_registry_register_and_get():
    registry = StageRegistry()
    decl = StageDeclaration(
        name="test-stage",
        version=1,
        inputs={},
        outputs={"out": "schema/1"},
        toolchain=[],
        fixtures=None,
        memory_budget_mb=128,
        queue="q.test",
        description="",
        fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
    )
    registry.register(decl)
    assert registry.get("test-stage") == decl
    assert "test-stage" in registry.names()


def test_stage_registry_duplicate_rejected():
    registry = StageRegistry()
    decl = StageDeclaration(
        name="dup-stage", version=1, inputs={}, outputs={},
        toolchain=[], fixtures=None, memory_budget_mb=128,
        queue="q.test", description="",
        fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
    )
    registry.register(decl)
    
    import pytest
    with pytest.raises(ValueError, match="already registered"):
        registry.register(decl)


def test_dag_derivation():
    registry = StageRegistry()

    registry.register(StageDeclaration(
        name="extract", version=1,
        inputs={"source": "raw-source/1"},
        outputs={"html": "typescript-html/1"},
        toolchain=[], fixtures=None, memory_budget_mb=128,
        queue="q.test", description="",
        fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
    ))

    registry.register(StageDeclaration(
        name="structure", version=1,
        inputs={"html": "typescript-html/1"},
        outputs={"ast": "ast/1"},
        toolchain=[], fixtures=None, memory_budget_mb=128,
        queue="q.test", description="",
        fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
    ))

    dag = registry.derive_dag()
    assert "extract" in dag
    assert "structure" in dag
    assert "extract" in dag["structure"]  # structure depends on extract
    assert dag["extract"] == []  # extract has no deps


def test_topological_sort():
    registry = StageRegistry()

    for name, inputs, outputs in [
        ("extract", {"source": "raw/1"}, {"html": "html/1"}),
        ("structure", {"html": "html/1"}, {"ast": "ast/1"}),
        ("design", {"ast": "ast/1"}, {"css": "css/1"}),
        ("paginate", {"html": "html/1", "css": "css/1"}, {"pdf": "pdf/1"}),
    ]:
        registry.register(StageDeclaration(
            name=name, version=1,
            inputs=inputs, outputs=outputs,
            toolchain=[], fixtures=None, memory_budget_mb=128,
            queue="q.test", description="",
            fn=lambda ctx, **kwargs: StageResult(artifacts=[]),
        ))

    order = registry.topological_sort()
    # extract must come before structure, which comes before design/paginate
    assert order.index("extract") < order.index("structure")
    assert order.index("structure") < order.index("design")


def test_error_kind_retryable():
    infra_err = StageError(kind=ErrorKind.INFRA, message="OOM", retryable=True)
    assert infra_err.retryable

    import pytest
    with pytest.raises(ValueError, match="Only infra and external_limit"):
        StageError(kind=ErrorKind.ENGINE_BUG, message="bug", retryable=True)
