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
import glob
import json
import logging
import os
import shutil
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from services import build_agents, chat_store, llm_router, ownership

log = logging.getLogger("workshop.build_streaming")

CLAUDE_CLI = llm_router.CLAUDE_CLI

# Max bytes for a single stdout line (one stream-json event) before asyncio's
# StreamReader raises LimitOverrunError. Default is 64 KiB — far too small for
# events carrying file contents / large tool results. 64 MiB is generous and
# still memory-bounded.
STREAM_LINE_LIMIT = 64 * 1024 * 1024


def _stdin_fifo(turn_id: str) -> Path:
    """The turn's realtime input channel.

    A turn runs `--input-format stream-json`, so its stdin stays open for the
    life of the run and the CLI reads whatever arrives at the next boundary
    BETWEEN steps. That is what lets a message sent while the agent is working
    reach it now instead of waiting out the whole turn.

    It is a FIFO on disk, not a pipe, for the same reason stdout goes to a file:
    the child is spawned detached and has to survive a backend restart. A pipe
    dies with the parent; a FIFO can be reopened by whoever is running later."""
    return _journal_dir(turn_id) / "stdin.fifo"


def _user_message_line(text: str) -> str:
    """One stream-json user message, as the CLI expects it on stdin."""
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n"


def write_to_turn(turn_id: str, text: str) -> bool:
    """Hand a message to a RUNNING turn. True if it was written.

    Non-blocking on purpose: opening a FIFO for writing blocks until a reader is
    present, and if the turn has already ended there is no reader and this would
    hang the request forever. O_NONBLOCK turns that case into ENXIO, which is
    the honest answer — "that turn is gone, queue it as the next one" — and the
    caller falls back to the queue."""
    fifo = _stdin_fifo(turn_id)
    if not fifo.exists():
        return False
    fd = None
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, _user_message_line(text).encode())
        return True
    except OSError as e:
        # ENXIO = FIFO open for write with no reader: the turn's process is gone.
        log.debug("turn %s: injection failed (%s)", turn_id, e)
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def session_alive(session_id: str | None, cwd: str) -> bool:
    """True if the claude CLI still has this session on disk. Claude stores a
    session as ~/.claude/projects/<cwd-with-slashes-as-dashes>/<id>.jsonl. Old
    agents (or anything from before a session was pruned) point at an id that no
    longer exists, and `--resume` on it fails the whole turn with "No
    conversation found with session ID". Checking the file lets callers start a
    fresh session instead of failing."""
    if not session_id:
        return False
    home = os.path.expanduser("~")
    encoded = cwd.rstrip("/").replace("/", "-")
    primary = os.path.join(home, ".claude", "projects", encoded, f"{session_id}.jsonl")
    if os.path.exists(primary):
        return True
    # Encoding could shift across CLI versions; the id is a unique uuid, so a
    # scoped glob across all projects is a safe fallback.
    return bool(glob.glob(os.path.join(home, ".claude", "projects", "*", f"{session_id}.jsonl")))


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
    # Ordered text/tool segments, persisted incrementally so a reload mid-turn
    # reconstructs the interleaving (text → tool → text …).
    segments: list[dict] = field(default_factory=list)
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
            # Carry this message's usage so the driver can track the real context
            # size (input + cached input tokens) of the latest model call — the
            # honest "how full is the window" signal that also drops after an
            # in-session /compact, unlike counting the stored transcript.
            return ("_multi", {"events": out, "usage": msg.get("usage")})
        return ("meta", ev)
    if t == "user":
        # Tool result reply (we don't need to surface tool results to the UI
        # in detail; just count them). Content is usually a list of blocks, but
        # some events (e.g. /compact's synthetic user turns) carry a plain string
        # — guard so a non-dict block can't crash the stream reader.
        content = ev.get("message", {}).get("content")
        tool_use_id = None
        if isinstance(content, list) and content and isinstance(content[0], dict):
            tool_use_id = content[0].get("tool_use_id")
        return ("tool_result", {"tool_use_id": tool_use_id})
    if t == "result":
        return ("result", {
            "stop_reason": ev.get("stop_reason") or ev.get("terminal_reason"),
            # How the CLI ended the turn: "success", "error_during_execution",
            # … Kept as telemetry for diagnosing a turn that ended badly. Note
            # that "error_max_turns" can no longer occur: we pass no step cap.
            "subtype": ev.get("subtype"),
            "is_error": bool(ev.get("is_error")),
            "duration_ms": ev.get("duration_ms"),
            "total_cost_usd": ev.get("total_cost_usd"),
            "usage": ev.get("usage"),
        })
    if t == "system":
        return ("meta", {"subtype": ev.get("subtype")})
    return ("meta", {"type": t})


