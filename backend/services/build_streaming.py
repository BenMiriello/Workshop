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
# the child keeps running and keeps writing its journal. We read events by
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
                     max_turns: int = 60, timeout_s: float = 900,
                     allowed_tools: list[str] | None = None,
                     mcp_servers: list[str] | None = None,
                     tools_disabled: bool = False) -> Turn:
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
    or project chat streams without a persistent Claude Code session."""
    turn = _make_turn(thread_id, agent_slug)
    try:
        owner_id = ownership.current_owner()
    except Exception:
        owner_id = None
    meta = {
        "turn_id": turn.id, "thread_id": thread_id, "agent_slug": agent_slug,
        "cwd": cwd, "model": model, "session_id": session_id, "resume": bool(resume),
        "owner_id": owner_id, "prompt": prompt, "max_turns": max_turns,
        "timeout_s": timeout_s, "pid": None, "status": "running", "started": turn.started,
        "allowed_tools": allowed_tools, "mcp_servers": mcp_servers,
        "tools_disabled": bool(tools_disabled),
    }
    _write_meta(meta)
    asyncio.create_task(_spawn_and_drive(turn, meta))
    return turn


async def _spawn_and_drive(turn: Turn, meta: dict) -> None:
    cmd: list[str] = [
        CLAUDE_CLI, "-p", meta["prompt"],
        "--model", meta["model"],
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(meta["max_turns"]),
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
        chat_store.append_streaming_assistant(turn.thread_id, turn.id, model=meta["model"])
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
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=meta["cwd"], env=env,
            stdout=out_f, stderr=err_f,
            start_new_session=True,  # detach: survive a backend restart / pkill uvicorn
        )
    except FileNotFoundError as e:
        out_f.close(); err_f.close()
        turn.error = f"claude CLI not found: {e}"
        turn.complete = True
        _push(turn, "error", {"detail": turn.error})
        _mark_meta(meta, "done")
        return
    out_f.close(); err_f.close()
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
    deadline = (meta.get("started") or time.time()) + float(meta.get("timeout_s", 900))
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
        elif kind == "result":
            state["saw_result"] = True
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
            break
        alive = (proc.returncode is None) if proc is not None else _proc_alive(meta.get("pid"))
        if not alive:
            _drain(final=True)
            _persist()
            break
        if time.time() > deadline:
            timed_out = True
            _kill_turn_proc(meta.get("pid"), proc)
            _drain(final=True)
            break
        await asyncio.sleep(0.15)

    turn.final_text = _content()
    if proc is not None:
        rc = proc.returncode if proc.returncode is not None else -1
    else:
        rc = 0 if state["saw_result"] else -1
    await _finalize_turn(turn, meta, rc=rc, timed_out=timed_out, compacted=state["compacted"],
                         context_tokens=int(state.get("last_ctx") or 0))


async def _finalize_turn(turn: Turn, meta: dict, *, rc: int, timed_out: bool, compacted: bool,
                         context_tokens: int = 0) -> None:
    prompt = meta.get("prompt", "") or ""
    cwd = meta.get("cwd", "") or ""
    timeout_s = float(meta.get("timeout_s", 900))
    _push(turn, "done", {"exit_code": rc, "text_len": len(turn.final_text)})
    turn.complete = True
    if timed_out:
        turn.error = turn.error or f"timeout after {int(timeout_s)}s"

    # Same plain-language finalize as the original path: salvage any text; when
    # nothing came back, say WHY (timeout / compact / clean-no-summary / failure).
    is_command = prompt.strip().startswith("/")
    minutes = max(1, round(timeout_s / 60))
    if turn.final_text:
        text = turn.final_text
        is_error = False
    elif timed_out:
        text = (
            f"This turn was stopped after reaching the {minutes}-minute time limit. "
            "Any files it changed are already saved — send a follow-up (e.g. "
            "\"continue\") to pick up where it left off."
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
        )
    except Exception as e:
        log.warning("turn %s: chat_store finalize failed: %s", turn.id, e)
    # Record the real context occupancy reported by the model this turn, so the
    # composer meter reflects the live window (and its post-compaction drop)
    # instead of the ever-growing stored transcript. Only on a successful turn
    # with a real reading; a failed/timed-out turn keeps the prior value.
    if context_tokens > 0 and not is_error:
        try:
            chat_store.set_thread_context_tokens(turn.thread_id, context_tokens)
        except Exception as e:
            log.debug("turn %s: context-token persist failed: %s", turn.id, e)
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
