"""Autonomous app monitor — keep desired-running apps running.

The "autonomously monitor and fix and maintain" half of the platform. A periodic
pass over every app the runner knows about:

  - For an app whose desired state is `running`, any enabled service whose process
    has died (crashed, OOM-killed, or orphaned by a backend restart) is restarted —
    no rebuild, just re-spawn — with exponential backoff and a crashloop cap so a
    service that cannot stay up is not hammered forever (it is parked as
    `crashlooping` until the operator intervenes or it is restarted manually).
  - Dead ports are freed by port_registry.reconcile_loop(), not here.

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

from services import app_runner

log = logging.getLogger("workshop.app_monitor")

INTERVAL = 15.0      # seconds between passes
BASE_DELAY = 5.0     # first retry waits ~5s
MAX_DELAY = 300.0    # backoff caps at 5 min
MAX_ATTEMPTS = 6     # consecutive failures before backing off to the slow lane

# How long a parked service waits before it is tried again.
#
# `crashlooping` used to be TERMINAL: the tick skipped the service forever and
# nothing in the system could lift the flag, so the only cure was a human who
# knew to look. One app's api sat parked through 542 restarts and simply read as
# "the API is down" for hours. A supervisor that gives up permanently on a
# service the operator has asked to keep running is not a supervisor.
#
# So parking is now a SLOW LANE rather than a grave: hammering stops (which is
# what the cap is for), and one retry every five minutes costs nothing and means
# a service whose dependency came back, or whose port finally freed, heals on its
# own. systemd's own default is the same shape (Restart=always with RestartSec).
PARKED_RETRY_S = 300.0

# Between full passes, a pid-only check runs this often and starts a pass the
# moment a desired-running service's process is gone. A full pass health-probes
# every service, so it stays at INTERVAL; without this, a deliberate restart (an
# agent SIGTERMing an app's api after an edit) sat dead for up to INTERVAL.
WATCH_S = 1.0

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
    capped = app_runner.cap_logs()  # bound runaway app logs before anything else

    actions: list[dict] = []
    if capped:
        actions.append({"action": "capped-logs", "files": capped})
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
                bk = _BACKOFF.setdefault(_key(slug, name),
                                         {"attempts": MAX_ATTEMPTS, "next_at": 0.0})
                if now < bk.get("parked_until", 0.0):
                    continue
                bk["parked_until"] = now + PARKED_RETRY_S
                bk["attempts"] = MAX_ATTEMPTS - 1  # one try, then park again
                try:
                    app_runner.restart_service(slug, name)
                    actions.append({"slug": slug, "service": name,
                                    "action": "unparked-retry"})
                    log.info("retrying parked %s/%s", slug, name)
                except Exception as e:
                    actions.append({"slug": slug, "service": name,
                                    "action": "unpark-failed", "error": str(e)[:200]})
                continue
            # status is failed/stopped while the app is desired-running → restart.
            bk = _BACKOFF.setdefault(_key(slug, name), {"attempts": 0, "next_at": 0.0})
            if now < bk["next_at"]:
                continue
            if bk["attempts"] >= MAX_ATTEMPTS:
                app["services"][name]["status"] = "crashlooping"
                app_runner._persist_state()
                # Parking moves the service to the slow lane handled by the
                # `crashlooping` branch above: one retry every PARKED_RETRY_S
                # instead of the exponential backoff. The first slow-lane retry
                # waits a full interval, not the next 15s pass. Logged loudly
                # because tick()'s return value is discarded by monitor_loop, and
                # a service parked in silence reads from the app as "the API is
                # down" with no way to find out why.
                bk["parked_until"] = now + PARKED_RETRY_S
                log.error(
                    "PARKED %s/%s as crashlooping after %d failed restarts; "
                    "retrying once every %ds until it stays up (last error: %s)",
                    slug, name, bk["attempts"], int(PARKED_RETRY_S),
                    app["services"][name].get("error") or "unknown")
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


def any_died() -> bool:
    """True when a desired-running, non-container service tracked as `running` has no
    live process. Reads /proc only — no health probe — so it is cheap per second."""
    if app_runner._use_containers():
        return False
    for app in list(app_runner._STATE.values()):
        if app.get("desired") != "running" or app.get("backend") == "container":
            continue
        for s in (app.get("services") or {}).values():
            if s.get("status") == "running" and not app_runner._alive(s.get("pid")):
                return True
    return False


async def _wait_for_next_pass(interval: float) -> None:
    deadline = time.monotonic() + interval
    while time.monotonic() < deadline:
        await asyncio.sleep(min(WATCH_S, interval))
        if any_died():
            return


async def monitor_loop(interval: float = INTERVAL) -> None:
    """Run tick() forever on `interval`. Started from the backend lifespan. A tick
    failure is logged and the loop continues — the monitor must not die quietly."""
    log.info("app monitor started (interval=%ss)", interval)
    while True:
        try:
            result = await asyncio.to_thread(tick)
            for act in result.get("actions", ()):
                if act.get("action") == "capped-logs":
                    continue
                log.info("app monitor: %s", act)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("app monitor tick failed")
        await _wait_for_next_pass(interval)
