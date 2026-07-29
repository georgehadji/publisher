"""
Override layer — human decisions stored separately from the derived AST.

From ARCHITECTURE.md §2.6 and BUILD_PLAN.md §3.17:
Overrides are addressed by sourceRef, not by index.
On re-ingest, rebase by exact sourceRef → content-hash match → fuzzy text match → orphan.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


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
    3. Fall back to fuzzy text match
    4. Mark orphaned and surface to user
    
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
        
        # Step 3: Fuzzy text match
        if op.sourceFallbackText:
            matches = _fuzzy_match(op.sourceFallbackText, new_map, cutoff=0.8)
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
        
        # Step 4: Orphaned
        orphaned.append(OrphanedOp(
            op=op,
            reason="no_source_ref",
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


def _fuzzy_match(text: str, source_map: dict, cutoff: float = 0.8) -> list[dict]:
    """Fuzzy-match text against all entries in the source map."""
    matches = []
    for ref, entry in source_map.items():
        ratio = difflib.SequenceMatcher(None, text.lower(), entry["text"].lower()).ratio()
        if ratio >= cutoff:
            matches.append({"sourceRef": ref, **entry, "similarity": ratio})
    return sorted(matches, key=lambda m: -m["similarity"])


def apply_overrides(ast: dict, ops: list[OverrideOp]) -> dict:
    """
    Apply override operations to an AST.
    
    Overrides never mutate the original AST in place — they produce
    an "effective document" that is the AST with overrides applied.
    The underlying AST remains unchanged.
    """
    effective = json.loads(json.dumps(ast))  # deep copy
    
    for op in ops:
        if op.op == "reclassify":
            _apply_reclassify(effective, op)
        elif op.op == "retitle":
            _apply_retitle(effective, op)
        elif op.op == "delete":
            _apply_delete(effective, op)
        elif op.op == "flag_ambiguity":
            _apply_flag_ambiguity(effective, op)
        # Other ops: split, merge, etc.
    
    return effective


def _apply_reclassify(ast: dict, op: OverrideOp):
    """Reclassify a node (e.g., heading → chapter-title)."""
    node = _find_by_source_ref(ast, op.sourceRef)
    if node:
        node["type"] = op.to_value
        node["_override"] = op.id


def _apply_retitle(ast: dict, op: OverrideOp):
    """Change a node's title attribute."""
    node = _find_by_source_ref(ast, op.sourceRef)
    if node and "attrs" in node and isinstance(node["attrs"], dict):
        if "title" in node["attrs"]:
            node["attrs"]["title"] = op.value
            node["_override"] = op.id


def _apply_delete(ast: dict, op: OverrideOp):
    """Remove a node from the AST."""
    parent, key, index = _find_parent(ast, op.sourceRef)
    if parent is not None and key is not None and index is not None:
        if isinstance(parent[key], list) and 0 <= index < len(parent[key]):
            parent[key][index]["_deleted"] = True


def _apply_flag_ambiguity(ast: dict, op: OverrideOp):
    """Mark a node as ambiguous for human review."""
    node = _find_by_source_ref(ast, op.sourceRef)
    if node:
        node.setdefault("_flags", []).append({
            "id": op.id,
            "message": op.rationale or "Flagged for review",
            "actor": op.actor,
        })


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


def _find_parent(ast: dict, source_ref: str):
    """Find the parent list and index of a node by sourceRef."""
    def _search(node, path=""):
        if not isinstance(node, dict):
            return None, None, None
        for key in ("content", "frontMatter", "backMatter", "body"):
            val = node.get(key)
            if isinstance(val, list):
                for i, item in enumerate(val):
                    src = item.get("sourceRef") or item.get("sourceRefLink") or {}
                    if isinstance(src, dict) and src.get("docxId") == source_ref:
                        return node, key, i
                    if isinstance(src, str) and src == source_ref:
                        return node, key, i
                    result = _search(item, f"{path}/{key}/{i}")
                    if result[0]:
                        return result
        return None, None, None
    return _search(ast)
