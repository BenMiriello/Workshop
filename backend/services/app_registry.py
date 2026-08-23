"""Built-in app catalog — the pluggable-app registration contract.

Workshop is a container: the platform ships the shell (agents, projects, chat,
voice, auth, launcher, runner, and the per-user primitives apps build on) and a
*catalog* of built-in apps, but installs none of them. This module is that
catalog. Which apps are actually wired up is decided by the per-instance
installed-apps registry (`installed_apps.py`); a fresh clone installs nothing and
boots blank.

Each app declares, in one place, everything the platform needs to plug it in:

  * `card`      — the launcher card (title, tagline, icon, route) or None (headless).
  * `identity`  — the identity manifest fields (sensitivity, api_prefixes, caps).
  * `mounts`    — the FastAPI routers + their auth, mounted only when installed.
  * `mcp`       — MCP servers the app registers for chat tool-use (e.g. plaid).
  * `on_startup`/`background` — lifespan hooks + loops, started only when installed.

`main.py` and the launcher iterate installed apps over this catalog. Adding or
removing an app is an installed-apps edit, never an edit here or to any
platform-core file — this catalog is the built-in library, not the install list.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import importlib

from fastapi import Depends, FastAPI

from deps_auth import require_internal_caller
from deps_user import require_user, require_domain_owner
from services import installed_apps

log = logging.getLogger("workshop.apps")


# Workshop is a container: the catalog is the built-in app LIBRARY, but a given
# instance need not ship every app's route module (a slimmed build, or a
# downstream that removed apps it doesn't use). So the catalog must NEVER
# hard-import an app's route module at load time — doing so would crash the whole
# platform (base_apps/main/llm_router all import this) the moment one app is
# absent. Instead each app is built by a factory that imports its own module(s);
# a factory whose modules aren't present is skipped and its app never enters the
# catalog. See _build_catalog() below.
def _has_module(name: str) -> bool:
    try:
        importlib.import_module(f"routes.{name}")
        return True
    except ModuleNotFoundError:
        return False


@dataclass(frozen=True)
class RouterMount:
    """One FastAPI router an app contributes, with its prefix/tags/auth."""
    router: Any
    prefix: str = ""
    tags: tuple[str, ...] = ()
    deps: tuple = ()  # FastAPI Depends() dependencies for the mount


@dataclass(frozen=True)
class AppSpec:
    id: str
    card: dict[str, Any] | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    mounts: tuple[RouterMount, ...] = ()
    mcp: dict[str, dict] = field(default_factory=dict)
    # Provider interface for each MCP server this app registers (Phase 4). The
    # `mcp` field carries the LAUNCH spec (type/command/args) consumed by the chat
    # tool-use router; `mcp_provider` carries the PROVIDER-INTERFACE ops, kept
    # separate so adding ops never perturbs the launch-spec path. Every provider
    # MUST declare an `export` op naming the Store it dumps:
    #   {"<server>": {"export": {"store_uri": "workshop://store/<app>/<name>"}}}
    # See mcp_provider_export() below.
    mcp_provider: dict[str, dict] = field(default_factory=dict)
    on_startup: tuple[Callable[[FastAPI], Awaitable[None]], ...] = ()
    background: tuple[Callable[[], Awaitable[None]], ...] = ()
    # Chat surface (services/chat_context, services/app_persona):
    #   chat_provider — factory returning this app's ContextProvider (its in-app
    #                   assistant persona). Exposed only when the app is installed.
    #   chat_snapshot — a compact live-state string for the GENERAL assistant's
    #                   cross-app snapshot. None => this app contributes none.
    chat_provider: Callable[[], Any] | None = None
    chat_snapshot: Callable[[], str] | None = None
    # Model tier for this app's in-app assistant. None => the platform default.
    # Here rather than in routes/chat.py so opting an app up to a bigger model
    # is a property of the app, not a line the platform keeps about it.
    chat_model_tier: str | None = None
    # Event-bus sources this app accepts webhooks from (routes/events.py unions
    # these with the platform's own over the installed set).
    event_sources: tuple[str, ...] = ()
    # Webhook signature verifiers, keyed by the source they verify. Signature:
    #   (headers: dict[str, str], body: bytes) -> (ok: bool, reason: str)
    # with `headers` lowercased. A source with no verifier is accepted with
    # reason "no_verifier_registered", which is why the platform must not be the
    # place this lives: whether a webhook is authentic is knowledge the app that
    # owns the integration has, and the platform's default is permissive.
    # Declared lazily (the factory can reference an app module) and read per
    # request, so an app can be installed without restarting the receiver.
    event_verifiers: dict[str, Callable[[dict, bytes], tuple[bool, str]]] = field(default_factory=dict)
    # Cross-arrival idempotency, keyed by source. Signature:
    #   (event_summary: dict) -> str | None
    # Return a key that is stable across redeliveries of the SAME event (Plaid
    # retries identical webhooks), or None to fall back to the platform's
    # content hash. Only the app knows which payload fields identify an event
    # rather than merely varying with it.
    event_dedup: dict[str, Callable[[dict], str | None]] = field(default_factory=dict)
    # Store adapters this app contributes, keyed by the Store `type` string it
    # shapes. `stores` above declares that a store of type T exists; this is the
    # thing that knows how to select and project rows out of it.
    store_adapters: tuple[Any, ...] = ()
    # Semantic-index collectors this app contributes: kind -> () -> list[Chunk].
    # `default` is False for a corpus the operator opts into rather than one
    # indexed on every build (semantic_index.DEFAULT_KINDS).
    index_collectors: dict[str, Callable[[], Any]] = field(default_factory=dict)
    index_collectors_default: tuple[str, ...] = ()
    # An app's TOOLS are declared in its manifest, not here: see
    # `services/tool_registry.app_tools` and
    # `docs/platform-redesign/tools-are-declared-2026-08-23.md`. The `tools`
    # field that used to sit here fed `routes/agents.known_tools`, in the same
    # made-up vocabulary as that route's deleted five-string platform list --
    # not CLI tool names, not MCP tool names, and enforced by nothing.
    # Stores this app owns: (name, path_factory, type). The factory is called
    # lazily so a store whose directory does not exist yet costs nothing at
    # import time. `per_owner` stores resolve under the owner's runtime dir.
    stores: tuple[tuple[str, Callable[[], Any], str, bool], ...] = ()
    # Paths the PUBLIC edge must NEVER forward to this app: unauthenticated or
    # loopback-trusted ingest that would otherwise inherit the proxy's trust.
    # `_exact` is for an ingest POST whose siblings under the same prefix are
    # ordinary authed reads the SPA uses (workshop_public_app.py).
    edge_blocked_prefixes: tuple[str, ...] = ()
    edge_blocked_exact: tuple[str, ...] = ()
    # Env vars to strip from a spawned agent turn because they are this app's
    # secrets and nothing downstream should inherit them (llm_router).
    secret_env: tuple[str, ...] = ()
    # Processes this app expects to be running, probed by routes/services.py.
    # Each entry: (name, category, url, probe_path, expect_status). An app that
    # runs out of process is the only thing that knows what "up" means for it.
    service_probes: tuple[tuple[str, str, str, str, tuple[int, ...]], ...] = ()


# --------------------------------------------------------------------------- #
# Finance / Budget MCP: the plaid server the finance & budget chat assistants
# use for on-demand transaction lookup.
#
# The source now lives IN-TREE at backend/mcp/plaid/ (final-plan PM.4): it used
# to be sourced from ~/clawdbot/plaid-mcp/, outside the repo, which made it
# undiffable and tied a first-class platform capability to the lifetime of the
# chat bridge's home directory. Repo-relative, so a fresh clone gets it after
# one `npm install` in that dir; override with the env var.
#
# NOT moved: the secrets it reads. `~/.config/clawdbot/plaid-auth.env` (creds)
# and `~/.config/clawdbot/plaid-tokens.json` (per-bank access tokens, mode 0600)
# keep their host-level paths — server.js reads them from there directly. Code
# moves into the repo; secrets never do.
# --------------------------------------------------------------------------- #
_PLAID_MCP = {
    "plaid": {
        "type": "stdio",
        "command": "node",
        "args": [os.environ.get(
            "WORKSHOP_PLAID_MCP",
            str(Path(__file__).resolve().parents[1] / "mcp" / "plaid" / "server.js"),
        )],
    },
}

# Provider interface for the plaid MCP server (Phase 4). Every MCP provider must
# declare an `export` op naming the Store it dumps; plaid exports the finance
# plaid-items Store. This establishes the pattern (currently only plaid is wired)
# without touching the launch spec above.
_PLAID_MCP_PROVIDER = {
    "plaid": {
        "export": {"store_uri": "workshop://store/finance/plaid-items"},
    },
}


# --------------------------------------------------------------------------- #
# App-specific background loops + startup, moved off platform-core main.py so
# they run only when their app is installed.
# --------------------------------------------------------------------------- #
_PREWARM_INTERVAL_S = 9 * 60
_CATEGORIZE_HOUR_LOCAL = 23


async def _prewarm_finance_cache(realtime: bool = False) -> None:
    """Pre-fetch balances + transactions so the next browser load is already hot.
    Finance is per-owner; prewarm the operator's cache. Best-effort."""
    from services import auth_store, ownership, finance_cache
    from services import finance_plaid as plaid_bridge

    oid = auth_store.operator_id()
    if oid is None:
        return
    ownership.set_current_owner(oid)
    try:
        await finance_cache.get_or_fetch(
            "balances",
            lambda: plaid_bridge.balances(realtime=realtime),
            force_refresh=realtime,
        )
        await finance_cache.get_or_fetch("transactions:30", lambda: plaid_bridge.transactions(days=30))
    except Exception:
        pass


