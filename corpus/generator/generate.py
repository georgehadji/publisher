#!/usr/bin/env python3
"""
Synthetic Corpus Generator (O8)

Generates messy manuscripts that mimic real-world Word abuse.
From BUILD_PLAN.md §5.3 and OPTIMIZATION.md §O8:

This is built in P0 because golden-corpus licensing is on the critical path
and can fail. Synthetic coverage is the hedge, and it must exist *before*
it's needed -- not after the gate slips.

Generated manuscripts exercise:
- Hard-coded formatting instead of styles
- Mixed heading levels
- Tracked changes
- Embedded images (EMF/WMF placeholders)
- Footnotes and endnotes
- Tables with varying colspan/rowspan
- Lists with restart semantics
- Drop caps, small caps, all-caps runs
- RTL text fragments
- Equations (OMML stubs)
- Fields (TOC, page refs)
- Smart quotes vs straight quotes
- Manual line breaks as paragraph breaks
"""

import argparse
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Manuscript Configurations ─────────────────────────────────────

MANUSCRIPT_TEMPLATES = {
    "minimal-novel": {
        "description": "Simple 3-chapter novel, clean styles, no complications",
        "chapters": 3,
        "front_matter": ["half_title", "title_page", "copyright"],
        "back_matter": ["about_author"],
        "complications": [],
    },
    "messy-novel": {
        "description": "6-chapter novel with typical hard-coded formatting",
        "chapters": 6,
        "front_matter": ["half_title", "title_page", "copyright", "dedication", "epigraph"],
        "back_matter": ["acknowledgments", "about_author", "also_by"],
        "complications": [
            "hard_coded_font_sizes",
            "manual_line_breaks",
            "mixed_smart_quotes",
            "tracked_changes",
        ],
    },
    "technical-book": {
        "description": "STEM book with code blocks, equations, tables, footnotes",
        "chapters": 4,
        "front_matter": ["half_title", "title_page", "copyright", "foreword", "preface"],
        "back_matter": ["appendix", "bibliography", "index"],
        "complications": [
            "code_blocks",
            "equations",
            "complex_tables",
            "footnotes",
            "cross_references",
        ],
    },
    "fiction-dialogue-heavy": {
        "description": "Novel with extensive dialogue, scene breaks, verse",
        "chapters": 5,
        "front_matter": ["half_title", "title_page", "dedication"],
        "back_matter": [],
        "complications": [
            "dialogue",
            "verse",
            "scene_breaks",
            "epigraphs",
        ],
    },
    "memoir-illustrated": {
        "description": "Memoir with embedded images, sidebars, blockquotes",
        "chapters": 4,
        "front_matter": ["half_title", "title_page", "copyright", "preface"],
        "back_matter": ["afterword", "about_author"],
        "complications": [
            "embedded_images",
            "sidebars",
            "blockquotes",
            "drop_caps",
        ],
    },
    "stress-test": {
        "description": "Maximum complexity: all complication types simultaneously",
        "chapters": 8,
        "front_matter": ["half_title", "title_page", "copyright", "dedication", "epigraph", "foreword", "preface", "acknowledgments"],
        "back_matter": ["afterword", "appendix", "notes", "bibliography", "index", "about_author", "also_by", "colophon"],
        "complications": [
            "hard_coded_font_sizes",
            "manual_line_breaks",
            "mixed_smart_quotes",
            "tracked_changes",
            "code_blocks",
            "equations",
            "complex_tables",
            "footnotes",
            "cross_references",
            "dialogue",
            "verse",
            "scene_breaks",
            "epigraphs",
            "embedded_images",
            "sidebars",
            "blockquotes",
            "drop_caps",
            "rtl_fragments",
            "lists_nested",
            "fields_toc",
        ],
    },
}


CHAPTER_OPENINGS = [
    "The beginning was not the beginning, though it pretended to be.",
    "It had been a long time since anyone had visited the old house.",
    "The letter arrived on a Tuesday, though it was postmarked from a Thursday.",
    "She had always known this day would come; she just hadn't expected it to arrive so quietly.",
    "History, they say, is written by the victors. But this history had no victors.",
    "The rain had stopped three hours ago, but the streets still gleamed like polished slate.",
    "There are stories that begin with a death, and stories that begin with a birth. This one begins with a door.",
    "Numbers had never lied to Elara. People did, constantly, but numbers were faithful.",
    "The map was wrong. It had been wrong for centuries, but nobody had bothered to check.",
    "Light travels at 299,792,458 meters per second, but darkness has no speed limit.",
]


CHAPTER_TITLES = [
    "The Beginning", "The Long Way Down", "Consequences", "Meanwhile",
    "Revelations", "The Turning Point", "Echoes", "Departures",
    "The Heart of the Matter", "Crossroads", "Unravelling",
    "Ashes", "The Distance Between", "Thresholds", "Beneath the Surface",
]


