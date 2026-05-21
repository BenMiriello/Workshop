import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.memory_tree import effective_memory as resolve_memory_tree
from services.budget import budget_for
from services import global_memory, per_channel_agents

router = APIRouter()

RUNS_JSONL = Path(os.environ.get("WORKSHOP_RUNS_JSONL", "/home/clawd-a/workshop/.runtime/agent-runs.jsonl"))
HEARTBEATS_DIR = Path(os.environ.get("WORKSHOP_HEARTBEATS_DIR", "/home/clawd-a/workshop/.runtime/heartbeats"))
SUBSCRIPTIONS_PATH = Path(os.environ.get("WORKSHOP_SUBSCRIPTIONS", "/home/clawd-a/workshop/.runtime/agent-subscriptions.json"))

KNOWN_TOOLS = ["filesystem", "plaid", "google-docs", "playwright", "ollama", "web"]
KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"]
AUTONOMY_MODES = {"off", "scheduled", "event-driven", "continuous"}


def _update_subscriptions(agent_id: str, sources: list[str]) -> None:
    try:
        data = json.loads(SUBSCRIPTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {s: [] for s in KNOWN_SOURCES}
    for key in list(data.keys()):
        if isinstance(data[key], list) and agent_id in data[key]:
            data[key].remove(agent_id)
    for src in sources:
        if src in data and isinstance(data[src], list):
            if agent_id not in data[src]:
                data[src].append(agent_id)
    SUBSCRIPTIONS_PATH.write_text(json.dumps(data, indent=2))


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    persona: str = ""
    human: str = ""
    persona_path: str = ""  # e.g. "ops/personal-ai" — drives memory inheritance tree
    tools: list[str] = []
    autonomy_mode: str = "off"
    cron_expression: str = ""
    event_sources: list[str] = []


class AgentSpinoff(BaseModel):
    base_agent_id: str
    new_name: str = Field(min_length=1, max_length=64)
    persona_override: str = ""


class AgentMetadataUpdate(BaseModel):
    tools: list[str] = []
    autonomy_mode: str | None = None
    cron_expression: str | None = None
    event_sources: list[str] | None = None
    persona_path: str | None = None


class MemoryBlockUpdate(BaseModel):
    value: str


@router.get("/")
async def list_agents(request: Request):
    try:
        data = await request.app.state.letta.list_agents()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    return [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "created_at": a.get("created_at"),
            "memory_blocks": [b.get("label") for b in a.get("memory", {}).get("blocks", [])],
        }
        for a in data
    ]


@router.get("/tools")
async def list_tools():
    return {"tools": KNOWN_TOOLS}


@router.get("/memory/tree-nodes")
async def memory_tree_nodes():
    """Return all known global blocks + archival namespaces from the memory tree.
    Used by the UI to populate the memory-scope multi-selects."""
    def _collect(node: dict, blocks: set, namespaces: set):
        blocks.update(node.get("blocks") or [])
        namespaces.update(node.get("archival_namespaces") or [])
        for child in (node.get("children") or {}).values():
            _collect(child, blocks, namespaces)

    from services.memory_tree import _load_tree
    tree = _load_tree()
    root = tree.get("root", {})
    all_blocks: set = set()
    all_ns: set = set()
    _collect(root, all_blocks, all_ns)
    return {"blocks": sorted(all_blocks), "archival_namespaces": sorted(all_ns)}


@router.get("/memory/effective")
async def memory_effective_query(persona_path: str = ""):
    """Resolve the effective blocks + archival namespaces for a persona_path
    without needing an existing agent. Useful for planning and diagnostics."""
    if not persona_path:
        raise HTTPException(status_code=422, detail="persona_path query param required (e.g. ?persona_path=ops/personal-ai)")
    return resolve_memory_tree(persona_path)


@router.get("/{agent_id}/memory/effective")
async def agent_memory_effective(agent_id: str, request: Request):
    """Return the resolved memory inheritance for an existing agent. Reads
    persona_path from agent metadata and walks the tree."""
    try:
        agent = await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    meta = agent.get("metadata_") or agent.get("metadata") or {}
    persona_path = meta.get("persona_path", "")
    resolved = resolve_memory_tree(persona_path) if persona_path else {
        "persona_path": "", "blocks": [], "archival_namespaces": [], "resolved_depth": 0
    }
    return {**resolved, "agent_id": agent_id, "agent_name": agent.get("name")}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    try:
        return await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/")
