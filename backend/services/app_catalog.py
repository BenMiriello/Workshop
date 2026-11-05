"""The app catalog: which apps this installation can install, read from a repo.

The catalog is a git repository holding `catalog.json`:

    {"apps": [{"slug": "...", "name": "...", "repo": "<git url>",
               "description": "...", "default": true}]}

Its URL is an installation setting (`installation.app_catalog_url`). It is
fetched with a shallow clone into the runtime cache, then pulled, so a private
catalog works through whatever git credentials the machine already has. The
platform names no app: every name here is data in that repository.

This module reads the catalog and keeps the one piece of state it owns, the
set of apps whose default has been applied (`.runtime/app-catalog-state.json`).
Installing and turning apps on and off is `services/app_catalog_apply.py`.
See docs/dictation-and-clipboard.md, "Default apps".
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from routes import projects as projects_routes
from services import installation, registry
from wsplatform import paths

__all__ = ["Entry", "FETCH_TTL_S", "applied", "entries", "mark_applied", "parse_catalog"]

FETCH_TTL_S = 300
CATALOG_FILE = "catalog.json"

_LOCK = threading.Lock()
_last_fetch: dict[str, float] = {}


@dataclass(frozen=True)
class Entry:
    slug: str
    name: str
    repo: str
    description: str = ""
    default: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def parse_catalog(raw: object) -> list[Entry]:
    """`catalog.json` as entries. An entry without a valid slug or a repo is
    skipped, as is a second entry with a slug already taken: one bad line costs
    itself and nothing else."""
    items = raw.get("apps") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[Entry] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip().lower()
        repo = str(item.get("repo") or "").strip()
        if not projects_routes.SLUG_RE.match(slug) or not repo or slug in seen:
            continue
        seen.add(slug)
        name = str(item.get("name") or "").strip() or slug
        desc = str(item.get("description") or "").strip()
        out.append(Entry(slug=slug, name=name[:80], repo=repo,
                         description=desc[:400], default=item.get("default") is True))
    return out


def _cache_dir() -> Path:
    return paths.runtime("app-catalog")


def _state_path() -> Path:
    return paths.runtime("app-catalog-state.json")


def _remote_url(repo: Path) -> str:
    # Without its own .git, git would answer for the repository around it.
    if not (repo / ".git").exists():
        return ""
    try:
        r = registry._run_git(["remote", "get-url", "origin"], cwd=repo, what="git remote")
    except registry.RegistryError:
        return ""
    return r.stdout.strip()


def _fetch(url: str) -> None:
    """Bring the cached clone to the catalog's newest commit."""
    cache = _cache_dir()
    if _remote_url(cache) == url:
        registry._run_git(["fetch", "--depth", "1", "origin", "HEAD"], cwd=cache,
                          what="git fetch (app catalog)")
        registry._run_git(["reset", "--hard", "FETCH_HEAD"], cwd=cache,
                          what="git reset (app catalog)")
        return
    if cache.exists():
        shutil.rmtree(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    registry._run_git(["clone", "--depth", "1", url, str(cache)], what="git clone (app catalog)")


def entries(*, refresh: bool = False) -> tuple[list[Entry], str | None]:
    """The catalog's apps and the reason a fetch failed, if one did.

    Fetched at most every FETCH_TTL_S unless `refresh`. When the fetch fails
    the last copy fetched is still read, so a network blip costs freshness
    only. No catalog set is ([], None)."""
    url = installation.app_catalog_url()
    if not url:
        return [], None
    error: str | None = None
    with _LOCK:
        stale = time.monotonic() - _last_fetch.get(url, float("-inf")) > FETCH_TTL_S
        if refresh or stale:
            try:
                _fetch(url)
                _last_fetch[url] = time.monotonic()
            except registry.RegistryError as e:
                error = e.message
        cache = _cache_dir()
        if _remote_url(cache) != url:
            return [], error or "the catalog has not been fetched yet"
        try:
            raw = json.loads((cache / CATALOG_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return [], error or f"{CATALOG_FILE} is unreadable: {e}"
    return parse_catalog(raw), error


def applied() -> set[str]:
    """Apps whose default has been applied, or that the operator turned on or
    off: the catalog never installs or starts one of these again by itself."""
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    items = raw.get("applied") if isinstance(raw, dict) else None
    return {s for s in items if isinstance(s, str)} if isinstance(items, list) else set()


def mark_applied(slug: str) -> None:
    done = applied()
    if slug in done:
        return
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"applied": sorted(done | {slug})}, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
