"""Filesystem roots for the platform.

Every path the backend needs is derived from where the repository actually sits,
not from any one operator's home directory. A deployment that lives somewhere
else needs no source edits; the env overrides exist for layouts that split the
runtime or config directories off the repo.

    WORKSHOP_ROOT        the repository root (default: two levels above this file)
    WORKSHOP_RUNTIME     mutable per-instance state (default: <root>/.runtime)
    WORKSHOP_CONFIG_DIR  operator config + secrets (default: ~/.config/workshop)
"""

from __future__ import annotations

import os
from pathlib import Path

_THIS = Path(__file__).resolve()


def workshop_root() -> Path:
    """The repository root. `backend/services/paths.py` -> up two.

    Always absolute. compose_gen writes these paths into generated compose files
    as bind-mount sources, and docker resolves a bind mount on the DAEMON's
    filesystem: a relative source there does not fail, it silently creates an
    empty directory and starts the app against nothing. Resolving here makes
    every downstream path safe by construction rather than by each call site
    remembering.
    """
    env = os.environ.get("WORKSHOP_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return _THIS.parents[2]


def runtime_dir() -> Path:
    env = os.environ.get("WORKSHOP_RUNTIME")
    if env:
        return Path(env).expanduser().resolve()
    return workshop_root() / ".runtime"


def config_dir() -> Path:
    env = os.environ.get("WORKSHOP_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "workshop"


def projects_root() -> Path:
    """`projects/` — where project working trees live. ONE directory, for
    everybody, deliberately.

    A project is owned (see the `owner_id` on its registry record), but it is
    owned by RECORD, not by directory, and the reason is that `project:<slug>`
    is a global scope key. It is the address a chat binds to, a task store is
    keyed by, a knowledge layer sits under, an agent's role uniqueness is
    checked against, and — in `secrets_store` — the AAD a project's secrets are
    encrypted with. Give two owners a `projects/` each and both can mint
    `project:alpha`: one key, two directories, which is precisely the alias
    multiplication ADR 0014 exists to end, arriving through the back door.

    So slugs stay globally unique and ownership is enforced where the record is
    read. A second owner cannot take a slug someone else holds — the same trade
    a shared username namespace makes, and the honest cost is that "alpha is
    taken" is observable.

    One source of truth, because this was previously spelled in NINE modules
    under THREE different env var names (`worktrees.py` read
    `WORKSHOP_REGISTRY_PATH`, which belongs to the marketplace registry — a
    latent bug where setting one moved the other). This file's own history
    already records what that costs: "a path spelled twice is a path that will
    be spelled wrong in one of them."
    """
    return env_path("WORKSHOP_PROJECTS_ROOT_NEW", "projects", base="root")


def projects_registry() -> Path:
    """`.runtime/projects.json` — the project records (slug -> {dir, owner_id,
    state, sensitivity, ...}). Global and flat, keyed by a globally unique slug;
    see `projects_root` for why, and `routes/projects.py:_owns` for the check
    that makes the `owner_id` on each record mean something."""
    return Path(os.environ.get(
        "WORKSHOP_PROJECTS_REGISTRY", str(runtime_dir() / "projects.json")))


def chat_cwd() -> Path:
    """Working directory for a chat turn that has no scope of its own.

    The general assistant, app assistants and assistant-runtime agents are not
    bound to a project or a build tree, so their turns still need *a* directory
    to run in. `WORKSHOP_CHAT_CWD` names it. What it must never be is the
    account's home: that default (`os.path.expanduser("~")`, spelled separately
    in three call sites) put every turn without a scope in the directory
    holding `~/.config/clawdbot`, `~/.claude.json`, `~/.ssh` and every other
    credential on the box — so a scope with any filesystem tool at all had the
    whole host in reach, and nothing about the scope said so.

    Unset now resolves to a real, empty, per-owner directory instead. Created
    on demand so the spawn cannot fail on a missing cwd, and per-owner for the
    same reason the notes vault is: two accounts running a scope-less turn
    should not share a scratch directory either.

    See docs/security-audit-2026-08-11-scope-separation.md section 2.
    """
    env = os.environ.get("WORKSHOP_CHAT_CWD")
    if env:
        return Path(env).expanduser()
    from services import ownership
    owner = ownership.owner_or_none()
    base = (ownership.user_runtime_dir(owner) if owner is not None
            else runtime_dir() / "chat")
    d = base / "chat-cwd" if owner is not None else base
    d.mkdir(parents=True, exist_ok=True)
    return d


def root(*parts: str) -> Path:
    return workshop_root().joinpath(*parts)


def runtime(*parts: str) -> Path:
    return runtime_dir().joinpath(*parts)


def shared(*parts: str) -> Path:
    return workshop_root().joinpath("shared", *parts)


def scripts(*parts: str) -> Path:
    return workshop_root().joinpath("scripts", *parts)


def config(*parts: str) -> Path:
    return config_dir().joinpath(*parts)


def env_path(var: str, *default_parts: str, base: str = "runtime") -> Path:
    """Read `var`, else fall back to a derived path under root/runtime/config.

    Lets a call site keep its documented env var while losing the machine
    specific literal it used to carry as the default.
    """
    raw = os.environ.get(var)
    if raw:
        return Path(raw).expanduser()
    bases = {"root": root, "runtime": runtime, "shared": shared, "config": config}
    return bases[base](*default_parts)
