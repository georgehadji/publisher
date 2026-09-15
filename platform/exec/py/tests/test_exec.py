"""
E1.1 acceptance: `plan()` is pure. These tests construct no CAS, open no
socket, and never call `run()` -- just a `StageRegistry` built from plain
`StageDeclaration`s and a dict of root inputs.
"""

from __future__ import annotations

from publisher_exec import ExecutionPlan, plan
from publisher_stages import StageDeclaration, StageRegistry


def _decl(name, *, inputs=None, outputs=None, root_inputs=None,
          optional_root_inputs=None, implements=None) -> StageDeclaration:
    return StageDeclaration(
        name=name,
        version=1,
        fn=lambda ctx, **kw: None,  # never called -- plan() does not execute stages
        inputs=inputs or {},
        outputs=outputs or {},
        root_inputs=root_inputs,
        optional_root_inputs=optional_root_inputs,
        implements=implements,
    )


def test_plan_orders_a_simple_two_stage_chain():
    registry = StageRegistry()
    registry.register(_decl("acquire", outputs={"manifest": "manifest/1"}, root_inputs=["manifest_path"]))
    registry.register(_decl("assemble", inputs={"manifest_path": "manifest/1"}, outputs={"ast": "ast/1"}))

    result = plan(registry, {"acquire": {"manifest_path": "x.json"}})

    assert isinstance(result, ExecutionPlan)
    assert result.order == ("acquire", "assemble")


def test_plan_excludes_a_stage_whose_root_input_is_unsupplied():
    registry = StageRegistry()
    registry.register(_decl("acquire", outputs={"manifest": "manifest/1"}, root_inputs=["manifest_path"]))
    registry.register(_decl("assemble", inputs={"manifest_path": "manifest/1"}, outputs={"ast": "ast/1"}))
    registry.register(_decl("cover_brief", outputs={"brief": "cover-brief/1"}, root_inputs=["brief_prompt"]))

    result = plan(registry, {"acquire": {"manifest_path": "x.json"}})

    assert "cover_brief" not in result.order
    assert result.order == ("acquire", "assemble")


def test_plan_treats_a_declared_optional_root_input_as_satisfiable_when_absent():
    registry = StageRegistry()
    registry.register(_decl(
        "resolve", outputs={"resolved": "resolved/1"},
        root_inputs=["overrides_path"], optional_root_inputs=["overrides_path"],
    ))

    result = plan(registry, {})

    assert result.order == ("resolve",)


def test_plan_respects_selected_implementation_not_the_deselected_alternative():
    registry = StageRegistry()
    registry.register(_decl(
        "acquire", implements="ingest", outputs={"manifest": "manifest/1"},
        root_inputs=["manifest_path"],
    ))
    registry.register(_decl(
        "ingest", implements="ingest", outputs={"manifest": "manifest/1"},
        root_inputs=["docx_path"],
    ))
    registry.select_implementation("ingest", "ingest")

    # Only the selected implementation's root input is supplied; the
    # deselected alternative must not sneak into the plan even though its
    # own root input happens to be satisfiable in principle.
    result = plan(registry, {"ingest": {"docx_path": "book.docx"}, "acquire": {"manifest_path": "x.json"}})

    assert result.order == ("ingest",)


def test_plan_is_empty_for_an_empty_registry():
    assert plan(StageRegistry(), {}).order == ()
