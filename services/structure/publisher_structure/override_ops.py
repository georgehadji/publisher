"""
Override transforms -- what each `overrides/1` op does to a document.

`overrides.py` reads the log and applies it in order; this module holds the
one function per op that `apply_overrides` dispatches to (`_TRANSFORMS`), and
the helpers they share. Every transform returns a new document and shares every
subtree it did not change (ARCHITECTURE.md §3.6); none mutates its input.

An op that matches a node but cannot act on it raises `InapplicableOverride`
with the reason. One that matches nothing returns the document unchanged, the
same object, which is how `apply_overrides` tells an orphan apart.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .overrides import OverrideOp


def _matches(node: dict, source_ref: str) -> bool:
    src = node.get("sourceRef") or node.get("sourceRefLink") or {}
    if isinstance(src, dict):
        return src.get("docxId") == source_ref
    return src == source_ref


_CHILD_KEYS = ("content", "frontMatter", "backMatter", "body")


def _rewrite(node: Any, source_ref: str, transform) -> Any:
    """
    Return `node` with the descendant matching `source_ref` replaced by
    `transform(match)`. Subtrees that contain no match are returned BY IDENTITY, so
    they are shared with the input rather than copied.

    This is the structural sharing ARCHITECTURE.md §3.6 and BUILD_PLAN.md §3.7
    specify: "200 overrides on a 5000-node AST allocates ~200 paths, not a copy" —
    O(depth) per override instead of O(n). Only the nodes along the path from the root
    to the changed node are shallow-copied.
    """
    if isinstance(node, list):
        out, changed = [], False
        for item in node:
            new_item = _rewrite(item, source_ref, transform)
            changed = changed or new_item is not item
            out.append(new_item)
        return out if changed else node

    if not isinstance(node, dict):
        return node

    if _matches(node, source_ref):
        return transform(node)

    for key in _CHILD_KEYS:
        val = node.get(key)
        if val is None:
            continue
        new_val = _rewrite(val, source_ref, transform)
        if new_val is not val:
            # Shallow-copy only this node, re-pointing the one child that changed.
            copied = dict(node)
            copied[key] = new_val
            return copied

    return node


class InapplicableOverride(ValueError):
    """A valid op that does not fit the document it meets: a split at a
    chapter's first block, a merge of the book's first chapter, a retitle of a
    paragraph. Not fatal -- the log is append-only, so failing on it would
    leave the manuscript unbuildable for good -- and not silent:
    `apply_overrides` reports it, and `resolve` turns it into a warning."""


# ── The AST schema, read once ───────────────────────────────────
#
# Every op that changes a node's type or attributes checks the result against
# ast.schema.json, read from the file -- not a copy of its rules here. An op
# whose result the schema rejects is inapplicable: the effective document is
# what every renderer reads, and it must stay a valid AST.

_AST_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "ast" / "ast.schema.json"
_ERROR_CHARS = 200


@functools.lru_cache(maxsize=1)
def _ast_defs() -> dict:
    return json.loads(_AST_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]


@functools.lru_cache(maxsize=None)
def _validator(pointer: str):
    from jsonschema import Draft202012Validator
    return Draft202012Validator({"$defs": _ast_defs(), "$ref": pointer})


def _public(value: Any) -> Any:
    """`value` without this layer's own `_` markers, which the schema does not know."""
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


def _type_def(node_type: Any) -> Optional[dict]:
    """The schema's definition of one node type, or None for a type it does not name."""
    found = _ast_defs().get(node_type) if isinstance(node_type, str) else None
    if isinstance(found, dict) and found.get("properties", {}).get("type", {}).get("enum") == [node_type]:
        return found
    return None


def _attrs_of(node_type: Any) -> dict:
    """The attributes the schema lets a node of this type carry."""
    return (((_type_def(node_type) or {}).get("properties") or {}).get("attrs") or {}).get("properties") or {}


def _schema_error(pointer: str, instance: Any) -> Optional[str]:
    from jsonschema.exceptions import best_match
    error = best_match(_validator(pointer).iter_errors(_public(instance)))
    if error is None:
        return None
    if len(error.message) <= _ERROR_CHARS:
        return error.message
    # The message quotes the offending value, which may be a whole title or a
    # chapter: name the rule it broke instead.
    where = "/".join(str(part) for part in error.absolute_path) or "the node"
    return f"{where} breaks the schema's {error.validator} ({error.validator_value!r:.60})"


