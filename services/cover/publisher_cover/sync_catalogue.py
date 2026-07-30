"""
Sync the image-model catalogue from OpenRouter into pinned YAML.

Run: python -m publisher_cover.sync_catalogue   (from services/cover/, with the package installed)

Why this exists (COVER_DESIGN.md D3): model discovery MUST be a scheduled job, never a
request-time lookup. Resolving "the best image model" per request makes the effective
model a runtime choice while `model_id` in the cache key stays constant -- two different
(slug, provider, params) triples then collide on one key, and the frozen artifact wins
forever. So: probe here, pin in YAML, review the diff as a PR.

Two endpoints are needed because neither is a superset of the other:
  GET /api/v1/models?output_modalities=image   -> pricing table, `benchmarks.design_arena`
  GET /api/v1/images/models                    -> per-model supported_parameters enums
  GET /api/v1/images/models/{id}/endpoints      -> PER-PROVIDER caps + billing unit

The third call is the important one: capability varies by provider *within one model id*
(google/gemini-3-pro-image tops out at 2K via google-vertex, 4K via google-ai-studio).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

BASE = "https://openrouter.ai/api/v1"
TIMEOUT_S = 90
RETRIES = 3

# 2:3 is exactly 6x9in -- the default trim. A model without it cannot render a
# native cover and must crop, which loses the type zone the brief reserved.
COVER_ASPECT = "2:3"

OUT_PATH = Path(__file__).parent / "model_catalogue.yaml"

# Dynamic-routing meta-models: `pricing: -1`, model chosen at request time.
# Hard-excluded -- they are cache-key poison, not a cost question.
EXCLUDED = {
    "openrouter/auto": "pricing -1, model chosen at request time -- cache-key poison",
    "openrouter/auto-beta": "pricing -1, model chosen at request time -- cache-key poison",
}


def _get(path: str) -> dict[str, Any]:
    """GET a public OpenRouter endpoint with bounded retries."""
    url = f"{BASE}{path}"
    last: Optional[Exception] = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed after {RETRIES} attempts: {last}")


def classify_role(model_id: str, aspect_ratios: list[str]) -> str:
    """
    Assign a pipeline role. Roles are policy, not capability -- `cover-art` may only
    dispatch to `cover_art`, and only `ornament_vector_svg` reaches the ornament slot.
    """
    if model_id in EXCLUDED:
        return "excluded"
    if "vector" in model_id:
        return "ornament_vector_svg"
    if model_id.startswith("recraft/"):
        return "ornament_raster"
    if COVER_ASPECT not in aspect_ratios:
        return "unsuitable_no_native_2_3"
    return "cover_art"


def collect() -> dict[str, Any]:
    general = _get("/models?output_modalities=image&limit=1000")
    images = _get("/images/models")

    # design_arena ELO lives ONLY on the general models endpoint, never on
    # /images/models. `graphicdesign` is the closest proxy for cover quality.
    elo: dict[str, dict[str, int]] = {}
    for m in general.get("data", []):
        for d in ((m.get("benchmarks") or {}).get("design_arena") or []):
            elo.setdefault(m["id"], {})[d["category"]] = d["elo"]

    models: list[dict[str, Any]] = []
    for c in sorted(images.get("data", []), key=lambda x: x["id"]):
        mid = c["id"]
        sp = c.get("supported_parameters") or {}
        ars = (sp.get("aspect_ratio") or {}).get("values") or []

        providers: list[dict[str, Any]] = []
        try:
            detail = _get(f"/images/models/{mid}/endpoints")
        except RuntimeError:
            detail = {"endpoints": []}

        for ep in detail.get("endpoints", []):
            esp = ep.get("supported_parameters") or {}
            out_price = next(
                (p for p in (ep.get("pricing") or []) if p.get("billable") == "output_image"),
                None,
            )
            providers.append({
                "slug": ep.get("provider_slug"),
                # image=flat per image | megapixel=scales with pixels | token=scales with res/quality
                "billing_unit": (out_price or {}).get("unit") or "UNPRICED",
                "rate_usd": (out_price or {}).get("cost_usd"),
                "resolutions": (esp.get("resolution") or {}).get("values") or [],
                "n_max": (esp.get("n") or {}).get("max", 1),
                "input_references_max": (esp.get("input_references") or {}).get("max", 0),
                "seed": "seed" in esp,
                "streaming": bool(ep.get("supports_streaming")),
                "passthrough": ep.get("allowed_passthrough_parameters") or [],
            })

        models.append({
            "id": mid,
            "name": c.get("name", ""),
            "role": classify_role(mid, ars),
            "native_2_3": COVER_ASPECT in ars,
            "seed": "seed" in sp,
            "n_max": (sp.get("n") or {}).get("max", 1),
            "input_references_max": (sp.get("input_references") or {}).get("max", 0),
            "streaming": bool(c.get("supports_streaming")),
            "design_arena_elo": elo.get(mid) or None,
            "aspect_ratios": ars,
            "providers": providers,
        })

    return {
        "catalogue_version": 1,
        "probed_at": time.strftime("%Y-%m-%d", time.gmtime()),
        "total_count": general.get("total_count"),
        "models": models,
        "exclude": [{"id": k, "reason": v} for k, v in EXCLUDED.items()],
    }


# ── Minimal YAML emitter ─────────────────────────────────────────
# Deliberately dependency-free and deterministic: this file is committed and
# diff-reviewed, so byte-stable output matters more than YAML feature coverage.


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    # NOTE: `:` is deliberately EXCLUDED from the bareword-safe charset. PyYAML's
    # SafeLoader follows YAML 1.1, which resolves an unquoted `N:N` scalar as
    # sexagesimal (base-60) -- "2:3" parses back as the integer 123, not the string
    # "2:3". Every aspect ratio in this catalogue has that shape. Quote anything
    # containing a colon (and anything else outside the safe set) via json.dumps,
    # which produces a valid double-quoted YAML scalar.
    return s if s and all(ch.isalnum() or ch in "-_./" for ch in s) else json.dumps(s)


def _emit(node: Any, indent: int = 0, lines: Optional[list[str]] = None) -> list[str]:
    lines = lines if lines is not None else []
    pad = "  " * indent
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{k}:")
                _emit(v, indent + 1, lines)
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{pad}{k}: []")
                elif all(not isinstance(i, (dict, list)) for i in v):
                    lines.append(f"{pad}{k}: [{', '.join(_scalar(i) for i in v)}]")
                else:
                    lines.append(f"{pad}{k}:")
                    for item in v:
                        # Render the item two levels in, then hoist its first line
                        # onto a dash one level in, so continuations align under it.
                        sub = _emit(item, indent + 2)
                        if not sub:
                            continue
                        lines.append(f"{'  ' * (indent + 1)}- {sub[0].lstrip()}")
                        lines.extend(sub[1:])
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    else:
        lines.append(f"{pad}{_scalar(node)}")
    return lines


HEADER = """\
# GENERATED -- do not hand-edit. Regenerate: python -m services.cover.art_policy.sync_catalogue
# Source: GET /api/v1/models?output_modalities=image
#       + GET /api/v1/images/models[/{id}/endpoints]
#
# `billing_unit` is the field that decides model choice at PRINT resolution:
#   image      flat per image, resolution-independent   -- cheapest at 4K
#   megapixel  scales with pixel count                  -- 3-10x worse at print res
#   token      scales with resolution/quality           -- read usage.cost back
#
# A 6x9in cover at 300dpi is 4.86 MP front-only, 10.82 MP full wrap. At that size a
# megapixel-billed model can cost 19x a flat-rate one for indistinguishable output.
#
# `providers[]` is the unit of dispatch, NOT `id`: capability varies by provider within
# one model id. Validate resolution/aspect_ratio against the chosen provider before
# dispatch, and always send provider.allow_fallbacks=false.
"""


def main() -> None:
    data = collect()
    body = "\n".join(_emit(data))
    OUT_PATH.write_text(HEADER + body + "\n", encoding="utf-8")

    usable = [m for m in data["models"] if m["role"] == "cover_art"]
    seeded = [m for m in usable if m["seed"]]
    print(f"wrote {OUT_PATH}")
    print(f"  models probed      : {len(data['models'])} (total_count={data['total_count']})")
    print(f"  role=cover_art     : {len(usable)}")
    print(f"  ...of which seeded : {len(seeded)}")


if __name__ == "__main__":
    main()
