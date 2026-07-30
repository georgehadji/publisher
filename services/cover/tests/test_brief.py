"""Tests for publisher_cover.brief -- ArtBrief validation and deterministic prompt rendering."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_cover.brief import ArtBrief, SourceRef, render_prompt


def _valid_brief(**overrides) -> ArtBrief:
    defaults = dict(
        concept="A lone figure crossing a frozen lake at dusk.",
        subject="solitary human figure, back to viewer",
        composition="rule-of-thirds",
        palette=("desaturated-blue", "bone-white"),
        lighting="low-key",
        medium="painterly-digital",
        mood="tense",
        genre_signals=("literary-thriller",),
        type_zone="lower-third",
        negative=("text", "readable-typography", "logo"),
        source_ref=SourceRef(
            title_meta_hash="sha256:" + "a" * 64,
            design_spec_hash="sha256:" + "b" * 64,
        ),
    )
    defaults.update(overrides)
    return ArtBrief(**defaults)


class TestArtBriefValidation:
    def test_valid_brief_constructs(self):
        brief = _valid_brief()
        assert brief.schema == "art-brief/1"

    def test_rejects_unknown_composition(self):
        with pytest.raises(ValueError, match="composition"):
            _valid_brief(composition="dutch-angle-extreme")

    def test_rejects_unknown_palette_tone(self):
        with pytest.raises(ValueError, match="palette"):
            _valid_brief(palette=("neon-cyberpunk",))

    def test_rejects_missing_mandatory_negative_tags(self):
        """COVER_DESIGN.md §5: every brief must ban rendered text, regardless of what
        the LLM proposed."""
        with pytest.raises(ValueError, match="text.*readable-typography|negative must include"):
            _valid_brief(negative=("logo", "watermark"))

    def test_rejects_empty_concept(self):
        with pytest.raises(ValueError, match="concept"):
            _valid_brief(concept="")

    def test_rejects_too_many_palette_entries(self):
        with pytest.raises(ValueError, match="palette"):
            _valid_brief(palette=(
                "desaturated-blue", "warm-amber", "bone-white",
                "charcoal", "blood-red", "forest-green",
            ))

    def test_to_dict_matches_schema_field_names(self):
        brief = _valid_brief()
        d = brief.to_dict()
        assert set(d.keys()) == {
            "schema", "concept", "subject", "composition", "palette", "lighting",
            "medium", "mood", "genreSignals", "typeZone", "negative", "sourceRef",
        }
        assert d["sourceRef"]["manuscriptSampleUsed"] is False


class TestRenderPrompt:
    def test_prose_dialect_includes_all_fields(self):
        brief = _valid_brief()
        prompt = render_prompt(brief, dialect="openai")
        assert brief.concept in prompt
        assert "rule of thirds" in prompt
        assert "desaturated blue" in prompt
        assert "text" in prompt.lower()  # negative constraint present

    def test_tag_dialect_is_comma_separated(self):
        brief = _valid_brief()
        prompt = render_prompt(brief, dialect="seedream")
        assert "," in prompt
        assert "negative:" in prompt

    def test_conversational_dialect_addresses_the_model(self):
        brief = _valid_brief()
        prompt = render_prompt(brief, dialect="gemini")
        assert prompt.startswith("Generate a book cover illustration.")

    def test_rendering_is_deterministic(self):
        """Same brief + dialect -> byte-identical prompt. Required for cache-key
        stability (COVER_DESIGN.md §11)."""
        brief = _valid_brief()
        assert render_prompt(brief, "openai") == render_prompt(brief, "openai")

    def test_unknown_dialect_falls_back_to_prose(self):
        brief = _valid_brief()
        assert render_prompt(brief, "nonexistent-vendor") == render_prompt(brief, "generic")

    def test_type_zone_none_omits_zone_instruction(self):
        brief = _valid_brief(type_zone="none")
        prompt = render_prompt(brief, "openai")
        assert "low-detail" not in prompt
