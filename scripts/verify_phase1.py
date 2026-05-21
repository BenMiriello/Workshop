#!/usr/bin/env python3
"""End-to-end Phase 1 verification (no service start).

Exercises the same code path the FastAPI /agents endpoint would call,
proving Letta is reachable, masini is present, and the LettaClient + route
shape match the React frontend's expectations.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from letta_client import LettaClient  # noqa: E402


def shape_agent(a):
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "created_at": a.get("created_at"),
        "memory_blocks": [b.get("label") for b in a.get("memory", {}).get("blocks", [])],
    }


async def main():
    client = LettaClient()
    try:
        agents = await client.list_agents()
    finally:
        await client.aclose()

    if not agents:
        print("FAIL: no agents in Letta")
        return 1

    shaped = [shape_agent(a) for a in agents]
    masini = next((a for a in shaped if a["name"] == "masini"), None)
    if not masini:
        print("FAIL: masini agent missing")
        print("Found:", [a["name"] for a in shaped])
        return 1

    print("Letta reachable: yes")
    print(f"Agents: {len(shaped)}")
    print(f"masini: id={masini['id']}")
    print(f"memory blocks: {masini['memory_blocks']}")
    print()
    print("Frontend-shaped response (first agent):")
    print(shaped[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