def _ctx_tokens_from_usage(u: object) -> int:
    """Real context size from a Claude usage block: the input the model actually
    read = fresh input + cache-read + cache-creation tokens. This is what fills the
    window; it drops after an in-session /compact, which counting the stored
    transcript never does. Output tokens are not context, so they're excluded."""
    if not isinstance(u, dict):
        return 0
    return (
        int(u.get("input_tokens") or 0)
        + int(u.get("cache_read_input_tokens") or 0)
        + int(u.get("cache_creation_input_tokens") or 0)
    )


def _usage_scope_for_turn(turn: "Turn") -> str:
    """The ledger scope a chat turn bills to: a bare project slug, matching the
    values autonomous sweeps already write ("workshop", "demo-x"). The knowledge
    scope is the right source (it is what the chat's own learnings key on) but it
    uses a "project:<slug>" form, so strip the prefix. Falls back to "workshop"
    rather than raising — a scope lookup must never cost the operator a turn."""
    try:
        from services import chat_store, knowledge
        t = chat_store.get_thread(turn.thread_id) or {}
        scope = knowledge.scope_for_context(t.get("context_key") or "workshop")
        if scope.startswith("project:"):
            return scope.split(":", 1)[1]
        return scope or "workshop"
    except Exception:
        return "workshop"


def _spend_tokens_from_usage(u: object) -> int:
    """Billable tokens from a usage block: everything the turn actually cost,
    including output. Distinct from _ctx_tokens_from_usage, which answers "how
    full is the window" and therefore excludes output on purpose. Spend and
    occupancy are different questions and must not share a helper."""
    if not isinstance(u, dict):
        return 0
    return (
        int(u.get("input_tokens") or 0)
        + int(u.get("cache_read_input_tokens") or 0)
        + int(u.get("cache_creation_input_tokens") or 0)
        + int(u.get("output_tokens") or 0)
    )


def _summarize_tool_input(inp: dict) -> str:
    """One-line preview for the UI. Long tool inputs (full file contents in
    a Write) shouldn't bloat the SSE stream — show file path + size only."""
    # Cap at 300 chars: enough that expanding a tool chip shows a useful detail
    # (a full command / path), without streaming whole file bodies into history.
    if not isinstance(inp, dict):
        return str(inp)[:300]
    for key in ("file_path", "path", "pattern", "command", "url", "query", "prompt"):
        if key in inp:
            v = str(inp[key])
            return v if len(v) <= 300 else v[:297] + "…"
    # Generic fallback: short keys list.
    keys = ",".join(list(inp.keys())[:4])
    return f"({keys})"


# ==========================================================================
# Crash/restart resilience — detached, journal-backed turns.
#
# A build turn's claude subprocess is DETACHED (start_new_session) and streams
# its stdout STRAIGHT TO A FILE, not an in-process pipe. So a backend restart
# (which kills uvicorn but not the detached child) never interrupts the build:
# the child keeps running and keeps writing its journal.
#
# THIS DEPENDS ON THE SERVICE UNIT. start_new_session gives the child its own
# session + process group, but NOT its own cgroup — it stays in the backend's.
# Under systemd's default KillMode=control-group a restart therefore SIGTERMs
# the child too and the guarantee above silently does not hold: the in-flight
# reply collapses to whatever text had been flushed and finalizes via the
# `lost` path. infra/systemd/workshop-backend.service sets KillMode=process to
# make it hold; keep that line if you touch the unit. We read events by
# TAILING that file — identical whether we just spawned the process or are
# re-attaching to one that outlived us. On startup, reattach_running_turns()
# resumes any journal still marked "running": tails a live process to completion,
# or finalizes a dead one from whatever it wrote (so the chat is never stuck).
# ==========================================================================

TURNS_DIR = Path(__file__).resolve().parents[2] / ".runtime" / "turns"


def _journal_dir(turn_id: str) -> Path:
    return TURNS_DIR / turn_id


