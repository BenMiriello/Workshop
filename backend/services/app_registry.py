"""The app catalog — which apps this instance can plug in, and how.

Workshop is a container: the platform ships the shell (agents, projects, chat,
voice, auth, launcher, runner, and the per-user primitives apps build on) and
installs no apps of its own. This module assembles the catalog. Which apps are
actually wired up is decided by the per-instance installed-apps registry
(`installed_apps.py`); a fresh clone installs nothing and boots blank.

THIS MODULE NAMES NO APP. It used to: eight per-app factories and a literal
tuple listing them lived here, so the platform hardcoded the operator's apps and
adding one meant editing platform source. Apps now declare themselves and this
module discovers those declarations, by two paths and no others:

  * in-tree   — `services/<app>_app.py` exposing `app_spec() -> AppSpec`
  * out-of-tree — a distribution with a `workshop.apps` entry point

The declaration contract itself (`AppSpec`, `RouterMount`, and the store-path
helpers an app needs to fill one in) is `services/app_spec.py`, kept separate so
an app module can import what it must fill in without importing the discoverer
that imports it. Both names are re-exported here: this module was the import
site for years and there is no reason to break that.

`main.py` and the launcher iterate installed apps over this catalog. Adding or
removing an app is an installed-apps edit, never an edit here or to any
platform-core file.
"""
from __future__ import annotations

import contextvars
import importlib
import logging
import os
from pathlib import Path
from typing import Any, Callable

from services import installed_apps
# THE CONTRACT, re-exported. Apps import these from `services.app_spec`; every
# existing platform caller imports them from here, and both keep working.
from services.app_spec import AppSpec, RouterMount, owner_file, runtime_dir  # noqa: F401

log = logging.getLogger("workshop.apps")


# --------------------------------------------------------------------------- #
# In-tree apps: DISCOVERED, never enumerated.
#
# THIS MODULE NAMES NO APP, and keeping it that way is the point. Until
# 2026-09-01 it held eight hardcoded per-app factories and a literal tuple
# listing them, so the file that decides what an app IS also decided which apps
# EXIST. Adding an app meant editing platform source; the operator asked
# repeatedly why the platform hardcodes his apps, and this tuple was the answer.
#
# An app now declares itself in its OWN module, `services/<app>_app.py`,
# exposing `app_spec() -> AppSpec`. The suffix IS the contract: this scan reads
# the directory, so an app appears by existing and disappears by being deleted,
# with no platform edit either way. The app id comes from the spec the module
# returns, not from its filename, so nothing here has to know or guess a name.
#
# Workshop is a container and a given instance need not ship every app's route
# module (a slimmed build, or a downstream that removed apps it does not use),
# so a declaration is still only ever called INSIDE the builder's try/except: a
# module whose routes are absent raises ModuleNotFoundError from its own import
# and is skipped, exactly as a missing `required` module used to be.
# --------------------------------------------------------------------------- #
APP_MODULE_SUFFIX = "_app"

# What an app module may declare, besides `app_spec()`, so the catalog can drop
# it cleanly on an instance that does not ship its routes:
#
#   REQUIRES = ("birdsong", "environment")   # names under `routes.`
#
# THE NAMES LIVE IN THE APP'S OWN FILE, which is the point; the platform only
# knows the attribute. It exists because `from routes import x` inside a
# declaration is NOT a reliable presence test: once `routes.x` has been imported
# anywhere, `x` stays bound as an attribute of the `routes` package, and the
# `from ... import` finds it there without ever consulting the import system.
# So an instance that dropped the module would still admit the app, and the app
# would fail later at request time instead of never entering the catalog. This
# probes through `import_module`, which does consult it.
APP_REQUIRES_ATTR = "REQUIRES"


def _has_route_module(name: str) -> bool:
    try:
        importlib.import_module(f"routes.{name}")
        return True
    except ModuleNotFoundError:
        return False


