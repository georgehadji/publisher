"""
ArtBrief -- the semantic layer between book metadata and per-model image prompts.

From COVER_DESIGN.md §2: two tiers, on purpose.
  - The brief is semantic: one LLM call, cached, human-editable, versioned. Free text is
    confined to `concept`/`subject` -- everything else is a closed enum, mirroring
    LLM_STRATEGY.md §3's prose-contamination discipline (this is never the author's
    prose; it describes cover IMAGERY).
  - The prompt is a DETERMINISTIC render of brief x model dialect, versioned like a
    stage. Adding a model to the panel never costs a second LLM call.

Enum sets below mirror schemas/cover/art-brief.schema.json exactly. Validated by hand
against closed sets (no jsonschema/pydantic dependency in this service, matching the
rest of the codebase -- see platform/cas/py/publisher_cas/types.py's Sha256 for the same
"parse, don't validate" pattern applied to a scalar).
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_VERSION = "art-brief/1"
PROMPT_TEMPLATE_VERSION = 1  # bump on ANY change to render_prompt's output shape

COMPOSITIONS = frozenset({
    "rule-of-thirds", "centered", "symmetrical", "diagonal",
    "negative-space-dominant", "full-bleed-texture", "silhouette",
    "close-crop", "wide-establishing",
})
PALETTE_TONES = frozenset({
    "desaturated-blue", "warm-amber", "bone-white", "charcoal", "blood-red",
    "forest-green", "dusty-rose", "deep-violet", "muted-ochre", "stark-black",
    "cool-teal", "burnt-sienna", "pale-gold", "slate-grey", "ivory",
})
LIGHTINGS = frozenset({
    "low-key", "high-key", "backlit", "golden-hour", "overcast-flat",
    "hard-noon", "candlelit", "moonlit", "studio-soft", "harsh-fluorescent",
})
MEDIUMS = frozenset({
    "painterly-digital", "photographic", "watercolor", "linocut", "collage",
    "flat-vector", "gouache", "ink-wash", "oil-painting", "risograph", "3d-render",
})
MOODS = frozenset({
    "tense", "melancholic", "whimsical", "ominous", "hopeful", "nostalgic",
    "clinical", "romantic", "cold", "urgent", "serene",
})
GENRE_SIGNALS = frozenset({
    "literary-thriller", "cozy-mystery", "epic-fantasy", "hard-scifi", "romance",
    "literary-fiction", "memoir", "horror", "historical-fiction", "ya-contemporary",
    "business-nonfiction", "self-help", "true-crime", "poetry",
})
TYPE_ZONES = frozenset({"upper-third", "lower-third", "center-band", "none"})
NEGATIVE_TAGS = frozenset({
    "text", "readable-typography", "logo", "watermark", "photorealistic-face",
    "real-person-likeness", "brand-marks", "signature", "clutter",
    "low-contrast-center", "extra-limbs", "distorted-anatomy",
})

# COVER_DESIGN.md §5: no image model ever renders text. Every brief carries these two,
# regardless of what the LLM proposed -- enforced here, not left to prompt discipline.
MANDATORY_NEGATIVE_TAGS = frozenset({"text", "readable-typography"})


@dataclass(frozen=True)
class SourceRef:
    title_meta_hash: str
    design_spec_hash: str
    manuscript_sample_used: bool = False


@dataclass(frozen=True)
class ArtBrief:
    """Closed-enum cover-art direction. Validates its own invariants at construction."""
    concept: str
    subject: str
    composition: str
    palette: tuple[str, ...]
    lighting: str
    medium: str
    mood: str
    genre_signals: tuple[str, ...]
    type_zone: str
    negative: tuple[str, ...]
    source_ref: SourceRef
    schema: str = field(default=SCHEMA_VERSION)

    def __post_init__(self) -> None:
        errors: list[str] = []

        if self.schema != SCHEMA_VERSION:
            errors.append(f"schema must be {SCHEMA_VERSION!r}, got {self.schema!r}")
        if not (1 <= len(self.concept) <= 500):
            errors.append("concept must be 1-500 chars")
        if not (1 <= len(self.subject) <= 300):
            errors.append("subject must be 1-300 chars")
        if self.composition not in COMPOSITIONS:
            errors.append(f"composition {self.composition!r} not in closed enum")
        if not self.palette or not (1 <= len(self.palette) <= 5):
            errors.append("palette must have 1-5 entries")
        elif not set(self.palette) <= PALETTE_TONES:
            errors.append(f"palette contains unknown tone(s): {set(self.palette) - PALETTE_TONES}")
        if self.lighting not in LIGHTINGS:
            errors.append(f"lighting {self.lighting!r} not in closed enum")
        if self.medium not in MEDIUMS:
            errors.append(f"medium {self.medium!r} not in closed enum")
        if self.mood not in MOODS:
            errors.append(f"mood {self.mood!r} not in closed enum")
        if not self.genre_signals or not (1 <= len(self.genre_signals) <= 3):
            errors.append("genreSignals must have 1-3 entries")
        elif not set(self.genre_signals) <= GENRE_SIGNALS:
            errors.append(f"genreSignals contains unknown value(s): {set(self.genre_signals) - GENRE_SIGNALS}")
        if self.type_zone not in TYPE_ZONES:
            errors.append(f"typeZone {self.type_zone!r} not in closed enum")
        if not self.negative:
            errors.append("negative must be non-empty")
        elif not set(self.negative) <= NEGATIVE_TAGS:
            errors.append(f"negative contains unknown tag(s): {set(self.negative) - NEGATIVE_TAGS}")
        elif not MANDATORY_NEGATIVE_TAGS <= set(self.negative):
            errors.append(
                f"negative must include {sorted(MANDATORY_NEGATIVE_TAGS)} "
                "(COVER_DESIGN.md §5 — no model ever renders text)"
            )

        if errors:
            raise ValueError("ArtBrief invalid: " + "; ".join(errors))

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "concept": self.concept,
            "subject": self.subject,
            "composition": self.composition,
            "palette": list(self.palette),
            "lighting": self.lighting,
            "medium": self.medium,
            "mood": self.mood,
            "genreSignals": list(self.genre_signals),
            "typeZone": self.type_zone,
            "negative": list(self.negative),
            "sourceRef": {
                "titleMetaHash": self.source_ref.title_meta_hash,
                "designSpecHash": self.source_ref.design_spec_hash,
                "manuscriptSampleUsed": self.source_ref.manuscript_sample_used,
            },
        }


# ── Deterministic per-model prompt rendering (COVER_DESIGN.md §2) ──────────────


_TYPE_ZONE_PHRASE = {
    "upper-third": "leave the upper third of the frame low-detail and uncluttered",
    "lower-third": "leave the lower third of the frame low-detail and uncluttered",
    "center-band": "leave a horizontal band across the center low-detail and uncluttered",
    "none": "",
}


def _prose_dialect(brief: ArtBrief) -> str:
    """OpenAI/Riverflow-style: a flowing scene description."""
    zone = _TYPE_ZONE_PHRASE[brief.type_zone]
    parts = [
        brief.concept,
        f"Subject: {brief.subject}.",
        f"Composition: {brief.composition.replace('-', ' ')}.",
        f"Palette: {', '.join(t.replace('-', ' ') for t in brief.palette)}.",
        f"Lighting: {brief.lighting.replace('-', ' ')}.",
        f"Medium: {brief.medium.replace('-', ' ')}.",
        f"Mood: {brief.mood}.",
    ]
    if zone:
        parts.append(zone.capitalize() + ".")
    parts.append("Do not include: " + ", ".join(t.replace("-", " ") for t in brief.negative) + ".")
    return " ".join(parts)


def _tag_dialect(brief: ArtBrief) -> str:
    """Seedream/Recraft-style: a comma-separated tag stack."""
    tags = [
        brief.subject,
        brief.composition,
        *brief.palette,
        brief.lighting,
        brief.medium,
        brief.mood,
        *brief.genre_signals,
    ]
    zone = _TYPE_ZONE_PHRASE[brief.type_zone]
    if zone:
        tags.append(zone)
    return ", ".join(tags) + " || negative: " + ", ".join(brief.negative)


def _conversational_dialect(brief: ArtBrief) -> str:
    """Gemini-style: an instruction, addressed to the model."""
    zone = _TYPE_ZONE_PHRASE[brief.type_zone]
    zone_sentence = f" Please {zone}." if zone else ""
    return (
        f"Generate a book cover illustration. {brief.concept} "
        f"Focus on {brief.subject}, composed as {brief.composition.replace('-', ' ')}, "
        f"in a {brief.medium.replace('-', ' ')} style with {brief.lighting.replace('-', ' ')} "
        f"lighting and a {brief.mood} mood. Use this palette: "
        f"{', '.join(t.replace('-', ' ') for t in brief.palette)}.{zone_sentence} "
        f"Avoid: {', '.join(t.replace('-', ' ') for t in brief.negative)}."
    )


_DIALECTS = {
    "openai": _prose_dialect,
    "riverflow": _prose_dialect,
    "seedream": _tag_dialect,
    "recraft": _tag_dialect,
    "gemini": _conversational_dialect,
    "flux": _tag_dialect,
    "krea": _tag_dialect,
    "grok": _prose_dialect,
    "mai": _prose_dialect,
    "generic": _prose_dialect,
}

# Map an OpenRouter model slug to its prompt dialect.
#
# The obvious `model_id.split("/")[0]` is WRONG and fails silently: OpenRouter slugs are
# `vendor/model`, and the vendor half is almost never the model family. It yields
# "bytedance-seed" for seedream, "sourceful" for riverflow, "black-forest-labs" for flux
# and "google" for gemini -- none of which are dialect keys, so four of five panel models
# silently received the prose dialect instead of their own. Match on the MODEL half.
_MODEL_FAMILY_MARKERS = (
    ("seedream", "seedream"),
    ("riverflow", "riverflow"),
    ("gemini", "gemini"),
    ("recraft", "recraft"),
    ("flux", "flux"),
    ("krea", "krea"),
    ("grok", "grok"),
    ("mai-image", "mai"),
    ("gpt-image", "openai"),
    ("gpt-5", "openai"),
)


def dialect_for_model(model_id: str) -> str:
    """
    Resolve an OpenRouter model slug (`vendor/model`) to a prompt dialect key.

    Raises ValueError for an unrecognised model rather than defaulting. A silent default
    is exactly what let the `.split("/")[0]` bug ship unnoticed: every model appeared to
    work while four of five were handed the wrong prompt shape. Adding a model to
    art_policy_tiers.yaml must therefore be a deliberate decision about how to prompt it.
    """
    model_half = model_id.split("/", 1)[-1].lower()
    for marker, dialect in _MODEL_FAMILY_MARKERS:
        if marker in model_half:
            return dialect
    raise ValueError(
        f"no prompt dialect registered for model {model_id!r}; "
        f"add its family to _MODEL_FAMILY_MARKERS in publisher_cover.brief"
    )


def render_prompt(brief: ArtBrief, dialect: str = "generic") -> str:
    """
    Deterministically render `brief` into a per-model-family prompt string. Versioned by
    PROMPT_TEMPLATE_VERSION -- a template change must bump it, since it is a cache-key
    input for cover-art (the rendered prompt, not the brief object, is what gets sent).
    """
    renderer = _DIALECTS.get(dialect, _prose_dialect)
    return renderer(brief)
