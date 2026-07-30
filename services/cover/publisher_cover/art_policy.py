"""
Art policy — loads the pinned model catalogue + tiered dispatch panels, and validates
every dispatch against the (model, provider) pair's actual capability before a call is
made.

From COVER_DESIGN.md §3:
- Model discovery is a scheduled job (sync_catalogue.py), never a request-time lookup.
- The dispatch unit is (model_id, provider_slug), never bare model_id — capability
  (resolution ceiling, aspect ratios, seed support) varies by PROVIDER within one model.
- provider.allow_fallbacks is always false; a silent failover would return different
  pixels under an unchanged cache key.

Validation failures return a structured result rather than raising — the stage boundary
(stages/cover_stages.py) is what turns an invalid dispatch into a StageError(BAD_INPUT),
matching this codebase's existing convention of services returning data and stages
translating it into the error taxonomy (see services/prepress/publisher_prepress).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

_PACKAGE_DIR = Path(__file__).parent
DEFAULT_CATALOGUE_PATH = _PACKAGE_DIR / "model_catalogue.yaml"
DEFAULT_TIERS_PATH = _PACKAGE_DIR / "art_policy_tiers.yaml"


# ── Data types ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderCapability:
    """What one (model, provider) endpoint actually accepts. From /images/models/{id}/endpoints."""
    model_id: str
    provider_slug: str
    billing_unit: str  # "image" | "megapixel" | "token"
    rate_usd: Optional[float]
    resolutions: tuple[str, ...]  # empty tuple == no resolution parameter on this endpoint
    n_max: int
    input_references_max: int
    seed: bool
    aspect_ratios: tuple[str, ...]


@dataclass(frozen=True)
class DispatchSpec:
    """One resolved, capability-checked call to make against POST /api/v1/images."""
    model_id: str
    provider_slug: str
    aspect_ratio: str
    resolution: Optional[str]
    n: int
    seeds: tuple[int, ...] = ()
    role: str = "cover_art"


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()

    @staticmethod
    def valid() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def invalid(*errors: str) -> "ValidationResult":
        return ValidationResult(ok=False, errors=errors)


@dataclass(frozen=True)
class ResolvedPanel:
    tier: str
    dispatches: tuple[DispatchSpec, ...]
    estimated_cost_usd: float
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# ── Catalogue loading ───────────────────────────────────────────


def load_catalogue(path: Path | str = DEFAULT_CATALOGUE_PATH) -> dict[str, Any]:
    """Load the pinned, committed model catalogue (see sync_catalogue.py)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_tiers(path: Path | str = DEFAULT_TIERS_PATH) -> dict[str, Any]:
    """Load the product-tier -> panel mapping (COVER_DESIGN.md §3, your tier decision)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def capability_index(catalogue: dict[str, Any]) -> dict[tuple[str, str], ProviderCapability]:
    """Flatten the catalogue into a (model_id, provider_slug) -> ProviderCapability index."""
    index: dict[tuple[str, str], ProviderCapability] = {}
    for model in catalogue.get("models", []):
        model_id = model["id"]
        aspect_ratios = tuple(model.get("aspect_ratios") or ())
        for p in model.get("providers", []):
            key = (model_id, p["slug"])
            index[key] = ProviderCapability(
                model_id=model_id,
                provider_slug=p["slug"],
                billing_unit=p.get("billing_unit", "UNPRICED"),
                rate_usd=p.get("rate_usd"),
                resolutions=tuple(p.get("resolutions") or ()),
                n_max=p.get("n_max", 1),
                input_references_max=p.get("input_references_max", 0),
                seed=bool(p.get("seed")),
                aspect_ratios=aspect_ratios,
            )
    return index


# ── Validation ───────────────────────────────────────────────────


def validate_dispatch(
    index: dict[tuple[str, str], ProviderCapability],
    spec: DispatchSpec,
) -> ValidationResult:
    """
    Validate one dispatch against the endpoint's actual capability. This is what turns a
    would-be runtime 400 from OpenRouter into a BAD_INPUT StageError at the pipeline
    boundary — see COVER_DESIGN.md §3 "capability varies by provider".
    """
    cap = index.get((spec.model_id, spec.provider_slug))
    if cap is None:
        return ValidationResult.invalid(
            f"no such (model, provider) endpoint: ({spec.model_id!r}, {spec.provider_slug!r})"
        )

    errors: list[str] = []

    if cap.aspect_ratios and spec.aspect_ratio not in cap.aspect_ratios:
        errors.append(
            f"{spec.model_id}@{spec.provider_slug} does not support aspect_ratio "
            f"{spec.aspect_ratio!r}; supported: {cap.aspect_ratios}"
        )

    # An empty `resolutions` tuple means the endpoint has no resolution parameter at
    # all (e.g. flux.2-pro) -- absence of a requested resolution is then correct, and
    # presence of one is an error either way, so both directions are checked.
    if cap.resolutions:
        if spec.resolution is None:
            errors.append(
                f"{spec.model_id}@{spec.provider_slug} requires a resolution; "
                f"supported: {cap.resolutions}"
            )
        elif spec.resolution not in cap.resolutions:
            errors.append(
                f"{spec.model_id}@{spec.provider_slug} does not support resolution "
                f"{spec.resolution!r}; supported: {cap.resolutions}"
            )
    elif spec.resolution is not None:
        errors.append(
            f"{spec.model_id}@{spec.provider_slug} has no resolution parameter; "
            f"got {spec.resolution!r}"
        )

    if spec.n > cap.n_max:
        errors.append(
            f"{spec.model_id}@{spec.provider_slug} allows n<={cap.n_max}, got n={spec.n}"
        )

    if spec.seeds and not cap.seed:
        errors.append(
            f"{spec.model_id}@{spec.provider_slug} does not support seed, "
            f"but {len(spec.seeds)} seed(s) were requested"
        )

    return ValidationResult(ok=not errors, errors=tuple(errors))


def estimate_cost_usd(
    index: dict[tuple[str, str], ProviderCapability],
    spec: DispatchSpec,
    *,
    megapixels: float = 4.86,  # 6x9in @ 300dpi, front-only (COVER_DESIGN.md §3)
) -> Optional[float]:
    """
    Estimate cost for one dispatch. Billing-unit aware — see COVER_DESIGN.md §3's
    billing-unit trap: `megapixel`-billed models cost far more at print resolution than
    a flat `image`-billed model of equal quality.
    """
    cap = index.get((spec.model_id, spec.provider_slug))
    if cap is None or cap.rate_usd is None:
        return None
    n_images = max(spec.n, len(spec.seeds) or 1)
    if cap.billing_unit == "image":
        return round(cap.rate_usd * n_images, 4)
    if cap.billing_unit == "megapixel":
        return round(cap.rate_usd * megapixels * n_images, 4)
    # "token" billing depends on resolution/quality in a way not captured by the
    # catalogue's flat rate field alone -- report the per-token rate as a floor and
    # rely on usage.cost (returned per call) for the authoritative figure.
    return None


# ── Tier resolution ─────────────────────────────────────────────


def resolve_tier_panel(
    tier_name: str,
    catalogue: Optional[dict[str, Any]] = None,
    tiers: Optional[dict[str, Any]] = None,
) -> ResolvedPanel:
    """
    Resolve a product tier (basic | pro | studio) to a validated, cost-estimated list of
    DispatchSpecs. Every entry is checked against the live-probed catalogue before it is
    returned — an invalid tier definition fails here, at policy-load time, not mid-fan-out.
    """
    catalogue = catalogue if catalogue is not None else load_catalogue()
    tiers = tiers if tiers is not None else load_tiers()
    index = capability_index(catalogue)

    tier = (tiers.get("tiers") or {}).get(tier_name)
    if tier is None:
        known = sorted((tiers.get("tiers") or {}).keys())
        return ResolvedPanel(
            tier=tier_name, dispatches=(), estimated_cost_usd=0.0,
            errors=(f"unknown tier {tier_name!r}; known tiers: {known}",),
        )

    dispatches: list[DispatchSpec] = []
    errors: list[str] = []
    total_cost = 0.0

    for entry in tier.get("panel", []):
        spec = DispatchSpec(
            model_id=entry["model"],
            provider_slug=entry["provider"],
            aspect_ratio=entry.get("aspect_ratio", "2:3"),
            resolution=entry.get("resolution"),
            n=entry.get("n", 1),
            seeds=tuple(entry.get("seeds", ())),
        )
        result = validate_dispatch(index, spec)
        if not result.ok:
            errors.extend(result.errors)
            continue
        dispatches.append(spec)
        cost = estimate_cost_usd(index, spec)
        if cost is not None:
            total_cost += cost

    return ResolvedPanel(
        tier=tier_name,
        dispatches=tuple(dispatches),
        estimated_cost_usd=round(total_cost, 4),
        errors=tuple(errors),
    )