def _in_tree_factories() -> "list[tuple[str, Callable[[], AppSpec]]]":
    """Every `services/<app>_app.py` that exposes `app_spec()`, by module name.

    The id is deliberately NOT derived from the filename: `notes_app.py`
    declares the app `notes-app`, and a filename-derived id would have to
    special-case that -- which is a platform file knowing an app's name again.
    The module is imported, `app_spec()` is called by the builder, and the id is
    read off the returned spec.
    """
    here = Path(__file__).resolve().parent
    out: "list[tuple[str, Callable[[], AppSpec]]]" = []
    for path in sorted(here.glob(f"*{APP_MODULE_SUFFIX}.py")):
        mod_name = path.stem
        try:
            mod = importlib.import_module(f"services.{mod_name}")
        except ModuleNotFoundError as e:
            log.info("app module %r not loaded: %s", mod_name, e)
            continue
        except Exception as e:
            log.warning("app module %r failed to import: %s", mod_name, e)
            continue
        factory = getattr(mod, "app_spec", None)
        if not callable(factory):
            continue
        missing = [m for m in getattr(mod, APP_REQUIRES_ATTR, ()) if not _has_route_module(m)]
        if missing:
            log.info("app module %r not in catalog: route module(s) not shipped: %s",
                     mod_name, ", ".join(missing))
            continue
        out.append((mod_name, factory))
    return out


# --------------------------------------------------------------------------- #
# Out-of-tree apps.
#
# The scan above is the IN-TREE library: apps whose code still ships in this
# repo. It was, until 2026-09-01, a literal tuple consumed once at import,
# with the factory required to be defined in this file and the route module
# required to sit in `backend/routes/`. So nothing outside `backend/` could be
# an app, which makes "an app is installed content, not platform source"
# unachievable rather than merely undone.
#
# The second path is the standard Python one: a distribution declares
#
#     [project.entry-points."workshop.apps"]
#     my-app = "my_app.spec:app_spec"
#
# and `app_spec()` returns an AppSpec exactly as a built-in factory does. No new
# format, no manifest parser, and `pip install` is the install step.
#
# A discovered app SHADOWS a built-in of the same id, deliberately and loudly:
# that is what extraction looks like from the platform's side. An app moves out
# of this repo, ships as a distribution, and takes over from the remnant still
# sitting in the tree, without a flag day where both are half-present.
# --------------------------------------------------------------------------- #
APP_ENTRY_POINT_GROUP = "workshop.apps"


def _entry_point_factories() -> "list[tuple[str, Callable[[], AppSpec]]]":
    out: "list[tuple[str, Callable[[], AppSpec]]]" = []
    try:
        from importlib.metadata import entry_points

        found = list(entry_points(group=APP_ENTRY_POINT_GROUP))
    except Exception as e:
        log.warning("app discovery: could not read entry points: %s", e)
        return out
    for ep in found:
        def _factory(_ep=ep) -> AppSpec:
            spec = _ep.load()()
            if not isinstance(spec, AppSpec):
                raise TypeError(
                    f"{_ep.value} returned {type(spec).__name__}, not an AppSpec"
                )
            return spec

        # An out-of-tree app's importability is its own entry point's problem; a
        # failure to load is caught below and skipped exactly like an in-tree
        # declaration that raises.
        out.append((ep.name, _factory))
    return out


def _build_catalog() -> dict[str, AppSpec]:
    """Every app that declared itself and can actually be built here.

    THE ID COMES FROM THE SPEC, never from the module or entry-point name. That
    is what lets this function assemble the catalog without a single app name in
    it: the source is asked what it is rather than told.
    """
    catalog: dict[str, AppSpec] = {}
    in_tree = _in_tree_factories()
    discovered = _entry_point_factories()
    in_tree_ids: set[str] = set()
    tagged = [(s, f, True) for s, f in in_tree] + [(s, f, False) for s, f in discovered]
    for source, factory, is_in_tree in tagged:
        try:
            spec = factory()
        except ModuleNotFoundError as e:
            # Its route module is not shipped on this instance. Expected on a
            # slimmed build, and the reason a declaration is never called at
            # import time: the app simply never enters the catalog.
            log.info("app %r not in catalog: module(s) not shipped: %s", source, e)
            continue
        except Exception as e:  # a declaration that fails must not sink the rest
            log.warning("app %r skipped: failed to build spec: %s", source, e)
            continue
        if not isinstance(spec, AppSpec) or not spec.id:
            log.warning("app %r skipped: declaration returned no usable AppSpec", source)
            continue
        if spec.id in catalog and spec.id in in_tree_ids:
            log.info("app %r: an installed distribution supersedes the in-tree copy", spec.id)
        if is_in_tree:
            in_tree_ids.add(spec.id)
        catalog[spec.id] = spec
    log.info("app catalog: %d in-tree declaration(s), %d out-of-tree via %s entry points",
             len(in_tree), len(discovered), APP_ENTRY_POINT_GROUP)
    return catalog


