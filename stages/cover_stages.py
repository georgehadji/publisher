"""
Cover design stages — cover-brief, cover-art, cover-judge, cover-compose, cover-preflight.

From COVER_DESIGN.md §0-§1: cover ART is page-count-free and runs in parallel with the
entire interior build. Only cover-compose (which joins art with the existing `cover`
stage's geometry, see stages/prepress_stages.py) sits behind the interior's final page
count. This is the split that keeps expensive, nondeterministic image generation off the
interior's critical path.

No image model ever renders title/author/series text (COVER_DESIGN.md §5) — cover-compose
sets type deterministically, through the same DesignSpec emitter path as the interior.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

from publisher_cover.art_policy import capability_index, load_catalogue, resolve_tier_panel
from publisher_cover.brief import ArtBrief, SourceRef, dialect_for_model, render_prompt
from publisher_cover.image_gen_port import ImageGenPort, ImageGenRequest
from publisher_cover.judge import (
    PairwiseVerdict, RasterMetrics, aggregate_panel, run_deterministic_gates,
)


def _cas(ctx: StageCtx) -> ContentAddressedStore:
    return ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.work_dir) / ".cas"))


def _deterministic_timestamp(ctx: StageCtx) -> str:
    """
    A timestamp derived from the build's cache key rather than the wall clock.

    ARCHITECTURE.md §2.5 requires nondeterministic bytes to be "set to fixed values
    derived from the cache key" so that artifacts are byte-identical across rebuilds.
    A `datetime.now()` here would give the same inputs a different artifact hash on
    every run, silently defeating the cache and the 2029-rebuild contract.
    """
    seed = int(hashlib.sha256(ctx.cache_key.encode("utf-8")).hexdigest()[:8], 16)
    return datetime.fromtimestamp(seed, tz=timezone.utc).isoformat()


def _put_json(ctx: StageCtx, kind: str, payload: dict) -> StageArtifactRef:
    cas = _cas(ctx)
    raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    ref = cas.put(raw, media_type=MediaType("application/json"))
    return StageArtifactRef(kind=kind, hash=str(ref.hash), media_type="application/json", size=len(raw))


# ── cover-brief ──────────────────────────────────────────────────


@stage(
    name="cover-brief",
    version=1,
    inputs={"title_meta": "title-meta/1", "designspec": "designspec/1"},
    outputs={"brief": "art-brief/1"},
    toolchain=["inference-gateway"],
    fixtures="fixtures/cover-brief/v1",
    root_inputs=["title_meta", "designspec", "manuscript_sample"],
    memory_budget_mb=256,
    queue="q.external",
    description="One structured-output LLM call -> ArtBrief. Routed per platform/routing/policy.yaml.",
)
def cover_brief_stage(
    ctx: StageCtx,
    title_meta: dict | None = None,
    designspec: dict | None = None,
    manuscript_sample: str | None = None,
    brief_override: dict | None = None,
) -> StageResult:
    """
    Produce an ArtBrief from title metadata + DesignSpec (+ opt-in manuscript sample).

    `brief_override` lets a caller (or a test) supply a pre-built ArtBrief payload in
    place of an actual model call — the inference gateway call itself is out of scope
    for this stage function (LLM_STRATEGY.md §0: the call happens at the gate boundary,
    its output is frozen here as a CAS artifact; the gateway integration is the
    InferenceGateway's job, not this stage's).
    """
    if not title_meta:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="title_meta is required")
    if not designspec:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="designspec is required")
    if not brief_override:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="brief_override is required until the inference-gateway call is wired "
                    "(COVER_DESIGN.md §14 open item)",
        )

    try:
        source_ref = SourceRef(
            title_meta_hash=brief_override["sourceRef"]["titleMetaHash"],
            design_spec_hash=brief_override["sourceRef"]["designSpecHash"],
            manuscript_sample_used=bool(brief_override["sourceRef"].get("manuscriptSampleUsed", False)),
        )
        brief = ArtBrief(
            concept=brief_override["concept"],
            subject=brief_override["subject"],
            composition=brief_override["composition"],
            palette=tuple(brief_override["palette"]),
            lighting=brief_override["lighting"],
            medium=brief_override["medium"],
            mood=brief_override["mood"],
            genre_signals=tuple(brief_override["genreSignals"]),
            type_zone=brief_override["typeZone"],
            negative=tuple(brief_override["negative"]),
            source_ref=source_ref,
        )
    except (KeyError, ValueError) as exc:
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"invalid ArtBrief payload: {exc}")

    artifact = _put_json(ctx, "art-brief", brief.to_dict())
    return StageResult(artifacts=[artifact], metrics={"manuscript_sample_used": float(source_ref.manuscript_sample_used)})


# ── cover-art ────────────────────────────────────────────────────


@stage(
    name="cover-art",
    version=1,
    inputs={"brief": "art-brief/1"},
    outputs={"images": "cover-art/1", "provenance": "art-provenance/1"},
    terminal=True,   # provenance ships in the delivery package (COVER_DESIGN.md §9)
    toolchain=["image-gen-adapter"],
    fixtures=None,
    root_inputs=["brief", "tier", "adapter"],
    memory_budget_mb=512,
    queue="q.external",
    description="Fan out ArtBrief across a tiered model panel via ImageGenPort. Page-count-free.",
)
def cover_art_stage(
    ctx: StageCtx,
    brief: dict | None = None,
    tier: str = "basic",
    adapter: ImageGenPort | None = None,
) -> StageResult:
    """
    Dispatch the ArtBrief to every model in the resolved tier panel. Each dispatch is
    capability-checked against the pinned catalogue before it's sent (art_policy.py) —
    an invalid tier definition surfaces here as BAD_INPUT, never as a runtime 400.
    """
    if not brief:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="brief is required")
    if adapter is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="adapter (ImageGenPort) is required")

    panel = resolve_tier_panel(tier)
    if not panel.ok:
        raise StageError(
            kind=ErrorKind.POLICY_VIOLATION,
            message=f"tier {tier!r} failed capability validation",
            diagnostics=[Diagnostic(code="art-policy-invalid", severity="error",
                                     human_message=e) for e in panel.errors],
        )

    art_brief = ArtBrief(
        concept=brief["concept"], subject=brief["subject"], composition=brief["composition"],
        palette=tuple(brief["palette"]), lighting=brief["lighting"], medium=brief["medium"],
        mood=brief["mood"], genre_signals=tuple(brief["genreSignals"]),
        type_zone=brief["typeZone"], negative=tuple(brief["negative"]),
        source_ref=SourceRef(
            title_meta_hash=brief["sourceRef"]["titleMetaHash"],
            design_spec_hash=brief["sourceRef"]["designSpecHash"],
            manuscript_sample_used=bool(brief["sourceRef"].get("manuscriptSampleUsed", False)),
        ),
    )

    artifacts: list[StageArtifactRef] = []
    provenance_records: list[dict] = []
    total_cost = 0.0
    cas = _cas(ctx)

    # The ArtBrief's own hash — the provenance chain's link back from image to brief.
    # Must be computed from the brief, NOT from the image that the brief produced.
    brief_bytes = json.dumps(art_brief.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    art_brief_hash = "sha256:" + hashlib.sha256(brief_bytes).hexdigest()

    # Billing unit is a property of the (model, provider) endpoint, not a constant:
    # `image` is flat-rate, `megapixel` scales with pixels, `token` with resolution.
    # Recording a hardcoded "image" would misreport cost provenance for 18 of the
    # catalogue's endpoints (COVER_DESIGN.md §3, the billing-unit trap).
    caps = capability_index(load_catalogue())

    for spec in panel.dispatches:
        prompt = render_prompt(art_brief, dialect=dialect_for_model(spec.model_id))
        request = ImageGenRequest(
            model_id=spec.model_id,
            provider_slug=spec.provider_slug,
            prompt=prompt,
            aspect_ratio=spec.aspect_ratio,
            resolution=spec.resolution,
            n=spec.n,
            seed=spec.seeds[0] if spec.seeds else None,
        )
        result = adapter.generate(request)
        total_cost += result.cost_usd

        for image in result.images:
            raw = base64.b64decode(image.b64_json)
            ref = cas.put(raw, media_type=MediaType(image.media_type))
            artifacts.append(StageArtifactRef(
                kind="cover-art", hash=str(ref.hash), media_type=image.media_type, size=len(raw),
            ))
            cap = caps.get((spec.model_id, spec.provider_slug))
            provenance_records.append({
                "schema": "art-provenance/1",
                "modelId": spec.model_id,
                "canonicalSlug": spec.model_id,  # resolved at the adapter layer in production
                "providerSlug": result.resolved_provider_slug,
                "billingUnit": cap.billing_unit if cap else "token",
                "costUsd": result.cost_usd / max(len(result.images), 1),
                "artBriefHash": art_brief_hash,
                "imageHash": "sha256:" + str(ref.hash),
                "seed": spec.seeds[0] if spec.seeds else None,
                "replayClass": "seeded" if spec.seeds else "artifact-only",
                "resolution": spec.resolution or "n/a",
                "aspectRatio": spec.aspect_ratio,
                "sampleUsed": art_brief.source_ref.manuscript_sample_used,
                # Derived from the cache key, never wall-clock: an artifact whose bytes
                # embed `datetime.now()` hashes differently on every run, which breaks
                # the byte-identical-rebuild contract (ARCHITECTURE.md §2.5, D5).
                "createdAt": _deterministic_timestamp(ctx),
            })

    provenance_artifact = _put_json(ctx, "art-provenance", {"records": provenance_records})
    artifacts.append(provenance_artifact)

    return StageResult(
        artifacts=artifacts,
        metrics={"images_generated": float(len(artifacts) - 1), "total_cost_usd": round(total_cost, 4)},
    )


# ── cover-judge ──────────────────────────────────────────────────


@stage(
    name="cover-judge",
    version=1,
    inputs={"images": "cover-art/1"},
    outputs={"ranking": "art-ranking/1"},
    terminal=True,   # ranking is consumed by Human Gate 3, not another stage
    toolchain=["inference-gateway"],
    fixtures=None,
    root_inputs=["images", "raster_metrics_by_image", "panel_verdicts"],
    memory_budget_mb=256,
    queue="q.external",
    description="Deterministic gates first, cross-vendor vision panel second, ranking only.",
)
def cover_judge_stage(
    ctx: StageCtx,
    images: list[str] | None = None,
    raster_metrics_by_image: dict[str, dict] | None = None,
    panel_verdicts: list[dict] | None = None,
    quorum: int = 3,
) -> StageResult:
    """
    Apply deterministic gates (COVER_DESIGN.md §8) to every candidate, then aggregate the
    vision panel's pairwise verdicts among survivors. The panel call itself runs through
    the Batch API per platform/routing/policy.yaml's cover-judge route — out of scope for
    this stage function, same boundary as cover-brief.
    """
    if not images:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="images is required")
    raster_metrics_by_image = raster_metrics_by_image or {}
    panel_verdicts = panel_verdicts or []

    survivors: list[str] = []
    eliminated: list[dict] = []
    for image_hash in images:
        m = raster_metrics_by_image.get(image_hash)
        if m is None:
            eliminated.append({"imageHash": image_hash, "reason": "no raster metrics supplied"})
            continue
        metrics = RasterMetrics(
            long_edge_px=m["longEdgePx"],
            type_zone_mean_luminance_l=m["typeZoneMeanLuminanceL"],
            type_zone_luminance_stddev=m["typeZoneLuminanceStddev"],
            assumed_text_luminance_l=m.get("assumedTextLuminanceL", 95.0),
        )
        gate = run_deterministic_gates(metrics)
        if gate.passed:
            survivors.append(image_hash)
        else:
            eliminated.append({"imageHash": image_hash, "reason": gate.reason})

    verdicts = [PairwiseVerdict(winner=v["winner"], model_id=v["modelId"], provider_slug=v["providerSlug"])
                for v in panel_verdicts]
    panel_result = aggregate_panel(verdicts, quorum=quorum)

    ranking = {
        "schema": "art-ranking/1",
        "survivors": survivors,
        "eliminated": eliminated,
        "panelWinner": panel_result.winner,
        "quorumMet": panel_result.quorum_met,
        "voteCount": len(panel_result.votes),
    }
    artifact = _put_json(ctx, "art-ranking", ranking)
    return StageResult(
        artifacts=[artifact],
        metrics={"survivors": float(len(survivors)), "eliminated": float(len(eliminated))},
        warnings=[Diagnostic(code="no-quorum", severity="warning",
                              human_message="Panel verdict below quorum — do not present a ranking")]
        if not panel_result.quorum_met and panel_verdicts else [],
    )


# ── cover-compose ────────────────────────────────────────────────


@stage(
    name="cover-compose",
    version=1,
    inputs={
        "selected_art": "cover-art/1",
        "cover_geometry": "cover-geometry/1",
        "designspec": "designspec/1",
        "title_meta": "title-meta/1",
    },
    # Distinct from paginate's `raw-pdf/1`: the cover and the interior are two separate
    # PDF lineages. Sharing the kind made `raw-pdf/1` have two producers, so the derived
    # DAG bound downstream consumers to whichever stage happened to run first.
    outputs={"pdf": "cover-raw-pdf/1"},
    toolchain=["render-engine"],
    fixtures=None,
    # designspec/1 and title-meta/1 have no producing stage in this registry — they are
    # supplied by the caller. Declaring them keeps check_integrity() from reporting two
    # unsatisfiable_input errors (ARCHITECTURE.md §2.15, DAG integrity is CI-blocking).
    root_inputs=["designspec", "title_meta"],
    memory_budget_mb=512,
    queue="q.render.html",
    description=(
        "Joins selected art with the interior's real geometry (from the `cover` stage, "
        "stages/prepress_stages.py) and composes type DETERMINISTICALLY. No image model "
        "ever renders title/author/series text — COVER_DESIGN.md §5."
    ),
)
def cover_compose_stage(
    ctx: StageCtx,
    selected_art: str | None = None,
    cover_geometry: dict | None = None,
    designspec: dict | None = None,
    title_meta: dict | None = None,
) -> StageResult:
    """
    Placeholder for the deterministic type-setting compositor. In production this
    renders art + geometry + DesignSpec.cover tokens through the same emitter path as
    the interior (ARCHITECTURE.md §2.7). The tracer-bullet version records the join.
    """
    if not selected_art:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="selected_art is required")
    if not cover_geometry:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="cover_geometry is required")

    compose_record = {
        "schema": "cover-compose-record/1",
        "selectedArtHash": selected_art,
        "coverGeometry": cover_geometry,
        "typeSetDeterministically": True,
    }
    artifact = _put_json(ctx, "cover-compose-record", compose_record)
    return StageResult(artifacts=[artifact])
