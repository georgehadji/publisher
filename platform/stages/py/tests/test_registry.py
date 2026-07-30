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
