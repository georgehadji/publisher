"""
Override layer — human decisions stored separately from the derived AST.

From ARCHITECTURE.md §2.6 and BUILD_PLAN.md §3.17:
Overrides are addressed by sourceRef, not by index.
On re-ingest, rebase by exact sourceRef → content-hash match → fuzzy text match → orphan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .rules import normalize_text

# BUILD_PLAN.md §3.7: "fuzzy (token Jaccard >= 0.9)".
FUZZY_CUTOFF = 0.9


@dataclass
class OverrideOp:
    """A single override operation."""
    id: str
    sourceRef: str  # "docx:body/p[412]#h3a91c"
    op: str  # "reclassify", "retitle", "split", "merge", "delete", "flag_ambiguity"
    actor: str
    sourceContentHash: Optional[str] = None  # sha256 of the original text
    sourceFallbackText: Optional[str] = None  # fallback for fuzzy matching
    path: Optional[str] = None  # JSON Pointer within the AST
    from_value: Optional[str] = None  # original value
    to_value: Optional[str] = None  # new value
    value: Optional[Any] = None  # for retitle, etc.
    rationale: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class OverrideSet:
    """A set of overrides for one document lineage."""
    schema: str = "overrides/1"
    document_id: str = ""
    ast_version: int = 1
    ops: list[OverrideOp] = field(default_factory=list)
    orphaned_ops: list[dict] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class OrphanedOp:
    """An override that couldn't be rebased."""
    op: OverrideOp
    reason: str  # "no_source_ref", "content_mismatch", "fuzzy_match_failed"
    fuzzy_suggestions: list[str] = field(default_factory=list)


# ── Rebase ladder ───────────────────────────────────────────────

def rebase_overrides(
    overrides: list[OverrideOp],
    old_ast: dict,
    new_ast: dict,
) -> tuple[list[OverrideOp], list[OrphanedOp]]:
    """
    Rebase overrides from an old AST onto a new AST.
    
    Rebase strategy (ARCHITECTURE.md §2.6a):
    1. Exact sourceRef match (stable docx ID)
    2. Fall back to content-hash match
    3. Fall back to normalized-text match
    4. Fall back to fuzzy token-Jaccard match (>= 0.9)
    5. Mark orphaned and surface to user
    
    Returns (successfully_rebased, orphaned).
    """
    # Build source maps for both ASTs
    old_map = _build_source_map(old_ast)
    new_map = _build_source_map(new_ast)
    
    rebased: list[OverrideOp] = []
    orphaned: list[OrphanedOp] = []
    
    for op in overrides:
        # Step 1: Exact sourceRef match
        if op.sourceRef in new_map:
            rebased.append(op)
            continue
        
        # Step 2: Content-hash match
        if op.sourceContentHash:
            matched = _find_by_content_hash(op.sourceContentHash, new_map)
            if matched:
                # Update sourceRef to new AST position
                new_op = OverrideOp(
                    id=op.id,
                    sourceRef=matched["sourceRef"],
                    sourceContentHash=matched["contentHash"],
                    op=op.op, from_value=op.from_value, to_value=op.to_value,
                    value=op.value, rationale=op.rationale,
                    actor=op.actor, created_at=op.created_at,
                )
                rebased.append(new_op)
                continue
        
        # Step 3: Normalized-text match.
        # BUILD_PLAN.md §3.7's ladder is
        #   exact sourceRef -> content-hash -> NORMALIZED-TEXT -> fuzzy -> orphaned.
        # This rung was missing entirely, so a node whose text was unchanged but whose
        # whitespace/quotes/unicode form shifted (the common re-export case) skipped
        # straight to fuzzy matching, or orphaned.
        if op.sourceFallbackText:
            matched = _find_by_normalized_text(op.sourceFallbackText, new_map)
            if matched:
                rebased.append(_relocate(op, matched))
                continue

        # Step 4: Fuzzy text match
        if op.sourceFallbackText:
            matches = _fuzzy_match(op.sourceFallbackText, new_map, cutoff=FUZZY_CUTOFF)
            if matches:
                best = matches[0]
                new_op = OverrideOp(
                    id=op.id,
                    sourceRef=best["sourceRef"],
                    sourceContentHash=best["contentHash"],
                    op=op.op, from_value=op.from_value, to_value=op.to_value,
                    value=op.value, rationale=op.rationale,
                    actor=op.actor, created_at=op.created_at,
                )
                rebased.append(new_op)
                continue
        
        # Step 5: Orphaned
        # Report WHY it orphaned. "content_mismatch" and "fuzzy_match_failed" were
        # declared on OrphanedOp.reason and never emitted, so every orphan looked like
        # a missing sourceRef regardless of which rung actually failed.
        if op.sourceFallbackText:
            reason = "fuzzy_match_failed"
        elif op.sourceContentHash:
            reason = "content_mismatch"
        else:
            reason = "no_source_ref"
        orphaned.append(OrphanedOp(
            op=op,
            reason=reason,
            fuzzy_suggestions=[m["sourceRef"] for m in _fuzzy_match(
                op.sourceFallbackText or op.sourceRef, new_map, cutoff=0.5
            )[:3]],
        ))
    
    return rebased, orphaned