# The built-in catalog for THIS instance — only apps whose modules are present.
BUILTIN_APPS: dict[str, AppSpec] = _build_catalog()


def installed_specs() -> list[AppSpec]:
    """AppSpecs for installed apps, in installed order. Unknown ids are skipped."""
    out: list[AppSpec] = []
    for app_id in installed_apps.installed_app_ids():
        spec = BUILTIN_APPS.get(app_id)
        if spec is not None:
            out.append(spec)
    return out


def installed_chat_providers() -> list[Callable[[], Any]]:
    """Chat-provider factories for installed apps that register one, in install
    order. Drives chat_context.provider_map(): a persona is available only when
    its app is installed."""
    return [spec.chat_provider for spec in installed_specs() if spec.chat_provider]


def installed_snapshots() -> list[Callable[[], str]]:
    """Live-snapshot callables for installed apps that register one — the general
    assistant's cross-app context. Absent apps contribute nothing."""
    return [spec.chat_snapshot for spec in installed_specs() if spec.chat_snapshot]


# WHICH SCOPE this turn's search tool reaches. Mirrors `ownership`'s pattern
# exactly (a request-scoped contextvar, pinned once by the caller, read here
# rather than threaded as an argument through every function that might spawn
# this server): `_platform_mcp_servers` used to read
# `os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop")`, which is the BACKEND
# PROCESS's own environment. Nothing ever set that key in it, so every chat,
# on every turn, searched as `workshop` regardless of which chat it actually
# was -- and since `reach("workshop")` deliberately excludes `development`,
# the net effect was a DEVELOPMENT chat losing the ability to search
# development-scoped content, which is most of the platform's own history.
#
# The value pinned here is the chat's `context_key` ("general", "development",
# "project:birds", ...), not a resolved scope_key: `workshop_query_server.py`
# already does that translation (`knowledge.scope_for_context`) on the
# subprocess side, and it must stay the only place that does, per the design
# doc's two-namespace note (sec 7.1).
_query_scope_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workshop_query_scope", default=None
)


def set_current_query_scope(context_key: str | None) -> None:
    """Pin the chat's context_key for whatever spawns the workshop MCP server
    next. Called at the turn-start seam, where the real chat is known, exactly
    where `ownership.set_current_owner` is called for the same reason."""
    _query_scope_ctx.set(context_key)


def current_query_scope() -> str | None:
    return _query_scope_ctx.get()


# WHICH chat the workshop MCP server belongs to. Pinned at the same turn-start
# seam as the scope and owner, so the `spawn_background` tool (a background agent
# for THIS chat) knows which thread to attach its run to. None for a caller with
# no chat behind it (a background pass, a CLI invocation): the env var is then
# omitted and the server does not offer the spawn tool at all.
_query_thread_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workshop_query_thread", default=None
)


def set_current_query_thread(thread_id: str | None) -> None:
    """Pin the chat's thread id for whatever spawns the workshop MCP server next."""
    _query_thread_ctx.set(thread_id)


def current_query_thread() -> str | None:
    return _query_thread_ctx.get()


