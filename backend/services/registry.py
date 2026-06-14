"""Local marketplace / registry service.

The LOCAL dev version of the Workshop marketplace (docs/workshop-v1/
03-ui-kit-and-marketplace.md sections 4-5). It is the real registry, not a
stub, but it runs entirely on one box with no network: releases are stored as
**git bundles** so publish/install/fork are genuine git operations against
local artifacts. The registry interface is the same shape we will point at a
remote backend later (section 5, "local-now, remote-later"); only the storage
backend swaps.

Storage layout (rooted at the WORKSHOP dir, i.e. PROJECTS_ROOT.parent):

    WORKSHOP/registry.json                       # the tracked catalog
    WORKSHOP/registry-bundles/<id>/<version>.bundle   # large git artifacts (gitignored)

registry.json shape:

    {"items": {<item_id>: {
        id, kind, title, description, version, source_slug, sensitivity,
        manifest, published_at, bundle_path,
        history: [{version, bundle_path, published_at}, ...]
    }}}

Publish bundles a project repo. Install clones the latest bundle into the
workspace and registers it as a new project, REUSING projects.py's registry
helpers so there is exactly one writer of projects.json.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from services import manifests
# Reuse the projects hub registry as the single source of truth for the project
# catalog. We deliberately import its helpers rather than re-implement the
# projects.json read/write (one writer, no divergent shapes).
from routes import projects as projects_routes

Kind = Literal["app", "kit"]

# The registry lives next to projects.json, in the WORKSHOP root. Derived from
# the projects hub PROJECTS_ROOT so test overrides (WORKSHOP_PROJECTS_ROOT_NEW)
# move the registry too, keeping smokes off the real data. Also independently
# overridable for tests that want a separate registry path.
def _workshop_root() -> Path:
    return projects_routes.PROJECTS_ROOT.parent


def _registry_path() -> Path:
    override = os.environ.get("WORKSHOP_REGISTRY_PATH")
    if override:
        return Path(override)
    return _workshop_root() / "registry.json"


def _bundles_root() -> Path:
    override = os.environ.get("WORKSHOP_REGISTRY_BUNDLES")
    if override:
        return Path(override)
    return _workshop_root() / "registry-bundles"


# Neutral committer/author env so bundle + clone operations never depend on (or
# pollute) the operator's git identity. Mirrors projects.py's _git_init env.
def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Registry",
        "GIT_AUTHOR_EMAIL": "registry@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Registry",
        "GIT_COMMITTER_EMAIL": "registry@workshop.local",
        "GIT_TERMINAL_PROMPT": "0",
    }


class RegistryError(Exception):
    """Domain error with a clean, client-safe message. The route layer maps
    these to HTTP status codes; raw subprocess errors are wrapped, never
    surfaced verbatim."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _now() -> str:
    return projects_routes._now()


# ---- registry.json IO (atomic, corrupt-tolerant) ----------------------------

