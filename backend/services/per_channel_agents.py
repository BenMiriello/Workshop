"""Per-Discord-channel masini agent management.

Spec (operator, 2026-05-21): each Discord channel should be its own
conversation thread (no cross-channel context bleed) while sharing the
operator profile + long-term memories.

Implementation: spin off `masini-<channel-slug>` from base masini for each
new chat_id. The spinoff inherits persona, human, tools, autonomy mode,
and persona_path (which resolves to shared global memory blocks +
archival namespaces). Each agent has its own conversation history and its
own archival namespace, so per-channel context is isolated but operator
profile is shared via the auto-attached `workshop-context` global block.

Map of {chat_id: agent_id} cached at `.runtime/channel-agent-map.json`.

The base masini agent id is read from MASINI_AGENT_ID env or defaults to
the agent created during migrate_memory.py bootstrap.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import httpx

MAP_FILE = Path(os.environ.get(
    "WORKSHOP_CHANNEL_AGENT_MAP",
    "/home/clawd-a/workshop/.runtime/channel-agent-map.json",
))
LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")
BASE_MASINI_ID = os.environ.get("MASINI_AGENT_ID", "agent-1a4228d1-05e9-4f0a-a9cc-43f8dfeaef50")

_lock = asyncio.Lock()


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9-]", "-", s.lower()).strip("-")
    return s[:32] or "ch"


def _load_map() -> dict:
    try:
        return json.loads(MAP_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_map(m: dict) -> None:
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MAP_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(MAP_FILE)


async def _agent_exists(client: httpx.AsyncClient, agent_id: str) -> bool:
    r = await client.get(f"/v1/agents/{agent_id}")
    return r.status_code == 200


async def ensure(chat_id: str, channel_name: str | None = None) -> dict:
    """Return {agent_id, created, name} for the per-channel masini.

    - If `chat_id` is already mapped AND the agent still exists in Letta,
      return the existing mapping.
    - Otherwise spin off a new `masini-<slug>` from base masini, persist
      the mapping, return it.
    """
    async with _lock:
        mapping = _load_map()
        async with httpx.AsyncClient(
            base_url=LETTA_URL,
            headers={"Authorization": f"Bearer {LETTA_PASS}"},
            timeout=30.0,
        ) as client:
            existing = mapping.get(chat_id)
            if existing and await _agent_exists(client, existing):
                return {"agent_id": existing, "created": False, "chat_id": chat_id,
                        "name": (mapping.get(f"{chat_id}.name") or "masini")}

            # Spin off from base masini. The spinoff route handles persona/human
            # copy, metadata inheritance (tools+autonomy), and global-block attach.
            slug = _slug(channel_name or f"ch-{chat_id}")
            new_name = f"masini-{slug}"

            # Use our own backend's POST /agents/spinoff so we get global-memory
            # attach + memory_tree resolution for free.
            r = await client.post(
                "http://127.0.0.1:8000/agents/spinoff",
                json={"base_agent_id": BASE_MASINI_ID, "new_name": new_name},
                headers={"Authorization": "", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            agent = r.json()
            agent_id = agent["id"]

            mapping[chat_id] = agent_id
            mapping[f"{chat_id}.name"] = new_name
            _save_map(mapping)
            return {"agent_id": agent_id, "created": True, "chat_id": chat_id, "name": new_name}


async def get(chat_id: str) -> str | None:
    """Lookup-only: returns agent_id or None. Doesn't create."""
    mapping = _load_map()
    return mapping.get(chat_id)


def list_all() -> dict:
    """Diagnostic: full map of {chat_id: agent_id} (plus .name entries)."""
    return _load_map()


def unmap(chat_id: str) -> bool:
    """Remove a chat_id → agent_id mapping. Does NOT delete the Letta agent
    (operator can keep its history). Returns whether something was removed."""
    mapping = _load_map()
    removed = False
    for k in [chat_id, f"{chat_id}.name"]:
        if k in mapping:
            del mapping[k]
            removed = True
    if removed:
        _save_map(mapping)
    return removed