def _valid_node(node: dict, why: str) -> dict:
    if _type_def(node.get("type")) is None:
        raise InapplicableOverride(f"{why}: the AST schema has no node type {node.get('type')!r}")
    error = _schema_error(f"#/$defs/{node['type']}", node)
    if error:
        raise InapplicableOverride(f"{why}: {error}")
    return node


def _with_attrs(node: dict, attrs: dict, op: OverrideOp) -> dict:
    """`node` with `attrs`, which must be valid for its type."""
    error = _schema_error(f"#/$defs/{node.get('type')}/properties/attrs", attrs)
    if error:
        raise InapplicableOverride(error)
    return {**node, "attrs": attrs, "_override": op.id}


def _derived_id(prefix: str, op: OverrideOp) -> str:
    # Existing ids are kept (cross-references and ops point at them); a node an
    # op creates takes an id derived from the op, so the same log always builds
    # the same document.
    return f"{prefix}-" + hashlib.sha256(op.id.encode("utf-8")).hexdigest()[:12]


# ── Node ops: one node changes in place ─────────────────────────

_PARAGRAPH_WRAPPERS = ("blockquote", "epigraph", "dialogue", "sidebar")


def _reshape(node: dict, to: str, level: Optional[int] = None) -> dict:
    """`node` as a `to`, carrying what the new type can hold. A paragraph
    becomes the one paragraph of a blockquote, epigraph, dialogue or sidebar,
    and comes back out of one; the wrapper keeps the sourceRef, so the two
    reclassifies undo each other. A heading takes `level` when given (B3), so
    a paragraph read as a level-2 heading is one op, not a reclassify and a demote."""
    kind = node.get("type")
    # `confidence` scored ingest's reading, which the retype replaces (B6).
    keep = {k: v for k, v in node.items() if k not in ("type", "attrs", "content", "confidence")}
    content = node.get("content")
    attrs = {k: v for k, v in (node.get("attrs") or {}).items() if k in _attrs_of(to)}
    if kind == "paragraph" and to in _PARAGRAPH_WRAPPERS:
        content, attrs = [{k: v for k, v in node.items() if k not in ("sourceRef", "confidence")}], {}
    elif kind in _PARAGRAPH_WRAPPERS and to == "paragraph":
        if len(content or []) != 1 or content[0].get("type") != "paragraph":
            raise InapplicableOverride(f"the {kind} holds more than one paragraph")
        content, attrs = content[0].get("content"), dict(content[0].get("attrs") or {})
    if to == "heading":
        attrs["level"] = level if level is not None else attrs.get("level", 1)
    out = {**keep, "type": to, **({"content": content} if content is not None else {})}
    return {**out, "attrs": attrs} if attrs else out


def _check_from(node: dict, op: OverrideOp) -> None:
    kind = node.get("type")
    if op.from_value and kind != op.from_value:
        raise InapplicableOverride(f"it is a {kind}, not the {op.from_value} the op expected")
    if op.value is not None and op.to_value != "heading":
        raise InapplicableOverride(f"only a heading takes a level, not a {op.to_value}")


def _t_reclassify(op: OverrideOp):
    def t(node: dict) -> dict:
        _check_from(node, op)
        return _valid_node({**_reshape(node, op.to_value, op.value), "_override": op.id},
                           f"a {node.get('type')} cannot become a {op.to_value}")
    return t


# A section is not a $defs type of its own: it is a branch of its root's union,
# so a retyped one is checked against that union. That same check refuses a
# back-matter type in the front matter, and the reverse (B3).
_SECTION_UNIONS = (("frontMatter", "frontMatterNode"), ("backMatter", "backMatterNode"))