async def _prewarm_loop() -> None:
    while True:
        await asyncio.sleep(_PREWARM_INTERVAL_S)
        await _prewarm_finance_cache(realtime=False)


async def _categorize_loop() -> None:
    """Once-a-day Sonnet categorization of uncategorized/transfer transactions."""
    from services import auth_store, ownership, categorizer

    last_run_day = None
    while True:
        await asyncio.sleep(30 * 60)
        lt = time.localtime()
        today = (lt.tm_year, lt.tm_yday)
        if lt.tm_hour >= _CATEGORIZE_HOUR_LOCAL and today != last_run_day:
            try:
                oid = auth_store.operator_id()
                if oid is None:
                    continue
                ownership.set_current_owner(oid)
                summary = await categorizer.run(days=30)
                log.info("nightly categorizer: %s", summary)
            except Exception as e:
                log.warning("nightly categorizer failed: %s", e)
            last_run_day = today


async def _finance_on_startup(app: FastAPI) -> None:
    """Wire the cache-invalidate prewarm hook and fire an initial realtime warm."""
    from services import finance_cache

    finance_cache.set_invalidate_hook(_prewarm_finance_cache)
    asyncio.create_task(_prewarm_finance_cache(realtime=True))


async def _env_refresh_loop() -> None:
    """Keep the Birds weather/astro spine fresh (stdlib astro + Open-Meteo) and
    score any forecast predictions whose hour has passed. Best-effort, off-thread."""
    from services import environment as environment_svc

    while True:
        try:
            n = await asyncio.to_thread(environment_svc.refresh)
            log.info("env refresh: %s", "egress blocked" if n == -1 else f"{n} rows")
        except Exception as e:
            log.warning("env refresh failed: %s", e)
        try:
            from services import birds_forecast
            graded = await asyncio.to_thread(birds_forecast.score_due)
            if graded:
                log.info("forecast calibration: graded %s hours", graded)
        except Exception as e:
            log.warning("forecast scoring failed: %s", e)
        await asyncio.sleep(6 * 60 * 60)


