"""Tests for structure rules engine."""

from publisher_structure.rules import (
    parse_html, classify_blocks, find_low_confidence,
    build_ast_draft, Block, TextRun, CHAPTER_PATTERNS,
    compute_text_integrity, normalize_text,
)


SAMPLE_HTML = """<!DOCTYPE html>
<html><body>
<h1 class="chapter-title">Chapter 7</h1>
<p class="chapter-opening">It had been a long time since anyone visited.</p>
<p>She walked through the door and into the darkness beyond.</p>
<h2>A Section Heading</h2>
<p>More text here about something important.</p>
<blockquote class="epigraph">A short quote.</blockquote>
<p>And then the story continues.</p>
<hr class="scene-break" />
<p>After the break, things were different.</p>
<pre class="code-block">def hello():
    print("hi")</pre>
<div class="verse">
<p class="verse-line">The ink flows freely</p>
<p class="verse-line">From the pen.</p>
</div>
</body></html>"""


def test_parse_html_basic():
    blocks = parse_html(SAMPLE_HTML)
    assert len(blocks) > 0
    assert any(b.type == "heading" for b in blocks)
    assert any(b.type == "paragraph" for b in blocks)
    assert any(b.type == "blockquote" for b in blocks)
    assert any(b.type == "code" for b in blocks)


def test_parse_html_chapter_title():
    blocks = parse_html(SAMPLE_HTML)
    chapter_h1 = None
    for b in blocks:
        if b.tag == "h1" and "chapter-title" in b.classes:
            chapter_h1 = b
            break
    assert chapter_h1 is not None
    assert chapter_h1.text.strip() == "Chapter 7"


def test_classify_chapter_title():
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    
    chapter_titles = [c for c in classifications if c.classification == "chapter-title"]
    assert len(chapter_titles) >= 1
    assert chapter_titles[0].confidence >= 0.85


def test_classify_paragraphs():
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    
    paras = [c for c in classifications if c.classification == "paragraph"]
    assert len(paras) >= 1


def test_classify_chapter_opening():
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    
    openings = [c for c in classifications if c.classification == "chapter-opening"]
    assert len(openings) == 1
    assert openings[0].confidence >= 0.9


def test_classify_blockquote():
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    
    bq = [c for c in classifications if c.classification == "blockquote" or "epigraph" in c.classification]
    assert len(bq) >= 1


def test_classify_scene_break():
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    
    breaks = [c for c in classifications if c.classification == "scene-break"]
    assert len(breaks) >= 1


def test_find_low_confidence():
    """`isinstance(low_conf, list)` is guaranteed by the list comprehension inside
    find_low_confidence and cannot fail. Assert the threshold is actually applied."""
    blocks = parse_html(SAMPLE_HTML)
    classifications = classify_blocks(blocks)
    low_conf = find_low_confidence(blocks, threshold=0.8)
    expected = [c.block_index for c in classifications if c.confidence < 0.8]
    assert low_conf == expected
    # Raising the threshold can only widen the set; lowering it can only narrow it.
    assert set(low_conf) <= set(find_low_confidence(blocks, threshold=0.95))
    assert find_low_confidence(blocks, threshold=0.0) == []


def test_plain_paragraphs_are_not_escalated():
    """
    The cost guard. LLM_STRATEGY.md §5 budgets ~7% of nodes for model escalation; a
    plain <p> must clear the 0.8 threshold on rules alone.

    Regression test for a real bug: standard paragraphs were assigned confidence=0.7
    against a 0.8 threshold, so every body paragraph in the manuscript was queued for
    the LLM — inverting the ~93%/~7% split and blowing the <=$0.20/novel target by
    roughly 10x. The previous assertion here (`isinstance(low_conf, list)`) passed
    throughout.
    """
    html = "<p>" + "</p><p>".join(f"Ordinary body sentence number {i}." for i in range(20)) + "</p>"
    blocks = parse_html(html)
    low_conf = find_low_confidence(blocks, threshold=0.8)
    assert low_conf == [], (
        f"{len(low_conf)}/{len(blocks)} plain paragraphs were flagged for LLM "
        f"escalation; plain body text must be resolved by rules alone"
    )


def test_escalation_rate_stays_within_budget():
    """Across a mixed document, escalated nodes must stay a small minority."""
    classifications = classify_blocks(parse_html(SAMPLE_HTML))
    escalated = [c for c in classifications if c.confidence < 0.8]
    assert len(escalated) / len(classifications) <= 0.25, (
        f"escalation rate {len(escalated)}/{len(classifications)} exceeds the 25% "
        f"ceiling in BUILD_PLAN.md §0 ('Nodes escalated past tier 1 | <= 25%')"
    )


def test_ast_draft_preserves_every_block_after_a_skipped_container():
    """
    `classify_blocks` skips structural containers (an empty <div class="verse">), so it
    returns FEWER classifications than blocks. `build_ast_draft` used to pair them with
    `zip(blocks, classifications)`, which is positional — so every block after the skip
    took a later block's classification, and the trailing block was truncated away
    entirely. Pair by `classification.block_index` instead.
    """
    draft = build_ast_draft(SAMPLE_HTML)
    texts = [n["text"] for n in draft["body"][0]["content"]]

    # The final verse line sits after the skipped <div class="verse"> container and was
    # the block that zip() silently dropped.
    assert "From the pen." in texts, (
        f"trailing block lost from the AST; content ends with {texts[-1]!r}"
    )

    # And the pairing must be correct, not merely complete: the code block's text must
    # be classified as code, not as whatever followed it.
    code_nodes = [n for n in draft["body"][0]["content"] if 'print("hi")' in n["text"]]
    assert code_nodes, "code block missing from the AST"