def _write_meta(meta: dict) -> None:
    d = _journal_dir(meta["turn_id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta))
    tmp.replace(d / "meta.json")


def _read_meta(d: Path) -> dict | None:
    try:
        return json.loads((d / "meta.json").read_text())
    except Exception:
        return None


def _mark_meta(meta: dict, status: str) -> None:
    meta["status"] = status
    try:
        _write_meta(meta)
    except Exception:
        pass


def _proc_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _retire_turn_proc(pid: int | None, proc=None) -> None:
    """End a turn's process once its `result` line has landed.

    Under `--input-format stream-json` the CLI does not exit when it finishes a
    turn: stdin is still open, so it idles waiting for the next message. The
    result line is the end of THIS turn, so somebody has to retire it or every
    turn leaks a claude process holding its session.

    SIGTERM the whole group first and give it a moment. The group matters
    because the CLI owns children (MCP servers, tool subprocesses) and this now
    runs on every single turn rather than only on a timeout, so orphaning them
    would accumulate fast. SIGKILL is the fallback if it does not go."""
    try:
        pgid = os.getpgid(pid) if pid else None
    except OSError:
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        elif proc is not None and proc.returncode is None:
            proc.terminate()
    except Exception:
        pass
    for _ in range(20):  # up to ~2s for a clean shutdown
        await asyncio.sleep(0.1)
        if proc is not None and proc.returncode is not None:
            return
        if pid and not _proc_alive(pid):
            return
    _kill_turn_proc(pid, proc)


def _cleanup_turn_input(turn_id: str) -> None:
    """Drop the turn's input channel. Its absence is also the signal that makes
    a later injection fail fast and fall back to the queue instead of hanging."""
    try:
        _stdin_fifo(turn_id).unlink(missing_ok=True)
    except OSError as e:
        log.debug("turn %s: input channel cleanup failed: %s", turn_id, e)


def _kill_turn_proc(pid: int | None, proc=None) -> None:
    try:
        if proc is not None and proc.returncode is None:
            proc.kill()
            return
        if pid:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


async def start_turn(*, thread_id: str, agent_slug: str, prompt: str, model: str,
                     cwd: str, session_id: str | None, resume: bool,
                     timeout_s: float = 0,
                     allowed_tools: list[str] | None = None,
                     mcp_servers: list[str] | None = None,
                     tools_disabled: bool = False,
                     speaker: str | None = None) -> Turn:
    """Kick off a streaming turn. The claude subprocess is detached and streams
    to a disk journal so it survives a backend restart; the returned Turn is
    driven by tailing that journal. Returns immediately.

    Tool surface (shared with llm_router via tool_flags so the modes never drift):
      - default (allowed_tools=None, tools_disabled=False): full agentic toolset,
        the Build/Project path (cwd pinned, bypassPermissions).
      - allowed_tools set: narrow surface (web + the named MCP tools only), the
        data/assistant path. No cwd write-build without a real project.
      - tools_disabled: pure text (ENABLE_CHAT_TOOLS off).
    A None session_id spawns stateless (no --session-id/--resume), so a general
    or project chat streams without a persistent Claude Code session.

    `speaker` names the AGENT answering, when the turn was addressed to one by
    @mention. It rides through to the stored message as `meta.agent` so the
    transcript can attribute the reply. Absent (the normal case) the reply is
    the assistant's and needs no attribution."""
    turn = _make_turn(thread_id, agent_slug)
    try:
        owner_id = ownership.current_owner()
    except Exception:
        owner_id = None
    meta = {
        "turn_id": turn.id, "thread_id": thread_id, "agent_slug": agent_slug,
        "cwd": cwd, "model": model, "session_id": session_id, "resume": bool(resume),
        "speaker": speaker,
        "owner_id": owner_id, "prompt": prompt,
        "timeout_s": timeout_s, "pid": None, "status": "running", "started": turn.started,
        "allowed_tools": allowed_tools, "mcp_servers": mcp_servers,
        "tools_disabled": bool(tools_disabled),
    }
    _write_meta(meta)
    asyncio.create_task(_spawn_and_drive(turn, meta))
    return turn


async def _spawn_and_drive(turn: Turn, meta: dict) -> None:
    # The prompt goes in over stdin as a stream-json message rather than as an
    # argv positional, because stdin then STAYS open for the rest of the run and
    # anything else the operator sends lands at the next step boundary. (It also
    # takes the assembled prompt off the argv length limit.)
    cmd: list[str] = [
        CLAUDE_CLI, "-p",
        "--model", meta["model"],
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ]
    # Tool surface. Data/assistant turns (allowed_tools set) and tools-off turns
    # get their narrow flags from the SAME helper llm_router.call_cloud uses, so
    # a streamed data turn and a blocking one expose identical capabilities. Full
    # agentic turns (Build/Project) fall through to bypassPermissions: headless,
    # no interactive approver, so without it the model stalls asking to write.
    # The auto-checkpoint + git history are the safety net (every turn revertable).
    tool_env: dict = {}
    if meta.get("allowed_tools") is not None or meta.get("tools_disabled"):
        _tflags, tool_env, _block = llm_router.tool_flags(
            tools_disabled=bool(meta.get("tools_disabled")),
            allowed_tools=meta.get("allowed_tools"),
            mcp_servers=meta.get("mcp_servers"),
        )
        cmd.extend(_tflags)
    else:
        cmd.extend(["--permission-mode", "bypassPermissions"])
    if meta["session_id"] and meta["resume"]:
        cmd.extend(["--resume", meta["session_id"]])
    elif meta["session_id"]:
        cmd.extend(["--session-id", meta["session_id"]])

    _push(turn, "started", {"cwd": meta["cwd"], "model": meta["model"], "resume": meta["resume"]})
    # Persist an empty assistant placeholder bound to this turn. As the turn
    # streams we update its segments; a reload sees partial content + reconnects.
    try:
        chat_store.append_streaming_assistant(
            turn.thread_id, turn.id, model=meta["model"], agent=meta.get("speaker"))
    except Exception as e:  # pragma: no cover — defensive
        log.warning("turn %s: could not seed streaming message: %s", turn.id, e)

    jd = _journal_dir(turn.id)
    jd.mkdir(parents=True, exist_ok=True)
    env = llm_router._claude_env()
    # MCP-startup blocking for narrow-tool turns that spawn a server (else the
    # model fires before the handshake and sees no tools). tool_flags() returns
    # the env delta; empty for tools-off / no-server / full-agentic turns.
    if tool_env:
        env.update(tool_env)
    # stdout/stderr go STRAIGHT to files (not pipes) so the detached child keeps
    # writing even after the parent backend exits. The child dup's these fds at
    # exec, so the parent closes its own copies right after spawn.
    try:
        out_f = open(jd / "stream.jsonl", "ab")
        err_f = open(jd / "stream.err", "ab")
    except OSError as e:
        turn.error = f"journal open failed: {e}"
        turn.complete = True
        _push(turn, "error", {"detail": turn.error})
        _mark_meta(meta, "done")
        return
    # The realtime input channel. Opened O_RDWR rather than O_RDONLY on purpose:
    # an fd that is its own writer never reports EOF, so the child keeps reading
    # after an injector closes its write handle. Opened O_RDONLY the CLI would
    # see EOF the moment the first injection closed and would end the run.
    fifo = _stdin_fifo(turn.id)
    try:
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo, 0o600)
        in_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        out_f.close(); err_f.close()
        turn.error = f"input channel open failed: {e}"
        turn.complete = True
        _push(turn, "error", {"detail": turn.error})
        _mark_meta(meta, "done")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=meta["cwd"], env=env,
            stdin=in_fd, stdout=out_f, stderr=err_f,
            start_new_session=True,  # detach: survive a backend restart / pkill uvicorn
        )
    except FileNotFoundError as e:
        out_f.close(); err_f.close(); os.close(in_fd)
        turn.error = f"claude CLI not found: {e}"
        turn.complete = True
        _push(turn, "error", {"detail": turn.error})
        _mark_meta(meta, "done")
        return
    out_f.close(); err_f.close()
    # The child holds its own dup of the fifo fd, so drop ours; the child's copy
    # is what keeps the channel from hitting EOF.
    os.close(in_fd)
    # Deliver the prompt as the run's first message.
    if not write_to_turn(turn.id, meta["prompt"]):
        log.warning("turn %s: could not write the opening prompt to its input channel", turn.id)
    meta["pid"] = proc.pid
    _write_meta(meta)
    await _drive_turn(turn, meta, proc=proc)


