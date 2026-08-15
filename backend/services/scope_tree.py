"""scope_tree -- the single owner of the scope model (memory-and-documentation-
model-2026-08-14.md sec 4, sec 7.1).

Before this module, scope was a bare string parsed independently in
`knowledge._parse_scope`, `task_scope.root_for_context`/`key_for_root`, and two
frontend files. Adding containment to a shape parsed in four places would have
paid that tax a fifth time and guaranteed the four drift (ADR 0014's own
deferred item). This module is now the one place that knows what a scope key
means, what its parent is, and what it can reach.

THE TREE (design doc sec 4.1). The direction is the opposite of what you might
assume: `development` is ABOVE `workshop`, not below it.

    development                 ROOT on a developer build. Reaches everything.
    |-- workshop                root of a NORMAL install. Cannot see into
        |                       development.
        |-- project:<slug>
        |   |-- app:<name>      when bound to that project
        |   `-- agent:<slug>    when bound to that project
        |-- app:<name>          unattached
        `-- agent:<slug>        unattached

`development` is the developer's environment: the only scope whose work target
is the running application. It exists only when `app_runner.dev_mode_on()` is
true (ADR 0014, "developer mode gates the resolved directory"); off, it is not
a scope at all, and `workshop` is the root. This module does not invent a
second flag: every dev-mode question here defers to `app_runner.dev_mode_on()`.

TWO AXES, NOT ONE (see `knowledge.py` module docstring). `role:<role>` is NOT a
node in this tree; it is a second, separate axis. `parse` accepts it, `reach`
never walks it (it has no descendants and no ancestors in the tree), and
`govern` includes it as a facet alongside the tree.

REACH VS GOVERN, THE CORE DISTINCTION (design doc sec 4.2, 4.3):

  * `reach(S)` is `{S} + descendants(S)`, downward only, never siblings, never
    upward. Used for RETRIEVAL, on demand, unbounded.
  * `govern(S)` is `[widest_governance_layer, role:<r>, S, private]`, widest
    first. It is NOT the ancestor chain: on a developer build `development` is
    an ancestor of every scope, and injecting an ancestor chain would put
    platform working notes into a birds chat and a finance chat, and would grow
    unboundedly with tree depth. Governance is one small named layer, injected
    into every prompt; reach is unbounded and only ever pulled on demand.

NOT A SECURITY BOUNDARY. Per SEC-A2 ("stop representing scope as a security
boundary", see ADR 0014), `reach` is a CURATION mechanism, not confinement:
this is a single-operator system running as one uid, and a scope that cannot
reach another is a relevance rule, not an access-control wall. Nothing in this
module may be described, here or in the UI, as security.

THE PARENT EDGE IS STORED, NOT PARSED OUT OF THE STRING. `.runtime/scope-tree.json`
maps a child scope key to its parent scope key, loaded once and cached against
an mtime check (see `_load_overrides`). It is normally absent or empty: when a
scope has no explicit entry, its parent is derived from its KIND alone (see
`_default_parent`). An empty or missing file therefore means "every scope sits
where its kind implies", which is the correct resting state and is what makes a
future "move this app under that project" a data change instead of a code
change.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from services import paths

# Slug shape shared with `knowledge._SLUG_RE`: lowercase/uppercase letters,
# digits, hyphen/underscore separators. Kept as a module-level constant here
# too (rather than imported from knowledge) so this module has no import-time
# dependency on knowledge, which is itself becoming a caller of this one.
_SLUG_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)

# The kinds that are real nodes in the containment tree. `role` is a valid
# `parse()` result (the second axis) but is deliberately excluded: it has no
# parent and no children in this tree.
TREE_KINDS = ("development", "workshop", "project", "app", "agent")

# The ONE key that names the platform repo (ADR 0014). Exported so callers
# that used to spell "development" inline (frontend and backend alike) can
# reference this instead.
DEVELOPMENT_SCOPE = "development"
WORKSHOP_SCOPE = "workshop"

STORE = Path(os.environ.get(
    "WORKSHOP_SCOPE_TREE", str(paths.workshop_root() / ".runtime" / "scope-tree.json")))


@dataclass(frozen=True)
class Scope:
    """A parsed scope key: `kind` (development/workshop/project/app/agent/role),
    `slug` ("" for workshop and development), and the original `key`."""
    kind: str
    slug: str
    key: str


def _dev_mode_on() -> bool:
    from services import app_runner
    return app_runner.dev_mode_on()


def parse(scope_key: str) -> Scope:
    """kind + slug for `scope_key`, or raise ValueError on an unknown shape.

    `development` parses only when `app_runner.dev_mode_on()` is true; off, it
    is not a scope at all and raises exactly like any other unrecognised key
    -- the notes layer for work on the Workshop's own source does not exist in
    a normal install, rather than existing and refusing (ADR 0014)."""
    if scope_key == WORKSHOP_SCOPE:
        return Scope("workshop", "", scope_key)
    if scope_key == DEVELOPMENT_SCOPE:
        if _dev_mode_on():
            return Scope("development", "", scope_key)
        raise ValueError(
            "unknown scope_key: 'development' (developer mode is off; "
            "set WORKSHOP_DEV_MODE=1 for this installation to enable it)")
    if ":" in scope_key:
        kind, _, slug = scope_key.partition(":")
        if kind in ("project", "app", "agent", "role") and slug and _SLUG_RE.fullmatch(slug):
            return Scope(kind, slug, scope_key)
    raise ValueError(f"unknown scope_key: {scope_key!r}")


# --- the stored parent-edge override ------------------------------------------
_overrides_cache: tuple[tuple, dict[str, str]] | None = None


def _load_overrides() -> dict[str, str]:
    """Explicit child -> parent overrides from `.runtime/scope-tree.json`.

    Cached against the file's (size, mtime_ns), the same pattern
    `services.jobs` uses for its collapsed-log cache: a cheap `stat()` per
    call, a full re-read only when the file actually changed. Absent or
    unreadable means no overrides, which is the correct default, not an
    error."""
    global _overrides_cache
    try:
        st = STORE.stat()
        key = (str(STORE), st.st_size, st.st_mtime_ns)
    except OSError:
        key = (str(STORE), None, None)
    cached = _overrides_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    data: dict[str, str] = {}
    try:
        raw = json.loads(STORE.read_text())
        if isinstance(raw, dict):
            data = {str(k): str(v) for k, v in raw.items()
                    if isinstance(k, str) and isinstance(v, str)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    _overrides_cache = (key, data)
    return data


def _default_parent(scope: Scope) -> str | None:
    """Where `scope` sits when nothing in the override file says otherwise.

    For development/workshop/role this is purely a function of KIND (design
    doc sec 4.1's tree diagram). For an AGENT it also honours the registry's
    own `project_slug` binding when the agent has one -- existing data, not a
    scheme invented for this tree (see `_agent_bound_project`). APPS have no
    equivalent binding field anywhere today (`app_registry.AppSpec` carries no
    project reference), so every app defaults to the root; a future
    project-scoped app catalog entry, or a binding record alongside
    `installed_apps.json`, is what would carry that later."""
    if scope.kind == "development":
        return None  # the root, on a developer build
    if scope.kind == "workshop":
        return DEVELOPMENT_SCOPE if _dev_mode_on() else None
    if scope.kind == "role":
        return None  # the second axis, not a tree node
    if scope.kind == "agent":
        bound = _agent_bound_project(scope.slug)
        if bound:
            candidate = f"project:{bound}"
            try:
                parse(candidate)
                return candidate
            except ValueError:
                pass
    return WORKSHOP_SCOPE  # project / app / unattached agent, unattached by default


def parent(scope_key: str) -> str | None:
    """`scope_key`'s parent, or None at the root. Raises ValueError on an
    unknown scope shape, exactly like `parse`."""
    scope = parse(scope_key)
    if scope.kind == "role":
        return None
    override = _load_overrides().get(scope_key)
    if override:
        # An override naming an unparseable scope (a stale entry, a role
        # scope, a typo) is ignored rather than trusted blindly -- it came
        # from a file that could have been hand- or agent-edited.
        try:
            parse(override)
        except ValueError:
            return _default_parent(scope)
        return override
    return _default_parent(scope)


# --- enumerating the concrete scopes that exist --------------------------------
# `reach()` and `govern()` are called per turn and per search (design doc sec
# 7.2), so this is cached the same way `_load_overrides` is: a handful of
# cheap `stat()` calls per request, a full rebuild only when a watched path
# actually changed.
#
# PROJECTS stay off disk (`knowledge.all_scope_dirs()`): every project that
# exists today already has a knowledge directory, so this has not been wrong
# in practice. It carries the same latent gap described below for APPS AND
# AGENTS -- a project registered but never written to would be invisible too
# -- but fixing that is a project-registry read this module does not have a
# seam for yet, and it is out of scope for the app/agent gap this section
# exists to close.
#
# APPS AND AGENTS ARE READ FROM THEIR REGISTRIES, NEVER FROM DISK. A scope
# that is registered but has never had a knowledge note written is still a
# scope: `knowledge.scope_dir` mkdirs on demand, so keying off which knowledge
# directories happen to exist both MISSES a scope with no notes yet and RISKS
# CREATING one as a side effect of reading the tree (see
# `knowledge.scope_for_context`'s comment about `apps/zzz/knowledge/` for the
# exact shape of that mistake). `agents_registry.list_agents(include_retired=
# False)` is the source for agents specifically because it already excludes
# retired and per-channel agents, and because it does NOT fall back to
# `build_agents.json` for a slug with no definition file (ADR 0014 door 4: an
# undeclared build-agent process is a Run, not an Agent, and must not become a
# tree node just because it once ran).
_tree_cache: tuple[tuple, tuple[list[str], dict[str, str]]] | None = None


def _watched_roots() -> list[Path]:
    """Every path whose mtime signals "the set of concrete scopes might have
    changed". The three directories catch a project/app/agent knowledge
    directory being created or removed; the two registry FILES catch
    installing/removing an app and a builder agent's retirement
    (`build_agents.retire` rewrites `build_agents.json` in place); the user
    agents directory catches a new/deleted agent DEFINITION file.

    Not caught: retiring a non-builder (assistant) agent, which only rewrites
    its OWN existing definition file's frontmatter in place and touches none
    of these paths. That is an accepted gap in the cache, not the read: a cold
    cache (a fresh process, or any OTHER watched path changing meanwhile)
    always sees it correctly, and watching every individual definition file
    would mean enumerating them, which defeats the point of caching. A test
    exercising this reads with a cold cache, which is the common case."""
    from services import knowledge, paths, installed_apps, build_agents
    from services import agents_registry as ar
    roots = [paths.projects_root(), paths.workshop_root() / "apps",
             paths.runtime_dir() / "agents",
             installed_apps.REGISTRY_PATH, build_agents._registry(),
             ar._user_agents_dir()]
    try:
        roots.append(knowledge._shared_knowledge_root())
    except Exception:
        pass
    return roots


def _raw_project_scopes() -> list[str]:
    """`project:<slug>` scopes, off the filesystem. See the section docstring
    for why this differs from how apps and agents are read below."""
    from services import knowledge
    try:
        pairs = knowledge.all_scope_dirs()
    except Exception:
        return []
    out: list[str] = []
    for key, _d in pairs:
        if not key.startswith("project:"):
            continue
        try:
            parse(key)
        except ValueError:
            continue
        out.append(key)
    return out


def _raw_registry_scopes() -> tuple[list[str], dict[str, str]]:
    """(`app:<name>` and `agent:<slug>` scopes, agent slug -> bound
    `project:<slug>` for agents that have one) off the app and agent
    registries -- the actual reads, uncached."""
    scopes: list[str] = []
    agent_projects: dict[str, str] = {}

    from services import app_registry
    for app_id in app_registry.installed_app_ids():
        key = f"app:{app_id}"
        try:
            parse(key)
        except ValueError:
            continue
        scopes.append(key)

    from services import agents_registry
    for rec in agents_registry.list_agents(include_retired=False):
        slug = rec.get("slug")
        if not slug:
            continue
        key = f"agent:{slug}"
        try:
            parse(key)
        except ValueError:
            continue
        scopes.append(key)
        project_slug = rec.get("project_slug")
        if project_slug:
            agent_projects[slug] = str(project_slug)

    return scopes, agent_projects


def _tree_snapshot() -> tuple[list[str], dict[str, str]]:
    """Cached (every concrete scope key, agent slug -> bound project_slug).
    See `_watched_roots` for the invalidation key. Tests substitute a fixture
    tree by monkeypatching `_existing_scopes` and/or `_agent_bound_project`
    (below) directly, which sidesteps this cache entirely rather than
    fighting its invalidation key."""
    global _tree_cache
    key = []
    for r in _watched_roots():
        try:
            key.append((str(r), r.stat().st_mtime_ns))
        except OSError:
            key.append((str(r), None))
    cache_key = tuple(key)
    cached = _tree_cache
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    reg_scopes, agent_projects = _raw_registry_scopes()
    result = (_raw_project_scopes() + reg_scopes, agent_projects)
    _tree_cache = (cache_key, result)
    return result


def _existing_scopes() -> list[str]:
    """Every scope key that concretely exists, across projects (off disk) and
    apps/agents (off their registries)."""
    return list(_tree_snapshot()[0])


def _agent_bound_project(slug: str) -> str | None:
    """The project slug an agent is bound to, per the registry's own
    `project_slug` field, or None when unattached. Existing data, not a
    binding scheme invented for this tree."""
    return _tree_snapshot()[1].get(slug)


def children(scope_key: str) -> list[str]:
    """The scopes whose parent is `scope_key`, sorted for determinism."""
    scope = parse(scope_key)
    if scope.kind == "role":
        return []
    kids = {s for s in _existing_scopes() if parent(s) == scope_key}
    if scope_key == DEVELOPMENT_SCOPE and _dev_mode_on():
        kids.add(WORKSHOP_SCOPE)
    return sorted(kids)


def reach(scope_key: str) -> list[str]:
    """`{scope_key}` plus every descendant, downward only -- never siblings,
    never upward. RETRIEVAL ONLY (design doc sec 4.2); see the module
    docstring for why this is not a security boundary.

    Ordered: `scope_key` first, then breadth-first over its descendants."""
    parse(scope_key)  # validates; raises on an unknown shape
    out = [scope_key]
    seen = {scope_key}
    frontier = [scope_key]
    while frontier:
        nxt: list[str] = []
        for s in frontier:
            for c in children(s):
                if c not in seen:
                    seen.add(c)
                    out.append(c)
                    nxt.append(c)
        frontier = nxt
    return out


# --- governance: one small named layer, not the ancestor chain ----------------
# The widest governance layer (design doc sec 4.3: house style, operator
# preferences, cross-cutting rules). Hardcoded to `workshop` for now; a later
# phase makes this configurable (design doc sec 4.3's `house`), which is why
# `govern` reads this constant rather than every caller spelling "workshop" a
# second time.
GOVERNANCE_SCOPE = WORKSHOP_SCOPE


def govern(scope_key: str, role: str | None = None, private: str | None = None) -> list[str]:
    """The scopes an agent READS for context, widest first, narrowest last:

      GOVERNANCE_SCOPE  operator preferences, house style, cross-cutting facts
      role:<role>       procedural memory: how this KIND of work is done well
      scope_key         facts about this codebase or domain
      private           what this one agent instance tried, and how it went

    Deliberately NOT the ancestor chain -- see the module docstring. Narrowest
    wins on conflict, which is why callers render this in order: a later, more
    specific layer overrides a general one for a reader going top to bottom.

    Does not validate `scope_key`/`private` against `parse` (neither did the
    `knowledge.read_layers` this replaces): a caller with a scope this module
    does not yet recognise still gets a governance-plus-own-scope read rather
    than an exception.

    Deduped and order-preserving; a scope that is its own layer appears once.
    """
    layers: list[str] = [GOVERNANCE_SCOPE]
    if role:
        r = str(role).strip().lower()
        if r and _SLUG_RE.fullmatch(r):
            layers.append(f"role:{r}")
    if scope_key and scope_key not in layers:
        layers.append(scope_key)
    if private and private not in layers:
        layers.append(private)
    seen: set[str] = set()
    out: list[str] = []
    for s in layers:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def label(scope_key: str) -> str:
    """A human-readable label for a scope key. "unknown" for an empty key, the
    raw key for one this module cannot parse."""
    if not scope_key:
        return "unknown"
    if scope_key == DEVELOPMENT_SCOPE:
        return "Development"
    if scope_key == WORKSHOP_SCOPE:
        return "Workshop"
    try:
        scope = parse(scope_key)
    except ValueError:
        return scope_key
    kind_label = {"project": "Project", "agent": "Agent", "app": "App",
                  "role": "Role"}.get(scope.kind, scope.kind)
    return f"{kind_label}: {scope.slug}"
