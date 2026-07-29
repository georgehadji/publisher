"""Tests for P8 — Platform & API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── Billing tests ──────────────────────────────────────────────

class TestUsageTracker:
    def test_record_usage(self):
        from billing import UsageTracker
        t = UsageTracker()
        t.record("tenant-1", "render", 0.05)
        t.record("tenant-1", "llm", 0.02)
        assert t.total_cost("tenant-1") == 0.07

    def test_cost_by_service(self):
        from billing import UsageTracker
        t = UsageTracker()
        t.record("t1", "render", 0.05)
        t.record("t1", "render", 0.03)
        t.record("t1", "llm", 0.02)
        costs = t.cost_by_service("t1")
        assert costs["render"] == 0.08
        assert costs["llm"] == 0.02

    def test_recent_usage(self):
        from billing import UsageTracker
        t = UsageTracker()
        for i in range(5):
            t.record("t1", f"svc-{i}", 0.01)
        recent = t.recent_usage("t1", limit=3)
        assert len(recent) == 3

    def test_empty_tracker(self):
        from billing import UsageTracker
        t = UsageTracker()
        assert t.total_cost("nonexistent") == 0.0


class TestQuotaManager:
    def test_set_quota(self):
        from billing import QuotaManager
        q = QuotaManager()
        q.set_quota("t1", monthly_spend_limit=50.0)
        quota = q.get_quota("t1")
        assert quota["monthly_spend_limit"] == 50.0

    def test_check_build_allowed(self):
        from billing import QuotaManager, UsageTracker
        q = QuotaManager()
        t = UsageTracker()
        q.set_quota("t1", monthly_spend_limit=1.0)
        allowed, msg = q.check_build_allowed("t1", t)
        assert allowed

    def test_check_build_blocked(self):
        from billing import QuotaManager, UsageTracker
        q = QuotaManager()
        t = UsageTracker()
        q.set_quota("t1", monthly_spend_limit=0.05)
        t.record("t1", "render", 0.10)
        allowed, msg = q.check_build_allowed("t1", t)
        assert not allowed

    def test_no_quota(self):
        from billing import QuotaManager, UsageTracker
        q = QuotaManager()
        allowed, msg = q.check_build_allowed("unknown", UsageTracker())
        assert not allowed


class TestCostCeiling:
    def test_within_ceiling(self):
        from billing import CostCeiling, UsageTracker
        c = CostCeiling(0.50)
        t = UsageTracker()
        t.record("t1", "llm", 0.20)
        ok, cost = c.check("t1", t)
        assert ok
        assert cost <= 0.50

    def test_exceeds_ceiling(self):
        from billing import CostCeiling, UsageTracker
        c = CostCeiling(0.10)
        t = UsageTracker()
        t.record("t1", "llm", 0.50)
        ok, cost = c.check("t1", t)
        assert not ok


# ── Adapter tests ──────────────────────────────────────────────

class TestPrinceAdapter:
    def test_availability(self):
        from adapters import PrinceAdapter
        p = PrinceAdapter()
        # Should return False in CI without Prince installed
        assert isinstance(p.is_available(), bool)

    def test_not_available_by_default(self):
        from adapters import PrinceAdapter
        p = PrinceAdapter(prince_path="/nonexistent/prince")
        assert not p.is_available()


class TestAdobeInDesignAdapter:
    def test_not_available_without_key(self):
        from adapters import AdobeInDesignAdapter
        a = AdobeInDesignAdapter()
        assert not a.is_available()

    def test_available_with_key(self):
        from adapters import AdobeInDesignAdapter
        a = AdobeInDesignAdapter(api_key="test-key")
        assert a.is_available()

    def test_remaining_budget(self):
        from adapters import AdobeInDesignAdapter
        a = AdobeInDesignAdapter(api_key="key", max_monthly_spend=100.0)
        assert a.remaining_budget == 100.0

    def test_export_fallback(self):
        from adapters import AdobeInDesignAdapter
        import tempfile
        a = AdobeInDesignAdapter(api_key="key")
        ast = {"body": []}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test.idml"
            result = a.export_to_idml(ast, out)
            assert result.exists()
            assert result.suffix == ".idml"

    def test_spend_ceiling_blocks_external(self):
        from adapters import AdobeInDesignAdapter
        import tempfile
        a = AdobeInDesignAdapter(api_key="key", max_monthly_spend=0.01, cost_per_page=1.0)
        ast = {"body": []}
        with tempfile.TemporaryDirectory() as tmp:
            # First call triggers fallback
            result = a.export_to_idml(ast, Path(tmp) / "test.idml")
            assert result.exists()
            # Budget was consumed
            assert a.remaining_budget <= 0.01