def test_build_ast_draft():
    draft = build_ast_draft(SAMPLE_HTML)
    assert draft["schema"] == "ast/1"
    assert len(draft["body"]) >= 1
    assert draft["body"][0]["type"] == "chapter"
    assert draft["body"][0]["attrs"]["title"] == "Chapter 7"


def test_verse_lines_survive_parse_and_ast_draft():
    """Regression test: back-to-back same-tag siblings inside a container
    must not lose text, and verse-line paragraphs must classify as verse."""
    blocks = parse_html(SAMPLE_HTML)
    verse_texts = [b.text.strip() for b in blocks if "verse-line" in b.classes]
    assert verse_texts == ["The ink flows freely", "From the pen."]

    draft = build_ast_draft(SAMPLE_HTML)
    verse_nodes = [c for c in draft["body"][0]["content"] if c["type"] == "verse"]
    assert [v["text"] for v in verse_nodes] == ["The ink flows freely", "From the pen."]


def test_block_properties():
    block = Block(type="paragraph", tag="p", classes=["center", "special"])
    assert block.is_centered
    
    block2 = Block(type="paragraph", tag="p",
                   runs=[TextRun(text="ALL CAPS TEXT HERE")])
    assert block2.is_all_caps
    
    block3 = Block(type="paragraph", tag="p",
                   runs=[TextRun(text="short")])
    assert block3.is_short


def test_compute_text_integrity_passes_for_matching_text():
    """
    The base case: a draft built from HTML must round-trip against that HTML.

    Historically this avoided SAMPLE_HTML's `<div class="verse">` block, because
    `TypescriptHTMLParser` dropped the second consecutive `<p class="verse-line">`
    sibling (a stray unflushed text buffer survived an early return in
    `_save_previous_block()` when `_current_tag` was already empty). That parser bug
    is FIXED — `_save_previous_block` now preserves meaningful orphan text — so the
    flat fixture below is kept only because it isolates the integrity gate from
    parser behaviour. See test_verse_lines_survive_parsing for the regression cover.
    """
    flat_html = """<h1 class="chapter-title">Chapter 7</h1>
<p class="chapter-opening">It had been a long time since anyone visited.</p>
<p>She walked through the door and into the darkness beyond.</p>
<h2>A Section Heading</h2>
<p>More text here about something important.</p>"""
    draft = build_ast_draft(flat_html)
    result = compute_text_integrity(flat_html, draft)
    assert result["passed"] is True, result


def test_compute_text_integrity_detects_dropped_paragraph():
    """
    The gate's entire reason to exist: an AST missing source text must fail, not
    silently report success.

    Regression test for the original bug — `compute_text_integrity` computed a hash
    of the HTML alone, never read `ast_draft`, and its own docstring called it
    "Dummy: in production this compares against AST". `passed` could not be False.
    """
    draft = build_ast_draft(SAMPLE_HTML)
    html_missing_a_paragraph = SAMPLE_HTML.replace(
        "<p>And then the story continues.</p>", ""
    )
    result = compute_text_integrity(html_missing_a_paragraph, draft)
    assert result["passed"] is False
    assert "firstDivergenceAt" in result


def test_compute_text_integrity_detects_wrong_chapter_title():
    """
    The chapter title lives in `attrs.title`, not as a body text node. The original
    AST-side extractor only walked `content[].text`, so a title mismatch was
    invisible to it — this is a completeness regression test for that gap, not just
    the mismatch-detection test above.
    """
    draft = build_ast_draft(SAMPLE_HTML)
    draft["body"][0]["attrs"]["title"] = "A Completely Different Title"
    result = compute_text_integrity(SAMPLE_HTML, draft)
    assert result["passed"] is False


def test_normalize_text_folds_smart_quotes_and_dashes():
    straight_form = normalize_text("“don't” — she said")   # straight ' , em dash
    curly_form = normalize_text("“don’t” – she said")  # curly ' , en dash
    assert straight_form == curly_form


def test_normalize_text_is_nfc():
    # "e" + combining acute (NFD) vs precomposed "é" (NFC) must compare equal.
    nfd = normalize_text("café")
    nfc = normalize_text("café")
    assert nfd == nfc


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a  b\n\tc") == "a b c"


def test_chapter_patterns():
    import re
    for pattern in CHAPTER_PATTERNS:
        if pattern.match("Chapter 7"):
            break
    else:
        assert False, "No pattern matched 'Chapter 7'"
    
    for pattern in CHAPTER_PATTERNS:
        if pattern.match("Prologue"):
            break
    else:
        assert False, "No pattern matched 'Prologue'"


def test_verse_lines_survive_parsing():
    """
    Regression: `_save_previous_block()` returned early when `_current_tag` was empty,
    silently discarding text buffered between sibling tags. Two consecutive
    `<p class="verse-line">` inside a `<div class="verse">` lost the second line — text
    vanishing before the integrity gate ever saw it.
    """
    draft = build_ast_draft(SAMPLE_HTML)
    texts = [n["text"] for n in draft["body"][0]["content"]]
    assert any("The ink flows freely" in t for t in texts)
    assert any("From the pen." in t for t in texts), "second verse line lost in parsing"


def test_verse_lines_are_classified_as_verse():
    """A `verse-line` paragraph must not fall through to the body-paragraph branch."""
    classifications = classify_blocks(parse_html(SAMPLE_HTML))
    verse = [c for c in classifications if c.classification == "verse"]
    assert len(verse) >= 2, f"expected both verse lines classified, got {len(verse)}"
    assert all(c.confidence >= 0.8 for c in verse)   # never escalated to the LLM