def _op_reclassify(ast: dict, op: OverrideOp) -> dict:
    for root, union in _SECTION_UNIONS:
        sections = ast.get(root) or []
        at = next((i for i, node in enumerate(sections) if _matches(node, op.sourceRef)), None)
        if at is None or sections[at].get("type") not in _section_types():
            continue
        section = sections[at]
        _check_from(section, op)
        why = f"a {section['type']} cannot become a {op.to_value}"
        if op.to_value not in _section_types():
            raise InapplicableOverride(f"{why}: a section only becomes another kind of section")
        # Content, sourceRef, confidence and attrs are kept; the union decides.
        retyped = {**section, "type": op.to_value, "_override": op.id}
        error = _schema_error(f"#/$defs/{union}", retyped)
        if error:
            raise InapplicableOverride(f"{why} in the {root}: {error}")
        return {**ast, root: sections[:at] + [retyped] + sections[at + 1:]}
    return _rewrite(ast, op.sourceRef, _t_reclassify(op))


def _t_retitle(op: OverrideOp):
    def t(node: dict) -> dict:
        if "title" not in _attrs_of(node.get("type")):
            raise InapplicableOverride(f"a {node.get('type')} has no title")
        return _with_attrs(node, {**(node.get("attrs") or {}), "title": op.value}, op)
    return t


_SET_ATTR_PATH = re.compile(r"^/attrs/([A-Za-z][A-Za-z0-9]*)$")
# Attributes another op owns, or that the document derives. `None` = derived.
_OWNED_ATTRS = {"title": "retitle", "level": "promote/demote", "id": None, "number": None}


def _t_set_attr(op: OverrideOp):
    """Sets one attribute the node's type declares (`path` is `/attrs/<name>`),
    or removes it when the op has no `value`."""
    match = _SET_ATTR_PATH.match(op.path or "")

    def t(node: dict) -> dict:
        if match is None:
            raise InapplicableOverride(f"its path {op.path!r} is not /attrs/<name>")
        name = match.group(1)
        if name in _OWNED_ATTRS:
            owner = _OWNED_ATTRS[name]
            raise InapplicableOverride(f"`{name}` is set by {owner}" if owner else f"`{name}` is derived, not set")
        if name not in _attrs_of(node.get("type")):
            raise InapplicableOverride(f"a {node.get('type')} has no attribute `{name}`")
        attrs = {k: v for k, v in (node.get("attrs") or {}).items() if k != name}
        if op.value is not None:
            attrs[name] = op.value
        return _with_attrs(node, attrs, op)
    return t


def _t_flag_ambiguity(op: OverrideOp):
    def t(node: dict) -> dict:
        flags = list(node.get("_flags", []))
        flags.append({
            "id": op.id,
            "message": op.rationale or "Flagged for review",
            "actor": op.actor,
        })
        return {**node, "_flags": flags}
    return t


def _t_resolve_ambiguity(op: OverrideOp):
    """Clears the node's flags, or only the one whose op id is `value`."""
    def t(node: dict) -> dict:
        flags = node.get("_flags") or []
        if not flags:
            raise InapplicableOverride("it carries no flag to resolve")
        kept = [] if op.value is None else [f for f in flags if f.get("id") != op.value]
        if len(kept) == len(flags):
            raise InapplicableOverride(f"it carries no flag {op.value!r}")
        out = {k: v for k, v in node.items() if k != "_flags"}
        return {**out, **({"_flags": kept} if kept else {}), "_override": op.id}
    return t


# ── split / merge ──────────────────────────────────────────────
#
# The inverse pair for a chapter boundary the ingest got wrong. `split` targets a
# block directly inside a chapter -- the heading ingest missed -- and starts a new
# chapter there, titled with that block's text. `merge` targets a chapter and
# joins it to the one before, its title kept as the first paragraph of what it
# joins. Either way every word stays in the book, in order, so the effective
# document still carries the text the integrity gate proved. The node that marks
# the boundary keeps its sourceRef across both, so each undoes the other.

_TITLE_MAX = 512   # ast.schema.json chapter attrs.title maxLength


def _plain_text(node: dict) -> str:
    """A block's inline text, as a title reads it (a line break is a space)."""
    parts = []
    for child in node.get("content") or []:
        if child.get("type") == "text":
            parts.append(child.get("text", ""))
        elif child.get("type") == "hardBreak":
            parts.append(" ")
        else:
            parts.append(_plain_text(child))
    return " ".join("".join(parts).split())


