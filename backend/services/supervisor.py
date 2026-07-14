"""Long-running agent supervisor — host-side runtime for Letta agents.

Architecture (revised 2026-05-22, after Docker-network wall):

The supervisor IS the agent's runtime. Letta is the persistent identity layer —
core memory blocks, archival memory, persona. Tools execute on the host (where
they have file access, subprocess access, Ollama access). The Letta sandbox is
never used for tool execution.

Per agent heartbeat (every metadata.heartbeat_interval_s seconds):
  1. Fetch agent's persona + memory blocks + recent archival entries from Letta
  2. Gather host-side context (recent events for this agent, system health)
  3. Build a structured heartbeat prompt
  4. Call Ollama (qwen2.5:32b) for reasoning — agent decides what to do
  5. Parse Ollama's response as structured tool intents (JSON-mode)
  6. Execute each intent host-side
  7. Append a summary of the tick + tool results to Letta's archival memory
  8. Optionally update core memory blocks (current_focus, last_dispatched)
  9. Write heartbeat file for /agents/{id}/runs to observe

If Ollama is down or unresponsive: skip the tick, log "ollama unavailable",
try again next interval. No fallback to Claude for heartbeat reasoning by
default — heartbeats are the cheap thinking layer.

Concurrency: per-agent lock prevents overlapping ticks. Global Ollama
semaphore (1) prevents thrashing the GPU.

Lifecycle: bound to FastAPI lifespan in main.py. start() at app boot,
stop() at app shutdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from services import jobs


log = logging.getLogger("workshop.supervisor")


HEARTBEATS_DIR = Path(os.environ.get(
    "WORKSHOP_HEARTBEATS_DIR",
    "/home/clawd-a/workshop/.runtime/heartbeats",
))
TICKS_LOG = Path(os.environ.get(
    "WORKSHOP_TICKS_LOG",
    "/home/clawd-a/workshop/.runtime/agent-ticks.jsonl",
))
LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")

# Ollama global concurrency (1 = serial — single GPU)
OLLAMA_CONCURRENT_LIMIT = int(os.environ.get("WORKSHOP_OLLAMA_CONCURRENCY", "1"))

# Default heartbeat interval if agent doesn't set its own
DEFAULT_HEARTBEAT_S = 300
# Minimum interval — agents can't tick faster than this no matter what
MIN_HEARTBEAT_S = 60
# Per-tick wall-time cap (heartbeat shouldn't run longer than this)
TICK_WALL_CAP_S = 600

BACKOFF_INITIAL_S = 10
BACKOFF_MAX_S = 600


_ollama_semaphore: asyncio.Semaphore | None = None
_agent_locks: dict[str, asyncio.Lock] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ollama_sem() -> asyncio.Semaphore:
    global _ollama_semaphore
    if _ollama_semaphore is None:
        _ollama_semaphore = asyncio.Semaphore(OLLAMA_CONCURRENT_LIMIT)
    return _ollama_semaphore


def _agent_lock(agent_id: str) -> asyncio.Lock:
    if agent_id not in _agent_locks:
        _agent_locks[agent_id] = asyncio.Lock()
    return _agent_locks[agent_id]


def _write_heartbeat(agent_id: str, status: str, notes: str = "", extras: dict | None = None) -> None:
    HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": agent_id,
        "ts": _now_iso(),
        "status": status,
        "notes": notes,
        **(extras or {}),
    }
    (HEARTBEATS_DIR / f"{agent_id}.json").write_text(json.dumps(payload) + "\n")


def _append_tick_log(record: dict) -> None:
    TICKS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TICKS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


# Map a heartbeat tick outcome to a Job terminal status.
_TICK_JOB_STATUS = {
    "completed": "done",
    "skipped": "cancelled",
    "error": "failed",
    "crashed": "failed",
}


def _finish_tick_job(job_id: str, tick_record: dict | None) -> None:
    """Close the Job opened for a heartbeat cycle. Additive bookkeeping only."""
    tick_status = (tick_record or {}).get("status")
    status = _TICK_JOB_STATUS.get(tick_status, "done" if tick_record else "failed")
    evidence = None
    if tick_record:
        evidence = tick_record.get("thought") or tick_record.get("reason")
    jobs.finish(job_id, status=status, evidence=evidence)


# ============================================================
# Letta REST helpers (host → Letta, always reachable)
# ============================================================


async def _letta_get(client: httpx.AsyncClient, path: str) -> Any:
    r = await client.get(path, headers={"Authorization": f"Bearer {LETTA_PASS}"})
    r.raise_for_status()
    return r.json()


async def _letta_post(client: httpx.AsyncClient, path: str, json_body: dict) -> Any:
    r = await client.post(path, json=json_body, headers={"Authorization": f"Bearer {LETTA_PASS}"})
    r.raise_for_status()
    return r.json()


async def _letta_patch(client: httpx.AsyncClient, path: str, json_body: dict) -> Any:
    r = await client.patch(path, json=json_body, headers={"Authorization": f"Bearer {LETTA_PASS}"})
    r.raise_for_status()
    return r.json()


async def _fetch_agent_context(client: httpx.AsyncClient, agent_id: str) -> dict:
    """Pull what we need for the heartbeat prompt: persona, memory blocks,
    recent archival memory (last few entries)."""
    agent = await _letta_get(client, f"/v1/agents/{agent_id}")
    # Core memory blocks
    blocks_resp = await _letta_get(client, f"/v1/agents/{agent_id}/core-memory/blocks")
    blocks = {b["label"]: b.get("value", "") for b in blocks_resp}
    # Archival memory — fetch last ~5 entries via passages endpoint
    archival: list[str] = []
    try:
        passages = await _letta_get(client, f"/v1/agents/{agent_id}/archival-memory?limit=5")
        if isinstance(passages, list):
            archival = [p.get("text", "") for p in passages[-5:]]
    except Exception:
        pass
    return {
        "id": agent_id,
        "name": agent.get("name", "?"),
        "metadata": agent.get("metadata") or agent.get("metadata_", {}) or {},
        "blocks": blocks,
        "archival_recent": archival,
    }


async def _append_archival(client: httpx.AsyncClient, agent_id: str, text: str) -> None:
    """Letta 0.16.x: archival writes go to /archival-memory (despite the SDK calling
    the resource 'passages')."""
    try:
        await _letta_post(client, f"/v1/agents/{agent_id}/archival-memory", {"text": text})
    except Exception as e:
        log.warning("supervisor: archival append failed for %s: %s", agent_id, e)


async def _update_block(client: httpx.AsyncClient, agent_id: str, label: str, value: str) -> None:
    try:
        await _letta_patch(
            client,
            f"/v1/agents/{agent_id}/core-memory/blocks/{label}",
            {"value": value},
        )
    except Exception as e:
        log.warning("supervisor: block update failed for %s/%s: %s", agent_id, label, e)


# ============================================================
# Ollama heartbeat — the actual cognition
# ============================================================


HEARTBEAT_SCHEMA = """Respond with ONE JSON object only. No prose around it. Schema:
{
  "thought": "<one-sentence summary of what you observed and intend to do>",
  "actions": [
    {"tool": "wkshp_read_todo",    "args": {"section": "open", "max_items": 3}},
    {"tool": "wkshp_propose_task", "args": {"task_text": "...", "urgency": "normal"}},
    {"tool": "wkshp_write_log",    "args": {"entry": "...", "agent_name": "overseer"}},
    {"tool": "wkshp_mark_blocked", "args": {"task_substring": "...", "reason": "..."}},
    {"tool": "cloud_run_claude",   "args": {"task": "...", "model": "haiku", "redact_pii": true}},
    {"tool": "cloud_run_ralph",    "args": {"task_substring": "...", "max_iter": 6}},
    {"tool": "notify_operator",    "args": {"text": "...", "channel_hint": "default"}},
    {"tool": "noop",               "args": {}}
  ],
  "memory_updates": {
    "current_focus": "<new value or null to leave unchanged>",
    "last_dispatched": "<new value or null to leave unchanged>",
    "watchlist": "<new value or null to leave unchanged>"
  }
}