async def _drive_turn(turn: Turn, meta: dict, proc=None) -> None:
    """Tail the turn's journal, translating stream-json lines into events +
    persisted segments, until the turn completes (a `result` line) or its process
    dies. Works for a freshly-spawned proc and for a re-attached one (proc=None,
    liveness checked by pid)."""
    # Persist under the turn's owner even on the re-attach path (no request ctx).
    if meta.get("owner_id") is not None:
        try:
            ownership.set_current_owner(int(meta["owner_id"]))
        except Exception:
            pass

    path = _journal_dir(turn.id) / "stream.jsonl"
    # A wall clock is OPT-IN. `timeout_s` <= 0 means the turn runs until the
    # model is done, the process dies, or the operator stops it. There is no
    # step cap either (see start_turn), so nothing guillotines a turn mid-task:
    # spend is governed by the usage cap, which is the correct lever.
    _budget = float(meta.get("timeout_s", 0) or 0)
    deadline = ((meta.get("started") or time.time()) + _budget) if _budget > 0 else None
    state = {"compacted": False, "saw_result": False, "last_ctx": 0}
    buf = ""
    offset = 0
    last_persist = 0.0

    def _seg_text(s: str) -> None:
        if not s:
            return
        if turn.segments and turn.segments[-1].get("t") == "text":
            turn.segments[-1]["text"] += s
        else:
            turn.segments.append({"t": "text", "text": s})

    def _seg_tool(name, inp) -> None:
        turn.segments.append({"t": "tool", "name": name or "?", "input": inp or ""})

    def _content() -> str:
        return "".join(s["text"] for s in turn.segments if s.get("t") == "text").strip()

    def _persist() -> None:
        try:
            chat_store.update_streaming_assistant(turn.thread_id, turn.id, turn.segments, _content())
        except Exception as e:  # pragma: no cover — defensive
            log.debug("turn %s: incremental persist failed: %s", turn.id, e)

    def _ingest(line: str) -> bool:
        parsed = _parse_stream_event(line)
        if not parsed:
            return False
        kind, payload = parsed
        saw_tool = False
        if kind == "_multi":
            for k2, p2 in payload.get("events", []):
                if k2 == "text":
                    _seg_text(p2.get("content", ""))
                elif k2 == "tool":
                    _seg_tool(p2.get("name"), p2.get("input")); saw_tool = True
                _push(turn, k2, p2)
            # Track the latest assistant call's context size (preferred signal —
            # it's the exact input the final model call read).
            ctx = _ctx_tokens_from_usage(payload.get("usage"))
            if ctx:
                state["last_ctx"] = ctx
            # Spend accumulates across tool rounds: each assistant message is a
            # separate API call and is billed separately. (Contrast last_ctx,
            # which is overwritten because only the latest window size matters.)
            state["spend_sum"] = int(state.get("spend_sum") or 0) + _spend_tokens_from_usage(payload.get("usage"))
        elif kind == "result":
            state["saw_result"] = True
            state["result_subtype"] = payload.get("subtype") or ""
            # The result envelope's usage is the turn TOTAL across tool rounds.
            # That overcounts the context window (hence the fallback-only use
            # below) but it is exactly right for spend, so prefer it when present.
            state["spend_result"] = _spend_tokens_from_usage(payload.get("usage"))
            # Fallback only: the result usage can be a per-turn SUM across tool
            # rounds, so it overcounts the window. Use it only if no assistant
            # message reported usage.
            if not state.get("last_ctx"):
                ctx = _ctx_tokens_from_usage(payload.get("usage"))
                if ctx:
                    state["last_ctx"] = ctx
            _push(turn, "result", payload)
        else:
            if kind == "text":
                _seg_text(payload.get("content", ""))
            elif kind == "tool":
                _seg_tool(payload.get("name"), payload.get("input")); saw_tool = True
            elif kind == "meta" and payload.get("subtype") == "compact_boundary":
                state["compacted"] = True
            _push(turn, kind, payload)
        return saw_tool

    def _drain(final: bool = False) -> bool:
        nonlocal buf, offset
        try:
            with open(path, "r", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
        except FileNotFoundError:
            chunk = ""
        if not chunk and not (final and buf.strip()):
            return False
        buf += chunk
        saw_tool = False
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                saw_tool = _ingest(line) or saw_tool
        if final and buf.strip():
            # No trailing newline on the last event (process died mid-flush).
            saw_tool = _ingest(buf) or saw_tool
            buf = ""
        return saw_tool

    timed_out = False
    while True:
        saw_tool = _drain()
        now = time.time()
        if saw_tool or (turn.segments and now - last_persist > 0.4):
            _persist(); last_persist = now
        if state["saw_result"]:
            await _retire_turn_proc(meta.get("pid"), proc)
            break
        alive = (proc.returncode is None) if proc is not None else _proc_alive(meta.get("pid"))
        if not alive:
            _drain(final=True)
            _persist()
            break
        if deadline is not None and time.time() > deadline:
            timed_out = True
            _kill_turn_proc(meta.get("pid"), proc)
            _drain(final=True)
            break
        await asyncio.sleep(0.15)

    _cleanup_turn_input(turn.id)
    turn.final_text = _content()
    lost = False
    if state["saw_result"]:
        # The result line is the authoritative end of a turn, and we are the one
        # who signalled the process to stop afterwards — so its exit status
        # reports OUR signal, not the turn's outcome. Reading it here would
        # render every successful turn as "ended unexpectedly (exit -15)".
        rc = 0
    elif proc is not None:
        rc = proc.returncode if proc.returncode is not None else -1
    else:
        # Re-attach path (this backend did not spawn the process — it adopted the
        # turn's journal after a restart). No result line and no live pid means
        # the run is simply gone, which is a different thing from a crash and
        # deserves to be named as one.
        rc = -1
        lost = True
    await _finalize_turn(turn, meta, rc=rc, timed_out=timed_out, compacted=state["compacted"],
                         context_tokens=int(state.get("last_ctx") or 0),
                         spend_tokens=int(state.get("spend_result") or state.get("spend_sum") or 0),
                         subtype=str(state.get("result_subtype") or ""), lost=lost)


async def _finalize_turn(turn: Turn, meta: dict, *, rc: int, timed_out: bool, compacted: bool,
                         context_tokens: int = 0, spend_tokens: int = 0,
                         subtype: str = "", lost: bool = False) -> None:
    prompt = meta.get("prompt", "") or ""
    cwd = meta.get("cwd", "") or ""
    timeout_s = float(meta.get("timeout_s", 0) or 0)
    _push(turn, "done", {"exit_code": rc, "text_len": len(turn.final_text)})
    turn.complete = True
    if timed_out:
        turn.error = turn.error or f"timeout after {int(timeout_s)}s"

    # Same plain-language finalize as the original path: salvage any text; when
    # nothing came back, say WHY (timeout / compact / clean-no-summary / failure).
    is_command = prompt.strip().startswith("/")
    minutes = max(1, round(timeout_s / 60)) if timeout_s > 0 else 0
    # Neither cap is passed to the CLI any more (see start_turn): no step cap,
    # and by default no wall clock either. A turn ends when the model is done,
    # when the process dies, or when the operator stops it — never because it
    # took "too many" steps or "too long". Autonomous work is the point of this
    # platform; spend is bounded by the usage cap. The timeout branch below only
    # runs for a caller that deliberately asked for a budget.
    if turn.final_text:
        text = turn.final_text
        is_error = False
    elif timed_out:
        text = (
            f"This turn was stopped after reaching the {minutes}-minute limit set "
            "by whatever started it. Any files it changed are already saved — "
            "send a follow-up (e.g. \"continue\") to pick up where it left off."
        )
        is_error = False
    elif compacted:
        text = (
            "Conversation compacted — earlier messages were summarized to free up "
            "the context window. Continue where you left off."
        )
        is_error = False
    elif rc == 0 and is_command:
        text = "Command ran."
        is_error = False
    elif rc == 0:
        text = (
            "The turn finished without a closing message — the steps above are the "
            "result. Send a follow-up if you want a summary or the next change."
        )
        is_error = False
    elif lost:
        # The operator saw "ended unexpectedly (exit -1)" over and over without
        # ever being told what it meant. This is that case, named: the backend
        # was restarted while the turn was in flight and the run did not survive.
        text = (
            "This turn was lost when the backend restarted mid-run, so it never "
            "reported back. Nothing it had written to disk is affected. Send "
            "\"continue\" to pick the work back up."
        )
        is_error = False
    else:
        detail = f": {turn.error}" if turn.error else ""
        text = f"The turn ended unexpectedly (exit {rc}){detail}. Try sending it again."
        is_error = True
    # Provider actions (e.g. an app persona applying a plan-patch to budget.json).
    # Only providers that actually override apply_actions run here, so the general
    # chat and build turns pay nothing. Mirrors the blocking POST /messages path:
    # the action is applied server-side, its fence is stripped from the answer, and
    # any human-readable notes ("Updated the plan") ride along in meta.notes.
    notes: list[str] | None = None
    if not is_error and text:
        try:
            from services import chat_context
            thread = chat_store.get_thread(turn.thread_id)
            provider = chat_context.get_provider((thread or {}).get("context_key"))
            ctx_key = (thread or {}).get("context_key")
            new_text = text
            applied_notes: list[str] = []
            if type(provider).apply_actions is not chat_context.ContextProvider.apply_actions:
                new_text, pnotes = provider.apply_actions(new_text)
                applied_notes += (pnotes or [])
            # O2: apply a task-patch block against this chat's task scope.
            from services import task_scope
            new_text, tnotes = task_scope.apply_task_patch(ctx_key, new_text)
            applied_notes += tnotes
            # Phase 4: capture an explicit operator preference/correction emitted
            # as a ``learning-note`` block into the learnings store.
            new_text, lnotes = task_scope.apply_learning_note(ctx_key, new_text)
            applied_notes += lnotes
            # Phase N3: capture a durable scoped ``note`` into the knowledge store.
            new_text, nnotes = await task_scope.apply_note_block(ctx_key, new_text)
            applied_notes += nnotes
            if new_text != text or applied_notes:
                text = new_text or "(done)"
                notes = applied_notes or None
                # Replace the trailing text run with the cleaned answer so the
                # rendered segments match the applied result.
                cut = len(turn.segments)
                while cut > 0 and turn.segments[cut - 1].get("t") == "text":
                    cut -= 1
                turn.segments = turn.segments[:cut] + ([{"t": "text", "text": text}] if text else [])
        except Exception as e:
            log.warning("turn %s: provider apply_actions failed: %s", turn.id, e)
    try:
        chat_store.finalize_streaming_assistant(
            turn.thread_id, turn.id, turn.segments, text,
            error=is_error, timed_out=timed_out, notes=notes,
            agent=meta.get("speaker"),
        )
    except Exception as e:
        log.warning("turn %s: chat_store finalize failed: %s", turn.id, e)
    # Self-heal a bad resume. If a turn that was RESUMING this thread's session
    # failed outright, the session is the prime suspect (pruned, corrupted, or
    # written under a different cwd). Detach it so the next turn mints a fresh one
    # and re-sends the assembled history — retrying the same broken resume forever
    # is what turns one bad session into a chat that "always errors".
    if is_error and meta.get("resume") and meta.get("session_id"):
        try:
            thread = chat_store.get_thread(turn.thread_id)
            if thread and thread.get("claude_session") == meta["session_id"]:
                chat_store.clear_thread_session(turn.thread_id)
                log.info("turn %s: detached failed session %s", turn.id, meta["session_id"])
        except Exception as e:
            log.debug("turn %s: session detach failed: %s", turn.id, e)
    # Record the real context occupancy reported by the model this turn, so the
    # composer meter reflects the live window (and its post-compaction drop)
    # instead of the ever-growing stored transcript. Only on a successful turn
    # with a real reading; a failed/timed-out turn keeps the prior value.
    if context_tokens > 0 and not is_error:
        try:
            chat_store.set_thread_context_tokens(turn.thread_id, context_tokens)
        except Exception as e:
            log.debug("turn %s: context-token persist failed: %s", turn.id, e)
    elif compacted:
        # A native in-session /compact just emptied the live window, but the CLI
        # does not always report usage on that turn. Leaving the pre-compact
        # reading in place pins the composer meter at its old number, which is
        # what made /compact read as a no-op even when it had worked: a chat with
        # four stored messages sat at 142k/200k because that was the last thing
        # measured. Drop the stale measurement so the meter re-derives from the
        # compacted prompt, and let the next real turn re-measure. Same reasoning
        # (and same remedy) as the forced-fold path in routes/chat.py.
        try:
            chat_store.clear_thread_context_tokens(turn.thread_id)
        except Exception as e:
            log.debug("turn %s: context-token clear failed: %s", turn.id, e)

    # Meter the turn's SPEND. Reported, never gated: usage_ledger.can_admit
    # reserves headroom on the assumption that interactive chat is invisible to
    # the ledger, so metering chat AND gating on it would double-count that
    # reserve. Recorded with role="chat" so chat spend stays separable from
    # autonomous worker spend in every downstream view. Best-effort — a ledger
    # hiccup must never fail a turn the operator already received.
    if spend_tokens > 0:
        try:
            from services import usage_ledger
            usage_ledger.record(
                _usage_scope_for_turn(turn),
                spend_tokens,
                role="chat",
                run=turn.id,
                chat=turn.thread_id,
            )
        except Exception as e:
            log.debug("turn %s: chat usage record failed: %s", turn.id, e)
    try:
        build_agents.touch(turn.agent_slug)
        # Turn is over: drop the persisted "running" ratchet back to idle so the
        # store stops claiming every agent that ever ran is live.
        build_agents.mark_idle(turn.agent_slug)
    except Exception:
        pass

    if not is_error:
        try:
            from services import chat_naming
            chat_naming.schedule(turn.thread_id)
        except Exception as e:  # pragma: no cover — best-effort
            log.debug("turn %s: auto-name schedule failed: %s", turn.id, e)

    # Auto-checkpoint the working tree (skip the self/platform monorepo).
    try:
        from services import checkpoints, app_runner
        if cwd and not app_runner.is_self_dir(cwd):
            sha = await asyncio.to_thread(checkpoints.checkpoint, cwd, f"{turn.agent_slug} turn")
            if sha:
                _push(turn, "checkpoint", {"sha": sha})
    except Exception as e:
        log.info("turn %s: checkpoint skipped: %s", turn.id, e)

    try:
        first_user = _first_user_text(turn.thread_id)
        if first_user and text and not text.startswith("(model exited"):
            from routes import chat as _chat
            await _maybe_autoname_stream(_chat, turn.thread_id, first_user, text)
    except Exception as e:
        log.info("turn %s: auto-name skipped: %s", turn.id, e)

    # The thread now holds the final message; the journal is no longer needed.
    _mark_meta(meta, "done")
    try:
        shutil.rmtree(_journal_dir(turn.id), ignore_errors=True)
    except Exception:
        pass


def reattach_running_turns() -> None:
    """Startup hook: resume any build turn whose journal says it was still running
    when the backend went down. A live detached process is tailed to completion; a
    dead one is finalized from whatever it wrote so the chat is never stuck.
    Idempotent and best-effort — never raise into app startup."""
    try:
        if not TURNS_DIR.exists():
            return
        for d in sorted(TURNS_DIR.iterdir()):
            if not d.is_dir():
                continue
            meta = _read_meta(d)
            if not meta or meta.get("status") == "done":
                continue
            tid = meta.get("turn_id")
            if not tid or tid in TURNS:
                continue
            owner_id = meta.get("owner_id")
            if owner_id is None:
                # Can't safely persist without an owner — drop the orphan; the
                # thread self-heals its stuck streaming flag on next fetch.
                shutil.rmtree(d, ignore_errors=True)
                continue
            try:
                ownership.set_current_owner(int(owner_id))
                if not chat_store.get_thread(meta.get("thread_id") or ""):
                    shutil.rmtree(d, ignore_errors=True)
                    continue
            except Exception:
                continue
            turn = Turn(id=tid, thread_id=meta["thread_id"], agent_slug=meta.get("agent_slug", ""))
            turn.started = meta.get("started", time.time())
            TURNS[tid] = turn
            alive = _proc_alive(meta.get("pid"))
            log.info("re-attaching build turn %s (pid=%s alive=%s)", tid, meta.get("pid"), alive)
            asyncio.create_task(_drive_turn(turn, meta, proc=None))
    except Exception as e:  # pragma: no cover — never block startup
        log.warning("reattach_running_turns failed: %s", e)


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


def inject(turn_id: str, text: str) -> bool:
    """Deliver a message into a turn that is currently running.

    The operator's spec: a new message must not abort the work in flight, but it
    must not have to wait out the whole turn either — it lands at the next point
    between the agent's actions. Writing it to the turn's stdin is exactly that;
    the CLI reads pending input at each step boundary.

    Returns False when the turn is no longer accepting input, which is the
    caller's cue to queue it as the next turn instead. The message is never
    dropped either way."""
    text = (text or "").strip()
    if not text:
        return False
    return write_to_turn(turn_id, text)


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