def _shift_chapter_numbers(ast: dict, after_id: str, delta: int) -> dict:
    """Every chapter after `after_id`, in book order, renumbered by `delta`."""
    seen = False

    def walk(nodes: list) -> list:
        nonlocal seen
        out = []
        for node in nodes:
            if node.get("type") == "part":
                node = {**node, "content": walk(node.get("content") or [])}
            elif node.get("type") == "chapter":
                attrs = node.get("attrs") or {}
                if seen and isinstance(attrs.get("number"), int):
                    node = {**node, "attrs": {**attrs, "number": attrs["number"] + delta}}
                seen = seen or attrs.get("id") == after_id
            out.append(node)
        return out

    return {**ast, "body": walk(ast.get("body") or [])}


def _in_chapter_lists(ast: dict, edit) -> Optional[dict]:
    """Apply `edit(chapters) -> new list | None` to the body and to each part's
    chapter list; the document with the first list it changed, or None."""
    body = ast.get("body") or []
    edited = edit(body)
    if edited is not None:
        return {**ast, "body": edited}
    for index, node in enumerate(body):
        if node.get("type") == "part":
            edited = edit(node.get("content") or [])
            if edited is not None:
                new_body = list(body)
                new_body[index] = {**node, "content": edited}
                return {**ast, "body": new_body}
    return None


def _op_split(ast: dict, op: OverrideOp) -> dict:
    new: dict = {}

    def edit(chapters: list) -> Optional[list]:
        for index, chapter in enumerate(chapters):
            if chapter.get("type") != "chapter":
                continue
            content = chapter.get("content") or []
            at = next((i for i, block in enumerate(content) if _matches(block, op.sourceRef)), None)
            if at is None:
                continue
            title = _plain_text(content[at])
            if at == 0:
                raise InapplicableOverride("its block already opens the chapter")
            if at == len(content) - 1:
                raise InapplicableOverride("its block is the chapter's last; the new chapter would be empty")
            if not title:
                raise InapplicableOverride("its block has no text to title the new chapter")
            if len(title) > _TITLE_MAX:
                raise InapplicableOverride(f"its block is {len(title)} characters, too long for a title")
            attrs = chapter.get("attrs") or {}
            new.update({
                "type": "chapter",
                "attrs": {**{k: attrs[k] for k in ("startsOn",) if k in attrs},
                          "number": attrs.get("number", 0) + 1, "id": _derived_id("ch", op),
                          "title": title},
                "content": content[at + 1:],
                "_override": op.id,
            })
            if "sourceRef" in content[at]:
                new["sourceRef"] = content[at]["sourceRef"]
            head = {**chapter, "content": content[:at], "_override": op.id}
            return chapters[:index] + [head, new] + chapters[index + 1:]
        return None

    out = _in_chapter_lists(ast, edit)
    if out is None:
        folded = _in_back_section(ast, op.sourceRef)
        if folded is not None:
            # A chapter ingest folded into back matter (F1). A chapter starts
            # here, so the body reaches here: the section, and the back matter
            # before it, become chapters first -- what end_body does -- and the
            # split then applies inside the chapter that section became.
            at, index = folded
            if index == 0:
                raise InapplicableOverride("its block opens a back-matter section; end_body there instead")
            out = _in_chapter_lists(_body_ends_at(ast, at, op), edit)
        if out is None:
            if _find_by_source_ref(ast, op.sourceRef) is not None:
                raise InapplicableOverride("its block is not directly inside a chapter")
            return ast   # matches nothing: an orphan, which the API reports
    before = new["attrs"]["id"]
    # The new chapter took the next number; everything after it moves up one.
    return _shift_chapter_numbers(out, before, 1)


def _op_merge(ast: dict, op: OverrideOp, title_as: str = "paragraph") -> dict:
    """`title_as` is the block the joined chapter's title becomes: a paragraph
    (merge), or a level-1 heading (demote, the inverse of promote)."""
    removed: dict = {}

    def edit(chapters: list) -> Optional[list]:
        for index, chapter in enumerate(chapters):
            if chapter.get("type") != "chapter" or not _matches(chapter, op.sourceRef):
                continue
            previous = chapters[index - 1] if index else None
            if previous is None or previous.get("type") != "chapter":
                raise InapplicableOverride("no chapter precedes it in the same part")
            title = (chapter.get("attrs") or {}).get("title") or ""
            heading = []
            if title:
                heading = [{"type": title_as, "content": [{"type": "text", "text": title}]}]
                if title_as == "heading":
                    heading[0]["attrs"] = {"level": 1}
                if "sourceRef" in chapter:
                    heading[0]["sourceRef"] = chapter["sourceRef"]
            joined = {**previous, "content": (previous.get("content") or []) + heading
                      + (chapter.get("content") or []), "_override": op.id}
            removed["id"] = (previous.get("attrs") or {}).get("id")
            return chapters[:index - 1] + [joined] + chapters[index + 1:]
        return None

    out = _in_chapter_lists(ast, edit)
    if out is None:
        if _find_by_source_ref(ast, op.sourceRef) is not None:
            raise InapplicableOverride("it is not a chapter")
        return ast
    # One chapter fewer: everything after the joined one moves down one.
    return _shift_chapter_numbers(out, removed["id"], -1)