async def _atlas_rebuild_loop() -> None:
    """Nightly Soundscape Atlas UMAP/HDBSCAN rebuild, gated on enough new
    embeddings. Heavy ML runs detached in the analyzer venv; this just triggers."""
    from services import birds_analyze

    await asyncio.sleep(5 * 60)  # let startup settle before the first check
    while True:
        try:
            res = await asyncio.to_thread(birds_analyze.maybe_auto_rebuild)
            if res.get("triggered"):
                log.info("atlas auto-rebuild: triggered (%s embedded, %s new)",
                         res.get("embedded"), res.get("new"))
        except Exception as e:
            log.warning("atlas auto-rebuild check failed: %s", e)
        await asyncio.sleep(24 * 60 * 60)


# --------------------------------------------------------------------------- #
# The built-in catalog. Auth mirrors the previous hardcoded main.py wiring.
# --------------------------------------------------------------------------- #
# One factory per built-in app. A factory imports ITS OWN route module(s) at call
# time (safe — the builder only calls it once those modules are confirmed present)
# and returns the AppSpec. This is what keeps the catalog from hard-coupling to
# every app module: an app whose modules aren't shipped is simply never built.
def _finance_spec() -> AppSpec:
    from routes import finance, plaid_webhook
    from services import finance_events, finance_persona, finance_store_adapter
    return AppSpec(
        id="finance",
        chat_provider=finance_persona.FinanceContextProvider,
        chat_snapshot=finance_persona.finance_snapshot,
        card={
            "title": "Finance",
            "tagline": "Balances, accounts, recent transactions",
            "icon": "Banknote",
            "route": "/apps/finance",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/finance", "/plaid"],
            "provides": ["finance.read"],
            "needs": [],
        },
        mounts=(
            RouterMount(finance.router, prefix="/finance", tags=("finance",), deps=(Depends(require_user),)),
            # Plaid webhook — Plaid-signed, no user gate.
            RouterMount(plaid_webhook.router, prefix="/plaid/webhook", tags=("plaid-webhook",)),
        ),
        mcp=_PLAID_MCP,
        mcp_provider=_PLAID_MCP_PROVIDER,
        on_startup=(_finance_on_startup,),
        background=(_prewarm_loop, _categorize_loop),
        event_sources=("plaid",),
        event_verifiers={"plaid": finance_events.verify_webhook},
        event_dedup={"plaid": finance_events.dedup_key},
        edge_blocked_prefixes=("/plaid/webhook",),
        secret_env=("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_ENV_URL"),
        stores=(
            ("plaid-items", lambda: _owner_file("plaid-items.json"), "Finance", True),
            ("transactions", lambda: _owner_file("finance-cache.json"), "Finance", True),
        ),
        store_adapters=(finance_store_adapter.FinanceAdapter(),),
    )


