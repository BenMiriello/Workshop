import os

import httpx


LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")


class LettaClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=LETTA_URL,
            headers={"Authorization": f"Bearer {LETTA_PASS}"},
            timeout=30.0,
        )

    async def aclose(self):
        await self._client.aclose()

    async def list_agents(self):
        r = await self._client.get("/v1/agents/")
        r.raise_for_status()
        return r.json()

    async def get_agent(self, agent_id: str):
        r = await self._client.get(f"/v1/agents/{agent_id}")
        r.raise_for_status()
        return r.json()

    async def create_agent(self, name: str, persona: str, human: str, llm_config: dict | None = None, embedding_config: dict | None = None):
        payload: dict = {"name": name, "memory_blocks": [
            {"label": "persona", "value": persona},
            {"label": "human", "value": human},
        ]}
        if llm_config:
            payload["llm_config"] = llm_config
        else:
            payload["model"] = "letta/letta-free"
        if embedding_config:
            payload["embedding_config"] = embedding_config
        r = await self._client.post("/v1/agents/", json=payload)
        r.raise_for_status()
        return r.json()

    async def update_memory_block(self, agent_id: str, label: str, value: str):
        # Letta API path: /v1/agents/{id}/core-memory/blocks/{label}
        # (the "core-" prefix is required; /memory/blocks/ returns 404)
        r = await self._client.patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{label}",
            json={"value": value},
        )
        r.raise_for_status()
        return r.json()

    async def delete_agent(self, agent_id: str):
        r = await self._client.delete(f"/v1/agents/{agent_id}")
        r.raise_for_status()
        return {"ok": True}

    async def update_agent_metadata(self, agent_id: str, metadata: dict):
        r = await self._client.patch(
            f"/v1/agents/{agent_id}",
            json={"metadata": metadata},
        )
        r.raise_for_status()
        return r.json()

    async def insert_archival(self, agent_id: str, text: str):
        r = await self._client.post(
            f"/v1/agents/{agent_id}/archival-memory",
            json={"text": text},
        )
        r.raise_for_status()
        return r.json()
