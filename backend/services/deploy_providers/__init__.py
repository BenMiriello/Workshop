"""Deploy provider registry.

Each provider module exposes a module-level KIND string and the functions
defined in .base (preflight, build, upload, status, rollback). New providers
are one file each — add to _REGISTRY below.
"""
from __future__ import annotations

from types import ModuleType

from . import cloudflare_pages, rsync_ssh


_REGISTRY: dict[str, ModuleType] = {
    cloudflare_pages.KIND: cloudflare_pages,
    rsync_ssh.KIND: rsync_ssh,
}


def kinds() -> list[str]:
    return sorted(_REGISTRY.keys())


def get(kind: str) -> ModuleType:
    if kind not in _REGISTRY:
        raise KeyError(f"unknown deploy provider: {kind}")
    return _REGISTRY[kind]


def describe_all() -> list[dict]:
    """Provider catalog for the frontend 'Add target' modal. One entry per
    provider: kind + human label + env_requirements + fields the target
    config expects."""
    out = []
    for kind, mod in sorted(_REGISTRY.items()):
        out.append({
            "kind": kind,
            "label": getattr(mod, "LABEL", kind),
            "env_requirements": mod.env_requirements(),
            "target_fields": getattr(mod, "TARGET_FIELDS", []),
            "source_kinds": getattr(mod, "SOURCE_KINDS", ["git-branch"]),
        })
    return out