def _build_source_map(ast: dict) -> dict[str, dict]:
    """
    Build a flat map of sourceRef → { text, contentHash } from an AST.
    Walks all nodes recursively.
    """
    source_map: dict[str, dict] = {}
    
    def _walk(node, path=""):
        if not isinstance(node, dict):
            return
        
        # Check for sourceRef
        src = node.get("sourceRef") or node.get("sourceRefLink")
        if isinstance(src, dict):
            ref = src.get("docxId", "") or src.get("sourceRef", "")
            if ref:
                text = _collect_text(node)
                source_map[ref] = {
                    "sourceRef": ref,
                    "text": text,
                    "contentHash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "path": path,
                }
        
        # Recurse into content arrays
        for key in ("content", "frontMatter", "backMatter", "body"):
            val = node.get(key)
            if isinstance(val, list):
                for i, item in enumerate(val):
                    _walk(item, f"{path}/{key}/{i}")
            elif isinstance(val, dict):
                _walk(val, f"{path}/{key}")
    
    _walk(ast)
    return source_map


def _collect_text(node: dict) -> str:
    """Collect all text from a node and its children."""
    texts: list[str] = []
    
    def _walk(n):
        if isinstance(n, dict):
            if n.get("type") == "text":
                texts.append(n.get("text", ""))
            for val in n.values():
                if isinstance(val, (dict, list)):
                    _walk(val)
        elif isinstance(n, list):
            for item in n:
                _walk(item)
    
    _walk(node)
    return "".join(texts)


def _find_by_content_hash(content_hash: str, source_map: dict) -> Optional[dict]:
    """Find a node by content hash."""
    for entry in source_map.values():
        if entry.get("contentHash") == content_hash:
            return entry
    return None


def _token_jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity, the metric BUILD_PLAN.md §3.7 specifies."""
    ta, tb = set(normalize_text(a).split()), set(normalize_text(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_by_normalized_text(text: str, source_map: dict) -> Optional[dict]:
    """Exact match after normalization — the ladder rung between content-hash and fuzzy."""
    target = normalize_text(text)
    for ref, entry in source_map.items():
        if normalize_text(entry["text"]) == target:
            return {"sourceRef": ref, **entry}
    return None


def _relocate(op: "OverrideOp", matched: dict) -> "OverrideOp":
    """Copy `op` onto a new sourceRef/contentHash. Overrides are immutable."""
    return OverrideOp(
        id=op.id,
        sourceRef=matched["sourceRef"],
        sourceContentHash=matched["contentHash"],
        op=op.op, from_value=op.from_value, to_value=op.to_value,
        value=op.value, rationale=op.rationale,
        actor=op.actor, created_at=op.created_at,
    )


def _fuzzy_match(text: str, source_map: dict, cutoff: float = FUZZY_CUTOFF) -> list[dict]:
    """
    Fuzzy-match text against the source map using token-set Jaccard.

    Was `difflib.SequenceMatcher` at cutoff 0.8. Two deviations from §3.7, which
    specifies "fuzzy (token Jaccard >= 0.9)" and "never silently reattached below
    threshold": the wrong metric, and a threshold below the stated one — so overrides
    WERE silently reattached below the documented bar.
    """
    matches = []
    for ref, entry in source_map.items():
        ratio = _token_jaccard(text, entry["text"])
        if ratio >= cutoff:
            matches.append({"sourceRef": ref, **entry, "similarity": ratio})
    return sorted(matches, key=lambda m: -m["similarity"])


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


def _t_reclassify(op: OverrideOp):
    def t(node: dict) -> dict:
        return {**node, "type": op.to_value, "_override": op.id}
    return t


def _t_retitle(op: OverrideOp):
    def t(node: dict) -> dict:
        attrs = node.get("attrs")
        if not isinstance(attrs, dict) or "title" not in attrs:
            return node
        return {**node, "attrs": {**attrs, "title": op.value}, "_override": op.id}
    return t


def _t_delete(op: OverrideOp):
    def t(node: dict) -> dict:
        return {**node, "_deleted": True}
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


_TRANSFORMS = {
    "reclassify": _t_reclassify,
    "retitle": _t_retitle,
    "delete": _t_delete,
    "flag_ambiguity": _t_flag_ambiguity,
}


def apply_overrides(ast: dict, ops: list[OverrideOp]) -> dict:
    """
    Apply override operations to an AST.

    Overrides never mutate the original AST in place — they produce an "effective
    document" that is the AST with overrides applied. The underlying AST is untouched,
    and every subtree that no override reached is SHARED with it, not copied.

    Previously this did `json.loads(json.dumps(ast))` — a full serialize+reparse of the
    whole document, then in-place mutation of the copy. That is O(n) per call and
    conflicts with D4 (a function taking a whole document into memory needs a
    justification) and with §3.7's structural-sharing requirement.
    """
    effective = ast
    for op in ops:
        make_transform = _TRANSFORMS.get(op.op)
        if make_transform is None:
            continue  # split/merge not implemented yet
        effective = _rewrite(effective, op.sourceRef, make_transform(op))
    return effective


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