# Platform-level MCP servers, always available (not tied to an installed app):
# the read-only workshop-query tool the general chat uses to answer "what is
# going on" from the task + knowledge digest (Stage 7 / 2e). It is NOT a
# filesystem grant: the server returns a computed digest only, so it cannot leak
# `.runtime` secrets. Runs under the backend venv with PYTHONPATH=backend so it
# can reuse services.workspace_digest / services.knowledge.
def _platform_mcp_servers() -> dict[str, dict]:
    backend_dir = Path(__file__).resolve().parent.parent  # backend/
    server = backend_dir / "mcp" / "workshop_query_server.py"
    python = os.environ.get(
        "WORKSHOP_MCP_PYTHON", str(backend_dir / ".venv" / "bin" / "python")
    )
    env = {
        "PYTHONPATH": str(backend_dir),
    }
    # WHICH scope this turn's search tool reaches. `current_query_scope()` is
    # None for a caller that never pinned one (a background pass with no chat
    # behind it, a CLI invocation) -- the env var is OMITTED rather than
    # guessed, exactly like WORKSHOP_QUERY_OWNER below, and the subprocess's
    # own fallback (`workshop`, never `development`) applies.
    scope = current_query_scope()
    if scope:
        env["WORKSHOP_QUERY_SCOPE"] = scope
    # WHOSE digest this turn gets. The server is a separate stdio subprocess —
    # it does not inherit this process's request-scoped `ownership` contextvar,
    # so it used to hardcode itself to the operator ("Single-operator system")
    # regardless of who was actually asking. Read here, where the real caller
    # IS known (this function runs inside the request, before the spawn), and
    # handed down explicitly. `owner_or_none()` rather than `current_owner()`:
    # a background pass that hasn't pinned an owner gets no env var and the
    # server's own fallback (still the operator, which is correct there — see
    # ownership.py's "top of each background loop... to the operator's id").
    from services import ownership
    owner_id = ownership.owner_or_none()
    if owner_id is not None:
        env["WORKSHOP_QUERY_OWNER"] = str(owner_id)
    # WHICH chat, so the server can offer spawn_background bound to it. Omitted
    # for a caller with no chat behind it, exactly like scope and owner above.
    thread_id = current_query_thread()
    if thread_id:
        env["WORKSHOP_QUERY_THREAD"] = str(thread_id)
    return {
        "workshop": {
            "type": "stdio",
            "command": python,
            "args": [str(server)],
            "env": env,
        }
    }



def installed_service_probes() -> list[tuple[str, str, str, str, tuple[int, ...]]]:
    """Every installed app's expected processes, for the service probe route."""
    return [pr for spec in installed_specs() for pr in spec.service_probes]


def installed_app_ids() -> list[str]:
    """Ids of the apps this instance actually has, in catalog order."""
    return [s.id for s in installed_specs()]


def installed_event_sources() -> set[str]:
    out: set[str] = set()
    for spec in installed_specs():
        out.update(spec.event_sources)
    return out


def installed_event_verifiers() -> dict[str, Callable[[dict, bytes], tuple[bool, str]]]:
    """source -> verifier, across installed apps. An UNINSTALLED app's verifier
    is absent, which is correct and not a hole: its source is also not a known
    source, so its webhook is logged as unknown rather than trusted."""
    out: dict[str, Callable[[dict, bytes], tuple[bool, str]]] = {}
    for spec in installed_specs():
        out.update(spec.event_verifiers)
    return out


def installed_event_dedup() -> dict[str, Callable[[dict], str | None]]:
    """source -> idempotency-key deriver, across installed apps."""
    out: dict[str, Callable[[dict], str | None]] = {}
    for spec in installed_specs():
        out.update(spec.event_dedup)
    return out


def installed_store_adapters() -> list[Any]:
    """Store adapters contributed by installed apps, in install order."""
    return [a for spec in installed_specs() for a in spec.store_adapters]


def installed_index_collectors() -> tuple[dict[str, Callable[[], Any]], set[str]]:
    """(kind -> collector, kinds indexed by default) across installed apps."""
    collectors: dict[str, Callable[[], Any]] = {}
    default: set[str] = set()
    for spec in installed_specs():
        collectors.update(spec.index_collectors)
        default.update(spec.index_collectors_default)
    return collectors, default


def installed_chat_model_tiers() -> dict[str, str]:
    return {s.id: s.chat_model_tier for s in installed_specs() if s.chat_model_tier}


def installed_edge_blocks() -> tuple[set[str], set[str]]:
    """(prefixes, exact) the public edge must refuse, across installed apps."""
    prefixes: set[str] = set()
    exact: set[str] = set()
    for spec in installed_specs():
        prefixes.update(spec.edge_blocked_prefixes)
        exact.update(spec.edge_blocked_exact)
    return prefixes, exact