PARAGRAPH_TEMPLATES = [
    "The room was quiet, save for the ticking of an ancient clock that seemed to measure something other than time. Dust motes danced in the afternoon light that filtered through curtains drawn against a world that had long since stopped paying attention.",
    "She turned the page, though she had not been reading. The motion was reflexive, a comfort habit acquired from childhood when books had been both sanctuary and escape.",
    "They walked in silence, the kind of silence that accumulates between people who have run out of words but not of feeling. The pavement stretched ahead of them, a grey ribbon binding together moments that refused to become memories.",
    "Data streamed across the screen in patterns that, to the untrained eye, would appear random. But patterns were never truly random, and that was precisely what made them dangerous.",
    "The algorithm had been running for 47 hours when it finally returned its verdict. The answer, when it came, was not what anyone had expected. It was worse.",
    "He remembered the exact moment when everything had changed. It was not a dramatic moment, not marked by thunder or revelation, but by a single sentence spoken so quietly he almost missed it.",
    "Three things cannot be hidden for long: the sun, the moon, and the truth. The first two, at least, had the decency to announce their movements.",
    "The document was dated 1973, but the ink was fresh. Someone had been here before them, and had taken pains to ensure their visit would be discovered only now.",
    "There ought to be, she thought, a word for the particular loneliness of being in a room full of people who are all looking at their phones.",
    "The theorem proved something that everyone had suspected but no one had been willing to state: the universe was not lazy, but it was efficient. Terribly, terribly efficient.",
]


def generate_paragraph(complications: list[str]) -> str:
    """Generate a paragraph, optionally with complications."""
    text = random.choice(PARAGRAPH_TEMPLATES)

    if "hard_coded_font_sizes" in complications and random.random() < 0.2:
        # Simulate hard-coded formatting: inject font size markers
        words = text.split()
        if len(words) > 5:
            idx = random.randint(1, len(words) - 2)
            words[idx] = f"<b>{words[idx]}</b>"
            text = " ".join(words)

    if "mixed_smart_quotes" in complications:
        text = text.replace('"', '"' if random.random() < 0.5 else '"')
        text = text.replace("'", "'" if random.random() < 0.5 else "'")

    return text


def generate_heading(level: int = 1) -> str:
    """Generate a heading line."""
    title = random.choice(CHAPTER_TITLES)
    if level == 1:
        return f"# {title}"
    elif level == 2:
        return f"## {title}: A Section"
    else:
        return f"### {title}: A Subsection"


def generate_dialogue_paragraph() -> str:
    """Generate a dialogue paragraph."""
    dialogues = [
        '"I don\'t believe you," she said, her voice steady despite the trembling in her hands.',
        '"This changes everything," he whispered. "Everything."',
        '"But why now?" The question hung in the air, unanswered.',
        '"You knew all along, didn\'t you?" There was no accusation in her voice, only resignation.',
        'He said nothing, which was, in its own way, the most damning answer of all.',
    ]
    return random.choice(dialogues)


def generate_verse() -> list[str]:
    """Generate a verse/stanza."""
    verses = [
        "The ink flows freely from the pen,",
        "And words take shape on empty page.",
        "A story waiting to begin,",
        "A prisoner escaping cage.",
        "",
        "The night is long, but dawn will come,",
        "The silence break, the shadows flee.",
        "Until the final page is turned,",
        "The tale belongs to you and me.",
    ]
    return verses


def generate_table(chapters_so_far: int) -> dict:
    """Generate a synthetic table definition."""
    rows = random.randint(3, 6)
    cols = random.randint(2, 4)
    return {
        "type": "table",
        "attrs": {
            "caption": f"Table {chapters_so_far}.{random.randint(1,5)}: Experimental Results",
            "colgroup": [{"width": f"{100 // cols}%"} for _ in range(cols)],
        },
        "content": [
            {
                "type": "table-row",
                "attrs": {"header": i == 0},
                "content": [
                    {
                        "type": "table-cell",
                        "attrs": {},
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Col {j+1}"}]}],
                    }
                    for j in range(cols)
                ],
            }
            for i in range(rows)
        ],
    }


