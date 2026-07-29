"""Tests for structure rules engine."""

from publisher_structure.rules import (
    parse_html, classify_blocks, find_low_confidence, 
    build_ast_draft, Block, TextRun, CHAPTER_PATTERNS,
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
    blocks = parse_html(SAMPLE_HTML)
    low_conf = find_low_confidence(blocks, threshold=0.8)
    # Low confidence should be for uncertain items
    assert isinstance(low_conf, list)


def test_build_ast_draft():
    draft = build_ast_draft(SAMPLE_HTML)
    assert draft["schema"] == "ast/1"
    assert len(draft["body"]) >= 1
    assert draft["body"][0]["type"] == "chapter"
    assert draft["body"][0]["attrs"]["title"] == "Chapter 7"


def test_block_properties():
    block = Block(type="paragraph", tag="p", classes=["center", "special"])
    assert block.is_centered
    
    block2 = Block(type="paragraph", tag="p",
                   runs=[TextRun(text="ALL CAPS TEXT HERE")])
    assert block2.is_all_caps
    
    block3 = Block(type="paragraph", tag="p",
                   runs=[TextRun(text="short")])
    assert block3.is_short


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
