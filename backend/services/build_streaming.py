"""SSE streaming for Build-mode chat turns.

Spawns `claude -p --output-format stream-json --verbose` as an async subprocess
and forwards each event line to subscribers. State for in-flight turns lives
in-process; the final assistant text is appended to chat_store on completion.

Pattern adopted from clawdius gateway/clawdius_gateway/app.py:714 + handoff.js:
the SSE response carries `id: <byte-offset>` per event so an EventSource
auto-reconnect can resume exactly via Last-Event-ID with no replay/drop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

from services import build_agents, chat_store, llm_router

log = logging.getLogger("workshop.build_streaming")

CLAUDE_CLI = llm_router.CLAUDE_CLI


@dataclass
class TurnEvent:
    """One line emitted by the underlying claude subprocess, after parsing.
    `kind` lets the frontend distinguish thinking / tool-use / text / done."""
    kind: str
    payload: dict
    ts: float = field(default_factory=time.time)


@dataclass
class Turn:
    id: str
    thread_id: str
    agent_slug: str
    events: list[TurnEvent] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    final_text: str = ""
    started: float = field(default_factory=time.time)
    # Subscribers get notified on each new event so SSE generators can wake
    # without polling. Use a single asyncio.Event reset on each push.
    new_event: asyncio.Event = field(default_factory=asyncio.Event)


# In-memory turn registry. Build sessions are short-lived enough that this is
# acceptable; a future upgrade can spill to disk for cross-process resume.
TURNS: dict[str, Turn] = {}


def _make_turn(thread_id: str, agent_slug: str) -> Turn:
    t = Turn(id=str(uuid.uuid4()), thread_id=thread_id, agent_slug=agent_slug)
    TURNS[t.id] = t
    return t


def _push(turn: Turn, kind: str, payload: dict) -> None:
    turn.events.append(TurnEvent(kind=kind, payload=payload))
    turn.new_event.set()


def _parse_stream_event(line: str) -> tuple[str, dict] | None:
    """Map a single stream-json line into (kind, payload) for the SSE channel.

    The Claude Code stream-json schema emits {"type": ...} envelopes. We
    collapse them into a smaller set the UI cares about:
      - "thinking"    — model is reasoning (no visible chunks but progress)
      - "tool"        — a tool was called (name + brief input summary)
      - "text"        — assistant text chunk to render in the bubble
      - "result"      — final terminal event with stop_reason + total
    Everything else we forward as "meta" so the UI can ignore-or-log.
    """
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    t = ev.get("type")
    if t == "assistant":
        # An assistant message containing content blocks. Forward text blocks
        # as "text" events; tool_use blocks as "tool".
        msg = ev.get("message", {})
        out: list[tuple[str, dict]] = []
        for block in msg.get("content", []) or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    out.append(("text", {"content": block.get("text", "")}))
                elif block.get("type") == "tool_use":
                    out.append(("tool", {
                        "name": block.get("name"),
                        "input": _summarize_tool_input(block.get("input", {})),
                    }))
        # Multi-block: return a synthetic "compound" wrapper; the caller pushes
        # each individually. (We just return the FIRST here; the loop in
        # _run_turn handles multi.)
        if out:
            # Return a sentinel meaning "multi"; the caller iterates outputs.
            return ("_multi", {"events": out})
        return ("meta", ev)
    if t == "user":
        # Tool result reply (we don't need to surface tool results to the UI
        # in detail; just count them).
        return ("tool_result", {"tool_use_id": ev.get("message", {}).get("content", [{}])[0].get("tool_use_id")})
    if t == "result":
        return ("result", {
            "stop_reason": ev.get("stop_reason") or ev.get("terminal_reason"),
            "duration_ms": ev.get("duration_ms"),
            "total_cost_usd": ev.get("total_cost_usd"),
        })
    if t == "system":
        return ("meta", {"subtype": ev.get("subtype")})
    return ("meta", {"type": t})


def _summarize_tool_input(inp: dict) -> str:
    """One-line preview for the UI. Long tool inputs (full file contents in
    a Write) shouldn't bloat the SSE stream — show file path + size only."""
    if not isinstance(inp, dict):
        return str(inp)[:80]
    for key in ("file_path", "path", "pattern", "command", "url"):
        if key in inp:
            v = str(inp[key])
            return v if len(v) <= 120 else v[:117] + "…"
    # Generic fallback: short keys list.
    keys = ",".join(list(inp.keys())[:4])
    return f"({keys})"