# ── promote / demote ───────────────────────────────────────────
#
# A heading's level moves by one. At the ends of the scale the heading becomes
# a chapter or a chapter becomes a heading: a level-1 heading directly in a
# chapter is promoted into a chapter of its own (as `split`, titled with the
# heading's text), and a chapter is demoted into a level-1 heading of the one
# before (as `merge`). Each undoes the other.

_LEVELS = (1, 6)   # ast.schema.json heading attrs.level minimum/maximum


def _op_promote(ast: dict, op: OverrideOp) -> dict:
    node = _find_by_source_ref(ast, op.sourceRef)
    if node is None:
        return ast
    kind = node.get("type")
    if kind == "chapter":
        raise InapplicableOverride("a chapter is already the top level")
    if kind != "heading":
        raise InapplicableOverride(f"a {kind} has no level to promote")
    level = (node.get("attrs") or {}).get("level", _LEVELS[0])
    if level > _LEVELS[0]:
        return _rewrite(ast, op.sourceRef,
                        lambda n: _with_attrs(n, {**(n.get("attrs") or {}), "level": level - 1}, op))
    return _op_split(ast, op)


def _op_demote(ast: dict, op: OverrideOp) -> dict:
    node = _find_by_source_ref(ast, op.sourceRef)
    if node is None:
        return ast
    kind = node.get("type")
    if kind == "chapter":
        return _op_merge(ast, op, title_as="heading")
    if kind != "heading":
        raise InapplicableOverride(f"a {kind} has no level to demote")
    level = (node.get("attrs") or {}).get("level", _LEVELS[0])
    if level >= _LEVELS[1]:
        raise InapplicableOverride(f"a level-{level} heading is already the lowest")
    return _rewrite(ast, op.sourceRef,
                    lambda n: _with_attrs(n, {**(n.get("attrs") or {}), "level": level + 1}, op))


# ── start_body / end_body: where the body begins and ends ──────
#
# When ingest finds no prose to mark the body's start it falls back, and real
# chapters can land in the front matter (each scored 0.5). `start_body` targets
# the front-matter section where the body really begins: it and every section
# after it become chapters, in order, ahead of the body -- each titled by its
# first block, as `split` titles a chapter. `end_body` is its mirror: it targets
# the back-matter section where the body really ends, and it and every section
# before it become chapters after the body. Nothing is reordered, so every word
# stays where the integrity gate saw it. Undo is dropping the op from the log.

def _section_as_chapter(section: dict, number: int, chapter_id: str, op: OverrideOp,
                        where: str) -> dict:
    kind = section.get("type")
    if "content" not in section or kind not in _section_types():
        raise InapplicableOverride(f"the {where} holds a {kind}, not a section")
    first, *rest = section["content"]
    title = _plain_text(first)
    if not title:
        raise InapplicableOverride(f"the {kind} opens with no text to title a chapter")
    if len(title) > _TITLE_MAX:
        raise InapplicableOverride(f"the {kind}'s first block is {len(title)} characters, too long for a title")
    if not rest:
        raise InapplicableOverride(f"the {kind} has nothing left once its first block titles it")
    chapter = {"type": "chapter",
               "attrs": {"number": number, "id": chapter_id, "title": title, "startsOn": "recto"},
               "content": rest, "_override": op.id}
    return {**chapter, "sourceRef": section["sourceRef"]} if "sourceRef" in section else chapter


