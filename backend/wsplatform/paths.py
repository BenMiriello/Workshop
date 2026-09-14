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


class TreeUnresolvable(RuntimeError):
    """A project record names a tree that does not exist in THIS namespace.

    Raised rather than returned, because every historical caller of the raw
    `dir` string degraded to an empty answer instead: `list_branches` returned
    `[]`, the merged-worktree sweep found zero candidates, and both read as
    "nothing to do" rather than "I could not find the tree". An exception is
    the difference between those two.
    """


def project_dir(slug: str, record: dict) -> Path:
    """The project's working tree, resolved in the CURRENT mount namespace.

    THE STORED `dir` IS NOT PORTABLE, AND IT IS NOT WRONG EITHER. The backend
    service and an agent can see the same content at two different absolute
    paths (a service unit rooted one place, a sandboxed agent's bind mount
    rooted another). A `dir` written by one is a correct answer for that
    namespace and a path to an empty directory -- or to nothing -- in the
    other. Measured: a record's `dir` pointed at a tree with 6 worktrees from
    the service's view and at a husk holding three files from an agent's, and
    every caller that joined the string straight onto `git` silently answered
    "no branches".

    So the tree is DERIVED wherever it is derivable, because every path this
    installation lays out is already a function of `workshop_root()`, which is
    computed from this file's own location and is therefore correct in
    whichever namespace is asking:

      * a record declaring `self: true` IS the platform checkout -> `workshop_root()`
      * a record whose `dir` ends in `<something>/projects/<slug>` is a tree in
        the standard layout -> `projects_root()/<slug>`
      * anything else is a genuinely external tree (an imported repo somewhere
        else on the host) and the stored string is the only answer there is

    The derived candidate is preferred only when it EXISTS; otherwise the
    stored path is tried, and if neither exists this raises `TreeUnresolvable`
    naming both candidates. Refusing loudly is the point: the alternative,
    which is what every call site did before, is an empty list that cannot be
    told apart from a project with no work in it.
    """
    stored_raw = record.get("dir") if isinstance(record, dict) else None
    stored = Path(str(stored_raw)).expanduser() if stored_raw else None

    candidates: list[Path] = []
    if isinstance(record, dict) and record.get("self"):
        candidates.append(workshop_root())
    elif stored is not None and stored.parent.name == "projects":
        candidates.append(projects_root() / stored.name)
    if stored is not None:
        candidates.append(stored)

    for cand in candidates:
        if cand.is_dir():
            return cand

    if not candidates:
        raise TreeUnresolvable(
            f"project {slug!r} has no `dir` in the registry, so it names no "
            f"tree at all")
    tried = ", ".join(str(c) for c in candidates)
    raise TreeUnresolvable(
        f"project {slug!r} has no working tree in this namespace (tried: "
        f"{tried}). The registry `dir` is one namespace's spelling; see "
        f"paths.project_dir.")


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


def required_env_path(var: str, purpose: str) -> Path:
    """Read `var` as a path, or fail loudly. There is NO default.

    For a path that names something OUTSIDE the deployment -- a rig's database,
    a folder in the operator's home, a mount only this installation has. Those
    have no platform-derivable default: any value written into source is one
    machine's answer, and it keeps working perfectly on that machine while being
    wrong everywhere else, which is the exact defect `check_no_app_names.py`'s
    `home_path_sites` count exists to find.

    So the value comes from `~/.config/workshop/instance.env` and an absent one
    is an error with the variable's name in it, the way `WORKSHOP_GITHUB_OWNER`
    and `WORKSHOP_PUBLIC_ORIGIN` already behave. Contrast `env_path` above,
    which is for a path that DOES have a sane platform-derived default under
    root/runtime/config and therefore needs no configuration at all.
    """
    raw = (os.environ.get(var) or "").strip()
    if not raw:
        raise RuntimeError(
            f"{var} is not set. It names {purpose}, which is a property of this "
            f"installation and has no default: set it in "
            f"{config_dir() / 'instance.env'}."
        )
    return Path(raw).expanduser()
