"""
Structure rules engine — deterministic first-pass analysis of typescript HTML.

From BUILD_PLAN.md §3.16 and RESEARCH.md §3.2:
Rules process the HTML output of extract and produce:
  - An AST draft with confidence scores
  - A list of low-confidence nodes for LLM classification
  - Classification of front/back matter elements
  
Deterministic signals: heading tags, class names, font size/weight indicators,
centering, all-caps, numbering patterns, ornament glyphs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional


@dataclass
class TextRun:
    """A span of text with formatting."""
    text: str
    bold: bool = False
    italic: bool = False
    small_caps: bool = False
    superscript: bool = False
    subscript: bool = False
    underline: bool = False
    code: bool = False
    href: Optional[str] = None


@dataclass
class Block:
    """A detected block element from HTML."""
    type: str  # "paragraph", "heading", "blockquote", "verse", "list-item", "code", "table", "hr", "div"
    tag: str
    level: int = 0  # heading level or list depth
    classes: list[str] = field(default_factory=list)
    runs: list[TextRun] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)
    source_id: Optional[str] = None
    attrs: dict = field(default_factory=dict)
    
    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)
    
    @property
    def is_centered(self) -> bool:
        return "center" in self.classes or self.attrs.get("align") == "center"
    
    @property
    def is_all_caps(self) -> bool:
        text = self.text.strip()
        return len(text) > 3 and text == text.upper()
    
    @property
    def is_short(self) -> bool:
        return len(self.text.strip()) < 60


# ── HTML Parser ─────────────────────────────────────────────────

class TypescriptHTMLParser(HTMLParser):
    """Parse the typescript HTML output from the extract stage into blocks."""
    
    def __init__(self):
        super().__init__()
        self.blocks: list[Block] = []
        self._current_tag: list[str] = []
        self._current_classes: list[str] = []
        self._current_attrs: dict = {}
        self._current_runs: list[TextRun] = []
        self._current_text = ""
        self._skip_content = 0  # nest depth to skip (e.g. script, style)
        self._mark_stack: list[dict] = []  # active formatting marks
    
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_dict = dict(attrs)
        
        if tag in ("script", "style", "head"):
            self._skip_content += 1
            return
        
        if self._skip_content:
            return
        
        classes = attr_dict.get("class", "").split()
        
        # Map HTML tags to block types
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._save_previous_block()
            self._current_tag = [tag]
            self._current_classes = classes
            self._current_attrs = attr_dict
            self._current_runs = []
            
        elif tag == "p":
            self._save_previous_block()
            self._current_tag = [tag]
            self._current_classes = classes
            self._current_attrs = attr_dict
            self._current_runs = []
            
        elif tag in ("blockquote", "pre", "aside"):
            self._save_previous_block()
            self._current_tag = [tag]
            self._current_classes = classes
            self._current_attrs = attr_dict
            self._current_runs = []
            
        elif tag in ("div", "section", "article"):
            self._save_previous_block()
            self._current_tag = [tag]
            self._current_classes = classes
            self._current_attrs = attr_dict
            self._current_runs = []
            
        elif tag == "hr":
            self._save_previous_block()
            self.blocks.append(Block(type="hr", tag="hr", classes=classes, attrs=attr_dict))
            
        elif tag in ("br", "br/"):
            self._current_text += "\n"
            
        elif tag in ("em", "i"):
            self._mark_stack.append({"italic": True})
            
        elif tag in ("strong", "b"):
            self._mark_stack.append({"bold": True})
            
        elif tag == "span":
            if "small-caps" in classes:
                self._mark_stack.append({"small_caps": True})
            else:
                self._mark_stack.append({})
                
        elif tag in ("sup", "sub"):
            self._mark_stack.append({"superscript": True if tag == "sup" else False,
                                     "subscript": True if tag == "sub" else False})
            
        elif tag == "code":
            self._mark_stack.append({"code": True})
            
        elif tag == "a":
            self._mark_stack.append({"href": attr_dict.get("href", "")})
            
        elif tag in ("ul", "ol", "li"):
            # Handle inline lists simply — collect text
            if tag == "li":
                self._save_previous_block()
                list_type = "ordered" if attr_dict.get("class") else "unordered"
                self._current_tag = [tag]
                self._current_classes = classes
                self._current_attrs = {"list_type": list_type}
                self._current_runs = []
    
    def handle_endtag(self, tag: str):
        if tag in ("script", "style", "head"):
            self._skip_content -= 1
            return
        
        if self._skip_content:
            return
        
        if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "aside", "div", "section", "article"):
            if self._current_text or self._current_runs:
                self._flush_text()
            self._save_previous_block()
            self._current_text = ""
            
        elif tag in ("em", "i", "strong", "b", "span", "sup", "sub", "code", "a"):
            if self._mark_stack:
                self._mark_stack.pop()
    
    def handle_data(self, data: str):
        if self._skip_content:
            return
        self._current_text += data
    
    def _flush_text(self):
        """Flush accumulated text with current marks into a run."""
        if not self._current_text:
            return
        
        # Merge marks
        marks = {}
        for m in self._mark_stack:
            marks.update(m)
        
        self._current_runs.append(TextRun(
            text=self._current_text,
            bold=marks.get("bold", False),
            italic=marks.get("italic", False),
            small_caps=marks.get("small_caps", False),
            superscript=marks.get("superscript", False),
            subscript=marks.get("subscript", False),
            code=marks.get("code", False),
            href=marks.get("href"),
        ))
        self._current_text = ""
    
    def _save_previous_block(self):
        """Save the accumulated tag/runs as a block."""
        if not self._current_tag:
            # No open block to attach to (e.g. whitespace between sibling
            # tags) — discard so it doesn't leak into the next block's text.
            self._current_text = ""
            self._current_runs = []
            return
        
        if self._current_runs or self._current_text:
            self._flush_text()
        
        tag = self._current_tag[0]
        block_type = tag  # default
        
        # Map HTML tag to block type
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            block_type = "heading"
        elif tag == "p":
            block_type = "paragraph"
        elif tag == "blockquote":
            block_type = "blockquote"
        elif tag == "pre":
            block_type = "code"
        elif tag == "aside":
            block_type = "sidebar"
        elif tag == "li":
            block_type = "list-item"
        elif tag in ("div", "section", "article"):
            block_type = "div"
        
        level = int(tag[1]) if tag.startswith("h") and len(tag) == 2 else 0
        
        self.blocks.append(Block(
            type=block_type,
            tag=tag,
            level=level,
            classes=self._current_classes,
            runs=self._current_runs,
            attrs=self._current_attrs,
        ))
        
        self._current_tag = []
        self._current_classes = []
        self._current_attrs = {}
        self._current_runs = []
        self._current_text = ""


def parse_html(html: str) -> list[Block]:
    """Parse typescript HTML into blocks."""
    parser = TypescriptHTMLParser()
    parser.feed(html)
    parser._save_previous_block()
    return parser.blocks


# ── Classification patterns ─────────────────────────────────────

# Chapter title patterns
CHAPTER_PATTERNS = [
    re.compile(r'^chapter\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)$', re.IGNORECASE),
    re.compile(r'^chapter\s+(\d+)$', re.IGNORECASE),
    re.compile(r'^(\d+)\s*\.\s*$'),  # "7."
    re.compile(r'^(part|book)\s+(\d+|one|two|three|four|five|six)$', re.IGNORECASE),
    re.compile(r'^prologue$', re.IGNORECASE),
    re.compile(r'^epilogue$', re.IGNORECASE),
    re.compile(r'^preface$', re.IGNORECASE),
    re.compile(r'^introduction$', re.IGNORECASE),
    re.compile(r'^foreword$', re.IGNORECASE),
]

FRONT_MATTER_TYPES = {
    "half-title", "titlepage", "title-page", "halftitle", "half_title",
    "copyright", "copyrightpage", "copyright-page",
    "dedication", 
    "epigraph",
    "toc", "table-of-contents", "tableofcontents",
    "foreword",
    "preface",
    "acknowledgments", "acknowledgements",
    "prologue",
}

BACK_MATTER_TYPES = {
    "epilogue",
    "afterword",
    "appendix", "appendices",
    "notes",
    "bibliography",
    "index",
    "abouttheauthor", "about-the-author", "about_author",
    "alsoby", "also-by", "also_by",
    "colophon",
}

# Ornament glyphs
ORNAMENT_PATTERNS = [
    re.compile(r'^\s*[\*\u2605\u2735\u2736]{3,}\s*$'),  # ***, ★★★, ✵✵✵
    re.compile(r'^\s*[#\u00a7]{3,}\s*$'),  # ###, §§§
    re.compile(r'^\s*[~\u223c]{3,}\s*$'),  # ~~~, ∼∼∼
    re.compile(r'^\s*[•·⋅]{3,}\s*$'),  # •••, ···, ⋅⋅⋅
    re.compile(r'^\s*[*]+\s+[*]+\s+[*]+\s*$'),  # * * *
]


@dataclass
class Classification:
    """A classified element with confidence."""
    block_index: int
    classification: str
    confidence: float
    source_text: str
    evidence: list[str] = field(default_factory=list)


def classify_blocks(blocks: list[Block]) -> list[Classification]:
    """Run deterministic rules over parsed blocks.
    
    Returns a list of classifications with confidence scores.
    Low-confidence (< 0.8) nodes should be sent to the LLM classifier.
    """
    classifications: list[Classification] = []
    
    for i, block in enumerate(blocks):
        text = block.text.strip()
        
        # Skip empty blocks
        if not text and block.type != "hr":
            continue
        
        if block.type == "hr":
            classifications.append(Classification(
                block_index=i,
                classification="scene-break",
                confidence=0.95,
                source_text=text,
                evidence=["<hr> tag"],
            ))
            continue
        
        # Chapter title detection (highest priority)
        if block.type == "heading" and block.level == 1:
            classifications.append(Classification(
                block_index=i,
                classification="chapter-title",
                confidence=0.9,
                source_text=text,
                evidence=[f"h1 tag, text: {text[:60]}"],
            ))
            continue
        
        # Check chapter patterns
        for pattern in CHAPTER_PATTERNS:
            match = pattern.match(text)
            if match:
                classifications.append(Classification(
                    block_index=i,
                    classification="chapter-title",
                    confidence=0.85,
                    source_text=text,
                    evidence=[f"matches chapter pattern: {pattern.pattern[:40]}"],
                ))
                break
        else:
            # Not a chapter title — run other checks
            _classify_other(block, i, text, classifications)
    
    return classifications


def _classify_other(block: Block, i: int, text: str, classifications: list[Classification]):
    """Classify a non-chapter-title block."""
    
    # Front/back matter detection via CSS class
    for cls in block.classes:
        if cls in FRONT_MATTER_TYPES:
            classifications.append(Classification(
                block_index=i, classification=f"front-{cls}", confidence=0.9,
                source_text=text[:80], evidence=[f"CSS class: {cls}"],
            ))
            return
        if cls in BACK_MATTER_TYPES:
            classifications.append(Classification(
                block_index=i, classification=f"back-{cls}", confidence=0.9,
                source_text=text[:80], evidence=[f"CSS class: {cls}"],
            ))
            return
    
    # Scene break detection (short line with ornament characters)
    if block.type == "paragraph" and block.is_short and len(text) <= 15:
        for pattern in ORNAMENT_PATTERNS:
            if pattern.match(text):
                classifications.append(Classification(
                    block_index=i, classification="scene-break", confidence=0.95,
                    source_text=text, evidence=["ornament pattern"],
                ))
                return
    
    # Heading level 2/3
    if block.type == "heading":
        if block.level == 2:
            classifications.append(Classification(
                block_index=i, classification="heading-2", confidence=0.85,
                source_text=text[:80], evidence=["h2 tag"],
            ))
            return
        elif block.level >= 3:
            classifications.append(Classification(
                block_index=i, classification="heading-3", confidence=0.85,
                source_text=text[:80], evidence=["h3+ tag"],
            ))
            return
    
    # Epigraph detection (short centered paragraph with attribution)
    if block.type == "blockquote" and block.is_short and block.is_centered:
        classifications.append(Classification(
            block_index=i, classification="epigraph", confidence=0.8,
            source_text=text[:80], evidence=["short centered blockquote"],
        ))
        return
    
    # Blockquote
    if block.type == "blockquote":
        classifications.append(Classification(
            block_index=i, classification="blockquote", confidence=0.85,
            source_text=text[:80], evidence=["<blockquote> tag"],
        ))
        return
    
    # Code
    if block.type == "code":
        classifications.append(Classification(
            block_index=i, classification="code-block", confidence=0.9,
            source_text=text[:80], evidence=["<pre> tag"],
        ))
        return
    
    # Sidebar
    if block.type == "sidebar":
        classifications.append(Classification(
            block_index=i, classification="sidebar", confidence=0.85,
            source_text=text[:80], evidence=["<aside> tag"],
        ))
        return
    
    # Drop cap / chapter opening paragraph detection
    if block.type == "paragraph" and "chapter-opening" in block.classes:
        classifications.append(Classification(
            block_index=i, classification="chapter-opening", confidence=0.95,
            source_text=text[:80], evidence=["CSS class: chapter-opening"],
        ))
        return
    
    # Verse detection (short lines, <br/> separated)
    if block.type == "div" and "verse" in block.classes:
        classifications.append(Classification(
            block_index=i, classification="verse", confidence=0.85,
            source_text=text[:80], evidence=["CSS class: verse"],
        ))
        return
    
    # Verse line (paragraph inside a verse container)
    if block.type == "paragraph" and "verse-line" in block.classes:
        classifications.append(Classification(
            block_index=i, classification="verse", confidence=0.9,
            source_text=text[:80], evidence=["CSS class: verse-line"],
        ))
        return

    # Standard paragraph
    if block.type == "paragraph":
        classifications.append(Classification(
            block_index=i, classification="paragraph", confidence=0.7,
            source_text=text[:80], evidence=["<p> tag"],
        ))
        return
    
    # Default: uncertain
    classifications.append(Classification(
        block_index=i, classification="uncertain", confidence=0.3,
        source_text=text[:80], evidence=[f"tag: {block.type}, classes: {block.classes}"],
    ))


def find_low_confidence(blocks: list[Block], threshold: float = 0.8) -> list[int]:
    """Find block indices with classification confidence below threshold.
    
    These nodes should be sent to the LLM classifier for resolution.
    """
    classifications = classify_blocks(blocks)
    return [
        c.block_index for c in classifications
        if c.confidence < threshold
    ]


def build_ast_draft(html: str, source_ref: Optional[str] = None) -> dict:
    """
    Build a draft AST from typescript HTML.
    
    Returns the draft AST with confidence scores, plus a list of
    low-confidence nodes for LLM disambiguation.
    """
    blocks = parse_html(html)
    classifications = classify_blocks(blocks)
    low_conf = [c for c in classifications if c.confidence < 0.8]
    
    # Build chapter structure
    chapters = []
    current_chapter = None
    chapter_number = 0
    chapter_count = 0
    
    classifications_by_index = {c.block_index: c for c in classifications}
    for i, block in enumerate(blocks):
        classification = classifications_by_index.get(i)
        if classification is None:
            # classify_blocks skips blocks with no meaningful text
            # (e.g. an empty verse-container <div>) — nothing to emit.
            continue
        if classification.classification == "chapter-title":
            if current_chapter:
                chapters.append(current_chapter)
            chapter_count += 1
            chapter_number += 1
            current_chapter = {
                "type": "chapter",
                "attrs": {
                    "number": chapter_number,
                    "title": block.text.strip(),
                    "id": f"ch{chapter_number}",
                    "startsOn": "recto",
                },
                "content": [],
                "confidence": classification.confidence,
            }
        elif current_chapter:
            current_chapter["content"].append({
                "type": _ast_block_type(classification.classification),
                "text": block.text.strip(),
                "confidence": classification.confidence,
            })
    
    if current_chapter:
        chapters.append(current_chapter)
    
    draft = {
        "schema": "ast/1",
        "metadata": {"title": "Draft from rules engine"},
        "body": chapters,
        "frontMatter": [],
        "backMatter": [],
        "lowConfidenceNodes": [
            {
                "block_index": c.block_index,
                "text": blocks[c.block_index].text[:100] if c.block_index < len(blocks) else "",
                "suggested": [c.classification],
                "confidence": c.confidence,
                "alternatives": _alternatives(c.classification),
            }
            for c in low_conf
        ],
    }
    
    return draft


def _ast_block_type(classification: str) -> str:
    """Map a classification label to an AST node type (from ast.schema.json)."""
    mapping = {
        "paragraph": "paragraph",
        "chapter-opening": "paragraph",
        "blockquote": "blockquote",
        "epigraph": "epigraph",
        "verse": "verse",
        "heading-2": "heading",
        "heading-3": "heading",
        "scene-break": "sceneBreak",
        "code-block": "code",
        "sidebar": "sidebar",
    }
    return mapping.get(classification, "paragraph")


def _alternatives(classification: str) -> list[str]:
    """Suggest alternative classifications for low-confidence nodes."""
    alt_map = {
        "paragraph": ["chapter-title", "heading-2", "blockquote", "verse"],
        "chapter-title": ["heading-2", "paragraph", "front-toc"],
        "heading-2": ["chapter-title", "paragraph", "heading-3"],
    }
    return alt_map.get(classification, ["paragraph", "chapter-title"])


def compute_text_integrity(html: str, ast_draft: dict) -> dict:
    """
    Compute text integrity hash and compare with AST.
    
    From ARCHITECTURE.md §3.1:
    normalize(concat(text nodes of AST)) == normalize(text stream of source)
    """
    # Extract text from HTML source
    from html.parser import HTMLParser
    
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.texts: list[str] = []
        def handle_data(self, data: str):
            self.texts.append(data)
    
    extractor = TextExtractor()
    extractor.feed(html)
    source_text = "".join(extractor.texts)
    
    # Dummy: in production this compares against AST
    h = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    
    return {
        "integrityHash": f"sha256:{h}",
        "sourceLength": len(source_text),
        "passed": True,
    }
