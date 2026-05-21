#!/usr/bin/env python3
"""Migrate masini's CLAUDE.md + MEMORY.md into a Letta agent.

Creates (or updates) an agent named "masini" in the local Letta server. The
project CLAUDE.md becomes the persona block, the global user CLAUDE.md fills
the human block, and each MEMORY.md-indexed memory file becomes one archival
memory record.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx


LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")
PROJECT_CLAUDE_MD = Path("/home/clawd-a/clawdbot/CLAUDE.md")
USER_CLAUDE_MD = Path("/home/clawd-a/.claude/CLAUDE.md")
MEMORY_DIR = Path("/home/clawd-a/.claude/projects/-home-clawd-a-clawdbot/memory")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=LETTA_URL,
        headers={"Authorization": f"Bearer {LETTA_PASS}"},
        timeout=60.0,
    )


def _read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _find_agent(c: httpx.Client, name: str):
    r = c.get("/v1/agents/")
    r.raise_for_status()
    for a in r.json():
        if a.get("name") == name:
            return a
    return None


def _upsert_agent(c: httpx.Client, persona: str, human: str) -> str:
    existing = _find_agent(c, "masini")
    if existing:
        agent_id = existing["id"]
        for label, value in (("persona", persona), ("human", human)):
            c.patch(
                f"/v1/agents/{agent_id}/memory/blocks/{label}",
                json={"value": value},
            ).raise_for_status()
        return agent_id

    # Ollama embedding requires /v1 suffix that Letta's auto-discovery strips.
    # Pass explicit embedding_config to bypass the strip behavior.
    r = c.post(
        "/v1/agents/",
        json={
            "name": "masini",
            "model": "letta/letta-free",
            "embedding_config": {
                "embedding_endpoint_type": "openai",
                "embedding_endpoint": "http://host.docker.internal:11434/v1",
                "embedding_model": "nomic-embed-text:latest",
                "embedding_dim": 768,
                "embedding_chunk_size": 300,
                "batch_size": 32,
            },
            "memory_blocks": [
                {"label": "persona", "value": persona},
                {"label": "human", "value": human},
            ],
        },
    )
    r.raise_for_status()
    return r.json()["id"]


def _seed_archival(c: httpx.Client, agent_id: str):
    if not MEMORY_DIR.exists():
        return 0
    count = 0
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        text = path.read_text().strip()
        if not text:
            continue
        c.post(
            f"/v1/agents/{agent_id}/archival-memory",
            json={"text": f"[{path.name}]\n{text}"},
        ).raise_for_status()
        count += 1
    return count


def main() -> int:
    persona = _read(PROJECT_CLAUDE_MD)
    human = _read(USER_CLAUDE_MD)
    if not persona:
        print("WARN: project CLAUDE.md missing — persona will be empty", file=sys.stderr)
    with _client() as c:
        agent_id = _upsert_agent(c, persona, human)
        n = _seed_archival(c, agent_id)
    print(f"masini agent: {agent_id}")
    print(f"archival records inserted: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
