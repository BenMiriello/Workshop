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

from services import build_agents, chat_log, chat_store, llm_router, ownership

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
    life of the run and a message sent while the agent is working reaches the
    process immediately instead of waiting out the whole turn. (Reaching it is
    not the same as being acted on — see write_to_turn.)

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
    # Open subagent segments by their Agent tool_use id. A subagent's own steps
    # arrive as sidechain events carrying `parent_tool_use_id`; this is how they
    # find the segment they belong under instead of flattening into the parent's
    # work. Rebuilt from the journal on re-attach (the drive loop re-reads from
    # offset 0), so it never has to survive a restart.
    agent_segs: dict[str, dict] = field(default_factory=dict)
    # Set once this run's message has been committed to the thread, with the
    # committed message in `committed`. This is what the blocking POST /messages
    # path awaits, so there is ONE finalize pipeline instead of a streaming one
    # and a blocking one that drift apart.
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    committed: dict | None = None
    notes: list[str] | None = None
    # True once the subprocess exists and its input channel is open. A run is
    # CLAIMED before its prompt is assembled, so there is a window where it owns
    # the thread but cannot be delivered to yet; that is not the same thing as a
    # run whose channel has been cleaned up because it is finishing, and the
    # injection path has to tell them apart.
    spawned: bool = False
    # The turn's process, for the one caller that has to end it early (stop_turn).
    # `pid` is set on the re-attach path too, where this process never held a
    # handle; `proc` only when we spawned it ourselves.
    pid: int | None = None
    proc: object | None = None
    # The operator pressed Stop. Two flags, not one, because two different
    # coroutines act on it: stop_turn signals the process group, the drive loop
    # words the finalize. `stop_signalled` is the interlock that keeps the group
    # from being signalled twice — whichever gets there first sets it, and the
    # check-and-set is await-free so they cannot both win.
    stop_requested: bool = False
    stop_signalled: bool = False


# In-memory turn registry. Build sessions are short-lived enough that this is
# acceptable; a future upgrade can spill to disk for cross-process resume.
TURNS: dict[str, Turn] = {}


class RunInFlight(RuntimeError):
    """A run is already open on this thread; join it instead of starting a rival.

    Carries the id of the run that owns the thread. Raised by `start_turn` when
    it claims for itself and loses the race, which is the backstop for two POSTs
    landing in the same event-loop tick."""

    def __init__(self, run_id: str):
        super().__init__(f"a run is already in flight on this thread: {run_id}")
        self.run_id = run_id


def _make_turn(thread_id: str, agent_slug: str, turn_id: str | None = None) -> Turn:
    # A claimed run is already registered (see claim_run); adopt it rather than
    # replacing it, so nothing that looked it up between the claim and the spawn
    # is holding an object this one has orphaned.
    t = TURNS.get(turn_id or "")
    if t is None:
        t = Turn(id=turn_id or new_run_id(), thread_id=thread_id, agent_slug=agent_slug)
        TURNS[t.id] = t
    else:
        t.agent_slug = agent_slug
        t.started = time.time()
    return t


def new_run_id() -> str:
    return str(uuid.uuid4())


def run_is_live(run_id: str | None) -> bool:
    """Is this process driving this run right now?

    A run this process does not hold is not live however the thread's meta reads:
    it belongs to a previous boot, and the startup reconcile owns it. A complete
    one is not live either. Both are cases where `active_run_id` outlives the
    thing it names, and the point of one predicate is that every caller agrees
    about them."""
    if not run_id:
        return False
    t = TURNS.get(run_id)
    return t is not None and not t.complete


def run_is_ending(run_id: str | None) -> bool:
    """Has this run spawned and lost its input channel?

    That is the one state where a message cannot be delivered and never will be:
    the drive loop has ended and the finalize is running. A run that has been
    claimed but not yet spawned looks the same to `write_to_turn` (no FIFO) and
    is the opposite situation — it is about to start — so the two must not be
    treated alike, or a message sent in the startup window would kill the run it
    was meant for."""
    t = TURNS.get(run_id or "")
    return t is not None and t.spawned and not _stdin_fifo(run_id or "").exists()