@functools.lru_cache(maxsize=None)
def section_types(root: str) -> frozenset:
    """The section types `root` ("frontMatter" or "backMatter") may hold: the
    section branch of ast.schema.json's frontMatterNode or backMatterNode."""
    return frozenset(kind for branch in _ast_defs()[f"{root}Node"]["allOf"] if "$ref" not in branch["then"]
                     for kind in branch["then"]["properties"]["type"]["enum"])


def _section_types() -> frozenset:
    """The section types valid at either end."""
    return section_types("frontMatter") | section_types("backMatter")


def _section_at(ast: dict, op: OverrideOp, root: str, name: str) -> Optional[int]:
    """The index of the section `op` targets in `root`; None for an orphan.
    Raises when the node exists somewhere else."""
    sections = ast.get(root) or []
    at = next((i for i, node in enumerate(sections) if _matches(node, op.sourceRef)), None)
    if at is None and _find_by_source_ref(ast, op.sourceRef) is not None:
        raise InapplicableOverride(f"it is not a {name} section")
    return at


def _op_start_body(ast: dict, op: OverrideOp) -> dict:
    at = _section_at(ast, op, "frontMatter", "front-matter")
    if at is None:
        return ast
    front, base = ast["frontMatter"], _derived_id("ch", op)
    moved = [_section_as_chapter(section, n, f"{base}-{n}", op, "front matter after it")
             for n, section in enumerate(front[at:], start=1)]
    out = {**ast, "frontMatter": front[:at], "body": moved + (ast.get("body") or [])}
    # The book's existing chapters now follow the moved ones.
    return _shift_chapter_numbers(out, moved[-1]["attrs"]["id"], len(moved))


def _op_end_body(ast: dict, op: OverrideOp) -> dict:
    moved = next((c for c in ast.get("body") or [] if _matches(c, op.sourceRef) and c.get("_fromBack")), None)
    if moved is not None:
        return ast   # a split on a title folded into it moved it already: the body ends here
    at = _section_at(ast, op, "backMatter", "back-matter")
    if at is None:
        return ast
    return _body_ends_at(ast, at, op)


def _body_ends_at(ast: dict, at: int, op: OverrideOp) -> dict:
    """The back-matter section at `at`, and every one before it, moved to the
    end of the body as chapters, numbered on from its last."""
    back, base = ast["backMatter"], _derived_id("ch", op)
    last = max(_chapter_numbers(ast.get("body") or []), default=0)
    moved = [{**_section_as_chapter(section, last + n, f"{base}-{n}", op, "back matter before it"), "_fromBack": True}
             for n, section in enumerate(back[:at + 1], start=1)]
    return {**ast, "body": (ast.get("body") or []) + moved, "backMatter": back[at + 1:]}


def _in_back_section(ast: dict, source_ref: str) -> Optional[tuple[int, int]]:
    """(section index, block index) of a block directly inside a back-matter
    section, or None."""
    for at, section in enumerate(ast.get("backMatter") or []):
        if section.get("type") in _section_types():
            for index, block in enumerate(section.get("content") or []):
                if _matches(block, source_ref):
                    return at, index
    return None


def _chapter_numbers(nodes: list) -> list[int]:
    """Every chapter number in `nodes`, parts included."""
    out: list[int] = []
    for node in nodes:
        if node.get("type") == "part":
            out += _chapter_numbers(node.get("content") or [])
        elif node.get("type") == "chapter" and isinstance((node.get("attrs") or {}).get("number"), int):
            out.append(node["attrs"]["number"])
    return out


# ── delete / insert: the list that holds a node changes ────────

# Lists that may be left empty: the book's front and back matter.
_MAY_BE_EMPTY = ("frontMatter", "backMatter")


def _edit_holding_list(ast: dict, source_ref: str, edit) -> Optional[dict]:
    """The document with `edit(holder, items, index) -> items` applied to the
    list holding the node `source_ref` names, or None when no list does.
    `holder` is the owning node's type, or the root list's name."""
    def walk(node: dict) -> Optional[dict]:
        for key in _CHILD_KEYS:
            items = node.get(key)
            if not isinstance(items, list):
                continue
            holder = node.get("type") or key
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if _matches(item, source_ref):
                    return {**node, key: edit(holder, items, index)}
                changed = walk(item)
                if changed is not None:
                    return {**node, key: items[:index] + [changed] + items[index + 1:]}
        return None
    return walk(ast)