def installed_secret_env() -> tuple[str, ...]:
    out: list[str] = []
    for spec in installed_specs():
        for var in spec.secret_env:
            if var not in out:
                out.append(var)
    return tuple(out)


def installed_stores() -> list[tuple[str, str, object, str, bool]]:
    """(store_owner, name, path_factory, type, per_owner) for every installed app.

    The first element is the URI's owner segment, which is the app id unless the
    spec pins a different one (`AppSpec.store_owner`).
    """
    return [
        (spec.store_owner or spec.id, name, factory, type_, per_owner)
        for spec in installed_specs()
        for (name, factory, type_, per_owner) in spec.stores
    ]


def installed_mcp_servers() -> dict[str, dict]:
    """Merged MCP server launch specs: the always-present platform servers plus
    each server registered by an installed app.

    Replaces the hardcoded llm_router._MCP_SERVER_SPECS: an app server (e.g.
    plaid) is available to chat tool-use only if an app that registers it is
    installed; platform servers (workshop-query) are always available."""
    merged: dict[str, dict] = dict(_platform_mcp_servers())
    for spec in installed_specs():
        merged.update(spec.mcp)
    return merged


def installed_mcp_providers() -> dict[str, dict]:
    """Merged PROVIDER-INTERFACE descriptors for installed apps' MCP servers — the
    formalized provider surface (Phase 4). Each entry declares the server's ops,
    including the required `export` op:
        {"plaid": {"export": {"store_uri": "workshop://store/finance/plaid-items"}}}
    Separate from installed_mcp_servers() (launch specs) so the provider interface
    can grow without perturbing chat tool-use wiring."""
    merged: dict[str, dict] = {}
    for spec in installed_specs():
        merged.update(spec.mcp_provider)
    return merged


def mcp_provider_export(server: str) -> str:
    """Run an MCP provider's `export` op and return the portable artifact path.

    Resolves the server's declared export Store and dumps it via services.stores.
    Raises KeyError if the server is unknown or declares no export op. This is the
    single generic entrypoint every provider's export flows through — establishing
    the pattern on plaid without special-casing it."""
    providers = installed_mcp_providers()
    prov = providers.get(server)
    if not prov or "export" not in prov:
        raise KeyError(f"MCP provider has no export op: {server!r}")
    store_uri = prov["export"].get("store_uri")
    if not store_uri:
        raise KeyError(f"MCP provider {server!r} export op declares no store_uri")
    from services import stores
    stores.register_all()
    return stores.export(store_uri)


def launcher_cards() -> list[dict[str, Any]]:
    """Launcher card descriptors for installed apps that have a card, in order.

    Each card carries display fields (title/tagline/icon/route|href) plus the
    app id and, from the identity manifest, its `agent_button` preference so the
    launcher and in-app assistant dock derive entirely from this data.

    It also carries the app's resolved SURFACE (`services/app_surface.py`):
    where the app opens and which per-app tabs it has. Both the launcher grid
    and the shell's dock read this list, so resolving it once here is what
    stops the two from sending the operator to different places."""
    from services import manifests

    cards: list[dict[str, Any]] = []
    for spec in installed_specs():
        if not spec.card:
            continue
        m = manifests.get_manifest(spec.id) or {}
        ui = m.get("ui") if isinstance(m.get("ui"), dict) else {}
        cards.append({
            "id": spec.id,
            "agent_button": ui.get("agent_button", True) is not False,
            **spec.card,
        })

    # EVERY APP THE PLATFORM KNOWS ABOUT, not just the ones with a built-in
    # catalog spec. An app row with no spec is an app all the same: it has
    # chats, memory and tickets served by the generic per-app tabs, and leaving
    # it off the launcher made it unreachable from anywhere in the UI while the
    # database, the API and every collection endpoint agreed it existed.
    #
    # That is what "an app declares what it is and the platform reads the
    # declaration" means here: the catalog is one SOURCE of apps, not the
    # definition of which apps exist. Nothing below names an app; every field
    # comes off the row.
    #
    # `list_apps` already hides a dev_only row when dev mode is off, so a
    # production launcher does not need a second rule.
    try:
        from services import apps as apps_service

        # An app whose spec declares `card=None` is HEADLESS BY DECLARATION and
        # must stay off the launcher; it is already skipped by the loop above,
        # and adding it back from its row would override the thing it declared.
        # Only a row with no spec at all is new information here.
        specced = {s.id for s in installed_specs()}
        seen = {c.get("id") for c in cards} | specced
        for row in apps_service.list_apps(archived=False):
            slug = row.get("slug")
            if not slug or slug in seen:
                continue
            cards.append({
                "id": slug,
                "agent_button": False,
                "title": row.get("name") or slug,
                "tagline": "",
                "icon": row.get("icon"),
                # The generic per-app tabs are the only surface an app without
                # a spec has. WHICH of them it opens on is the app's to say --
                # `_attach_surfaces` below fills `route` from the declaration,
                # and until 2026-09-01 this line spelled `/chats` for every one
                # of them, which is the landing nobody chose.
            })
    except Exception:  # pragma: no cover - the launcher must not die on this
        log.warning("could not read app rows for the launcher", exc_info=True)

    return _self_first(_attach_surfaces(cards))