async def create_agent(body: AgentCreate, request: Request):
    try:
        agent = await request.app.state.letta.create_agent(body.name, body.persona, body.human)
        meta: dict = {"tools": [t for t in body.tools if t in KNOWN_TOOLS]}
        if body.persona_path:
            resolved = resolve_memory_tree(body.persona_path)
            meta["persona_path"] = resolved["persona_path"]
            meta["effective_blocks"] = resolved["blocks"]
            meta["effective_namespaces"] = resolved["archival_namespaces"]
        if body.autonomy_mode and body.autonomy_mode in AUTONOMY_MODES:
            meta["autonomy"] = body.autonomy_mode
            meta["autonomy_mode"] = body.autonomy_mode
            if body.autonomy_mode == "scheduled":
                meta["cron_expression"] = body.cron_expression
            elif body.autonomy_mode == "event-driven":
                valid_sources = [s for s in body.event_sources if s in KNOWN_SOURCES]
                meta["event_sources"] = valid_sources
                _update_subscriptions(agent["id"], valid_sources)
        if any(v for v in meta.values() if v):
            await request.app.state.letta.update_agent_metadata(agent["id"], meta)
            agent.setdefault("metadata_", {}).update(meta)
        # Attach the Workshop global memory block (operator profile + active services + plan).
        try:
            await global_memory.attach_to(agent["id"])
        except Exception as e:
            # Non-fatal: agent exists; operator can re-sync later via /agents/global-memory/sync.
            pass
        return agent
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.patch("/{agent_id}/memory/{label}")
async def patch_memory_block(agent_id: str, label: str, body: MemoryBlockUpdate, request: Request):
    try:
        return await request.app.state.letta.update_memory_block(agent_id, label, body.value)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.patch("/{agent_id}/metadata")
async def patch_agent_metadata(agent_id: str, body: AgentMetadataUpdate, request: Request):
    meta: dict = {"tools": [t for t in body.tools if t in KNOWN_TOOLS]}
    if body.autonomy_mode is not None:
        if body.autonomy_mode not in AUTONOMY_MODES:
            raise HTTPException(status_code=422, detail=f"autonomy_mode must be one of {sorted(AUTONOMY_MODES)}")
        meta["autonomy"] = body.autonomy_mode
        meta["autonomy_mode"] = body.autonomy_mode
        if body.autonomy_mode == "scheduled":
            meta["cron_expression"] = body.cron_expression or ""
        elif body.autonomy_mode in ("event-driven", "continuous"):
            # Continuous agents may ALSO subscribe to event sources — supervisor's
            # loop can drain events for the agent the same way an event-driven
            # agent does, just on its own polling schedule. Honor event_sources
            # for both modes.
            valid_sources = [s for s in (body.event_sources or []) if s in KNOWN_SOURCES]
            meta["event_sources"] = valid_sources
            _update_subscriptions(agent_id, valid_sources)
        else:
            # off — clear subscriptions
            _update_subscriptions(agent_id, [])
    if body.persona_path is not None:
        resolved = resolve_memory_tree(body.persona_path) if body.persona_path else {
            "persona_path": "", "blocks": [], "archival_namespaces": [], "resolved_depth": 0
        }
        meta["persona_path"] = resolved["persona_path"]
        meta["effective_blocks"] = resolved["blocks"]
        meta["effective_namespaces"] = resolved["archival_namespaces"]
    try:
        return await request.app.state.letta.update_agent_metadata(agent_id, meta)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/spinoff")