def _op_delete(ast: dict, op: OverrideOp) -> dict:
    """Removes the node. It used to be marked `_deleted` -- which no renderer
    reads, so a "deleted" paragraph still printed."""
    node = _find_by_source_ref(ast, op.sourceRef)
    if node is None:
        return ast
    if node.get("type") == "chapter":
        # Renumber first, while the chapter still marks where "after" begins.
        ast = _shift_chapter_numbers(ast, (node.get("attrs") or {}).get("id"), -1)

    def edit(holder: str, items: list, index: int) -> list:
        if len(items) == 1 and holder not in _MAY_BE_EMPTY:
            raise InapplicableOverride(f"it is the only node in its {holder}, which cannot be empty")
        return items[:index] + items[index + 1:]

    return _edit_holding_list(ast, op.sourceRef, edit) or ast


# What `insert` may add: structure with no text. Inserted words would reach the
# book without ever passing the integrity gate, which runs before `resolve`.
_INSERTABLE = ("sceneBreak", "pageBreak")


@functools.lru_cache(maxsize=1)
def _block_holders() -> frozenset:
    """Node types (and root lists) whose content is any block: where a break may go."""
    defs = _ast_defs()
    holders = {name for name, d in defs.items() if isinstance(d, dict)
               and d.get("properties", {}).get("content", {}).get("items", {}).get("$ref") == "#/$defs/blockNode"}
    for union in ("frontMatterNode", "backMatterNode"):
        for branch in defs[union]["allOf"]:
            if "$ref" not in branch["then"]:
                holders.update(branch["then"]["properties"]["type"]["enum"])
    return frozenset(holders | set(_MAY_BE_EMPTY))


def _op_insert(ast: dict, op: OverrideOp) -> dict:
    """Inserts a scene or page break after the target block. The break gets an
    id derived from the op, so a later `delete` can take it out again."""
    if op.value not in _INSERTABLE:
        raise InapplicableOverride(
            f"only {' or '.join(_INSERTABLE)} can be inserted, not {op.value!r}: "
            "inserted text would bypass the integrity gate")
    new = {"type": op.value, "sourceRef": {"docxId": _derived_id("ins", op)}, "_override": op.id}

    def edit(holder: str, items: list, index: int) -> list:
        if holder not in _block_holders():
            raise InapplicableOverride(f"a {op.value} cannot go inside a {holder}")
        return items[:index + 1] + [new] + items[index + 1:]

    return _edit_holding_list(ast, op.sourceRef, edit) or ast


def _node_op(make_transform):
    """A node-level transform as a document-level op."""
    return lambda ast, op: _rewrite(ast, op.sourceRef, make_transform(op))


# Every op this layer applies: `(document, op) -> document`.
_TRANSFORMS = {
    "reclassify": _op_reclassify,
    "retitle": _node_op(_t_retitle),
    "set_attr": _node_op(_t_set_attr),
    "flag_ambiguity": _node_op(_t_flag_ambiguity),
    "resolve_ambiguity": _node_op(_t_resolve_ambiguity),
    "delete": _op_delete,
    "insert": _op_insert,
    "split": _op_split,
    "merge": _op_merge,
    "promote": _op_promote,
    "demote": _op_demote,
    "start_body": _op_start_body,
    "end_body": _op_end_body,
}

# Ops `schemas/overrides/overrides.schema.json` accepts that no transform above
# implements. Empty now; kept so a new schema op must be either implemented or
# named here, and test_override_ops_are_applied_or_refused.py fails otherwise.
UNIMPLEMENTED_OPS: frozenset = frozenset()


def _find_by_source_ref(ast: dict, source_ref: str) -> Optional[dict]:
    """Find an AST node by its sourceRef."""
    def _search(node):
        if not isinstance(node, dict):
            return None
        src = node.get("sourceRef") or node.get("sourceRefLink") or {}
        if isinstance(src, dict) and src.get("docxId") == source_ref:
            return node
        if isinstance(src, str) and src == source_ref:
            return node
        for key in ("content", "frontMatter", "backMatter", "body"):
            val = node.get(key)
            if isinstance(val, list):
                for item in val:
                    result = _search(item)
                    if result:
                        return result
        return None
    return _search(ast)
