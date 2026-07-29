"""
Billing system — usage tracking, per-tenant quotas, per-title cost ceilings.

From BUILD_PLAN.md P8 and ARCHITECTURE.md §2.16:
- Cost per book: cents (Chrome CPU-seconds + GS + storage)
- LLM inference ~$0.02-0.10 per title
- Per-tenant spend ceiling, per-tenant quotas
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class UsageRecord:
    """A single usage record for billing."""
    tenant_id: str
    title_id: Optional[str] = None
    build_id: Optional[str] = None
    service: str = ""  # "ingest", "render", "llm", "storage", "prepress"
    cost_usd: float = 0.0
    duration_ms: int = 0
    tokens_used: int = 0
    pages_processed: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class UsageTracker:
    """Tracks usage and costs per tenant."""
    
    def __init__(self):
        self._records: list[UsageRecord] = []
    
    def record(self, tenant_id: str, service: str, cost_usd: float,
               title_id: str = "", build_id: str = "", **extra):
        """Record a usage event."""
        self._records.append(UsageRecord(
            tenant_id=tenant_id,
            title_id=title_id or None,
            build_id=build_id or None,
            service=service,
            cost_usd=cost_usd,
            **extra,
        ))
    
    def total_cost(self, tenant_id: str) -> float:
        """Get total cost for a tenant."""
        return sum(r.cost_usd for r in self._records if r.tenant_id == tenant_id)
    
    def cost_by_service(self, tenant_id: str) -> dict[str, float]:
        """Get cost breakdown by service for a tenant."""
        costs: dict[str, float] = {}
        for r in self._records:
            if r.tenant_id == tenant_id:
                costs[r.service] = costs.get(r.service, 0.0) + r.cost_usd
        return costs
    
    def recent_usage(self, tenant_id: str, limit: int = 10) -> list[UsageRecord]:
        """Get recent usage records for a tenant."""
        records = [r for r in self._records if r.tenant_id == tenant_id]
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[:limit]


class QuotaManager:
    """Per-tenant quota enforcement."""
    
    def __init__(self):
        self._quotas: dict[str, dict] = {}
    
    def set_quota(self, tenant_id: str, monthly_spend_limit: float = 100.0,
                  monthly_builds: int = 100, max_concurrent_builds: int = 5):
        """Set quotas for a tenant."""
        self._quotas[tenant_id] = {
            "monthly_spend_limit": monthly_spend_limit,
            "monthly_builds": monthly_builds,
            "max_concurrent_builds": max_concurrent_builds,
        }
    
    def check_build_allowed(self, tenant_id: str, tracker: UsageTracker) -> tuple[bool, str]:
        """Check if a tenant can start a new build."""
        quota = self._quotas.get(tenant_id)
        if quota is None:
            return False, "No quota configured for tenant"
        
        total = tracker.total_cost(tenant_id)
        if total >= quota["monthly_spend_limit"]:
            return False, f"Monthly spend limit ${quota['monthly_spend_limit']:.2f} exceeded (${total:.2f})"
        
        return True, "ok"
    
    def get_quota(self, tenant_id: str) -> Optional[dict]:
        return self._quotas.get(tenant_id)


class CostCeiling:
    """Per-title cost ceiling enforcement (from BUILD_PLAN.md §2.16)."""
    
    def __init__(self, default_ceiling: float = 0.50):
        self._ceiling = default_ceiling
    
    def check(self, title_id: str, tracker: UsageTracker) -> tuple[bool, float]:
        """Check if a title's cost is within ceiling."""
        cost = tracker.total_cost(title_id)  # simplified
        return cost <= self._ceiling, cost