async def spinoff_agent(body: AgentSpinoff, request: Request):
    """Create a new agent by forking a base agent's persona block.
    The new agent inherits the human block and metadata (tools/autonomy) from
    the base, uses persona_override if given, and starts with empty archival
    memory (fresh namespace). Archival memories are intentionally NOT copied."""
    try:
        base = await request.app.state.letta.get_agent(body.base_agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")

    # Extract persona + human values from base agent memory blocks
    blocks = {b.get("label"): b.get("value", "") for b in base.get("memory", {}).get("blocks", [])}
    persona_val = body.persona_override if body.persona_override else blocks.get("persona", "")
    human_val = blocks.get("human", "")

    # Inherit metadata from base (tools, autonomy settings)
    base_meta = base.get("metadata_") or base.get("metadata") or {}

    llm_config = base.get("llm_config") or None
    embedding_config = base.get("embedding_config") or None

    try:
        new_agent = await request.app.state.letta.create_agent(
            body.new_name, persona_val, human_val,
            llm_config=llm_config, embedding_config=embedding_config,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta create failed: {e}")

    if base_meta:
        # Re-resolve memory inheritance for the inherited persona_path so the
        # new agent's effective_blocks/effective_namespaces are freshly computed.
        if base_meta.get("persona_path"):
            resolved = resolve_memory_tree(base_meta["persona_path"])
            base_meta = {**base_meta,
                         "effective_blocks": resolved["blocks"],
                         "effective_namespaces": resolved["archival_namespaces"]}
        try:
            await request.app.state.letta.update_agent_metadata(new_agent["id"], base_meta)
            new_agent.setdefault("metadata_", {}).update(base_meta)
        except Exception:
            pass  # metadata copy is best-effort

    # Wire event subscriptions if base was event-driven
    event_sources = base_meta.get("event_sources") or []
    if event_sources:
        _update_subscriptions(new_agent["id"], event_sources)

    # Attach the Workshop global memory block (same as create_agent).
    try:
        await global_memory.attach_to(new_agent["id"])
    except Exception:
        pass

    return {
        **new_agent,
        "spinoff_from": body.base_agent_id,
        "persona_source": "override" if body.persona_override else "inherited",
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    try:
        return await request.app.state.letta.delete_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")


@router.post("/per-channel/ensure")
async def per_channel_ensure(chat_id: str, channel_name: str = ""):
    """Return (or create) the masini spinoff agent for a Discord channel.
    chat_id is the Discord channel snowflake. channel_name is optional human
    label used for slug generation. Idempotent — repeat calls return the same
    agent_id unless the underlying Letta agent has been deleted."""
    if not chat_id:
        raise HTTPException(status_code=422, detail="chat_id required")
    try:
        return await per_channel_agents.ensure(chat_id, channel_name or None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"per-channel ensure failed: {e}")


@router.get("/per-channel/list")
async def per_channel_list():
    """Diagnostic: full map of {chat_id: agent_id}."""
    return per_channel_agents.list_all()


@router.delete("/per-channel/{chat_id}")
async def per_channel_unmap(chat_id: str):
    """Remove a chat_id → agent_id mapping. Does NOT delete the Letta agent."""
    removed = per_channel_agents.unmap(chat_id)
    return {"removed": removed, "chat_id": chat_id}


@router.post("/global-memory/sync")
async def sync_global_memory():
    """Re-read shared/global-memory.md and PATCH the Letta operator-context
    block. All agents already attached will see the new value on next inference
    (Letta refreshes blocks per-request)."""
    try:
        return await global_memory.sync_value()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"global memory sync failed: {e}")


@router.get("/global-memory/info")
async def global_memory_info():
    """Return the cached block_id and file path — diagnostic."""
    from services.global_memory import GLOBAL_FILE, GLOBAL_ID_CACHE
    return {
        "file": str(GLOBAL_FILE),
        "file_bytes": GLOBAL_FILE.stat().st_size if GLOBAL_FILE.exists() else 0,
        "cached_block_id": GLOBAL_ID_CACHE.read_text().strip() if GLOBAL_ID_CACHE.exists() else None,
    }


@router.get("/{agent_id}/budget")
async def agent_budget(agent_id: str, request: Request):
    """Per-agent call budget. Cron consumer hits this before firing claude -p
    to enforce metadata.max_calls_per_hour. Returns {limit, used, remaining,
    over}. `over: true` means consumer should skip and write status=throttled."""
    try:
        agent = await request.app.state.letta.get_agent(agent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Letta unreachable: {e}")
    meta = agent.get("metadata_") or agent.get("metadata") or {}
    return budget_for(meta, agent_id)


@router.get("/{agent_id}/runs")
async def agent_runs(agent_id: str, limit: int = 50):
    """Recent activity feed for an agent: cron-runs JSONL (filtered) + last
    heartbeat from .runtime/heartbeats/{id}.json + heartbeat freshness flag.
    Powers per-agent activity cards on the Agents page."""
    limit = max(1, min(limit, 200))

    # Heartbeat
    heartbeat = None
    heartbeat_age_s = None
    hb_path = HEARTBEATS_DIR / f"{agent_id}.json"
    if hb_path.exists():
        try:
            heartbeat = json.loads(hb_path.read_text())
            from datetime import datetime, timezone
            hb_ts = heartbeat.get("ts")
            if hb_ts:
                hb_dt = datetime.fromisoformat(hb_ts.replace("Z", "+00:00"))
                heartbeat_age_s = (datetime.now(timezone.utc) - hb_dt).total_seconds()
        except (json.JSONDecodeError, ValueError):
            pass

    # Cron runs JSONL — filter by agent_id if records have it (cron_agent.sh
    # doesn't currently tag runs with agent_id since it's a single global loop;
    # future per-agent supervisor will. For now, return up to `limit` recent
    # runs if requested agent matches anything tagged, else empty.)
    runs = []
    if RUNS_JSONL.exists():
        for line in reversed(RUNS_JSONL.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # If the record is tagged with agent_id, filter; else include for masini-style global.
            if "agent_id" in rec and rec["agent_id"] != agent_id:
                continue
            runs.append(rec)
            if len(runs) >= limit:
                break

    return {
        "agent_id": agent_id,
        "heartbeat": heartbeat,
        "heartbeat_age_s": heartbeat_age_s,
        "liveness": "unresponsive" if (heartbeat_age_s is not None and heartbeat_age_s > 120) else ("alive" if heartbeat else "no-heartbeat"),
        "runs": runs,
        "runs_count": len(runs),
    }