def _load() -> dict:
    path = _registry_path()
    if not path.exists():
        return {"items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt/unreadable catalog: serve empty rather than crash the
        # read endpoints. A subsequent publish rewrites a clean file.
        return {"items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"items": {}}
    return data


def _save(data: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


# ---- git helpers ------------------------------------------------------------

def _run_git(args: list[str], *, cwd: Path | None = None, what: str) -> subprocess.CompletedProcess:
    """Run a git command with the neutral env. On failure raise a RegistryError
    carrying a trimmed stderr — never the raw subprocess exception."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise RegistryError(f"{what} timed out after 180s", status=504)
    except OSError as e:
        raise RegistryError(f"{what} could not run: {e}", status=500)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()[:300]
        raise RegistryError(f"{what} failed (exit {result.returncode}): {stderr}", status=400)
    return result


def _is_git_repo_with_commit(repo: Path) -> bool:
    if not (repo / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(repo), env=_git_env(),
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


# ---- version bumping --------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _next_version(existing: list[str]) -> str:
    """Pick the next release version. We use a minor-bump semver-ish scheme
    starting at 0.1.0 -> 0.2.0 -> ... The newest existing version is the basis;
    on any unparseable history we fall back to a fresh 0.1.0 line counted by
    history length so versions stay strictly increasing and unique."""
    if not existing:
        return "0.1.0"
    parsed: list[tuple[int, int, int]] = []
    for v in existing:
        m = _SEMVER_RE.match(v)
        if m:
            parsed.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if parsed:
        major, minor, _patch = max(parsed)
        return f"{major}.{minor + 1}.0"
    # No parseable history: derive from count so it never collides.
    return f"0.{len(existing) + 1}.0"


# ---- public API -------------------------------------------------------------

def _slug_to_item_id(slug: str) -> str:
    # Item ids share the slug discipline; the slug is already validated by the
    # projects hub on creation, so reuse it verbatim as the registry id.
    return slug


def publish(
    slug: str,
    *,
    version: str | None = None,
    kind: Kind = "app",
    description: str = "",
) -> dict[str, Any]:
    """Publish a project as a new registry release.

    Bundles the project's git history into WORKSHOP/registry-bundles/<id>/
    <version>.bundle and records (or appends to) the catalog item. Manifest +
    sensitivity come from the app manifest store when the slug is a declared
    app, else from the projects registry (default private), with an empty
    manifest. Returns the item record.
    """
    pdata = projects_routes._load_registry()
    project = (pdata.get("projects") or {}).get(slug)
    if not project:
        raise RegistryError(f"project not found: {slug}", status=404)

    repo = Path(project["dir"])
    if not repo.exists():
        raise RegistryError(f"project dir missing on disk: {repo}", status=400)
    if not _is_git_repo_with_commit(repo):
        raise RegistryError(
            f"project '{slug}' is not a git repo with at least one commit", status=400
        )

    item_id = _slug_to_item_id(slug)
    reg = _load()
    existing_item = reg["items"].get(item_id)
    history = list(existing_item.get("history", [])) if existing_item else []
    existing_versions = [h["version"] for h in history]

    if version:
        new_version = version.strip()
        if new_version in existing_versions:
            raise RegistryError(
                f"version {new_version} already published for {item_id}", status=409
            )
    else:
        new_version = _next_version(existing_versions)

    # Title + sensitivity + manifest resolution.
    title = (project.get("title") or slug).strip() or slug
    manifest = manifests.get_manifest(slug)
    if manifest is not None:
        sensitivity = manifest.get("sensitivity", "private")
        manifest_blob: dict[str, Any] = dict(manifest)
    else:
        sensitivity = project.get("sensitivity", "private")
        manifest_blob = {}

    # Create the git bundle. --all captures every branch + tag so the clone on
    # install restores a complete, checkoutable repo (HEAD resolves to the
    # default branch). The bundle file is the release artifact.
    bundle_dir = _bundles_root() / item_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / f"{new_version}.bundle"
    _run_git(
        ["bundle", "create", str(bundle_path), "--all"],
        cwd=repo,
        what="git bundle create",
    )

    published_at = _now()
    history_entry = {
        "version": new_version,
        "bundle_path": str(bundle_path),
        "published_at": published_at,
    }
    history.append(history_entry)

    item = {
        "id": item_id,
        "kind": kind if kind in ("app", "kit") else "app",
        "title": title,
        "description": (description or (existing_item or {}).get("description") or "").strip(),
        "version": new_version,
        "source_slug": slug,
        "sensitivity": sensitivity,
        "manifest": manifest_blob,
        "published_at": published_at,
        "bundle_path": str(bundle_path),
        "history": history,
    }
    reg["items"][item_id] = item
    _save(reg)
    return item


def list_items() -> list[dict[str, Any]]:
    reg = _load()
    return list(reg["items"].values())


def get_item(item_id: str) -> dict[str, Any] | None:
    return _load()["items"].get(item_id)


def _pick_target_slug(item_id: str, requested: str | None) -> str:
    """Resolve a free target slug. Honors an explicit request; otherwise tries
    the item id, then <id>-fork-2, -3, ... A slug is free only when it is absent
    from projects.json AND no dir exists for it (both checked, per spec)."""
    pdata = projects_routes._load_registry()
    taken_in_registry = set((pdata.get("projects") or {}).keys())
    root = projects_routes.PROJECTS_ROOT

    def _free(slug: str) -> bool:
        return slug not in taken_in_registry and not (root / slug).exists()

    if requested:
        requested = requested.strip().lower()
        if not projects_routes.SLUG_RE.match(requested):
            raise RegistryError(
                "target_slug must be kebab-case alphanumeric, start with a letter, max 48 chars",
                status=400,
            )
        if not _free(requested):
            raise RegistryError(f"target slug already exists: {requested}", status=409)
        return requested

    if _free(item_id):
        return item_id
    for n in range(2, 1000):
        candidate = f"{item_id}-fork-{n}"
        if len(candidate) > 48:
            break
        if _free(candidate):
            return candidate
    raise RegistryError(f"could not find a free target slug for {item_id}", status=409)


def install(item_id: str, *, target_slug: str | None = None) -> dict[str, Any]:
    """Fork a registry item into the workspace.

    Clones the item's latest bundle into PROJECTS_ROOT/<target_slug> and
    registers a new project via the projects hub registry helpers (single
    source of truth for projects.json). Returns the new project entry, shaped
    exactly like a projects hub record (with the slug key folded in).
    """
    item = get_item(item_id)
    if item is None:
        raise RegistryError(f"registry item not found: {item_id}", status=404)

    bundle_path = Path(item["bundle_path"])
    if not bundle_path.exists():
        raise RegistryError(
            f"bundle artifact missing for {item_id}@{item.get('version')}: {bundle_path}",
            status=400,
        )

    slug = _pick_target_slug(item_id, target_slug)
    root = projects_routes.PROJECTS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    target_dir = root / slug

    # Clone the bundle as a real git repo. git clone <bundle> restores the
    # bundled refs and checks out the default branch.
    _run_git(
        ["clone", str(bundle_path), str(target_dir)],
        what="git clone (bundle)",
    )

    now = _now()
    record = {
        "title": item.get("title") or slug,
        "purpose": f"forked from registry: {item_id}",
        "dir": str(target_dir),
        "sensitivity": item.get("sensitivity", "private"),
        "state": "mounted",
        "surfaces": {"integrated": None, "standalone": None, "public": None},
        "needs": [],
        "provides": [],
        "created": now,
        "last_touched": now,
        "claude_session": None,
    }
    # Single writer: read-modify-write projects.json through the projects hub
    # helpers, exactly as create_project / import_project do.
    pdata = projects_routes._load_registry()
    pdata.setdefault("projects", {})[slug] = record
    projects_routes._save_registry(pdata)

    return {"slug": slug, **record}