async def claim_run(thread_id: str, run_id: str, *, model: str | None = None,
                    agent: str | None = None, force: bool = False) -> str | None:
    """Take this thread for `run_id`. Returns the run that already owns it (join
    that one), or None when the claim succeeded.

    Held under the thread's WRITE LOCK across the whole check-and-act, so two
    POSTs in the same event-loop tick cannot both open a run: the second takes
    the lock, sees `active_run_id`, and is told to join. That is structural, not
    a timing accident — there is no await between the read and the write for the
    other coroutine to slip into.

    `force` takes over a thread whose active run has just refused a delivery.
    Only the injection path uses it, and only after a write to that run's input
    channel has actually failed.

    A run this process is not driving gets a `lost` message on the way out:
    nothing else is ever going to answer for it, and leaving the chat waiting on
    it is the "chats keep stalling" report."""
    async with chat_log.lock_for(thread_id):
        active = chat_store.active_run_id(thread_id)
        if active and active != run_id:
            if run_is_live(active) and not force:
                return active
            if active not in TURNS:
                chat_store.lose_run(thread_id, active)
        chat_store.begin_run(thread_id, run_id, model=model, agent=agent)
        # Register the run BEFORE returning. Prompt assembly awaits, and until
        # the run is in TURNS a message arriving during that window would see an
        # `active_run_id` naming a run nothing is driving and take the thread
        # over — starting the rival turn the claim exists to prevent.
        _make_turn(thread_id, "", turn_id=run_id)
        return None


def _push(turn: Turn, kind: str, payload: dict) -> None:
    turn.events.append(TurnEvent(kind=kind, payload=payload))
    turn.new_event.set()


# The CLI's subagent tool. `Agent` is the name it emits (CLI 2.1.143); `Task`
# was the older name and is still accepted on `--tools`, so both are recognised
# here rather than pinning the reader to one CLI version.
SUBAGENT_TOOLS = ("Agent", "Task")


def _subagent_label(block: dict) -> str | None:
    """For an Agent tool_use, which subagent was launched — the frontmatter name
    when one was asked for (`work-reviewer`), else the built-in general-purpose
    worker. None for any other tool, which is what marks the segment kind."""
    if block.get("name") not in SUBAGENT_TOOLS:
        return None
    inp = block.get("input")
    if isinstance(inp, dict):
        st = inp.get("subagent_type") or inp.get("agent_type")
        if st:
            return str(st)
    return "general-purpose"


