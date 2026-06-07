"""Register Letta tools and attach them to agents.

This module is invoked at FastAPI startup (from lifespan) to ensure all the
Python tool functions in services/letta_tools/ are registered with Letta and
attached to the appropriate agents.

Idempotent: re-runs are safe — upsert_from_function updates existing tools
in place; attaching an already-attached tool is a no-op.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from letta_client import Letta


log = logging.getLogger("workshop.letta_register")

LETTA_BASE = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")


def _client() -> Letta:
    return Letta(base_url=LETTA_BASE)


def _find_agent_id(client: Letta, name: str) -> str | None:
    for a in client.agents.list():
        if a.name == name:
            return a.id
    return None


def register_all() -> dict:
    """Register every tool in TOOL_REGISTRY with Letta, then attach to each
    designated agent. Returns a summary dict for logging."""
    from .letta_tools import TOOL_REGISTRY

    client = _client()

    # Cache agent name → id once at the top
    name_to_id: dict[str, str | None] = {}

    registered: list[str] = []
    failed_register: list[tuple[str, str]] = []
    attached: list[tuple[str, str]] = []
    failed_attach: list[tuple[str, str, str]] = []

    for tool_name, (func, agent_names) in TOOL_REGISTRY.items():
        # Register/upsert the tool
        try:
            tool = client.tools.upsert_from_function(func=func)
        except Exception as e:
            failed_register.append((tool_name, f"{type(e).__name__}: {e}"))
            log.warning("letta_register: failed to register %s: %s", tool_name, e)
            continue
        tool_id = getattr(tool, "id", None)
        if not tool_id:
            failed_register.append((tool_name, "no id returned"))
            continue
        registered.append(tool_name)

        # Attach to each named agent
        for agent_name in agent_names:
            if agent_name not in name_to_id:
                name_to_id[agent_name] = _find_agent_id(client, agent_name)
            agent_id = name_to_id[agent_name]
            if not agent_id:
                failed_attach.append((tool_name, agent_name, "agent not found"))
                continue
            try:
                client.agents.tools.attach(agent_id=agent_id, tool_id=tool_id)
                attached.append((tool_name, agent_name))
            except Exception as e:
                # Detect already-attached vs real failure: re-attach often errors
                # if the tool is already on the agent; that's fine.
                msg = str(e)
                if "already" in msg.lower():
                    attached.append((tool_name, agent_name))
                else:
                    failed_attach.append((tool_name, agent_name, msg[:200]))

    summary = {
        "registered_count": len(registered),
        "registered": registered,
        "failed_register": failed_register,
        "attached_count": len(attached),
        "attached": attached,
        "failed_attach": failed_attach,
    }
    log.info(
        "letta_register: registered=%d failed_register=%d attached=%d failed_attach=%d",
        len(registered), len(failed_register), len(attached), len(failed_attach),
    )
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(register_all(), indent=2, default=str))
