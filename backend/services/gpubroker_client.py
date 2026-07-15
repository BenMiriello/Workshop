"""Workshop's client for gpubrokerd.

Thin async wrapper around the broker HTTP API. All methods gracefully tolerate
the broker being down — returning None or empty state — so Workshop's UI
keeps working when gpubrokerd is offline. Workshop's own local off-switch
remains the authoritative control plane regardless.

URL configurable via GPUBROKER_URL env (default http://127.0.0.1:8447).
"""
from __future__ import annotations

import os
from typing import Any

import httpx


URL = os.environ.get("GPUBROKER_URL", "http://127.0.0.1:8447")
APP_NAME = "workshop-overseer"
DEFAULT_BUDGET_MIB = 22500
DEFAULT_PRIORITY = 30
# On preemption the broker frees Workshop's GPU by evicting any resident Ollama
# models (the heartbeat supervisor that used to be paused here is retired).
PREEMPT_URL = os.environ.get(
    "GPUBROKER_WORKSHOP_PREEMPT_URL",
    "http://127.0.0.1:8000/ollama/unload",
)


class BrokerUnavailable(Exception):
    pass


async def _get(path: str, timeout: float = 3.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(URL + path)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict, timeout: float = 30.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(URL + path, json=body)
        r.raise_for_status()
        return r.json()


async def _delete(path: str, timeout: float = 3.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.delete(URL + path)
        return r.json() if r.content else {}


async def status() -> dict:
    """Broker status. Returns {"available": False, ...} if broker is down."""
    try:
        s = await _get("/status")
        return {"available": True, **s}
    except (httpx.HTTPError, ValueError):
        return {"available": False}


async def my_leases() -> list[dict]:
    """All leases held by app=workshop-overseer."""
    try:
        r = await _get(f"/leases?app={APP_NAME}")
        return r.get("active", [])
    except (httpx.HTTPError, ValueError):
        return []


async def ensure_registered() -> bool:
    """Register workshop-overseer in the broker app registry. Safe to call
    on every startup — idempotent (POST /app does upsert)."""
    body = {
        "name": APP_NAME,
        "default_budget_mib": DEFAULT_BUDGET_MIB,
        "default_priority": DEFAULT_PRIORITY,
        "preempt_handler": {"type": "http", "url": PREEMPT_URL, "grace_s": 30},
        "cmdline_match": r"ollama.*serve",
        "watch_implicit": True,
    }
    try:
        await _post("/app", body)
        return True
    except (httpx.HTTPError, ValueError):
        return False


async def acquire(
    vram_budget_mib: int = DEFAULT_BUDGET_MIB,
    priority: int = DEFAULT_PRIORITY,
    reason: str = "",
    client_pid: int | None = None,
    ttl_s: int | None = None,
    blocking: bool = True,
    wait_timeout_s: int = 300,
) -> dict:
    """Acquire a Workshop lease. Raises BrokerUnavailable if broker is down."""
    body: dict[str, Any] = {
        "app": APP_NAME,
        "vram_budget_mib": vram_budget_mib,
        "priority": priority,
        "reason": reason,
        "client_pid": client_pid or os.getpid(),
        "blocking": blocking,
        "wait_timeout_s": wait_timeout_s,
    }
    if ttl_s is not None:
        body["ttl_s"] = ttl_s
    try:
        return await _post("/lease", body, timeout=wait_timeout_s + 10)
    except (httpx.HTTPError, ValueError) as e:
        raise BrokerUnavailable(str(e)) from e


async def release(lease_id: str) -> bool:
    try:
        r = await _delete(f"/lease/{lease_id}")
        return bool(r.get("released"))
    except (httpx.HTTPError, ValueError):
        return False


async def release_all_mine() -> int:
    """Release every workshop-overseer lease the broker knows about."""
    leases = await my_leases()
    released = 0
    for l in leases:
        if await release(l["id"]):
            released += 1
    return released


async def boost(priority: int, ttl_s: int = 1800) -> dict | None:
    try:
        return await _post("/priority", {"app": APP_NAME, "priority": priority, "ttl_s": ttl_s})
    except (httpx.HTTPError, ValueError):
        return None


async def reserve_exclusive(duration_s: int, reason: str = "operator hold") -> dict | None:
    """Acquire a high-priority short-TTL lease the operator can use to hold
    the GPU for themselves. Distinct app name 'workshop-manual' so it shows
    separately in the broker UI."""
    body = {
        "app": "workshop-manual",
        "vram_budget_mib": DEFAULT_BUDGET_MIB,
        "priority": 90,
        "ttl_s": duration_s,
        "reason": reason,
        "client_pid": os.getpid(),
        "blocking": False,
    }
    try:
        return await _post("/lease", body, timeout=10)
    except (httpx.HTTPError, ValueError):
        return None
