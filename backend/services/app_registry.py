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
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI

from deps_auth import require_internal_caller
from deps_user import require_user, require_domain_owner
from routes import (
    birds,
    budget,
    commute,
    environment,
    finance,
    health,
    location,
    owntracks,
    plaid_webhook,
)
from services import installed_apps

log = logging.getLogger("workshop.apps")


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
    on_startup: tuple[Callable[[FastAPI], Awaitable[None]], ...] = ()
    background: tuple[Callable[[], Awaitable[None]], ...] = ()


# --------------------------------------------------------------------------- #
# Finance / Budget MCP: the plaid server the finance & budget chat assistants
# use for on-demand transaction lookup. Path is home-relative (no operator-
# specific absolute path baked into platform code); override with the env var.
# --------------------------------------------------------------------------- #
_PLAID_MCP = {
    "plaid": {
        "type": "stdio",
        "command": "node",
        "args": [os.environ.get(
            "WORKSHOP_PLAID_MCP_SERVER",
            str(Path.home() / "clawdbot" / "plaid-mcp" / "server.js"),
        )],
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
BUILTIN_APPS: dict[str, AppSpec] = {
    "finance": AppSpec(
        id="finance",
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
        on_startup=(_finance_on_startup,),
        background=(_prewarm_loop, _categorize_loop),
    ),
    "budget": AppSpec(
        id="budget",
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
    ),
    "commute": AppSpec(
        id="commute",
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
    ),
    "location": AppSpec(
        id="location",
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
    ),
    "birds": AppSpec(
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
    ),
    "health": AppSpec(
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
    ),
    "wearables": AppSpec(
        id="wearables",
        card={
            "title": "Open Wearables",
            "tagline": "Wearables data UI",
            "icon": "Watch",
            # External app served from its own origin (Tailscale Serve per-port).
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
    ),
}


def installed_specs() -> list[AppSpec]:
    """AppSpecs for installed apps, in installed order. Unknown ids are skipped."""
    out: list[AppSpec] = []
    for app_id in installed_apps.installed_app_ids():
        spec = BUILTIN_APPS.get(app_id)
        if spec is not None:
            out.append(spec)
    return out


def installed_mcp_servers() -> dict[str, dict]:
    """Merged MCP server launch specs registered by installed apps.

    Replaces the hardcoded llm_router._MCP_SERVER_SPECS: a server (e.g. plaid) is
    available to chat tool-use only if an app that registers it is installed."""
    merged: dict[str, dict] = {}
    for spec in installed_specs():
        merged.update(spec.mcp)
    return merged


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
            }
            manifests.upsert_manifest(spec.id, entry)
    except Exception as e:  # pragma: no cover - best-effort seeding
        log.warning("identity manifest seeding failed: %s", e)