async def start_turn(*, thread_id: str, agent_slug: str, prompt: str, model: str,
                     cwd: str, session_id: str | None, resume: bool,
                     max_turns: int = 60, timeout_s: float = 900) -> Turn:
    """Kick off a streaming claude subprocess for this turn. Returns the Turn
    record immediately; the subprocess runs in a background task that pushes
    events to it."""
    turn = _make_turn(thread_id, agent_slug)
    asyncio.create_task(
        _run_turn(turn, prompt=prompt, model=model, cwd=cwd,
                  session_id=session_id, resume=resume,
                  max_turns=max_turns, timeout_s=timeout_s)
    )
    return turn


async def _run_turn(turn: Turn, *, prompt: str, model: str, cwd: str,
                    session_id: str | None, resume: bool,
                    max_turns: int, timeout_s: float) -> None:
    cmd: list[str] = [
        CLAUDE_CLI, "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
    ]
    if session_id and resume:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])

    env = llm_router._claude_env()
    _push(turn, "started", {"cwd": cwd, "model": model, "resume": resume})

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        turn.error = f"claude CLI not found: {e}"
        turn.complete = True
        _push(turn, "error", {"detail": turn.error})
        return

    async def _read_stdout() -> None:
        assert proc.stdout
        accumulated_text: list[str] = []
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            parsed = _parse_stream_event(line)
            if not parsed:
                continue
            kind, payload = parsed
            if kind == "_multi":
                for k2, p2 in payload.get("events", []):
                    if k2 == "text":
                        accumulated_text.append(p2.get("content", ""))
                    _push(turn, k2, p2)
            else:
                if kind == "text":
                    accumulated_text.append(payload.get("content", ""))
                _push(turn, kind, payload)
        turn.final_text = "".join(accumulated_text).strip()

    async def _read_stderr() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                _push(turn, "stderr", {"line": line[:500]})

    try:
        await asyncio.wait_for(
            asyncio.gather(_read_stdout(), _read_stderr(), proc.wait()),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        turn.error = f"timeout after {timeout_s}s"
        _push(turn, "error", {"detail": turn.error})
    except Exception as e:  # pragma: no cover — defensive
        turn.error = str(e)
        _push(turn, "error", {"detail": turn.error})

    rc = proc.returncode if proc.returncode is not None else -1
    _push(turn, "done", {"exit_code": rc, "text_len": len(turn.final_text)})
    turn.complete = True

    # Persist the final assistant message + touch the agent record. Even on
    # non-zero exit, salvage any text that was emitted (matches call_cloud's
    # recovery behavior).
    text = turn.final_text or ("(model exited without text)" if rc != 0 else "")
    try:
        chat_store.append_message(turn.thread_id, "assistant", text)
    except Exception as e:
        log.warning("turn %s: chat_store append failed: %s", turn.id, e)
    try:
        build_agents.touch(turn.agent_slug)
    except Exception:
        pass

    # Auto-name the thread after the first streamed exchange completes (mirrors
    # the sync /messages path). Reuses the same cheap-Haiku namer in routes.chat
    # so naming behavior can't drift between the two paths. Lazy import avoids an
    # import cycle (routes.chat imports build_streaming at module load). Runs off
    # the event loop; failures are swallowed (best-effort, never blocks the turn).
    try:
        first_user = _first_user_text(turn.thread_id)
        if first_user and text and not text.startswith("(model exited"):
            from routes import chat as _chat
            await _maybe_autoname_stream(_chat, turn.thread_id, first_user, text)
    except Exception as e:
        log.info("turn %s: auto-name skipped: %s", turn.id, e)


def _first_user_text(thread_id: str) -> str:
    t = chat_store.get_thread(thread_id)
    if not t:
        return ""
    for m in t.get("messages", []):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


async def _maybe_autoname_stream(chat_mod, thread_id: str, user_msg: str, assistant_msg: str) -> None:
    """Run the route's naming guard+generator for a just-completed streamed turn.
    chat_mod._maybe_autoname already checks 'first assistant reply + blank title',
    so this is safe to call unconditionally here."""
    await chat_mod._maybe_autoname(thread_id, user_msg, assistant_msg)


def get_turn(turn_id: str) -> Turn | None:
    return TURNS.get(turn_id)


async def iter_events(turn: Turn, after: int = 0):
    """Async generator yielding (offset, event) pairs starting from `after`.
    Suspends until new events arrive; ends when turn.complete and no more
    events remain. The caller (the SSE endpoint) maps each to an SSE frame
    with `id: <offset>` for byte-cursor resume."""
    while True:
        # Emit any events the subscriber hasn't seen yet.
        while after < len(turn.events):
            yield after + 1, turn.events[after]
            after += 1
        if turn.complete:
            return
        # Wait for the next push or a 1s heartbeat (so disconnected clients
        # get noticed eventually).
        turn.new_event.clear()
        try:
            await asyncio.wait_for(turn.new_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            # Heartbeat tick; loop and check turn.complete + emit a keepalive.
            yield after, TurnEvent(kind="ping", payload={})
