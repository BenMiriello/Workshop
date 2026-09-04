"""This app's declaration: what it is, what it mounts, what it runs.

Moved out of `services/app_registry.py` on 2026-09-01, where one hardcoded
factory per app made the platform's catalog module know every app by name. The
registry now discovers declarations and names none. Behaviour is unchanged: the
spec below is the same object, moved verbatim.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import Depends

from deps_auth import require_internal_caller
from deps_user import require_user
from services.app_spec import AppSpec, RouterMount

log = logging.getLogger("workshop.apps")


async def _env_refresh_loop() -> None:
    """Keep the weather/astro spine fresh (stdlib astro + Open-Meteo) and score
    any forecast predictions whose hour has passed. Best-effort, off-thread."""
    from services import birds_environment as environment_svc

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


# Route modules this app needs; the catalog drops the app when they are not
# shipped. See `app_registry.APP_REQUIRES_ATTR`.
REQUIRES = ("birds", "birds_environment",)


def app_spec() -> AppSpec:
    from routes import birds, birds_environment as environment
    spec = AppSpec(
        id="birds",
        card={
            "title": "Birds",
            "tagline": "Backyard bird-song detections, live",
            "icon": "Bird",
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
    spec.card["route"] = f"/apps/{spec.id}"
    return spec