def _attach_surfaces(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve each card's surface and let it decide the card's `route`.

    An external app (an `href`, no route of ours) is a link out rather than a
    place in this shell, so it has no per-app tabs and keeps its href.

    An app that ships its own screens declares its home as the card's `route`,
    and that stays its home unless it also declares a `surface` block -- which
    is the more specific statement and wins. An app that declares neither opens
    on the first tab the platform can actually serve it, which is what makes
    "an app with no bundled UI" stop meaning "an app that opens on a chat
    list": an app with no tickets, runs, deploys or agents no longer has those
    tabs at all, so the set it opens from is the set it really has."""
    from services import app_surface

    for card in cards:
        if card.get("external") or (card.get("href") and not card.get("route")):
            continue
        app_id = card.get("id") or ""
        try:
            surface = app_surface.surface_for(app_id, own_route=card.get("route"))
        except Exception:  # pragma: no cover - the launcher must not die on this
            log.warning("could not resolve the surface for %s", app_id, exc_info=True)
            continue
        card["surface"] = surface
        card["route"] = surface["home"]
    return cards


def _self_first(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The installation's OWN source sorts to the front of the launcher.

    The dock is drawn in launcher order, and an app with no catalog spec is
    appended after every specced one -- so the app that is this checkout landed
    dead last, under every app built with it. Operator instruction 2026-08-31:
    put it first when it is present.

    BY DECLARATION, never by name. `self: true` in the project registry is the
    same fact `dev_only` and the ADR-0014 address interlock already read, so
    this adds no second source of truth and spells no slug (which
    scripts/check_no_app_names.py counts and CLAUDE.md forbids). An
    installation with no self-declared app is unchanged: nothing matches and
    the order stands.

    Stable within each group: `sorted` with a boolean key preserves the
    catalog's order among the rest.
    """
    try:
        from services import app_links
    except Exception:  # pragma: no cover - ordering must not break the launcher
        return cards
    return sorted(cards, key=lambda c: not app_links.is_self_slug(c.get("id") or ""))


def ensure_identity_manifests() -> None:
    """Seed an identity manifest entry for each installed app that lacks one.

    Keeps `apps.json` (identity + ui-overrides, instance state) in sync with the
    installed roster: installing an app auto-creates its manifest from the
    catalog, preserving any existing `ui` overrides. Best-effort; never raises."""
    from services import manifests

    try:
        for spec in installed_specs():
            existing = manifests.get_manifest(spec.id)
            if existing is not None:
                continue
            entry = {
                "title": (spec.card or {}).get("title") or spec.id,
                "sensitivity": spec.identity.get("sensitivity", "private"),
                "api_prefixes": spec.identity.get("api_prefixes", []),
                "provides": spec.identity.get("provides", []),
                "needs": spec.identity.get("needs", []),
                # A built-in app declares its tools in `identity` and they are
                # seeded into its manifest here, so the manifest is the single
                # place `services/tool_registry` has to read for every app,
                # in-tree or not (tools-are-declared-2026-08-23.md sec 2.2).
                "tools": spec.identity.get("tools", []),
            }
            manifests.upsert_manifest(spec.id, entry)
    except Exception as e:  # pragma: no cover - best-effort seeding
        log.warning("identity manifest seeding failed: %s", e)