Rules:
- "actions" can be empty if there's nothing to do this tick — that's fine, return [].
- Each action runs on the host. Heavy ones (cloud_*) block until done.
- Use cloud_run_claude for any code/reasoning work that's beyond your weight class.
- Default model is "haiku" — only escalate to sonnet/opus for genuinely complex work.
- redact_pii defaults to true — set false ONLY when literal PII is needed by Claude.
- Don't dispatch the same task you dispatched in the last 3 ticks (check last_dispatched).
- Memory updates are PARTIAL: set blocks you want changed, null/omit the rest.
- Keep your "thought" to ONE sentence. The actions are what matters."""


def _build_heartbeat_prompt(ctx: dict, host_state: dict) -> str:
    persona = ctx["blocks"].get("persona", "")
    human = ctx["blocks"].get("human", "")
    current_focus = ctx["blocks"].get("current_focus", "(empty)")
    last_dispatched = ctx["blocks"].get("last_dispatched", "(empty)")
    watchlist = ctx["blocks"].get("watchlist", "(empty)")
    archival = "\n  ".join(f"- {a[:200]}" for a in ctx.get("archival_recent", [])[-3:]) or "(none recent)"

    iso = _now_iso()
    return f"""You are {ctx['name']}, an always-on Letta agent. This is a heartbeat tick.

