"""Register this project's Letta tools and attach them to agents.

Mirrors the Workshop's services/letta_register pattern: for each entry in
`tools.TOOL_REGISTRY`, upsert the tool from its function (Letta parses the Google
docstring into the schema the agent sees) and attach it to each named agent.
Idempotent: re-running upserts in place, and re-attaching an attached tool is a
no-op.

Run:
    LETTA_URL=http://127.0.0.1:8283 python register.py
"""
from __future__ import annotations

import os

from letta_client import Letta

from tools import TOOL_REGISTRY

LETTA_BASE = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")


def _find_agent_id(client: Letta, name: str) -> str | None:
    for a in client.agents.list():
        if a.name == name:
            return a.id
    return None


def register_all() -> dict:
    """Register every tool in TOOL_REGISTRY and attach it to its agents.
    Returns a summary of what was registered and attached."""
    client = Letta(base_url=LETTA_BASE)
    name_to_id: dict[str, str | None] = {}
    registered: list[str] = []
    attached: list[tuple[str, str]] = []
    missing_agents: list[tuple[str, str]] = []

    for tool_name, (func, agent_names) in TOOL_REGISTRY.items():
        tool = client.tools.upsert_from_function(func=func)
        registered.append(tool_name)
        for agent_name in agent_names:
            if agent_name not in name_to_id:
                name_to_id[agent_name] = _find_agent_id(client, agent_name)
            agent_id = name_to_id[agent_name]
            if not agent_id:
                missing_agents.append((tool_name, agent_name))
                continue
            client.agents.tools.attach(agent_id=agent_id, tool_id=tool.id)
            attached.append((tool_name, agent_name))

    return {
        "registered": registered,
        "attached": attached,
        "missing_agents": missing_agents,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(register_all(), indent=2, default=str))