def _budget_spec() -> AppSpec:
    from routes import budget
    from services import budget_persona
    return AppSpec(
        id="budget",
        chat_model_tier="sonnet",  # plan-patch needs careful path generation
        chat_provider=budget_persona.BudgetContextProvider,
        card={
            "title": "Budget",
            "tagline": "Payoff plan, savings goals, projections",
            "icon": "PiggyBank",
            "route": "/apps/budget",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/budget"],
            "provides": [],
            "needs": ["finance.read"],
        },
        mounts=(
            RouterMount(budget.router, prefix="/budget", tags=("budget",), deps=(Depends(require_user),)),
        ),
        # Budget chat shares the plaid MCP for transaction lookup.
        mcp=_PLAID_MCP,
        mcp_provider=_PLAID_MCP_PROVIDER,
        stores=(("plan", lambda: _owner_file("budget.json"), "Budget", True),),
    )


def _commute_spec() -> AppSpec:
    from routes import commute
    from services import commute_persona
    return AppSpec(
        id="commute",
        chat_provider=commute_persona.CommuteContextProvider,
        chat_snapshot=commute_persona.commute_snapshot,
        card={
            "title": "Commute",
            "tagline": "Day-of departures + transit status",
            "icon": "TrainFront",
            "route": "/apps/commute",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/commute"],
            "provides": [],
            "needs": ["location.read"],
        },
        mounts=(
            RouterMount(commute.router, deps=(Depends(require_domain_owner("commute")),)),
        ),
        stores=(("data", lambda: _runtime_dir("commute"), "Commute", False),),
    )


