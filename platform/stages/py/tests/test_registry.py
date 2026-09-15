"""Tests for the stage registry: alternative implementations and DAG derivation."""

import pytest



# ── Alternative implementations (see StageRegistry.select_implementation) ──────

def _reg_with_two_impls():
    from publisher_stages import StageRegistry, StageDeclaration
    r = StageRegistry()
    for name in ("finish-a", "finish-b"):
        r.register(StageDeclaration(name=name, version=1, fn=lambda ctx: None,
                                    inputs={"pdf": "raw-pdf/1"}, root_inputs=["pdf"],
                                    outputs={"pdf": "pdfx/1"}, implements="finish"))
    r.register(StageDeclaration(name="consumer", version=1, fn=lambda ctx: None,
                                inputs={"pdf": "pdfx/1"}, outputs={"r": "report/1"},
                                terminal=True))
    return r


def test_unselected_alternatives_is_an_error():
    """Declaring implements= records intent; it does not resolve it. Without a
    selection the executor would bind whichever producer ran first."""
    r = _reg_with_two_impls()
    kinds = {v["kind"] for v in r.check_integrity() if v["severity"] == "error"}
    assert "unselected_alternatives" in kinds


def test_selection_makes_the_dag_bind_one_producer():
    r = _reg_with_two_impls()
    r.select_implementation("finish", "finish-b")
    assert r.derive_dag()["consumer"] == ["finish-b"]
    assert not [v for v in r.check_integrity() if v["severity"] == "error"]


def test_select_implementation_rejects_a_mismatched_stage():
    r = _reg_with_two_impls()
    with pytest.raises(ValueError):
        r.select_implementation("finish", "consumer")     # does not implement the step
    with pytest.raises(ValueError):
        r.select_implementation("finish", "nonexistent")


# ── E1.2: build_registry() / FrozenRegistry (docs/ARCHITECTURE_SCORE_10_PLAN.md) ──
#
# Before this, `stages/__init__.py` selected `finish`/`ingest`/the render engine
# at IMPORT time on the one shared global registry, and `worker.run_build`
# selected `ingest` on it AGAIN per build -- import order and environment were
# part of the graph's identity. These exercise the real global declarations
# (`import stages`), because the bug was specifically about the SHARED registry.

def test_frozen_registry_rejects_mutation():
    import stages  # noqa: F401 -- registers the real stage set
    from publisher_stages import RegistryConfig, build_registry, StageDeclaration

    frozen = build_registry(RegistryConfig())
    with pytest.raises(TypeError):
        frozen.select_implementation("finish", "finish")
    with pytest.raises(TypeError):
        frozen.register(StageDeclaration(name="x", version=1, fn=lambda ctx: None))


def test_build_registry_selects_finish_gs_and_ingest_by_default():
    import stages  # noqa: F401
    from publisher_stages import RegistryConfig, build_registry

    registry = build_registry(RegistryConfig())
    assert registry.selected_implementation("finish") == "finish-gs"
    assert registry.selected_implementation("ingest") == "ingest"
    assert not [v for v in registry.check_integrity() if v["severity"] == "error"]


def test_two_registries_with_different_engines_coexist_in_one_process():
    """The acceptance test E1.2 names directly: two configs, two independent
    registries, neither's selection leaks into the other -- impossible when
    selection lived on one shared mutable singleton."""
    import stages  # noqa: F401
    from publisher_stages import RegistryConfig, RenderEngine, build_registry

    css = build_registry(RegistryConfig(render_engine=RenderEngine.CSS))
    typst = build_registry(RegistryConfig(render_engine=RenderEngine.TYPST))

    css_dag = css.derive_dag()
    typst_dag = typst.derive_dag()

    assert "design-compile" in css_dag["paginate"]
    assert "design-compile-typst" in typst_dag["paginate-typst"]
    assert not [v for v in css.check_integrity() if v["severity"] == "error"]
    assert not [v for v in typst.check_integrity() if v["severity"] == "error"]

    # Building the Typst registry must not have mutated the CSS one.
    assert css.derive_dag() == css_dag
    assert css.selected_implementation("design-compile") == "design-compile"


def test_build_registry_rejects_an_unknown_render_engine():
    from publisher_stages import RenderEngine

    with pytest.raises(ValueError):
        RenderEngine("bogus")
