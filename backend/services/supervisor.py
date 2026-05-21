"""Long-running agent supervisor — lifecycle for continuous-autonomy agents.

Spec (Tab 4, 5c): for each agent with `autonomy=continuous` in Letta metadata,
spawn an asyncio task that runs its loop. Restart on crash with exponential
backoff (cap 5 min). Write a heartbeat every 30s. Stale (>2min) = unresponsive.

This is the FastAPI-lifespan-bound version. One supervisor per process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


log = logging.getLogger("workshop.supervisor")

HEARTBEATS_DIR = Path(os.environ.get(
    "WORKSHOP_HEARTBEATS_DIR",
    "/home/clawd-a/workshop/.runtime/heartbeats",
))
LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")

HEARTBEAT_INTERVAL_S = 30
SCAN_INTERVAL_S = 60          # rescan Letta for new agents every minute
BACKOFF_INITIAL_S = 5
BACKOFF_MAX_S = 300            # 5 min cap per spec
OLLAMA_CONCURRENT_LIMIT = int(os.environ.get("WORKSHOP_OLLAMA_CONCURRENCY", "2"))


# Global semaphore — shared across all supervised agents to cap Ollama load.
# Future: per-agent token budget enforcement reads from agent.metadata.
ollama_semaphore: asyncio.Semaphore | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_heartbeat(agent_id: str, status: str, notes: str = "") -> None:
    HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)
    (HEARTBEATS_DIR / f"{agent_id}.json").write_text(json.dumps({
        "agent_id": agent_id,
        "ts": _now(),
        "status": status,
        "notes": notes,
    }) + "\n")


async def _letta_get(client: httpx.AsyncClient, path: str) -> Any:
    r = await client.get(path, headers={"Authorization": f"Bearer {LETTA_PASS}"})
    r.raise_for_status()
    return r.json()


def _is_continuous(agent: dict) -> bool:
    """Per spec: agents with metadata.autonomy='continuous' are supervised.
    masini and any agent without an explicit autonomy mode are NOT continuous
    (default = off / interactive)."""
    meta = agent.get("metadata") or agent.get("metadata_", {}) or {}
    return meta.get("autonomy") == "continuous"


async def _agent_loop(agent: dict) -> None:
    """One iteration of supervised work for one agent. Heartbeat + a no-op
    poll for now (the agent's actual work is TBD — for plaid-watcher this
    will eventually mean: drain new events from /events/recent?source=plaid
    and forward to Letta). For now: write heartbeat, sleep 30s, repeat."""
    aid = agent["id"]
    _write_heartbeat(aid, "running", "supervisor loop iteration")
    await asyncio.sleep(HEARTBEAT_INTERVAL_S)


async def _supervise_one(agent: dict) -> None:
    aid = agent["id"]
    name = agent.get("name", "<?>")
    backoff = BACKOFF_INITIAL_S
    log.info("supervisor: spawning loop for %s (%s)", name, aid)
    while True:
        try:
            await _agent_loop(agent)
            backoff = BACKOFF_INITIAL_S  # success resets backoff
        except asyncio.CancelledError:
            _write_heartbeat(aid, "stopped", "supervisor shutdown")
            raise
        except Exception as e:
            log.warning("supervisor: %s (%s) crashed: %s — backoff %ds", name, aid, e, backoff)
            _write_heartbeat(aid, "crashed", f"{type(e).__name__}: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)


class AgentSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._scan_task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self.started_at: str | None = None
        global ollama_semaphore
        if ollama_semaphore is None:
            ollama_semaphore = asyncio.Semaphore(OLLAMA_CONCURRENT_LIMIT)

    async def start(self) -> None:
        self._client = httpx.AsyncClient(base_url=LETTA_URL, timeout=30.0)
        self.started_at = _now()
        self._scan_task = asyncio.create_task(self._scan_loop(), name="supervisor-scan")
        log.info("supervisor: started")

    async def stop(self) -> None:
        if self._scan_task:
            self._scan_task.cancel()
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._client:
            await self._client.aclose()
        log.info("supervisor: stopped")

    async def _scan_loop(self) -> None:
        while True:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("supervisor: scan iteration failed: %s", e)
            await asyncio.sleep(SCAN_INTERVAL_S)

    async def _reconcile(self) -> None:
        assert self._client
        agents = await _letta_get(self._client, "/v1/agents/")
        wanted_ids = {a["id"] for a in agents if _is_continuous(a)}
        # Cancel tasks for agents that are no longer continuous (or deleted).
        for aid in list(self._tasks.keys()):
            if aid not in wanted_ids:
                self._tasks[aid].cancel()
                del self._tasks[aid]
        # Spawn tasks for newly-continuous agents.
        for agent in agents:
            if _is_continuous(agent) and agent["id"] not in self._tasks:
                t = asyncio.create_task(_supervise_one(agent), name=f"supervisor-{agent['name']}")
                self._tasks[agent["id"]] = t

    def status(self) -> dict:
        return {
            "started_at": self.started_at,
            "supervised_agents": [
                {"agent_id": aid, "task_name": t.get_name(), "done": t.done()}
                for aid, t in self._tasks.items()
            ],
            "ollama_concurrent_limit": OLLAMA_CONCURRENT_LIMIT,
        }
