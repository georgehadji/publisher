"""
How a build turns what it was given into what every stage reads: the profile,
the completed DesignSpec, the book's identity, the render path -- each resolved
in one place, so no stage carries a fallback of its own.
"""

from __future__ import annotations

import threading

import pytest

from publisher_stages import ErrorKind, RenderEngine, StageError


def test_no_profile_named_is_the_default_one():
    from profiles import DEFAULT_PROFILE_NAME, resolve_profile
    assert resolve_profile(None)["name"] == DEFAULT_PROFILE_NAME


def test_an_unknown_profile_is_bad_input_not_the_default():
    """Printing a KDP book to a typo's fallback geometry is a wrong book."""
    from profiles import resolve_profile
    with pytest.raises(StageError) as e:
        resolve_profile("KDP US Trade 6x9 typo")
    assert e.value.kind == ErrorKind.BAD_INPUT


def test_loaded_profiles_carry_schema_defaults_and_are_copies():
    from profiles import load_profile
    a = load_profile("KDP US Trade 6x9")
    assert a["composition"]["maxOrphanPages"] == 0
    a["bleed"]["all"] = 99
    assert load_profile("KDP US Trade 6x9")["bleed"]["all"] != 99


def test_completed_designspec_fills_defaults_and_converts_inches():
    from templates import complete_designspec
    spec = complete_designspec({"trimSize": {"width": 5.5, "height": 8.5, "unit": "in"}})
    trim = spec["trimSize"]
    assert trim["unit"] == "mm"
    assert (trim["width"], trim["height"]) == pytest.approx((139.7, 215.9))
    assert spec["typography"]["scaleRatio"] == 1.25            # schema default
    assert spec["typography"]["bodyFont"]["family"] == "GFS Didot"   # house spec


def test_book_identity_reads_the_ast_not_a_default():
    from stages.rendering import book_identity
    ast = {"metadata": {"title": "Τίτλος", "language": "el-GR", "contributors": [
        {"role": "author", "displayName": "A. Author"},
        {"role": "editor", "displayName": "E. Editor"}]}}
    assert book_identity(ast) == {"title": "Τίτλος", "authors": ["A. Author"], "language": "el-GR"}
    assert book_identity({})["language"] is None     # undeclared stays undeclared


@pytest.mark.parametrize("raw, engine", [(None, RenderEngine.CSS), ("", RenderEngine.CSS),
                                         (" Typst ", RenderEngine.TYPST)])
def test_render_engine_from_env(raw, engine):
    assert RenderEngine.from_env(raw) is engine


def test_render_engine_from_env_refuses_an_unknown_path():
    with pytest.raises(ValueError, match="not a render path"):
        RenderEngine.from_env("latex")


class _Conn:
    def __init__(self, log):
        self.log, self.closed = log, False

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                conn.log.append(sql.split()[0])

        return _Cur()

    def commit(self):
        pass

    def close(self):
        self.closed = True


def test_lease_is_renewed_on_its_own_connection_until_stopped(monkeypatch):
    """A build longer than the lease was reclaimed and run twice: the lease
    was only renewed between stages, and one stage can outlast it."""
    import worker
    log: list[str] = []
    conns: list[_Conn] = []
    fail_once = [True]

    def connect(_dsn):
        if fail_once.pop() if fail_once else False:
            raise worker.psycopg2.OperationalError("db blip")
        conns.append(_Conn(log))
        return conns[-1]

    monkeypatch.setattr(worker, "LEASE_RENEW_S", 0.01)
    monkeypatch.setattr(worker, "_dsn", lambda: "dsn")
    monkeypatch.setattr(worker.psycopg2, "connect", connect)
    stop = threading.Event()
    beat = threading.Thread(target=worker._keep_lease, args=("b1", "t1", stop))
    beat.start()
    while log.count("UPDATE") < 3:
        stop.wait(0.01)
    stop.set()
    beat.join(timeout=5)

    assert not beat.is_alive()
    assert log[0] == "SELECT"          # tenant GUC first: RLS hides the row otherwise
    assert len(conns) == 1 and conns[0].closed   # survived the blip, closed on stop