def _tool_result_text(content: object) -> str:
    """A tool result flattened to a short string. A subagent's result is a list
    of text blocks (its report, then its agent id); everything else is already a
    string. Capped like _summarize_tool_input — this rides the SSE channel."""
    if isinstance(content, list):
        parts = [str(b.get("text") or "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        content = "\n".join(p for p in parts if p)
    s = str(content or "")
    return s if len(s) <= 600 else s[:597] + "…"


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
    # A SIDECHAIN event: emitted by a subagent the turn launched, not by the
    # turn's own model. The CLI marks every one with the id of the Agent tool_use
    # that started it, which is the only way to tell "my subagent ran ls" from "I
    # ran ls" — without it a subagent's steps flatten into the parent's work and
    # its interim text lands in the parent's answer. Verified against CLI 2.1.143.
    parent = ev.get("parent_tool_use_id")
    if t == "assistant":
        # An assistant message containing content blocks. Forward text blocks
        # as "text" events; tool_use blocks as "tool".
        msg = ev.get("message", {})
        out: list[tuple[str, dict]] = []
        for block in msg.get("content", []) or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    out.append(("text", {"content": block.get("text", ""), "parent": parent}))
                elif block.get("type") == "tool_use":
                    out.append(("tool", {
                        "name": block.get("name"),
                        "input": _summarize_tool_input(block.get("input", {}), block.get("name")),
                        "detail": _tool_detail(block.get("input", {}), block.get("name")),
                        # The id is what a later sidechain event points back at,
                        # and what the closing tool_result names.
                        "id": block.get("id"),
                        "agent": _subagent_label(block),
                        "parent": parent,
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
            return ("_multi", {"events": out, "usage": msg.get("usage"), "parent": parent})
        return ("meta", ev)
    if t == "user":
        # Two different things arrive as `user`:
        #
        #  - a TOOL RESULT going back to the model. Content is a list of blocks
        #    carrying tool_use_id; the UI only counts these.
        #  - a REPLAYED STDIN MESSAGE. With --replay-user-messages the CLI
        #    re-emits each stdin user message once it has actually read it, which
        #    is the only receipt available for a mid-turn injection. Content is a
        #    text block (or, for the CLI's synthetic /compact turns, a bare
        #    string — guard so a non-dict block cannot crash the reader).
        #
        # A subagent's own prompt arrives here too, as a text block carrying
        # `parent_tool_use_id`. It is neither of the above: `parent` rides along
        # so the receipt path can tell an operator injection from a sidechain.
        content = ev.get("message", {}).get("content")
        if isinstance(content, str):
            return ("user_echo", {"text": content, "parent": parent})
        if isinstance(content, list) and content and isinstance(content[0], dict):
            b0 = content[0]
            if b0.get("type") == "text":
                return ("user_echo", {"text": b0.get("text") or "", "parent": parent})
            return ("tool_result", {
                "tool_use_id": b0.get("tool_use_id"),
                "text": _tool_result_text(b0.get("content")),
                "parent": parent,
            })
        return ("tool_result", {"tool_use_id": None, "text": "", "parent": parent})
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


# --------------------------------------------------------------- segment model
#
# A turn renders as ordered segments: text the operator reads, tool chips, and
# `agent` segments — one per subagent launched, with that subagent's own steps
# nested INSIDE it. The nesting is the point. A delegate exists to keep its
# working context out of this conversation, so its steps must not flatten into
# the parent's work and its interim text must never join the parent's answer.
#
# Extracted from _drive_turn's closures so the routing is addressable on its own:
# it is the part with the actual rules, and it was previously reachable only by
# running a subprocess. See tests/test_subagent_stream.py.


def _seg_text(turn: "Turn", s: str) -> None:
    if not s:
        return
    if turn.segments and turn.segments[-1].get("t") == "text":
        turn.segments[-1]["text"] += s
    else:
        turn.segments.append({"t": "text", "text": s})


def _seg_tool(turn: "Turn", name, inp) -> None:
    turn.segments.append({"t": "tool", "name": name or "?", "input": inp or ""})


def _seg_agent(turn: "Turn", payload: dict) -> None:
    """A subagent launch becomes ONE segment that its steps nest under, not a
    tool chip followed by a flat run of somebody else's work."""
    seg = {
        "t": "agent",
        "name": payload.get("agent") or "general-purpose",
        "input": payload.get("input") or "",
        # The brief, read only when the chip is opened. The one-line `input`
        # above is what the row says; this is what the row hides.
        "detail": payload.get("detail") or "",
        "id": payload.get("id") or "",
        "steps": [],
        "done": False,
        "result": "",
    }
    turn.segments.append(seg)
    if seg["id"]:
        turn.agent_segs[seg["id"]] = seg


def _agent_step(turn: "Turn", parent: str, seg: dict) -> bool:
    """Route a sidechain event under the subagent that produced it. Returns False
    when the parent is unknown, which is the caller's cue to treat the event as
    ordinary top-level work rather than drop it — an orphan step (a journal
    tailed from mid-stream) is still real work that happened."""
    host = turn.agent_segs.get(parent)
    if host is None:
        return False
    steps = host.setdefault("steps", [])
    if seg.get("t") == "text" and steps and steps[-1].get("t") == "text":
        steps[-1]["text"] += seg.get("text") or ""
    else:
        steps.append(seg)
    return True


def _turn_content(turn: "Turn") -> str:
    """The operator-visible answer: TOP-LEVEL text only. A subagent's interim
    text lives in its own segment's steps and is not part of the reply."""
    return "".join(s["text"] for s in turn.segments if s.get("t") == "text").strip()


def _segment_ingest(turn: "Turn", kind: str, payload: dict) -> bool:
    """Fold one parsed event into the turn's segments. Returns whether it was a
    tool-ish event (the driver's cue to persist promptly)."""
    if kind == "_multi":
        saw_tool = False
        for k2, p2 in payload.get("events", []):
            saw_tool = _segment_ingest(turn, k2, p2) or saw_tool
        return saw_tool
    nested = payload.get("parent")
    if kind == "text":
        text = payload.get("content", "")
        if not (nested and _agent_step(turn, nested, {"t": "text", "text": text})):
            _seg_text(turn, text)
        return False
    if kind == "tool":
        step = {"t": "tool", "name": payload.get("name") or "?", "input": payload.get("input") or ""}
        if not (nested and _agent_step(turn, nested, step)):
            if payload.get("agent"):
                _seg_agent(turn, payload)
            else:
                _seg_tool(turn, payload.get("name"), payload.get("input"))
        return True
    if kind == "tool_result":
        # The Agent tool's own result closes its segment. Nested tool results
        # (parent set) are the subagent's internal plumbing and are not rendered;
        # its report comes back in this one.
        host = turn.agent_segs.get(payload.get("tool_use_id") or "")
        if host is not None:
            host["done"] = True
            host["result"] = payload.get("text") or ""
            return True
    return False


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


def usage_scope_for_thread(thread_id: str) -> str:
    """The ledger scope a chat turn bills to: a bare project slug, matching the
    values autonomous sweeps already write ("workshop", "demo-x"). The knowledge
    scope is the right source (it is what the chat's own learnings key on) but it
    uses a "project:<slug>" form, so strip the prefix. Falls back to "workshop"
    rather than raising — a scope lookup must never cost the operator a turn.

    Public, and keyed by thread rather than by Turn, because the VOICE worker
    bills the same way: a spoken turn is the same chat spending the same money,
    and it went unmetered entirely until it shared this rule."""
    try:
        from services import chat_store, knowledge
        t = chat_store.get_thread(thread_id) or {}
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


def _summarize_tool_input(inp: dict, tool_name: str | None = None) -> str:
    """One-line preview for the UI. Long tool inputs (full file contents in
    a Write) shouldn't bloat the SSE stream — show file path + size only."""
    # Cap at 300 chars: enough that expanding a tool chip shows a useful detail
    # (a full command / path), without streaming whole file bodies into history.
    if not isinstance(inp, dict):
        return str(inp)[:300]
    # A subagent is named by the job it was given, not by the instructions it
    # was given. `prompt` is a page of them, and falling through to it printed
    # 300 characters of raw brief onto the chip where a task name belongs.
    # `description` is the one-line task, which is the whole point of the field.
    # Only for the Agent tool: elsewhere (Bash) `description` is a gloss and the
    # command itself is the useful thing.
    if tool_name in SUBAGENT_TOOLS:
        desc = str(inp.get("description") or "").strip()
        if desc:
            return desc if len(desc) <= 300 else desc[:297] + "…"
        # No description given. Falling back to the whole brief is how 300
        # characters of raw instructions ended up as a delegation's title, so
        # take only its first line and keep it to a title's length. The brief
        # itself still travels whole, in _tool_detail, for the opened chip.
        first = next((ln.strip() for ln in str(inp.get("prompt") or "").splitlines()
                      if ln.strip()), "")
        if not first:
            return "delegated a task"
        return first if len(first) <= 80 else first[:79] + "…"
    # A subagent's full brief is not a preview; see _tool_detail.
    for key in ("file_path", "path", "pattern", "command", "url", "query", "prompt"):
        if key in inp:
            v = str(inp[key])
            return v if len(v) <= 300 else v[:297] + "…"
    # Generic fallback: short keys list.
    keys = ",".join(list(inp.keys())[:4])
    return f"({keys})"


# A delegation's chip shows one line: what the agent was asked to do. The brief
# it was actually handed is the thing you open the chip to read, so it has to
# survive to the client instead of being truncated into the preview. Only for a
# subagent launch, and capped well below a file body.
SUBAGENT_BRIEF_MAX = 8000


def _tool_detail(inp: dict, tool_name: str | None = None) -> str | None:
    """The full brief behind a collapsed chip. None when there is nothing more
    to show than the preview already says."""
    if tool_name not in SUBAGENT_TOOLS or not isinstance(inp, dict):
        return None
    prompt = str(inp.get("prompt") or "").strip()
    if not prompt:
        return None
    return prompt if len(prompt) <= SUBAGENT_BRIEF_MAX else prompt[:SUBAGENT_BRIEF_MAX - 1] + "…"


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
                     disallowed_tools: list[str] | None = None,
                     mcp_servers: list[str] | None = None,
                     tools_disabled: bool = False,
                     speaker: str | None = None,
                     run_id: str | None = None,
                     effort: str | None = None) -> Turn:
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
    the assistant's and needs no attribution.

    `run_id` is a run the caller already claimed (`claim_run`). Passing one is
    how the route holds the thread from before it starts assembling a prompt —
    which takes awaits, and therefore ticks — through to the spawn. Omit it and
    this claims one itself, raising RunInFlight if the thread is busy."""
    turn = _make_turn(thread_id, agent_slug, turn_id=run_id)
    if run_id is None:
        joined = await claim_run(thread_id, turn.id, model=model, agent=speaker)
        if joined is not None:
            TURNS.pop(turn.id, None)
            raise RunInFlight(joined)
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
        "allowed_tools": allowed_tools, "disallowed_tools": disallowed_tools,
        "mcp_servers": mcp_servers,
        "tools_disabled": bool(tools_disabled),
        # None = send no --effort, i.e. the model's own default. Carried in meta
        # so a turn resumed after a restart respawns at the level it was started
        # at rather than silently dropping to the default.
        "effort": effort,
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
        # Re-emit stdin user messages on stdout once read. This is what turns
        # "the bytes reached the process" into "the turn has read it" — the
        # difference between a chat that is waiting and one that looks broken.
        "--replay-user-messages",
    ]
    # Effort is validated by the CLI against one fixed list for EVERY model, so a
    # level the model doesn't honour is accepted and quietly does nothing. The
    # caller resolves against services/model_caps before this point; by here a
    # value is already known-good for this model.
    if meta.get("effort"):
        cmd.extend(["--effort", meta["effort"]])
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
            disallowed_tools=meta.get("disallowed_tools"),
            mcp_servers=meta.get("mcp_servers"),
        )
        cmd.extend(_tflags)
    else:
        cmd.extend(["--permission-mode", "bypassPermissions"])
    if meta["session_id"] and meta["resume"]:
        cmd.extend(["--resume", meta["session_id"]])
    elif meta["session_id"]:
        cmd.extend(["--session-id", meta["session_id"]])

    _push(turn, "started", {"cwd": meta["cwd"], "model": meta["model"],
                            "resume": meta["resume"], "effort": meta.get("effort")})
    # NO PLACEHOLDER. The run is already claimed on the thread (`claim_run`) and
    # its output goes to the journal below plus `meta.live_message`; the reply
    # enters the log once, at finalize. Seeding an empty assistant message here
    # is what used to file the operator's mid-turn messages below a reply that
    # had not read them.
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
        _push(turn, "error", {"detail": turn.error})
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
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
        _push(turn, "error", {"detail": turn.error})
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
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
        _push(turn, "error", {"detail": turn.error})
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
        return
    out_f.close(); err_f.close()
    # The child holds its own dup of the fifo fd, so drop ours; the child's copy
    # is what keeps the channel from hitting EOF.
    os.close(in_fd)
    turn.spawned = True
    # Hand the process to the Turn as well as to the journal: Stop has a run id
    # and nothing else, and reading the pid back off disk to signal it would be a
    # second source of truth for something this process is holding.
    turn.proc, turn.pid = proc, proc.pid
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

    def _persist() -> None:
        # The live snapshot, not the log. The journal on disk is the buffer and
        # this is the copy a reload reads; the record gets one write, at commit.
        try:
            chat_store.update_run(
                turn.thread_id, turn.id, turn.segments, _turn_content(turn))
        except Exception as e:  # pragma: no cover — defensive
            log.debug("turn %s: incremental persist failed: %s", turn.id, e)

    def _ingest(line: str) -> bool:
        parsed = _parse_stream_event(line)
        if not parsed:
            return False
        kind, payload = parsed
        saw_tool = False
        if kind == "_multi":
            saw_tool = _segment_ingest(turn, kind, payload)
            for k2, p2 in payload.get("events", []):
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
            if kind in ("text", "tool", "tool_result"):
                saw_tool = _segment_ingest(turn, kind, payload)
            elif kind == "meta" and payload.get("subtype") == "compact_boundary":
                state["compacted"] = True
            elif kind == "user_echo":
                # DROPPED, ALWAYS. `--replay-user-messages` makes the CLI echo
                # every stdin user message back: the turn's own prompt, its
                # synthetic /compact turns, a subagent's prompt on the sidechain,
                # and anything injected mid-turn. None of it is output, and the
                # operator's own messages are already in the log where they were
                # sent — echoing one back would show it twice.
                #
                # This branch used to match an echo against a list of pending
                # injections and emit an `injection_read` receipt, which drove a
                # "Read by the turn in progress" line under the message. That was
                # an invented UI pattern: standard chat does not annotate your own
                # messages with delivery state. The line is gone and so is the
                # bookkeeping; the classification stays because these events still
                # have to be recognised in order to be dropped.
                return saw_tool
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
        if turn.stop_requested and not turn.stop_signalled:
            # The operator stopped a run whose process did not exist yet when
            # they pressed it (the claim window: a run owns the thread from
            # before its prompt is assembled). stop_turn had nothing to signal
            # then, so the loop signals it now — same helper, same interlock, so
            # the group is signalled exactly once either way.
            turn.stop_signalled = True
            await _retire_turn_proc(meta.get("pid"), proc)
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
    turn.final_text = _turn_content(turn)
    # THE STOP IS READ AFTER THE FINAL DRAIN, never before it. Everything the
    # process wrote before the signal reached it is on disk, and the drain above
    # is what turns those bytes into the segments this run commits.
    stopped = turn.stop_requested
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
        # deserves to be named as one. Unless we ended it ourselves: a run the
        # operator stopped is accounted for, and calling it lost would tell them
        # the backend dropped something they were holding the button on.
        rc = -1
        lost = not stopped
    await _finalize_turn(turn, meta, rc=rc, timed_out=timed_out, compacted=state["compacted"],
                         context_tokens=int(state.get("last_ctx") or 0),
                         spend_tokens=int(state.get("spend_result") or state.get("spend_sum") or 0),
                         subtype=str(state.get("result_subtype") or ""), lost=lost,
                         stopped=stopped)


async def _finalize_turn(turn: Turn, meta: dict, **kw) -> None:
    """Commit the run and wake whoever is waiting on it.

    Always wakes, even if the commit raised. The blocking POST /messages path
    awaits `turn.finished`, and a finalize that fails silently would leave that
    request hanging for as long as the client will hold the socket open."""
    try:
        await _commit_turn(turn, meta, **kw)
    finally:
        turn.complete = True
        turn.new_event.set()
        turn.finished.set()


async def _commit_turn(turn: Turn, meta: dict, *, rc: int, timed_out: bool, compacted: bool,
                       context_tokens: int = 0, spend_tokens: int = 0,
                       subtype: str = "", lost: bool = False,
                       stopped: bool = False) -> None:
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
    elif stopped:
        # The operator stopped it before it wrote anything. No sentence here: the
        # tag on the message already says who ended it, and a body repeating that
        # is the same thing said twice. The segments (the tools it had already
        # run) are committed regardless, and they are the honest record of what
        # the turn got through.
        text = ""
        is_error = False
    elif timed_out:
        text = (
            f"Stopped at the {minutes}-minute limit. Any file changes are saved. "
            "Send \"continue\" to pick it back up."
        )
        is_error = False
    elif compacted:
        text = "Compacted. Earlier messages are now a summary."
        is_error = False
    elif rc == 0 and is_command:
        text = "Command ran."
        is_error = False
    elif rc == 0:
        text = "Finished with no closing message. The steps above are the result."
        is_error = False
    elif lost:
        # The operator saw "ended unexpectedly (exit -1)" over and over without
        # ever being told what it meant. This is that case, named: the backend
        # was restarted while the turn was in flight and the run did not survive.
        text = chat_store.LOST_RUN_TEXT
        is_error = False
    else:
        detail = f": {turn.error}" if turn.error else ""
        text = f"Ended unexpectedly (exit {rc}){detail}."
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
            # Whose reply is this, and what may that speaker write?
            #
            # These protocol blocks are mediated writes: a reply containing a
            # task-patch files a ticket, a note block writes the knowledge
            # store. When an AGENT is speaking they are the agent's writes, and
            # a role that does not write tickets must not be able to file one by
            # emitting a fenced block. The chat's own assistant is not an agent
            # and is unaffected — it acts for the operator, who is present.
            _speaker = meta.get("speaker")
            _role, _is_agent = None, False
            if _speaker:
                try:
                    from services import agents_registry as _ar
                    _rec = _ar.get(_speaker)
                    if _rec:
                        _is_agent, _role = True, _rec.get("role")
                except Exception:
                    pass

            def _permitted(target: str) -> bool:
                if not _is_agent:
                    return True
                from services import agent_authority
                return agent_authority.may_write(_role, target)
            if type(provider).apply_actions is not chat_context.ContextProvider.apply_actions:
                new_text, pnotes = provider.apply_actions(new_text)
                applied_notes += (pnotes or [])
            # O2: apply a task-patch block against this chat's task scope.
            from services import task_scope
            if _permitted("tasks"):
                new_text, tnotes = task_scope.apply_task_patch(
                    ctx_key, new_text, (thread or {}).get("id"), (thread or {}).get("mode"))
                applied_notes += tnotes
            else:
                new_text = task_scope.strip_task_patch(new_text)
                applied_notes.append(f"(a {_role or 'roleless'} agent may not file tickets)")
            # Phase 4: capture an explicit operator preference/correction emitted
            # as a ``learning-note`` block into the learnings store.
            if _permitted("learnings"):
                new_text, lnotes = task_scope.apply_learning_note(ctx_key, new_text)
                applied_notes += lnotes
            # Phase N3: capture a durable scoped ``note`` into the knowledge store.
            if _permitted("notes"):
                new_text, nnotes = await task_scope.apply_note_block(ctx_key, new_text)
                applied_notes += nnotes
            # A data-mode assistant is told it may ask for a skill by emitting a
            # fenced block. It has tools and reads the skill itself, so nothing
            # expands one; take the raw protocol off the reply.
            new_text = chat_context.strip_use_skill(new_text)
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
    # THE COMMIT. One message, appended at the tail, whether or not a client is
    # watching — so it lands below every message this run was handed and above
    # nothing. `lost` is recorded as such rather than as a normal finish.
    turn.notes = notes
    try:
        turn.committed = chat_store.commit_run(
            turn.thread_id, turn.id, turn.segments, text,
            error=is_error, timed_out=timed_out, notes=notes,
            agent=meta.get("speaker"), state=("lost" if lost else "finished"),
            # A stopped run is FINISHED, not lost: it produced what it produced
            # and nothing is coming later. `stopped` is what tells the operator
            # apart from a restart in the transcript.
            stopped=(chat_store.STOPPED_BY_OPERATOR if stopped else None),
        )
    except Exception as e:
        log.warning("turn %s: chat_store commit failed: %s", turn.id, e)
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
                usage_scope_for_thread(turn.thread_id),
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

    # ONE naming path. chat_naming owns the guard ("still unnamed, and a reply
    # has landed") and reads the thread itself, so this is safe to call after
    # every turn and will keep trying until a name sticks. There used to be a
    # second call below routing through routes.chat._maybe_autoname, a function
    # that no longer exists: it raised AttributeError on every streamed turn into
    # a best-effort `except` and logged it where nobody looks.
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
                # Can't safely persist without an owner — drop the orphan. The
                # thread still points at this run; reconcile_threads() closes it
                # out with a `lost` message right after this pass.
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
            # This process did not spawn it, but the process it belongs to did:
            # its input channel is either on disk or gone for good, which is the
            # distinction `run_is_ending` needs to make. The pid comes off the
            # journal for the same reason — an adopted run must still be
            # stoppable, and it is a process group like any other.
            turn.spawned = True
            turn.pid = meta.get("pid")
            TURNS[tid] = turn
            alive = _proc_alive(meta.get("pid"))
            log.info("re-attaching build turn %s (pid=%s alive=%s)", tid, meta.get("pid"), alive)
            asyncio.create_task(_drive_turn(turn, meta, proc=None))
    except Exception as e:  # pragma: no cover — never block startup
        log.warning("reattach_running_turns failed: %s", e)


def reconcile_threads() -> None:
    """Startup hook, second pass: close out any thread still pointing at a run.

    `meta.active_run_id` is the whole reattachment story now — one field, not a
    backward scan for a streaming flag. So a thread is stuck if and only if that
    field names a run this process is not driving, and the fix is to say so in
    the transcript: a `run: lost` marker and a message in the same plain wording
    the re-attach path uses.

    Runs AFTER reattach_running_turns(), which registers every journal that is
    still alive. Anything left over has no process behind it and never will."""
    try:
        root = ownership._RUNTIME / "users"
        if not root.exists():
            return
        prior = ownership.owner_or_none()
        try:
            for user_dir in sorted(root.iterdir()):
                if not user_dir.is_dir() or not user_dir.name.isdigit():
                    continue
                ownership.set_current_owner(int(user_dir.name))
                for thread_id in chat_log.list_thread_ids():
                    run_id = chat_store.active_run_id(thread_id)
                    if not run_id or run_is_live(run_id):
                        continue
                    log.info("thread %s: run %s did not survive; recording it as lost",
                             thread_id, run_id)
                    chat_store.lose_run(thread_id, run_id)
        finally:
            if prior is None:
                ownership.clear_current_owner()
            else:
                ownership.set_current_owner(prior)
    except Exception as e:  # pragma: no cover — never block startup
        log.warning("reconcile_threads failed: %s", e)


def _first_user_text(thread_id: str) -> str:
    t = chat_store.get_thread(thread_id)
    if not t:
        return ""
    for m in t.get("messages", []):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def get_turn(turn_id: str) -> Turn | None:
    return TURNS.get(turn_id)


# How long a stop waits for the run to commit before answering anyway. The kill
# itself is bounded (_retire_turn_proc: ~2s of SIGTERM, then SIGKILL) and the
# drive loop polls at 0.15s, so this is slack, not a budget. Answering late is
# fine; answering before the commit would tell the client the run is over while
# the events endpoint still reports it active.
STOP_WAIT_S = 20.0


async def stop_turn(turn_id: str) -> dict:
    """END a run: signal its process group, commit what it produced, release the
    thread. What the Stop button does, and the only thing that does it.

    ONE PIPELINE. This does not write a message. It kills the process and lets
    the drive loop finish the turn the way every other ending finishes it — final
    drain, segments folded, `chat_store.commit_run` once at the tail — with
    `meta.stopped` naming the operator. A second finalize path here is exactly
    what the rebuild collapsed, and it would lose the partial output on the way.

    IDEMPOTENT. A run that already finished, one this process never held (a
    restart orphan, which the startup reconcile owns), and one another request is
    already stopping all return without raising: `stopped` says whether this call
    found a run to end, and neither answer is an error.

    The check-and-claim is under the thread's WRITE LOCK, like every other run
    transition, and holds no await — so two Stops cannot both claim the kill, and
    a Stop cannot land between a rival send's check and its claim. The kill and
    the wait happen outside the lock: they take seconds, and nothing else on the
    thread should block on them."""
    turn = TURNS.get(turn_id)
    if turn is None:
        return {"turn_id": turn_id, "stopped": False,
                "reason": "no run by that id is going here"}
    if turn.complete:
        return {"turn_id": turn_id, "stopped": False, "reason": "the turn already finished"}
    async with chat_log.lock_for(turn.thread_id):
        if turn.complete:
            return {"turn_id": turn_id, "stopped": False,
                    "reason": "the turn already finished"}
        first = not turn.stop_requested
        turn.stop_requested = True
        # Whoever claims the signal does it; the drive loop takes it instead when
        # the process does not exist yet (the claim window).
        signal_now = first and turn.pid is not None and not turn.stop_signalled
        if signal_now:
            turn.stop_signalled = True
        pid, proc = turn.pid, turn.proc
    if signal_now:
        log.info("turn %s: stopped by the operator; signalling pid %s", turn_id, pid)
        await _retire_turn_proc(pid, proc)
    try:
        await asyncio.wait_for(turn.finished.wait(), STOP_WAIT_S)
    except asyncio.TimeoutError:
        # The commit is still coming (the loop owns it and always runs). Say so
        # rather than claiming an ending that has not been written yet.
        log.warning("turn %s: stop signalled but it has not committed yet", turn_id)
        return {"turn_id": turn_id, "stopped": True, "committed": False,
                "reason": "stopping; it has not finished writing yet"}
    return {"turn_id": turn_id, "stopped": True,
            "committed": turn.committed is not None, "reason": ""}


def inject(turn_id: str, text: str) -> bool:
    """Deliver a message into a turn that is currently running.

    A new message must not abort the work in flight. Writing it to the turn's
    stdin delivers it without doing so.

    WHEN it is acted on is a separate question, and the answer is not the one
    this docstring used to give. Measured against the CLI (see
    docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md §2): the
    model surfaces the message but addresses it at the END of its current plan,
    skipping the step boundaries in between. The turn contract now asks for the
    next boundary instead; `--replay-user-messages` is what tells us it was read
    at all.

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