def _location_spec() -> AppSpec:
    from routes import location, owntracks
    from services import location_persona
    return AppSpec(
        id="location",
        chat_provider=location_persona.LocationContextProvider,
        chat_snapshot=location_persona.location_snapshot,
        card={
            "title": "Location",
            "tagline": "Live position + history from OwnTracks",
            "icon": "MapPin",
            "route": "/apps/location",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/location"],
            "provides": ["location.read"],
            "needs": [],
        },
        mounts=(
            RouterMount(location.router, deps=(Depends(require_user),)),
            # OwnTracks ingest — HTTP Basic bound to an owner inside the handler.
            RouterMount(owntracks.router),
        ),
        edge_blocked_prefixes=("/location/owntracks",),
    )


def _birds_spec() -> AppSpec:
    from routes import birds, environment
    return AppSpec(
        id="birds",
        card={
            "title": "Birds",
            "tagline": "Backyard bird-song detections, live",
            "icon": "Bird",
            "route": "/apps/birds",
        },
        identity={
            "sensitivity": "shareable",
            "api_prefixes": ["/birds", "/environment"],
            "provides": ["birds.read"],
            "needs": [],
        },
        mounts=(
            RouterMount(birds.router, deps=(Depends(require_user),)),
            # Yard-mic / BirdNET-Go ingest — internal caller only, carries its own token.
            RouterMount(birds.ingest_router, deps=(Depends(require_internal_caller),)),
            RouterMount(environment.router, deps=(Depends(require_user),)),
        ),
        background=(_env_refresh_loop, _atlas_rebuild_loop),
        edge_blocked_prefixes=("/birds/webhook", "/birds/live/publish"),
        edge_blocked_exact=("/birds/analyze",),
    )


def _health_spec() -> AppSpec:
    from routes import health
    return AppSpec(
        id="health",
        card=None,  # headless: iOS Health auto-export ingest + reads, no launcher card
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/health"],
            "provides": ["health.read"],
            "needs": [],
        },
        mounts=(
            # /ingest: HTTP Basic bound to owner; reads: require_user per-endpoint.
            RouterMount(health.router),
        ),
        edge_blocked_prefixes=("/health/ingest",),
    )


def _wearables_spec() -> AppSpec:
    # External app: served from its own origin, no in-platform route module.
    return AppSpec(
        id="wearables",
        card={
            "title": "Open Wearables",
            "tagline": "Wearables data UI",
            "icon": "Watch",
            "href": os.environ.get("WORKSHOP_WEARABLES_URL", ""),
            "external": True,
            "probe": "open-wearables-backend",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": [],
            "provides": [],
            "needs": [],
        },
        # This app IS three external processes; the probes are its whole
        # backend presence, which is why the platform used to carry them.
        service_probes=(
            ("open-wearables-backend", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_BACKEND", "http://127.0.0.1:8100"),
             "/", (200, 301, 302, 404)),
            ("open-wearables-frontend", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_FRONTEND", "http://127.0.0.1:3100"),
             "/", (200, 301, 302)),
            ("open-wearables-flower", "wearables",
             os.environ.get("WORKSHOP_WEARABLES_FLOWER", "http://127.0.0.1:5556"),
             "/", (200, 401)),
        ),
    )


