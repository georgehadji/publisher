"""
A stage's warnings survive to whoever reads the build (docs/WIRING_PLAN.md W0).

`StageResult.warnings` was read by nothing, and a cache hit rebuilt the result
from artifacts alone: a warning appeared on the first build and was silently
gone from every cached rebuild of the same inputs. The defect was still in the
book; the report of it was not.
"""

from __future__ import annotations

import json
import sqlite3

from publisher_cache import SqliteCacheStore
from publisher_exec import run_single_stage
from publisher_stages import Diagnostic, StageDeclaration, StageRegistry, StageResult

WARNING = Diagnostic(code="override-op-inapplicable", severity="warning",
                     human_message="override 'ov-1' (split) was not applied",
                     suggested_fix="Aim it elsewhere.", source_ref="p1")


def _registry(fn) -> StageRegistry:
    registry = StageRegistry()
    registry.register(StageDeclaration(name="probe", version=1, fn=fn, inputs={},
                                       outputs={"out": "probe-out/1"}))
    return registry


def test_a_diagnostic_round_trips_through_its_wire_form():
    assert Diagnostic.from_dict(json.loads(json.dumps(WARNING.to_dict()))) == WARNING


def test_a_cache_hit_reports_the_warnings_and_metrics_the_stage_reported(tmp_path):
    calls = []

    def probe(ctx, **kw):
        calls.append(1)
        return StageResult(artifacts=[], metrics={"overrides_inapplicable": 1}, warnings=[WARNING])

    registry, cache = _registry(probe), SqliteCacheStore(tmp_path / "cache.sqlite")
    runs = [run_single_stage("probe", registry, build_id=f"b{i}", resolved_inputs={},
                             cas_root=tmp_path / "cas", cache_store=cache)[0] for i in (1, 2)]

    assert [r.cache_hit for r in runs] == [False, True] and len(calls) == 1
    assert runs[1].warnings == [WARNING]
    assert runs[1].metrics == {"overrides_inapplicable": 1}


def test_an_index_written_before_diagnostics_were_kept_still_serves_its_entries(tmp_path):
    path = tmp_path / "cache.sqlite"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE cache_index (cache_key TEXT PRIMARY KEY, stage TEXT NOT NULL, "
                "version INTEGER NOT NULL, output_refs TEXT NOT NULL, created_at TEXT NOT NULL, "
                "hit_count INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO cache_index VALUES ('k', 'probe', 1, '{}', 'then', 0)")
    old.commit()
    old.close()

    cache = SqliteCacheStore(path)
    assert cache.get("k") == {"output_refs": {}, "diagnostics": {}}
    cache.put("k2", "probe", 1, {}, {"metrics": {}, "warnings": [WARNING.to_dict()]})
    assert cache.get("k2")["diagnostics"]["warnings"] == [WARNING.to_dict()]