# Your persona
{persona[:2000]}

# Operator profile
{human[:1000]}

# Your current memory blocks
- current_focus: {current_focus}
- last_dispatched: {last_dispatched}
- watchlist: {watchlist}

# Recent archival entries (last 3)
  {archival}

# Host-side context for this tick
- ts: {iso}
- ollama_health: {host_state.get('ollama_health', 'unknown')}
- todo_open_count: {host_state.get('todo_open_count', '?')}
- todo_blocked_count: {host_state.get('todo_blocked_count', '?')}
- recent_notify_lines: {host_state.get('recent_notify_count', 0)}

# Your task this tick

Decide what (if anything) to do. Output JSON only.

{HEARTBEAT_SCHEMA}"""


async def _ollama_reason(prompt: str, timeout_s: float = 120) -> tuple[str, float]:
    """Async Ollama call. Returns (response_text, elapsed_s)."""
    payload = {
        "model": "qwen2.5:32b",
        "prompt": prompt,
        "stream": False,
        "format": "json",  # request JSON-mode (qwen supports it)
        "options": {"num_ctx": 8192, "temperature": 0.2},
    }
    start = time.time()
    async with httpx.AsyncClient(timeout=timeout_s) as cl:
        r = await cl.post("http://127.0.0.1:11434/api/generate", json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("response", ""), time.time() - start


def _parse_intents(raw: str) -> dict:
    """Find a JSON object in `raw`. Returns the parsed dict, or a safe default."""
    import re
    raw = raw.strip()
    if not raw.startswith("{"):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"thought": "(no json)", "actions": [], "memory_updates": {}}
        raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("supervisor: bad JSON from Ollama: %s", e)
        return {"thought": "(json parse error)", "actions": [], "memory_updates": {}}
    # Normalize
    return {
        "thought": str(data.get("thought", ""))[:500],
        "actions": list(data.get("actions") or []),
        "memory_updates": dict(data.get("memory_updates") or {}),
    }


# ============================================================
# Tool execution — host-side, direct
# ============================================================


def _exec_tool(name: str, args: dict) -> str:
    """Dispatch one tool intent to the host-side implementation."""
    from services.letta_tools import workshop as t_workshop
    from services.letta_tools import local as t_local
    from services.letta_tools import cloud as t_cloud
    from services.letta_tools import notify as t_notify
    from services.letta_tools import health as t_health

    table = {
        "wkshp_read_todo":        t_workshop.wkshp_read_todo,
        "wkshp_propose_task":     t_workshop.wkshp_propose_task,
        "wkshp_write_log":        t_workshop.wkshp_write_log,
        "wkshp_mark_blocked":     t_workshop.wkshp_mark_blocked,
        "local_classify":         t_local.local_classify,
        "local_route_decision":   t_local.local_route_decision,
        "local_summarize_short":  t_local.local_summarize_short,
        "cloud_run_claude":       t_cloud.cloud_run_claude,
        "cloud_run_ralph":        t_cloud.cloud_run_ralph,
        "notify_operator":        t_notify.notify_operator,
        "system_health":          t_health.system_health,
        "noop":                   lambda **_: "noop",
    }
    func = table.get(name)
    if func is None:
        return f"ERROR: unknown tool {name}"
    try:
        result = func(**args)
        return str(result) if not isinstance(result, str) else result
    except TypeError as e:
        return f"ERROR: bad args: {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ============================================================
# Host state — fast snapshot the heartbeat prompt uses
# ============================================================


def _host_state_snapshot() -> dict:
    """Cheap snapshot — no LLM calls. Just file/socket reads."""
    todo = Path("/home/clawd-a/workshop/TODO.md")
    notify = Path("/home/clawd-a/workshop/.runtime/notify-masini.txt")
    open_count = blocked_count = 0
    if todo.exists():
        lines = todo.read_text().splitlines()
        open_count = sum(1 for l in lines if l.startswith("[ ] "))
        blocked_count = sum(1 for l in lines if l.startswith("[~] "))
    recent_notify = 0
    if notify.exists():
        # Count lines added in last 24h (cheap proxy: file mtime)
        try:
            recent_notify = len(notify.read_text().splitlines()[-20:])
        except OSError:
            pass

    # Ollama health quick-probe
    ollama_health = "down"
    try:
        with httpx.Client(timeout=2) as cl:
            r = cl.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                ollama_health = "up"
    except Exception:
        pass

    return {
        "todo_open_count": open_count,
        "todo_blocked_count": blocked_count,
        "recent_notify_count": recent_notify,
        "ollama_health": ollama_health,
    }


# ============================================================
# Per-agent tick
# ============================================================


async def _do_tick(client: httpx.AsyncClient, agent: dict) -> dict:
    """One heartbeat for one agent. Returns a tick record."""
    aid = agent["id"]
    name = agent.get("name", "?")
    tick_start = time.time()
    tick_record: dict[str, Any] = {
        "ts": _now_iso(),
        "agent_id": aid,
        "agent_name": name,
        "status": "started",
    }

    # Skip if Ollama is hard-down
    snap = _host_state_snapshot()
    if snap["ollama_health"] == "down":
        tick_record["status"] = "skipped"
        tick_record["reason"] = "ollama_down"
        _write_heartbeat(aid, "skipped", "ollama down")
        return tick_record

    # Fetch agent context from Letta (host→Letta, reachable)
    try:
        ctx = await _fetch_agent_context(client, aid)
    except Exception as e:
        tick_record["status"] = "error"
        tick_record["reason"] = f"letta_fetch_failed: {e}"
        _write_heartbeat(aid, "error", f"letta fetch: {e}")
        return tick_record

    # Build prompt + call Ollama for reasoning
    prompt = _build_heartbeat_prompt(ctx, snap)
    async with _ollama_sem():
        try:
            raw_response, ollama_elapsed = await _ollama_reason(prompt, timeout_s=180)
        except Exception as e:
            tick_record["status"] = "error"
            tick_record["reason"] = f"ollama_failed: {e}"
            _write_heartbeat(aid, "error", f"ollama: {e}")
            return tick_record

    parsed = _parse_intents(raw_response)
    tick_record["thought"] = parsed["thought"]
    tick_record["ollama_elapsed_s"] = round(ollama_elapsed, 2)
    tick_record["intent_count"] = len(parsed["actions"])

    # Execute tool intents — synchronously but caps the total wall-time
    results: list[dict] = []
    for action in parsed["actions"][:10]:  # cap at 10 intents per tick
        if time.time() - tick_start > TICK_WALL_CAP_S:
            results.append({"tool": "(skipped — wall cap)", "args": {}, "result": "skipped"})
            break
        tool_name = action.get("tool", "")
        args = action.get("args") or {}
        # Run tool in a thread to avoid blocking the event loop with subprocess calls
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, lambda: _exec_tool(tool_name, args))
        except Exception as e:
            result = f"ERROR: executor: {e}"
        results.append({"tool": tool_name, "args": args, "result": result[:500]})

    tick_record["results"] = results

    # Apply memory updates
    for label, new_value in (parsed["memory_updates"] or {}).items():
        if new_value and isinstance(new_value, str) and label in ctx["blocks"]:
            await _update_block(client, aid, label, new_value[:2000])

    # Append a tick summary to archival memory (so agent can recall this tick later)
    summary = f"[{_now_iso()}] tick: {parsed['thought']} | actions={len(results)} | sample={results[0]['tool'] if results else 'none'}"
    await _append_archival(client, aid, summary)

    elapsed_total = time.time() - tick_start
    tick_record["status"] = "completed"
    tick_record["elapsed_s"] = round(elapsed_total, 2)
    _write_heartbeat(aid, "completed", parsed["thought"][:200],
                     {"intent_count": len(results), "elapsed_s": tick_record["elapsed_s"]})
    return tick_record


async def _supervise_one(agent: dict) -> None:
    """Long-running loop for one agent: tick, sleep, tick, sleep, ..."""
    aid = agent["id"]
    name = agent.get("name", "?")
    meta = agent.get("metadata") or agent.get("metadata_", {}) or {}
    interval = max(MIN_HEARTBEAT_S, int(meta.get("heartbeat_interval_s", DEFAULT_HEARTBEAT_S)))
    log.info("supervisor: spawning loop for %s (%s), interval=%ds", name, aid, interval)
    backoff = BACKOFF_INITIAL_S
    async with httpx.AsyncClient(base_url=LETTA_URL, timeout=60.0) as client:
        while True:
            try:
                # Additive: each heartbeat work cycle is a bounded execution, so
                # record it as a Job. This does NOT touch any Letta call inside
                # _do_tick; the heartbeat file it writes is still the liveness
                # source. The Job is the platform-uniform activity record.
                job = jobs.start(agent_id=aid, trigger="loop", status="running")
                try:
                    async with _agent_lock(aid):
                        tick_record = await _do_tick(client, agent)
                finally:
                    _finish_tick_job(job["id"], locals().get("tick_record"))
                _append_tick_log(tick_record)
                backoff = BACKOFF_INITIAL_S
            except asyncio.CancelledError:
                _write_heartbeat(aid, "stopped", "supervisor shutdown")
                raise
            except Exception as e:
                log.warning("supervisor: %s (%s) tick crashed: %s — backoff %ds", name, aid, e, backoff)
                _write_heartbeat(aid, "crashed", f"{type(e).__name__}: {e}")
                _append_tick_log({"ts": _now_iso(), "agent_id": aid, "agent_name": name,
                                  "status": "crashed", "reason": str(e)})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
                continue
            await asyncio.sleep(interval)


# ============================================================
# AgentSupervisor — FastAPI lifespan-bound
# ============================================================

# Durable pause flag. The legacy Letta/Ollama heartbeat is being retired (see
# docs/platform-redesign/coherent-rebuild-2026-07-14.md, Stage 0/9). While it is
# paused we must survive a backend restart without silently resuming and
# re-flooding the Jobs feed, so the paused state is persisted here and re-read on
# start(). Delete the file (or call resume()) to bring the heartbeat back.
_PAUSE_FLAG = Path("/home/clawd-a/workshop/.runtime/supervisor-paused")


class AgentSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._scan_task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self.started_at: str | None = None
        self.paused: bool = False
        self.paused_at: str | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(base_url=LETTA_URL, timeout=30.0)
        self.started_at = _now_iso()
        if _PAUSE_FLAG.exists():
            # Honor a durable pause across restarts: keep the client for a cheap
            # resume() but do not start the scan loop, so no heartbeats fire.
            self.paused = True
            self.paused_at = _now_iso()
            log.info("supervisor: start suppressed (durable pause flag present)")
            return
        self.paused = False
        self.paused_at = None
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

    async def pause(self) -> None:
        """Stop scan loop + all heartbeat tasks but keep the HTTP client alive
        so resume() is cheap. Idempotent — calling on an already-paused
        supervisor is a no-op."""
        if self.paused:
            return
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
        for t in self._tasks.values():
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._scan_task = None
        self.paused = True
        self.paused_at = _now_iso()
        try:
            _PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            _PAUSE_FLAG.write_text(self.paused_at)
        except OSError as e:
            log.warning("supervisor: could not persist pause flag: %s", e)
        log.info("supervisor: paused")

    async def resume(self) -> None:
        """Restart scan loop after pause(). Recreates the HTTP client if
        stop() previously closed it. Idempotent."""
        if not self.paused and self._scan_task and not self._scan_task.done():
            return
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=LETTA_URL, timeout=30.0)
        self.started_at = _now_iso()
        self.paused = False
        self.paused_at = None
        try:
            _PAUSE_FLAG.unlink(missing_ok=True)
        except OSError as e:
            log.warning("supervisor: could not clear pause flag: %s", e)
        if self._scan_task is None or self._scan_task.done():
            self._scan_task = asyncio.create_task(self._scan_loop(), name="supervisor-scan")
        log.info("supervisor: resumed")

    async def _scan_loop(self) -> None:
        while True:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("supervisor: scan failed: %s", e)
            await asyncio.sleep(60)

    async def _reconcile(self) -> None:
        assert self._client
        agents = await _letta_get(self._client, "/v1/agents/")
        wanted_ids = {
            a["id"]
            for a in agents
            if (a.get("metadata") or a.get("metadata_") or {}).get("autonomy") == "continuous"
        }
        for aid in list(self._tasks.keys()):
            if aid not in wanted_ids:
                self._tasks[aid].cancel()
                del self._tasks[aid]
        for agent in agents:
            meta = agent.get("metadata") or agent.get("metadata_", {}) or {}
            if meta.get("autonomy") != "continuous":
                continue
            if agent["id"] in self._tasks:
                continue
            t = asyncio.create_task(_supervise_one(agent),
                                    name=f"supervisor-{agent.get('name','?')}")
            self._tasks[agent["id"]] = t

    def status(self) -> dict:
        return {
            "started_at": self.started_at,
            "paused": self.paused,
            "paused_at": self.paused_at,
            "supervised_agents": [
                {"agent_id": aid, "task_name": t.get_name(), "done": t.done()}
                for aid, t in self._tasks.items()
            ],
            "ollama_concurrent_limit": OLLAMA_CONCURRENT_LIMIT,
            "default_heartbeat_s": DEFAULT_HEARTBEAT_S,
        }
