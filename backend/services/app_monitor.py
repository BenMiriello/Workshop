"""Autonomous app monitor — keep desired-running apps running.

The "autonomously monitor and fix and maintain" half of the platform. A periodic
pass over every app the runner knows about:

  - For an app whose desired state is `running`, any enabled service whose process
    has died (crashed, OOM-killed, or orphaned by a backend restart) is restarted —
    no rebuild, just re-spawn — with exponential backoff and a crashloop cap so a
    service that cannot stay up is not hammered forever (it is parked as
    `crashlooping` until the operator intervenes or it is restarted manually).
  - Ports whose owning process has died are reconciled (freed) each pass.

This is operational supervision of the operator's OWN apps on a single-user box —
the maintenance the operator asked for — not multi-tenant guardrails.

Backoff: attempt N waits min(BASE * 2**(N-1), MAX_DELAY) before the next retry.
After MAX_ATTEMPTS consecutive failures the service is marked `crashlooping` and the
monitor leaves it alone until its restart counter is reset (a manual restart or a
healthy observation resets it).
"""
from __future__ import annotations

import asyncio
import logging
import time

from services import app_runner, port_registry

log = logging.getLogger("workshop.app_monitor")

INTERVAL = 15.0      # seconds between passes
BASE_DELAY = 5.0     # first retry waits ~5s
MAX_DELAY = 300.0    # backoff caps at 5 min
MAX_ATTEMPTS = 6     # consecutive failures before parking as crashlooping

# (slug, service) -> {"attempts": int, "next_at": float}
_BACKOFF: dict[tuple[str, str], dict] = {}


def _key(slug: str, svc: str) -> tuple[str, str]:
    return (slug, svc)


def _reset(slug: str, svc: str) -> None:
    _BACKOFF.pop(_key(slug, svc), None)


def tick(now: float | None = None) -> dict:
    """One monitor pass (synchronous). Returns a summary of actions taken. Safe to
    call directly in tests."""
    now = now if now is not None else time.time()
    port_registry.reconcile()

    actions: list[dict] = []
    for slug in list(app_runner._STATE.keys()):
        app = app_runner._STATE.get(slug) or {}
        if app.get("desired") != "running":
            continue
        # Once docker is reachable the subprocess supervisor is retired: container
        # apps self-heal via docker's restart policy, and a stale subprocess app must
        # NOT be resurrected here (it would race the container instance for the port)
        # — the operator restarts it once to adopt the container backend.
        if app.get("backend") == "container" or app_runner._use_containers():
            continue
        # Refresh live status (this marks crashed services as failed + frees ports).
        status = app_runner.app_status(slug)
        for svc in status["services"]:
            name = svc["name"]
            if svc["status"] in ("running", "starting", "building", "disabled"):
                # Healthy progress resets any prior backoff.
                if svc["status"] == "running":
                    _reset(slug, name)
                continue
            if svc["status"] == "crashlooping":
                continue
            # status is failed/stopped while the app is desired-running → restart.
            bk = _BACKOFF.setdefault(_key(slug, name), {"attempts": 0, "next_at": 0.0})
            if now < bk["next_at"]:
                continue
            if bk["attempts"] >= MAX_ATTEMPTS:
                app["services"][name]["status"] = "crashlooping"
                app_runner._persist_state()
                actions.append({"slug": slug, "service": name, "action": "parked-crashlooping"})
                continue
            bk["attempts"] += 1
            delay = min(BASE_DELAY * (2 ** (bk["attempts"] - 1)), MAX_DELAY)
            bk["next_at"] = now + delay
            try:
                app_runner.restart_service(slug, name)
                actions.append({"slug": slug, "service": name, "action": "restarted",
                                "attempt": bk["attempts"]})
            except Exception as e:  # AppError or unexpected — keep the loop alive
                actions.append({"slug": slug, "service": name, "action": "restart-failed",
                                "attempt": bk["attempts"], "error": str(e)[:200]})
    return {"actions": actions, "at": now}


async def monitor_loop(interval: float = INTERVAL) -> None:
    """Run tick() forever on `interval`. Started from the backend lifespan. A tick
    failure is logged and the loop continues — the monitor must not die quietly."""
    log.info("app monitor started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("app monitor tick failed")
        await asyncio.sleep(interval)
