"""
One fact, several implementations, one fixture each (W7, docs/WIRING_PLAN.md).

- `platform/cas/fixtures/agreement.json`: digest and shard path of the same
  bytes, in the Python store the worker writes with. The Rust crate
  (`cargo test`) and the API's `casPath` (packages/api/src/db.test.ts) read
  the same file.
- `platform/pagescan/fixtures/agreement.json`: widows, orphans and runts of one
  `pagemap/1`, by `preflight.scan_composition`. The Rust scanner reads it too.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

from publisher_cas import CasConfig, ContentAddressedStore
from publisher_prepress.preflight import scan_composition

ROOT = Path(__file__).resolve().parent.parent


def _fixture(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_the_python_store_writes_where_rust_and_the_api_read(tmp_path):
    store = ContentAddressedStore(CasConfig(local_cache_root=tmp_path))
    for case in _fixture("platform/cas/fixtures/agreement.json"):
        ref = store.put(case["text"].encode("utf-8"))
        assert str(ref.hash) == case["sha256"]
        assert store.get_path(ref).relative_to(tmp_path).as_posix() == case["path"]


def test_the_python_scan_finds_what_the_rust_scan_finds():
    fixture = _fixture("platform/pagescan/fixtures/agreement.json")
    validate(fixture["pagemap"], _fixture("schemas/pagemap/pagemap.schema.json"))
    found = scan_composition(fixture["pagemap"])
    assert {k: found[k] for k in ("orphans", "widows", "runts")} == fixture["expected"]
    assert found["measured"] == len(fixture["pagemap"]["pages"]) - 1   # page 5 carries no flags
