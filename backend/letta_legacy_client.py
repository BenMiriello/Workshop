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

    # --- archives + passages (knowledge scopes) ------------------------------
    # Raw /v1 surface for the KnowledgeStore. Archives back per-scope semantic
    # recall; passages carry a note-id tag so a note maps to one passage.

    # Local Ollama nomic-embed-text config, used when no agent exists to borrow
    # a known-good embedding_config from. An archive created without this never
    # embeds its passages, so search returns nothing.
    DEFAULT_EMBEDDING_CONFIG = {
        "embedding_endpoint_type": "openai",
        "embedding_endpoint": "http://host.docker.internal:11434/v1",
        "embedding_model": "nomic-embed-text:latest",
        "embedding_dim": 768,
        "embedding_chunk_size": 300,
        "batch_size": 32,
    }

    async def default_embedding_config(self) -> dict:
        """A known-good embedding_config: borrow the first agent's, else the
        local nomic-embed-text literal above."""
        r = await self._client.get("/v1/agents/", params={"limit": 1})
        r.raise_for_status()
        agents = r.json()
        if agents and agents[0].get("embedding_config"):
            return agents[0]["embedding_config"]
        return dict(self.DEFAULT_EMBEDDING_CONFIG)

    async def create_archive(self, name: str, embedding_config: dict, description: str = ""):
        r = await self._client.post("/v1/archives/", json={
            "name": name,
            "embedding_config": embedding_config,
            "description": description,
        })
        r.raise_for_status()
        return r.json()

    async def list_archives(self):
        r = await self._client.get("/v1/archives/")
        r.raise_for_status()
        return r.json()

    async def delete_archive(self, archive_id: str):
        r = await self._client.delete(f"/v1/archives/{archive_id}")
        r.raise_for_status()
        return {"ok": True}

    # Passage insert + search both run an embedding pass on the Letta side
    # (Ollama nomic-embed-text), which can take much longer than the default 30s
    # on a cold model load. Give those calls a wider timeout so the first write
    # to a fresh archive does not fail on a cold embedder.
    _EMBED_TIMEOUT = 120.0

    async def insert_passage(self, archive_id: str, text: str, tags: list, metadata: dict | None = None):
        body: dict = {"text": text, "tags": tags}
        if metadata is not None:
            body["metadata"] = metadata
        r = await self._client.post(f"/v1/archives/{archive_id}/passages", json=body, timeout=self._EMBED_TIMEOUT)
        r.raise_for_status()
        return r.json()

    async def insert_passages(self, archive_id: str, items: list):
        """Batch insert. items: [{text, tags, metadata?}, ...]."""
        r = await self._client.post(
            f"/v1/archives/{archive_id}/passages/batch",
            json={"passages": items},
            timeout=self._EMBED_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    async def delete_passage(self, archive_id: str, passage_id: str):
        r = await self._client.delete(f"/v1/archives/{archive_id}/passages/{passage_id}")
        r.raise_for_status()
        return {"ok": True}

    async def search_passages(self, archive_id: str, query: str, limit: int = 5):
        r = await self._client.post("/v1/passages/search", json={
            "query": query,
            "archive_id": archive_id,
            "limit": limit,
        }, timeout=self._EMBED_TIMEOUT)
        r.raise_for_status()
        return r.json()
