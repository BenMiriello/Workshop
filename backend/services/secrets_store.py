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

def _root() -> Path:
    """Where the values live. Derived from the runtime dir, not from a home path.

    This used to assume the repository sits at `~/workshop`, which is true of the
    machine it was written on and of nothing else. Resolved per call so an
    override set after import (tests, a relocated instance) is honoured.
    """
    from wsplatform import paths
    return paths.env_path("WORKSHOP_SECRETS_ROOT", "secrets", base="runtime")


def _path(slug: str) -> Path:
    # slug is a registered project slug (no path separators); guard anyway.
    safe = slug.replace("/", "_").replace("..", "_")
    return _root() / f"{safe}.json"


# Workspace-scope secrets — shared across projects. Deploy provider credentials
# (Cloudflare API tokens, account ids, etc.) commonly live here so the same
# token drives every project's deploys without per-project duplication. Per-
# project values still take precedence when both are set.
WORKSPACE_SLUG = "_workspace"


def load_at(path: Path, slug: str) -> dict[str, str]:
    """`load()`, for a store whose file is addressed directly.

    THE SLUG IS STILL REQUIRED, and it is not the file name. It is the AAD bound
    into both layers of the encrypted envelope (`secrets_crypto.encrypt`), so it
    is what stops one principal's file being renamed over another's. Passing the
    path and the slug separately is what lets a store MOVE without every value
    in it failing authentication afterwards.
    """
    from services import secrets_crypto

    p = Path(path)
    if not p.exists():
        return {}
    try:
        blob = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    if secrets_crypto.is_encrypted(blob):
        return secrets_crypto.decrypt(blob, slug)
    try:
        data = json.loads(blob)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def load(slug: str) -> dict[str, str]:
    """All stored values for a project. Empty dict if none. Internal/runner use
    only — do NOT return this through an API.

    Reads plaintext and encrypted files alike, so turning encryption on does not
    strand what is already stored. An encrypted file that CANNOT be decrypted
    raises rather than reading as empty: "no secrets here" and "this deployment
    cannot read its own store" are different answers, and returning the first
    for the second would tell the operator to paste their credentials again.
    """
    return load_at(_path(slug), slug)


def write_at(path: Path, slug: str, values: dict[str, str]) -> None:
    """Serialise `values` to `path`, encrypted when a KEK is configured.

    Creates the file 0600 from the start so a value never sits world readable,
    and replaces atomically so a crashed write cannot truncate a store. The
    parent directory is created but its mode is left alone: a caller-supplied
    directory (an owner's runtime partition) holds more than secrets and is not
    this module's to lock down.
    """
    from services import secrets_crypto

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    # Encrypted when a key encryption key is configured, plaintext otherwise.
    # A plaintext file therefore becomes encrypted on its next write, with no
    # migration step to run. 0600 either way: encryption is a second layer over
    # the file mode, not a replacement for it.
    if secrets_crypto.enabled():
        blob = secrets_crypto.encrypt(values, slug)
    else:
        blob = json.dumps(values, separators=(",", ":"))
    # Create the file 0600 from the start so the value never sits world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(blob)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _write(slug: str, values: dict[str, str]) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    write_at(_path(slug), slug, values)


def _merge(cur: dict[str, str], values: dict[str, str]) -> dict[str, str]:
    """Apply an update to a loaded store. A blank/None value DELETES the key
    (the UI 'clear' affordance)."""
    for k, v in values.items():
        k = str(k).strip()
        if not k:
            continue
        if v is None or str(v) == "":
            cur.pop(k, None)
        else:
            cur[k] = str(v)
    return cur


def set_values_at(path: Path, slug: str, values: dict[str, str]) -> None:
    """`set_values()` against a directly addressed file. Never logs the values."""
    write_at(path, slug, _merge(load_at(path, slug), values))


def delete_key_at(path: Path, slug: str, key: str) -> bool:
    cur = load_at(path, slug)
    if key in cur:
        cur.pop(key, None)
        write_at(path, slug, cur)
        return True
    return False


def set_values(slug: str, values: dict[str, str]) -> None:
    """Merge new key→value pairs into the store. A blank/None value DELETES the
    key (the UI 'clear' affordance). Never logs the values."""
    _write(slug, _merge(load(slug), values))


def delete_key(slug: str, key: str) -> bool:
    return delete_key_at(_path(slug), slug, key)


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
