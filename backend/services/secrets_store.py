"""Per-project secret VALUES — the store the env requirements (declared in the
app manifest) get filled into.

Split, per the universal platform model (Vercel/Railway/Heroku/Replit): the
manifest declares *requirements* (key names + metadata, no values, safe in the
repo); this store holds the *values* (gitignored, mode 0600, never logged,
injected into a service's env only at spawn/build time). The status() call
returns presence per key — never the value — which is the `check_secrets`
semantics any agent can safely consult.

Single-user system: no per-tenant ACLs or audit trails here. The only hard
rules are the security consensus: values never touch git, never get logged,
and never come back out through a presence/status path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(os.environ.get(
    "WORKSHOP_SECRETS_ROOT",
    str(Path.home() / "workshop" / ".runtime" / "secrets"),
))


def _path(slug: str) -> Path:
    # slug is a registered project slug (no path separators); guard anyway.
    safe = slug.replace("/", "_").replace("..", "_")
    return _ROOT / f"{safe}.json"


# Workspace-scope secrets — shared across projects. Deploy provider credentials
# (Cloudflare API tokens, account ids, etc.) commonly live here so the same
# token drives every project's deploys without per-project duplication. Per-
# project values still take precedence when both are set.
WORKSPACE_SLUG = "_workspace"


def load(slug: str) -> dict[str, str]:
    """All stored values for a project. Empty dict if none. Internal/runner use
    only — do NOT return this through an API."""
    p = _path(slug)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(slug: str, values: dict[str, str]) -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_ROOT, 0o700)
    except OSError:
        pass
    p = _path(slug)
    tmp = p.with_suffix(".json.tmp")
    # Create the file 0600 from the start so the value never sits world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(values, fh, separators=(",", ":"))
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def set_values(slug: str, values: dict[str, str]) -> None:
    """Merge new key→value pairs into the store. A blank/None value DELETES the
    key (the UI 'clear' affordance). Never logs the values."""
    cur = load(slug)
    for k, v in values.items():
        k = str(k).strip()
        if not k:
            continue
        if v is None or str(v) == "":
            cur.pop(k, None)
        else:
            cur[k] = str(v)
    _write(slug, cur)


def delete_key(slug: str, key: str) -> bool:
    cur = load(slug)
    if key in cur:
        cur.pop(key, None)
        _write(slug, cur)
        return True
    return False


def status(slug: str, keys: list[str]) -> dict[str, bool]:
    """Presence per requested key (True if set + non-empty). NEVER the value —
    this is the agent-safe `check_secrets` view."""
    cur = load(slug)
    return {k: bool(cur.get(k)) for k in keys}


def set_keys(slug: str) -> list[str]:
    """The key names that currently have a value (no values). Safe to surface."""
    return sorted(k for k, v in load(slug).items() if v)


def env_for(slug: str, keys: list[str] | None = None) -> dict[str, str]:
    """Values for env injection. If `keys` is given, only those (a service's
    scoped subset); else everything. Runner-only."""
    cur = load(slug)
    if keys is None:
        return dict(cur)
    return {k: cur[k] for k in keys if k in cur}


def env_with_workspace(slug: str, keys: list[str] | None = None) -> dict[str, str]:
    """Same as env_for but with a workspace-scope fallback: keys the project
    doesn't set are filled from the shared _workspace store. Per-project
    values always win. Runner-only."""
    project_vals = load(slug)
    workspace_vals = load(WORKSPACE_SLUG) if slug != WORKSPACE_SLUG else {}
    if keys is None:
        merged = dict(workspace_vals)
        merged.update(project_vals)
        return merged
    out: dict[str, str] = {}
    for k in keys:
        if k in project_vals:
            out[k] = project_vals[k]
        elif k in workspace_vals:
            out[k] = workspace_vals[k]
    return out


def status_with_workspace(slug: str, keys: list[str]) -> dict[str, dict]:
    """Presence per key with source, workspace-fallback aware. Each entry is
    {set: bool, source: "project"|"workspace"|None}. Safe to surface — never
    returns values."""
    project_vals = load(slug)
    workspace_vals = load(WORKSPACE_SLUG) if slug != WORKSPACE_SLUG else {}
    out: dict[str, dict] = {}
    for k in keys:
        if project_vals.get(k):
            out[k] = {"set": True, "source": "project"}
        elif workspace_vals.get(k):
            out[k] = {"set": True, "source": "workspace"}
        else:
            out[k] = {"set": False, "source": None}
    return out
