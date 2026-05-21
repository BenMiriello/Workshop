"""Letta global memory block (operator-context).

Source-of-truth: `~/workshop/shared/global-memory.md`.
On backend startup we ensure a Letta block named `operator-context` exists and
matches the file contents; its id is cached at `.runtime/global-block-id` so
we attach the same block on every new agent without recreating it.

Every `POST /agents/` (and `POST /agents/spinoff`) call attaches this block to
the new agent via Letta's PATCH /v1/agents/{id}/core-memory/blocks/attach/{block_id}.

Sync endpoint: POST /agents/global-memory/sync — re-reads the file and PATCHes
the block value, so editing the markdown file + hitting the endpoint propagates
to all agents that have it attached.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger("workshop.global_memory")

GLOBAL_FILE = Path(os.environ.get(
    "WORKSHOP_GLOBAL_MEMORY_FILE",
    "/home/clawd-a/workshop/shared/global-memory.md",
))
GLOBAL_ID_CACHE = Path(os.environ.get(
    "WORKSHOP_GLOBAL_BLOCK_ID_PATH",
    "/home/clawd-a/workshop/.runtime/global-block-id",
))
LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")
BLOCK_LABEL = "operator-context"


def _headers() -> dict:
    return {"Authorization": f"Bearer {LETTA_PASS}"}


async def _read_file() -> str:
    return GLOBAL_FILE.read_text() if GLOBAL_FILE.exists() else "# operator-context\n(empty)"


async def ensure_block() -> str:
    """Idempotent: return the Letta block_id for operator-context.
    If cached id exists and Letta still has the block, return as-is.
    If not, create the block, cache, return.
    Always re-syncs value from disk so file edits propagate on next backend start."""
    value = await _read_file()
    cached_id = GLOBAL_ID_CACHE.read_text().strip() if GLOBAL_ID_CACHE.exists() else None
    async with httpx.AsyncClient(base_url=LETTA_URL, headers=_headers(), timeout=15.0) as client:
        if cached_id:
            r = await client.get(f"/v1/blocks/{cached_id}")
            if r.status_code == 200:
                # Sync value (idempotent — Letta accepts PATCH even when unchanged)
                await client.patch(f"/v1/blocks/{cached_id}", json={"value": value})
                return cached_id
        # No cached id or stale — create fresh
        r = await client.post("/v1/blocks/", json={
            "value": value,
            "label": BLOCK_LABEL,
            "description": "Workshop global block: operator profile + active services + plan source. Mounted on every Workshop-created agent.",
        })
        r.raise_for_status()
        block_id = r.json()["id"]
        GLOBAL_ID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_ID_CACHE.write_text(block_id)
        log.info("global_memory: created Letta block %s for %s", block_id, BLOCK_LABEL)
        return block_id


async def attach_to(agent_id: str) -> None:
    """Attach the global block to an agent. No-op if already attached."""
    block_id = await ensure_block()
    async with httpx.AsyncClient(base_url=LETTA_URL, headers=_headers(), timeout=15.0) as client:
        r = await client.patch(f"/v1/agents/{agent_id}/core-memory/blocks/attach/{block_id}")
        # 409 / 400 likely means already attached — fine
        if r.status_code >= 400 and r.status_code != 409:
            log.warning("global_memory: attach to %s returned %s: %s", agent_id, r.status_code, r.text[:200])


async def sync_value() -> dict:
    """Re-read disk and PATCH the block value. Returns {block_id, bytes}."""
    value = await _read_file()
    block_id = await ensure_block()
    async with httpx.AsyncClient(base_url=LETTA_URL, headers=_headers(), timeout=15.0) as client:
        r = await client.patch(f"/v1/blocks/{block_id}", json={"value": value})
        r.raise_for_status()
    return {"block_id": block_id, "bytes": len(value)}