def _notes_spec() -> AppSpec:
    from routes import notes
    from services import notes_store_adapter, notes_vault
    return AppSpec(
        id="notes-app",
        card={
            "title": "Notes",
            "tagline": "Markdown vault — wikilinks, backlinks, search",
            "icon": "NotebookPen",
            "route": "/apps/notes",
        },
        identity={
            "sensitivity": "private",
            "api_prefixes": ["/notes-app"],
            "provides": list(notes.PROVIDES),
            "needs": [],
        },
        mounts=(
            RouterMount(notes.router, deps=(Depends(require_user),)),
        ),
        # Searchable, but only when asked for by name: `vault` is deliberately
        # absent from index_collectors_default.
        index_collectors={"vault": notes_vault.collect_index_chunks},
        store_adapters=(notes_store_adapter.VaultNoteAdapter(),),
    )


# (id, required route modules, factory). An app is admitted to the catalog only
# when every required module is importable on this instance; otherwise it is
# skipped (logged once) and can never be installed, mounted, or listed. Apps with
# no required modules (external apps like wearables) are always admitted.
_CATALOG_FACTORIES: tuple[tuple[str, tuple[str, ...], Callable[[], AppSpec]], ...] = (
    ("finance", ("finance", "plaid_webhook"), _finance_spec),
    ("budget", ("budget",), _budget_spec),
    ("commute", ("commute",), _commute_spec),
    ("location", ("location", "owntracks"), _location_spec),
    ("birds", ("birds", "environment"), _birds_spec),
    ("health", ("health",), _health_spec),
    ("notes-app", ("notes",), _notes_spec),
    ("wearables", (), _wearables_spec),
)


# --------------------------------------------------------------------------- #
# Out-of-tree apps.
#
# `_CATALOG_FACTORIES` above is the BUILT-IN library: apps whose code ships in
# this repo. It was also, until now, the only way into the catalog -- a literal
# tuple, consumed once at import, with the factory required to be defined in
# this file and the route module required to sit in `backend/routes/`. So
# nothing outside `backend/` could be an app, which makes "an app is installed
# content, not platform source" unachievable rather than merely undone.
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


def _entry_point_factories() -> "list[tuple[str, tuple[str, ...], Callable[[], AppSpec]]]":
    out: "list[tuple[str, tuple[str, ...], Callable[[], AppSpec]]]" = []
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

        # No `required` modules: an out-of-tree app's importability is its own
        # entry point's problem, and a failure to load is caught below and
        # skipped exactly like a built-in factory that raises.
        out.append((ep.name, (), _factory))
    return out


def _build_catalog() -> dict[str, AppSpec]:
    catalog: dict[str, AppSpec] = {}
    builtin_ids = {app_id for app_id, _, _ in _CATALOG_FACTORIES}
    discovered = _entry_point_factories()
    for app_id, required, factory in (*_CATALOG_FACTORIES, *discovered):
        missing = [m for m in required if not _has_module(m)]
        if missing:
            log.info("app %r not in catalog: route module(s) not shipped: %s", app_id, ", ".join(missing))
            continue
        try:
            spec = factory()
        except Exception as e:  # a factory that fails to build must not sink the rest
            log.warning("app %r skipped: failed to build spec: %s", app_id, e)
            continue
        if app_id in catalog and app_id in builtin_ids:
            log.info("app %r: an installed distribution supersedes the built-in", app_id)
        catalog[app_id] = spec
    if discovered:
        log.info("app discovery: %d out-of-tree app(s) via %s entry points",
                 len(discovered), APP_ENTRY_POINT_GROUP)
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


def _runtime_dir(name: str):
    """An app-shared (not per-owner) directory under the runtime root."""
    from services import stores as _stores

    return _stores._runtime_root() / name


def _owner_file(name: str):
    """A per-owner data file under the current owner's runtime dir."""
    from services import stores as _stores

    base = _stores._operator_runtime_dir()
    return None if base is None else base / name


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
    """(app_id, name, path_factory, type, per_owner) for every installed app."""
    return [
        (spec.id, name, factory, type_, per_owner)
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
    launcher and in-app assistant dock derive entirely from this data."""
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
    return cards


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