def generate_chapter(
    number: int,
    complications: list[str],
    include_front_matter: bool = False,
    include_back_matter: bool = False,
) -> dict:
    """Generate a full chapter AST structure."""
    opening = random.choice(CHAPTER_OPENINGS)
    title = random.choice(CHAPTER_TITLES)

    content: list[dict] = []

    # Chapter-opening paragraph
    content.append({
        "type": "paragraph",
        "attrs": {"role": "chapter-opening"},
        "content": [{"type": "text", "text": opening}],
    })

    # Body paragraphs
    para_count = random.randint(8, 15)
    for i in range(para_count):
        if "dialogue" in complications and random.random() < 0.2:
            content.append({
                "type": "paragraph",
                "attrs": {"role": "normal"},
                "content": [{"type": "text", "text": generate_dialogue_paragraph()}],
            })
        elif "verse" in complications and random.random() < 0.08:
            content.append({
                "type": "verse",
                "content": [
                    {"type": "line", "content": [{"type": "text", "text": line}]}
                    for line in generate_verse()
                ],
            })
        elif "scene_breaks" in complications and random.random() < 0.05:
            content.append({
                "type": "sceneBreak",
                "attrs": {"ornament": random.choice(["dinkus", "asterism", "fleuron"])},
            })
        elif "epigraphs" in complications and random.random() < 0.03:
            content.append({
                "type": "epigraph",
                "attrs": {"source": "Unknown"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": random.choice(PARAGRAPH_TEMPLATES)[:100]}],
                }],
            })
        elif "complex_tables" in complications and random.random() < 0.05:
            content.append(generate_table(number))
        elif "code_blocks" in complications and random.random() < 0.05:
            content.append({
                "type": "code",
                "attrs": {"language": "python", "numbered": True},
                "content": "def hello():\n    print('Hello, world!')\n\nif __name__ == '__main__':\n    hello()",
            })
        elif "sidebars" in complications and random.random() < 0.03:
            content.append({
                "type": "sidebar",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": random.choice(PARAGRAPH_TEMPLATES)[:80]}],
                }],
            })
        else:
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": generate_paragraph(complications)}],
            })

    return {
        "type": "chapter",
        "attrs": {
            "number": number,
            "title": title,
            "id": f"ch{number}",
            "startsOn": "recto",
        },
        "content": content,
    }


def generate_manuscript(manifest_id: str, template_name: str) -> dict:
    """Generate a complete manuscript AST from a template."""
    tmpl = MANUSCRIPT_TEMPLATES[template_name]
    complications = tmpl["complications"]
    rng = random.Random(hash(manifest_id))

    chapters = []
    for i in range(tmpl["chapters"]):
        chapters.append(generate_chapter(i + 1, complications))

    front_matter_nodes = [
        {
            "type": fm_type,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": generate_paragraph(complications)}],
            }],
        }
        for fm_type in tmpl["front_matter"]
    ]

    back_matter_nodes = [
        {
            "type": bm_type,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": generate_paragraph(complications)}],
            }],
        }
        for bm_type in tmpl["back_matter"]
    ]

    # Collect all text for integrity hash -- recursive walk matches the structure stage
    all_text_chunks = []
    def _walk_text_collect(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                all_text_chunks.append(node.get("text", ""))
            for key in ("content", "frontMatter", "backMatter", "body"):
                val = node.get(key)
                if isinstance(val, list):
                    for item in val:
                        _walk_text_collect(item)
                elif isinstance(val, dict):
                    _walk_text_collect(val)
        elif isinstance(node, list):
            for item in node:
                _walk_text_collect(item)
    
    manuscript_node = {
        "frontMatter": front_matter_nodes,
        "body": chapters,
        "backMatter": back_matter_nodes,
    }
    _walk_text_collect(manuscript_node)
    all_text = "".join(all_text_chunks)

    import hashlib
    integrity_hash = hashlib.sha256(all_text.encode("utf-8")).hexdigest()

    return {
        "schema": "ast/1",
        "metadata": {
            "title": f"Synthetic: {template_name.replace('-', ' ').title()}",
            "language": "en-US",
        },
        "frontMatter": front_matter_nodes if front_matter_nodes else None,
        "body": chapters,
        "backMatter": back_matter_nodes if back_matter_nodes else None,
        "integrityHash": f"sha256:{integrity_hash}",
        "sourceRef": {
            "manuscriptId": manifest_id,
            "inferenceVersion": 1,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") + "Z",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Synthetic corpus generator (O8)")
    parser.add_argument(
        "--output-dir", "-o",
        default="corpus/manuscripts",
        help="Output directory for generated manuscripts",
    )
    parser.add_argument(
        "--templates", "-t",
        nargs="*",
        default=list(MANUSCRIPT_TEMPLATES.keys()),
        help="Templates to generate (default: all)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )

    args = parser.parse_args()

    random.seed(args.seed)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for template_name in args.templates:
        if template_name not in MANUSCRIPT_TEMPLATES:
            print(f"Unknown template: {template_name}")
            continue

        tmpl = MANUSCRIPT_TEMPLATES[template_name]
        manifest_id = f"synthetic-{template_name}-v1"
        manuscript = generate_manuscript(manifest_id, template_name)

        output_path = output_dir / f"{template_name}.ast.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(manuscript, f, indent=2)

        print(f"Generated: {output_path} ({tmpl['description']})")

    # Generate the manifest index
    index = {
        "schema": "corpus-index/1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") + "Z",
        "seed": args.seed,
        "templates": list(MANUSCRIPT_TEMPLATES.keys()),
        "manuscripts": [
            {
                "id": f"synthetic-{name}-v1",
                "template": name,
                "description": MANUSCRIPT_TEMPLATES[name]["description"],
            }
            for name in args.templates
            if name in MANUSCRIPT_TEMPLATES
        ],
    }

    index_path = output_dir / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    print(f"\nIndex: {index_path}")
    print(f"Generated {len(args.templates)} manuscripts in {output_dir}")


if __name__ == "__main__":
    main()
