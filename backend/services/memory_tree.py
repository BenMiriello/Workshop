"""Memory inheritance tree resolver.

Reads `~/workshop/shared/memory-tree.yaml`. Given an agent's persona path
(e.g. "ops/personal-ai"), returns the effective set of global blocks +
archival namespaces it should have access to.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml


TREE_PATH = Path(os.environ.get(
    "WORKSHOP_MEMORY_TREE",
    "/home/clawd-a/workshop/shared/memory-tree.yaml",
))


@lru_cache(maxsize=1)
def _load_tree() -> dict:
    if not TREE_PATH.exists():
        return {"root": {"blocks": [], "archival_namespaces": [], "children": {}}}
    return yaml.safe_load(TREE_PATH.read_text()) or {}


def _walk(node: dict, path: list[str], depth: int = 0) -> tuple[set[str], set[str]]:
    """Walk a single node, collecting blocks + namespaces. Recurse into the
    child named at path[depth] if any."""
    blocks: set[str] = set(node.get("blocks") or [])
    namespaces: set[str] = set(node.get("archival_namespaces") or [])
    if depth < len(path):
        child = (node.get("children") or {}).get(path[depth])
        if child:
            b2, n2 = _walk(child, path, depth + 1)
            blocks |= b2
            namespaces |= n2
    return blocks, namespaces


def effective_memory(persona_path: str) -> dict:
    """`persona_path` is slash-separated, e.g. "ops/personal-ai". Returns
    {blocks, archival_namespaces, persona_path, resolved_depth}."""
    tree = _load_tree()
    root = tree.get("root", {})
    path = [p for p in persona_path.split("/") if p]
    blocks, namespaces = _walk(root, path)
    return {
        "persona_path": persona_path,
        "blocks": sorted(blocks),
        "archival_namespaces": sorted(namespaces),
        "resolved_depth": len(path),
    }


def invalidate_cache() -> None:
    _load_tree.cache_clear()
