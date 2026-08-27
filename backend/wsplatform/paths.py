"""Filesystem roots for the platform.

Every path the backend needs is derived from where the repository actually sits,
not from any one operator's home directory. A deployment that lives somewhere
else needs no source edits; the env overrides exist for layouts that split the
runtime or config directories off the repo.

    WORKSHOP_ROOT        the repository root (default: two levels above this file)
    WORKSHOP_RUNTIME     mutable per-instance state (default: <root>/.runtime)
    WORKSHOP_CONFIG_DIR  operator config + secrets (default: ~/.config/workshop)

MOVED FROM `services/paths.py` (ADR 0024): a pure leaf with in-degree ~49 and no
imports but `os`/`pathlib`, exactly the "closed vocabulary is platform, not
domain" shape the ADR names it as. `wsplatform.dev_mode -> services.paths` was
the upward edge the layering ratchet caught; this move is the fix, not a
rewrite of anything the module does.
"""

from __future__ import annotations

import os
from pathlib import Path

_THIS = Path(__file__).resolve()


def workshop_root() -> Path:
    """The repository root. `backend/wsplatform/paths.py` -> up two.

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

    The general assistant, app assistants and any agent that is not a maker are
    not bound to a project or a build tree, so their turns still need *a* directory
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

    See docs/masini/security-audit-2026-08-11-scope-separation.md section 2.
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


def scope_root(scope_key: str) -> Path:
    """The canonical root directory of a tree-owning scope: the one directory
    that is the whole world of that scope.

        project:<slug> -> projects_root()/<slug>
        app:<name>     -> workshop_root()/apps/<name>
        agent:<slug>   -> runtime_dir()/agents/<slug>

    The SINGLE mapping every other per-scope path derives from (Layer 0 of
    docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md).
    `knowledge.scope_dir` and any other per-scope subtree must spell their parent
    as `scope_root(scope)/…` rather than joining `projects/`, `apps/` or
    `agents/` themselves — a scope smeared across three literals is a scope you
    cannot mount, address or confine with one path.

    Splits the key via `services.scope_tree.parse` (the one owner of the scope
    model) rather than re-implementing the kind/slug split; the function-local
    import keeps this module the pure `os`/`pathlib` leaf ADR 0024 made it, the
    same shape `chat_cwd()` already uses for `services.ownership`.

    Raises ValueError for the non-tree scopes (`workshop`, `development`,
    `role:<role>`): those are facets of one `shared/knowledge/` layer, not a
    per-scope tree, so there is no single root to point at.
    """
    from services import scope_tree
    scope = scope_tree.parse(scope_key)
    if scope.kind == "project":
        return projects_root() / scope.slug
    if scope.kind == "app":
        return workshop_root() / "apps" / scope.slug
    if scope.kind == "agent":
        return runtime_dir() / "agents" / scope.slug
    raise ValueError(
        f"scope_root has no tree root for {scope_key!r} (kind {scope.kind!r}); "
        "only project/app/agent own a scope tree")


# --- per-instance state files (Layer 0 eviction) ----------------------------
# The four state files that used to sit inside the source tree (backend/) move
# under the untracked runtime root. Each keeps its documented env override; when
# neither the env var nor the new runtime file is present yet, a ONE-TIME
# fallback reads the old in-source location so a read still works in the window
# before the startup migration (services/state_file_relocate.run_once) physically
# moves it. Once the migration has run, the new path exists and the fallback is
# never taken again — so nothing keeps writing to the source tree.
def _state_file(env_var: str, name: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    new = runtime_dir() / name
    if not new.exists():
        old = workshop_root() / "backend" / name
        if old.exists():
            return old
    return new


def grants_path() -> Path:
    """`.runtime/grants.json` — app-level capability grants (was backend/)."""
    return _state_file("WORKSHOP_GRANTS_PATH", "grants.json")


def resource_grants_path() -> Path:
    """`.runtime/resource-grants.json` — resource-scoped Store grants (was
    backend/); the live, backend-written file (~136 KB on this instance)."""
    return _state_file("WORKSHOP_RESOURCE_GRANTS_PATH", "resource-grants.json")


def apps_manifest_path() -> Path:
    """`.runtime/apps.json` — per-app manifests / identity + ui overrides (was
    backend/)."""
    return _state_file("WORKSHOP_APPS_MANIFEST", "apps.json")


def store_policies_path() -> Path:
    """`.runtime/store-policies.json` — per-store access tiers (was backend/)."""
    return _state_file("WORKSHOP_STORE_POLICIES_PATH", "store-policies.json")


def state_file_relocations() -> list[tuple[Path, Path]]:
    """`(old_in_source, new_in_runtime)` for every evicted state file, for the
    startup migration to move. Env overrides are honored on the new side, so a
    deployment that points a file elsewhere still gets that destination."""
    specs = [
        ("WORKSHOP_GRANTS_PATH", "grants.json"),
        ("WORKSHOP_RESOURCE_GRANTS_PATH", "resource-grants.json"),
        ("WORKSHOP_APPS_MANIFEST", "apps.json"),
        ("WORKSHOP_STORE_POLICIES_PATH", "store-policies.json"),
    ]
    out: list[tuple[Path, Path]] = []
    for env_var, name in specs:
        raw = os.environ.get(env_var)
        new = Path(raw).expanduser() if raw else runtime_dir() / name
        old = workshop_root() / "backend" / name
        out.append((old, new))
    return out


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
