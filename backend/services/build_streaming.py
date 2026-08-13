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
import re
import select
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from services import (agent_slots, build_agents, chat_attachments, chat_drain,
                      chat_log, chat_queue, chat_store, llm_router, model_caps,
                      ownership, turn_end)

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


def _user_message_line(text: str, blocks: list[dict] | None = None) -> str:
    """One stream-json user message, as the CLI expects it on stdin.

    `blocks` are attachment content blocks (image / document / text) and go
    BEFORE the operator's text, the same order as attaching a file above what
    you wrote about it. Verified against the CLI on 2026-08-11: it accepts and
    replays native image and document blocks on this channel, and the model
    reads them with no tool call. See services/chat_attachments."""
    content: list[dict] = [*(blocks or []), {"type": "text", "text": text}]
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": content},
    }) + "\n"


def _initialize_line() -> str:
    """The control-protocol handshake the CLI expects before it will use the
    stdio permission channel.

    Verified against CLI 2.1.143: with `--permission-prompt-tool stdio` AND this
    frame sent first, a gated tool call arrives as a `can_use_tool`
    control_request and an approval written back lets it through. Without the
    flag the CLI never asks and silently auto-denies instead. Whether the
    handshake alone is sufficient was not isolated — this is the combination
    that was measured, so it is the combination that is sent."""
    return json.dumps({
        "type": "control_request",
        "request_id": "init-1",
        "request": {"subtype": "initialize", "hooks": None},
    }) + "\n"


# One frame is one line of JSON the CLI parses whole. Two writers to the same
# FIFO (a mid-turn message racing a permission answer) or a frame larger than the
# pipe's atomic PIPE_BUF (4096B on Linux) used to interleave or truncate, and the
# CLI's JSON parser then hit end-of-object early ("Expected '}'") and exited 1.
# So: serialise every writer, and write the whole frame before releasing.
_FIFO_WRITE_LOCK = threading.Lock()
# How long to keep trying to hand off one frame before giving up. A healthy CLI
# drains stdin continuously, so this is only ever approached if the reader has
# wedged — in which case failing is better than blocking the request forever.
_FIFO_WRITE_TIMEOUT_S = 10.0


def _write_all(fd: int, data: bytes) -> bool:
    """Write every byte of one frame to a non-blocking fd, looping over short
    writes and waiting for writability on a full pipe. Returns True only if the
    WHOLE frame went out: a partially written frame is the corruption this
    guards against, so a caller that gets False must not treat it as delivered."""
    view = memoryview(data)
    deadline = time.monotonic() + _FIFO_WRITE_TIMEOUT_S
    while view:
        try:
            n = os.write(fd, view)
            view = view[n:]
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                return False
    return True


def _write_line(turn_id: str, line: str) -> bool:
    """Write one raw frame to a running turn's stdin. See write_to_turn for why
    this is non-blocking; see _write_all for why it is written whole under a lock."""
    fifo = _stdin_fifo(turn_id)
    if not fifo.exists():
        return False
    data = line.encode()
    with _FIFO_WRITE_LOCK:
        fd = None
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            return _write_all(fd, data)
        except OSError as e:
            log.debug("turn %s: control write failed (%s)", turn_id, e)
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def answer_permission(turn_id: str, request_id: str, allow: bool,
                      updated_input: dict | None = None,
                      message: str | None = None) -> bool:
    """Answer one `can_use_tool` request. True if the frame reached the process.

    The turn is BLOCKED until this lands: the CLI is waiting on stdin and there
    is no wall clock on a chat turn, so an unanswered request waits forever. That
    is why every path that ends a turn resolves what is still pending."""
    response: dict = (
        {"behavior": "allow", "updatedInput": updated_input or {}}
        if allow else
        {"behavior": "deny", "message": message or "Denied by the operator."}
    )
    line = json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": response},
    }) + "\n"
    return _write_line(turn_id, line)


def write_to_turn(turn_id: str, text: str, blocks: list[dict] | None = None,
                  origin: list[dict] | None = None) -> bool:
    """Hand a message to a RUNNING turn. True if it was written.

    `origin` is the operator send(s) this delivery stands for — text, client id
    and send time, in the shape the thread queue holds. It is what goes BACK on
    the queue if the CLI never reads this write. Omit it and the delivery is
    still counted (the driver needs that either way), it simply has nothing to
    recover.

    Non-blocking on purpose: opening a FIFO for writing blocks until a reader is
    present, and if the turn has already ended there is no reader and this would
    hang the request forever. O_NONBLOCK turns that case into ENXIO, which is
    the honest answer — "that turn is gone, queue it as the next one" — and the
    caller falls back to the queue."""
    fifo = _stdin_fifo(turn_id)
    if not fifo.exists():
        return False
    # This is the path a pasted message travels, so it is the one that most often
    # carries a frame past PIPE_BUF. Same whole-frame, serialised write as every
    # other frame (see _write_all): a truncated user message was the "Expected
    # '}'" crash the operator hit on a long paste.
    data = _user_message_line(text, blocks).encode()
    with _FIFO_WRITE_LOCK:
        fd = None
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            if not _write_all(fd, data):
                return False
            # COUNTED ONLY ON A COMPLETE WRITE. From here the CLI owes this turn
            # one more result, and the driver must not retire the process until
            # it has one. A partial or refused write reached nothing and is owed
            # nothing. See services/turn_end.py.
            t = TURNS.get(turn_id)
            if t is not None:
                t.delivered += 1
                t.unread.append({"origin": list(origin or [])})
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
    # Messages that arrived BEFORE this turn had emitted anything, by cut token.
    # Every other arrival is recorded as `cut_after` on the segment it followed;
    # this is the one position that has no segment to hang on, and it needs a
    # name of its own rather than the integer 0 standing in for it. See
    # docs/decisions/0018.
    cut_head: list[str] = field(default_factory=list)
    # Can a message be handed to this turn while it runs? False for two kinds:
    #
    #   A SLASH COMMAND (`/compact`, `/clear`, `/model`) runs, reports, and exits
    #   without ever reading its input channel. On 2026-08-10 the operator sent
    #   `/compact`, typed a message 41s later, and it was filed against the
    #   compact run and swallowed whole; he had to send it again 22 minutes on.
    #
    #   A VOICE TURN is driven by voice_worker, not by this module's stdin FIFO.
    #   It owns the thread while it speaks, and the way to add something to it is
    #   to say it, not to type into a channel it does not read.
    #
    # One flag rather than one per subsystem: every caller asks the same question
    # ("may I deliver this?") and the answer must not depend on which reason
    # applies. `inject` refuses on it, and the client is told so it can queue.
    accepts_input: bool = True
    # A slash command was INJECTED into this turn after it started. The commit
    # path derives "was this a command turn?" from the turn's original prompt,
    # which cannot see a command delivered mid-flight. The CLI runs a slash
    # command locally and never calls the model, so such a turn legitimately
    # produces no text — and without this flag it reports "Nothing ran this
    # turn" about a command that did run.
    injected_command: bool = False
    # HOW MANY MESSAGES WE HAVE HANDED THIS TURN'S PROCESS: its own prompt, plus
    # every injection and every queued message drained into it. The driver
    # compares it against the CLI's own echoes to know whether a `result` line
    # means the turn is finished or only that ONE of the prompts in flight is.
    # See services/turn_end.py — retiring the process on the wrong result is what
    # killed the request that was answering the operator's message.
    delivered: int = 0
    # THE MESSAGES BEHIND THAT COUNT, oldest first, until the CLI echoes each
    # one back. Whatever is still here when the turn ends was written to a
    # process that never read it, and is put back on the thread's queue rather
    # than lost (`_requeue_unread`). Each entry carries the ORIGINAL sends it
    # stands for, so a requeue reuses their client ids and send times: the log
    # is idempotent on client id, so the message is re-sent without being
    # written to the transcript twice.
    unread: list[dict] = field(default_factory=list)
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
    # Open TOP-LEVEL tool segments by their tool_use id, so the matching
    # tool_result can close them with an outcome and a duration. Without this a
    # tool segment was write-once: no status, no timing, no way to tell a command
    # that failed from one that succeeded, which is why every step in a settled
    # turn rendered identically. Same rebuilt-from-journal lifetime as
    # `agent_segs`. See docs/decisions/0015 §5.
    tool_segs: dict[str, dict] = field(default_factory=dict)
    # Permission requests already answered, by request_id -> "allowed"/"denied".
    # Seeded from the turn's meta on re-attach, because the drive loop re-reads
    # the journal from offset 0 and would otherwise rebuild every answered
    # approval as pending — showing live buttons on a decision already made.
    answers: dict[str, str] = field(default_factory=dict)
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
    # Wall-clock at which this turn finished, or None while it is running. The
    # ONE thing that makes the registry prunable: `complete` says a turn is over
    # but not how long ago, so nothing could tell a turn that just ended (a
    # client may still be reading it) from one that ended last Tuesday. Set in
    # `_finalize_turn`, which is the single place a turn ends.
    finished_at: float | None = None


# In-memory turn registry. Build sessions are short-lived enough that this is
# acceptable; a future upgrade can spill to disk for cross-process resume.
TURNS: dict[str, Turn] = {}

# How long a finished turn stays reachable by id. It holds its full event list
# and segment tree, so without this the registry grew for the life of the
# process: every turn ever run, with everything it ever emitted, until a restart.
# Same window and the same reasoning as voice_worker's reaper.
FINISHED_TURN_TTL_S = 600


def _prune_turns(now: float | None = None) -> int:
    """Drop finished turns that nothing can still be watching. Returns how many.

    Called when a turn is created rather than from a timer, because a turn is
    the only thing that puts an entry in here: the registry cannot grow while
    nothing is happening, so it does not need sweeping while nothing is
    happening either. That keeps this to one function with no lifecycle.

    Safe by construction: a live SSE reader holds its own reference to the Turn
    object, so removing the dict entry never interrupts one in flight, and only
    a turn that finished more than the TTL ago qualifies. `run_is_live` already
    answers False for anything complete, so no caller's answer changes."""
    cutoff = (now or time.time()) - FINISHED_TURN_TTL_S
    stale = [tid for tid, t in TURNS.items()
             if t.finished_at is not None and t.finished_at < cutoff]
    for tid in stale:
        del TURNS[tid]
    if stale:
        log.debug("pruned %d finished turn(s) from the registry", len(stale))
    return len(stale)


class RunInFlight(RuntimeError):
    """A run is already open on this thread; join it instead of starting a rival.

    Carries the id of the run that owns the thread. Raised by `start_turn` when
    it claims for itself and loses the race, which is the backstop for two POSTs
    landing in the same event-loop tick."""

    def __init__(self, run_id: str):
        super().__init__(f"a run is already in flight on this thread: {run_id}")
        self.run_id = run_id


def _make_turn(thread_id: str, agent_slug: str, turn_id: str | None = None,
               accepts_input: bool = True) -> Turn:
    # The registry only grows here, so this is the only place it has to shrink.
    _prune_turns()
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
    # Only ever narrows. A turn that cannot be spoken to is decided at claim
    # time, and the spawn path re-makes the same object without knowing why.
    if not accepts_input:
        t.accepts_input = False
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


def run_accepts_input(run_id: str | None) -> bool:
    """May a message be delivered into the run holding this thread?

    True for a run this process is not driving at all: nothing is known against
    it, and the injection path has its own answer for that case. See
    Turn.accepts_input for the two kinds that say no."""
    t = TURNS.get(run_id or "")
    if t is None or t.complete:
        return True
    return t.accepts_input


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
                    agent: str | None = None, force: bool = False,
                    accepts_input: bool = True, announce: bool = True) -> str | None:
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
        chat_store.begin_run(thread_id, run_id, model=model, agent=agent,
                             announce=announce)
        # Register the run BEFORE returning. Prompt assembly awaits, and until
        # the run is in TURNS a message arriving during that window would see an
        # `active_run_id` naming a run nothing is driving and take the thread
        # over — starting the rival turn the claim exists to prevent.
        _make_turn(thread_id, "", turn_id=run_id, accepts_input=accepts_input)
        return None


def release_external_run(thread_id: str, run_id: str) -> None:
    """Give the thread back after a run this module did not drive.

    Voice turns claim a thread so the rest of the system can see them — "a run in
    flight is always visible" was simply false for voice, and a message typed
    during a spoken turn used to open a second, rival turn on the same session.
    They persist their own reply, so there is nothing here to commit; the claim
    just has to end, and the registry entry has to become prunable."""
    t = TURNS.get(run_id)
    if t is not None:
        t.complete = True
        t.finished_at = time.time()
    try:
        chat_store.release_run(thread_id, run_id)
    except Exception:  # pragma: no cover — the reconcile owns whatever is left
        log.debug("could not release external run %s on %s", run_id, thread_id)
    # A voice turn ending leaves the thread idle exactly as a text turn does, and
    # a message typed during one is queued behind it in the same way.
    chat_drain.drain_soon(thread_id, reason="external run released")


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


# The CLI's own no-op placeholder, and the ONLY synthetic message we drop.
#
# `model: "<synthetic>"` marks a message the CLI wrote itself: the model was
# never called (zero tokens, stop_reason "stop_sequence").
#
# IT IS EMITTED AT THE START OF A TURN, NOT THE END. This comment used to say
# the opposite ("the CLI emits one to CLOSE a turn it decided needs no reply"),
# and that reading is what made empty replies look like the placeholder's fault.
# Re-measured 2026-08-12 across 104 occurrences in the CLI session journals
# (2.1.185 through 2.1.228): it is RESUME REPAIR. On `--resume`, when the
# transcript's last record is an unanswered user message (an interrupt, a
# dangling tool_result, a session-init marker), the CLI injects the meta prompt
# "Continue from where you left off." and synthesizes this assistant slot to
# restore role alternation, THEN delivers the real prompt — which carries the
# same promptId as the meta one. In 102 of the 104 cases a real assistant reply
# follows immediately. Dropping it is correct and is NOT why a turn comes back
# empty; the real causes are locally-run slash commands and operator stops.
#
# DO NOT widen this to "drop synthetic messages". The same channel carries the
# things the operator most needs: "You've hit your session limit · resets
# 9:40pm" (260+ occurrences), "OAuth access token has been revoked",
# "API Error: 529 Overloaded", "Not logged in · Please run /login". Dropping
# those would turn a rate limit into a chat that silently stops answering,
# which is the exact failure the honesty work exists to remove. Match the one
# dead string, let every other synthetic message through untouched.
_NO_OP_PLACEHOLDER = "No response requested."


def _is_no_op_placeholder(msg: dict) -> bool:
    """True only for the CLI's empty-turn filler described above."""
    if msg.get("model") != "<synthetic>":
        return False
    blocks = msg.get("content") or []
    if not isinstance(blocks, list):
        return False
    texts, others = [], 0
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            texts.append((b.get("text") or "").strip())
        else:
            others += 1
    # A placeholder carries exactly the one string and nothing else. Anything
    # with a tool call, or any other prose, is a real message.
    return others == 0 and texts == [_NO_OP_PLACEHOLDER]


# CLI system confirmations rewritten into this app's own copy.
#
# These are fixed strings the CLI emits for an action the operator took HERE, so
# they render as our system messages and are held to our copy rules: no
# em-dashes, no padding, say what happened and stop. The CLI's compact line is
# 117 characters to say one thing, and it ends by telling the operator to
# "continue where you left off", which is advice nobody asked for.
#
# Keyed on the exact string. An unrecognised message is passed through
# unchanged: guessing at CLI text we have not seen would be worse than a
# slightly long sentence. Not a general rewriter, and it must not become one.
_CLI_COPY: dict[str, str] = {
    "Conversation compacted — earlier messages were summarized to free up the "
    "context window. Continue where you left off.":
        "Compacted. Earlier messages are now a summary.",
}


def _rewrite_cli_copy(text: str) -> str:
    """Our wording for a known CLI confirmation, else the text unchanged."""
    return _CLI_COPY.get(text.strip(), text)


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
    # The turn is ASKING. Only reachable in a mode that can stop and ask, and
    # only when the stdio permission channel was wired up at spawn. The process
    # is blocked on stdin from here until answer_permission writes back, so this
    # event has to reach the operator: it is the turn's only way out.
    if t == "control_request":
        req = ev.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            return ("meta", ev)
        return ("permission", {
            "id": ev.get("request_id") or "",
            "tool": req.get("tool_name") or "?",
            "label": req.get("display_name") or req.get("tool_name") or "?",
            # One line naming the target ("hello.txt"), the CLI's own summary.
            "detail": req.get("description") or "",
            "input": req.get("input") or {},
            # e.g. [{"type":"setMode","mode":"acceptEdits","destination":"session"}]
            # — "allow and stop asking", in the vendor's own words.
            "suggestions": req.get("permission_suggestions") or [],
            "tool_use_id": req.get("tool_use_id") or "",
        })
    # Our own answer, echoed. Nothing to render: the segment already moved when
    # the answer was written.
    if t == "control_response":
        return ("meta", ev)
    # (see _is_no_op_placeholder, applied to `assistant` below)
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
        if _is_no_op_placeholder(msg):
            return ("meta", ev)
        out: list[tuple[str, dict]] = []
        for block in msg.get("content", []) or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    out.append(("text", {
                        "content": _rewrite_cli_copy(block.get("text", "")),
                        "parent": parent,
                    }))
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
                # Whether the call FAILED. The one field the disclosure state
                # machine turns on: a failed step never merges and never folds.
                "is_error": bool(b0.get("is_error")),
                "parent": parent,
            })
        return ("tool_result", {"tool_use_id": None, "text": "", "is_error": False,
                                "parent": parent})
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


def unit_count(segments: list[dict]) -> int:
    """How many RENDERED UNITS a segment list holds: every top-level segment,
    plus every step nested inside a subagent.

    This is the cut coordinate for a mid-turn message, and counting top-level
    segments alone is why the placement broke. A delegation is ONE top-level
    segment that grows internally for as long as it runs, so the top-level count
    freezes for the whole delegation. Measured on the operator's own thread: one
    reply had 128 top-level segments and 672 nested steps, one chip holding 175
    of them, and two messages sent minutes apart both recorded index 42. Counting
    units, the coordinate advances with every step a delegate takes."""
    n = 0
    for seg in segments:
        n += 1
        if seg.get("t") == "agent":
            n += len(seg.get("steps") or ())
    return n


def seal_open_text(turn: "Turn") -> None:
    """Close every open text run, so nothing written from here on merges
    BACKWARDS into text that was already on screen.

    The counter alone does not fix the placement. Streamed text is appended to
    the trailing text segment rather than starting a new one, so a message that
    landed after "A" and before "B" still rendered below "AB": the two are one
    segment by the time the reply is cut. Sealing makes the boundary real. Every
    open subagent gets the same treatment, because a chip cut in half has a text
    step on each side of the cut."""
    if turn.segments and turn.segments[-1].get("t") == "text":
        turn.segments[-1]["sealed"] = True
    for host in turn.agent_segs.values():
        steps = host.get("steps") or ()
        if steps and steps[-1].get("t") == "text":
            steps[-1]["sealed"] = True


def cut_here(turn: "Turn", ref: str) -> int:
    """Record WHERE a mid-turn message landed, as a position rather than a count.

    `ref` is the message's cut token. It is written onto the segment the message
    arrived after, or onto `cut_head` when the turn has emitted nothing yet. The
    client splits the reply on those tokens and does no arithmetic.

    A POSITION CANNOT DRIFT AND A COUNT CAN, which is the whole reason this
    changed. `at`, still returned and still stored on the message, is a unit
    index into a buffer that keeps growing: when two subagents run at once (this
    system launches parallel delegates routinely) and the FIRST one gains steps
    after the message arrives, the recorded index slides into it, snaps to its
    end, and the second delegation falls BELOW a message that was typed after it
    was launched. Measured 2026-08-12. `cut_after` is immune: "after this
    segment" stays true however much anything else grows.

    One call, because the three parts are one fact. Sealing open text is what
    stops the next character merging backwards into text that was already on
    screen, and it has to happen in the same breath as recording the position or
    there is a window between them in which the anchor moves. See
    docs/decisions/0018."""
    at = unit_count(turn.segments)
    seal_open_text(turn)
    if turn.segments:
        turn.segments[-1].setdefault("cut_after", []).append(ref)
    else:
        turn.cut_head.append(ref)
    # AND ON THE WIRE, because the tab that is streaming builds its own copy of
    # the segments from these events and never sees the server's list until the
    # commit. Without this the live view would have to fall back to the index
    # for exactly the case the operator watches happen: the message he just
    # sent, into the turn he is watching run.
    _push(turn, "cut", {"ref": ref})
    return at


def carry_cuts(dropped: list[dict], survivors: list[dict], head: list[str]) -> None:
    """Move any recorded cut positions off segments that are being removed.

    A CUT LIVES ON A SEGMENT, SO A SEGMENT CARRYING ONE MAY NOT BE DROPPED OR
    REPLACED WITHOUT CARRYING IT FORWARD. Two paths rewrite a turn's segment
    list after the fact (the trailing-text replacement below, and the `/compact`
    text strip), and this is the invariant that keeps them honest. It is the way
    decision 0018 would silently rot, so it is one shared function rather than
    two open-coded loops.

    Cuts land on the last survivor, or on `head` if nothing survives. Both are
    the nearest true position once the segment they named no longer exists."""
    carried = [r for s in dropped if isinstance(s, dict) for r in (s.get("cut_after") or ())]
    if not carried:
        return
    if survivors:
        survivors[-1].setdefault("cut_after", []).extend(carried)
    else:
        head.extend(carried)


def _seg_text(turn: "Turn", s: str) -> None:
    if not s:
        return
    if turn.segments and turn.segments[-1].get("t") == "text" \
            and not turn.segments[-1].get("sealed"):
        turn.segments[-1]["text"] += s
    else:
        turn.segments.append({"t": "text", "text": s})


def _seg_tool(turn: "Turn", name, inp, tool_id: str = "") -> None:
    """One tool call, OPEN until its result lands.

    `id` and `at` are what make a step closable: the matching tool_result sets
    `ok` and `ms` on this same dict (see `_close_tool`). A segment recorded
    before this existed has neither, and renders as it always did.
    """
    seg = {"t": "tool", "name": name or "?", "input": inp or ""}
    if tool_id:
        seg["id"] = tool_id
        seg["at"] = time.time()
    turn.segments.append(seg)
    if tool_id:
        turn.tool_segs[tool_id] = seg


def _close_tool(turn: "Turn", tool_id: str, text: str, is_error: bool) -> bool:
    """Close a top-level tool segment with its outcome and how long it took.

    A FAILED step is never merged into a group and never collapses, so `ok` is
    the single field the whole disclosure state machine turns on. `ms` is
    dropped when the result arrives without a recorded start (a journal tailed
    from mid-stream), rather than being invented as zero.
    """
    seg = turn.tool_segs.pop(tool_id, None)
    if seg is None:
        return False
    seg["ok"] = not is_error
    started = seg.pop("at", None)
    if isinstance(started, (int, float)):
        seg["ms"] = int(max(0.0, time.time() - started) * 1000)
    if is_error and text:
        # Only the failure text is kept. A successful result is plumbing the
        # operator already sees the effect of, and storing every one of them
        # would put the whole tool output history in the message.
        seg["error"] = text if len(text) <= 600 else text[:597] + "…"
    return True


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
        # Empty while it runs and after it reports back normally. Set only when
        # the turn ended before the report arrived (`_close_open_agents`), so a
        # chip that says nothing here is a delegation that actually finished.
        "outcome": "",
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
    if seg.get("t") == "text" and steps and steps[-1].get("t") == "text" \
            and not steps[-1].get("sealed"):
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
        tool_id = payload.get("id") or ""
        if nested and _agent_step(turn, nested, step):
            # A DELEGATE'S STEP CLOSES THE SAME WAY A TOP-LEVEL ONE DOES.
            # It used to be appended bare, with no id and no start, so
            # `_close_tool` could never find it: every step inside an opened
            # chip carried no outcome, and an absent outcome reads as
            # succeeded. A delegate whose search failed looked like one that
            # had not. tool_use_id is unique across the whole turn, so
            # registering nested steps in the same map cannot collide.
            if tool_id:
                step["id"] = tool_id
                step["at"] = time.time()
                turn.tool_segs[tool_id] = step
            return True
        if payload.get("agent"):
            _seg_agent(turn, payload)
        else:
            _seg_tool(turn, payload.get("name"), payload.get("input"), tool_id)
        return True
    if kind == "permission":
        # An approval is a segment, not a transient event: the turn is blocked on
        # it, so it has to survive a reload of the page that is supposed to
        # answer it. `state` moves to allowed/denied when answered and to stale
        # if the turn ends without an answer.
        rid = payload.get("id") or ""
        for seg in turn.segments:
            if seg.get("t") == "approval" and seg.get("id") == rid:
                return True          # re-read of the journal, not a second ask
        turn.segments.append({
            "t": "approval",
            "id": rid,
            "tool": payload.get("tool") or "?",
            "label": payload.get("label") or "?",
            "detail": payload.get("detail") or "",
            "input": payload.get("input") or {},
            "suggestions": payload.get("suggestions") or [],
            "state": turn.answers.get(rid, "pending"),
        })
        return True
    if kind == "tool_result":
        # The Agent tool's own result closes its segment. A nested result's TEXT
        # is the subagent's internal plumbing and is still not rendered (its
        # report comes back in this one), but it does close the nested step
        # below, which is where that step's outcome and duration come from.
        rid = payload.get("tool_use_id") or ""
        host = turn.agent_segs.get(rid)
        if host is not None:
            host["done"] = True
            host["result"] = payload.get("text") or ""
            return True
        # Every OTHER tool result closes its own step. Not rendered as content:
        # what it sets is the step's outcome and duration, which is what decides
        # whether the step merges into a group, collapses, or stays open in red.
        if rid and _close_tool(turn, rid, payload.get("text") or "",
                               bool(payload.get("is_error"))):
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


# Lines the CLI's runtime writes that say nothing about why a turn failed.
# Matched at the START of a stripped line so a real message that happens to
# mention "warning" is never dropped.
_STDERR_NOISE = (
    "(node:",
    "warning:",
    "experimentalwarning",
    "deprecationwarning",
    "(use `node ",
    "debugger attached",
    "waiting for the debugger",
)

# How much of the tail to show. Long enough for a stack-ish message, short
# enough that it stays a chat message rather than becoming a log dump.
_STDERR_TAIL_CHARS = 600


def _stderr_tail(turn_id: str, limit: int = _STDERR_TAIL_CHARS) -> str:
    """The last useful thing the CLI wrote to stderr for this turn, or "".

    WHY THIS IS READ AT ALL. The child's stderr has always been captured to
    `stream.err` and nothing ever opened it. So when the CLI failed — an expired
    token being the case that actually bit — the operator got
    "Ended unexpectedly (exit 1)" while the sentence explaining it sat on disk,
    unread, three directories down. The turn knew; the chat did not.

    WHY THE TEXT IS PASSED THROUGH RATHER THAN CLASSIFIED. The obvious design is
    a table mapping known CLI phrasings to friendly messages. That table cannot
    be written honestly here: reproducing an auth failure on this host is not
    possible without logging the operator out of the CLI their system runs on,
    so every entry in it would be a guess at a vendor string. A guessed mapping
    fails in the worst direction — it either matches nothing (and adds nothing)
    or matches wrongly (and replaces the real reason with a confident wrong
    one). The CLI's own words are the thing that has actual information in it,
    so they are shown, and the only interpretation applied is dropping runtime
    noise and marking whether the message looks credential-related.

    Reads the tail only: a turn that spewed megabytes of stderr must not be read
    into memory to render one line.
    """
    p = _journal_dir(turn_id) / "stream.err"
    try:
        size = p.stat().st_size
        if not size:
            return ""
        with open(p, "rb") as fh:
            # 4x the display budget so there is room to drop noise lines and
            # still have something left.
            if size > limit * 4:
                fh.seek(size - limit * 4)
            raw = fh.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        low = s.lower()
        if any(low.startswith(n) for n in _STDERR_NOISE):
            continue
        lines.append(s)
    if not lines:
        return ""
    out = " ".join(lines)
    if len(out) > limit:
        # Keep the END: a failure message is written after whatever preceded it.
        out = "…" + out[-limit:]
    return out


# Words that make a failure look like a credential problem rather than a crash.
# Used ONLY to add a pointer to the settings screen, never to replace the CLI's
# own text, so a false positive costs one extra sentence and a false negative
# costs nothing.
_AUTH_HINTS = ("authentication", "unauthorized", "401", "403", "invalid api key",
               "invalid_api_key", "oauth", "credential", "not logged in",
               "log in", "login", "expired token", "token has expired",
               "permission_error", "authentication_error")


def _looks_like_auth_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _AUTH_HINTS)


# The repo root, resolved from this file so it carries no machine-identifying
# literal (check_no_hardcoding fails on those). Everything the operator works on
# lives under it; anything absolute outside it is system detail a chat has no
# business showing.
_WORKSHOP_ROOT = str(Path(__file__).resolve().parents[2])
# Any absolute filesystem path that survives the workshop-root rewrite points
# OUTSIDE the project (a home dir, a config or secrets path, a system dir), which
# a chat has no business revealing. Redact it whole rather than show it relative.
_ABS_PATH_RE = re.compile(r"/(?:home|Users|root|etc|var|opt|usr|tmp|private|Library|mnt|srv)/[^\s\"'):,]*")


def _scrub_paths(text: str) -> str:
    """Redact filesystem detail from a message before it reaches the chat.

    A user-facing error must never expose where on the host things live. A path
    inside workshop is shown project-relative (the root prefix dropped); anything
    ABOVE workshop is not the operator's project and is redacted to `[path]`.
    Operator directive 2026-08-12: in-app errors stay scoped to the project, and
    no chat scope has any business knowing the system outside workshop."""
    if not text:
        return text
    text = text.replace(_WORKSHOP_ROOT + "/", "").replace(_WORKSHOP_ROOT, "")
    return _ABS_PATH_RE.sub("[path]", text)


# ---- Memory ceiling for a turn -------------------------------------------
#
# WHY A WATCHDOG AND NOT AN RLIMIT OR A CGROUP.
#
# RLIMIT_AS is the obvious answer and it is wrong here: the CLI is Node, and V8
# reserves enormous VIRTUAL regions it never faults in, so an address-space cap
# that looks generous kills a perfectly ordinary turn. MemoryMax on
# workshop-backend.service is worse: the turns are children in the backend's own
# cgroup, so a runaway turn would OOM-kill THE BACKEND and take the whole app
# down with it. A transient systemd scope per turn would work but puts
# systemd-run in the path of every spawn, and these turns are deliberately
# detached (start_new_session) so they survive a backend restart.
#
# So: watch, and kill only the turn. It cannot break a normal turn (it does
# nothing below the ceiling), it kills the process GROUP so a turn's subagents
# count toward its own budget, and unlike an OOM kill it can say what happened.
#
# THE DEFAULT IS DELIBERATELY GENEROUS, and it is measured against the GROUP.
# Measured 2026-08-12 on this box (62 GB RAM): an individual CLI process sits at
# 250-600 MB, but a turn's whole group — the CLI plus its node children, tools
# and subagents, which is what this counts — reached 1.2 GB. 6 GB is ~5x that
# busiest observed group: high enough that no real turn, even one running
# several subagents, should reach it; low enough that a genuine runaway (tens of
# GB) is stopped long before the box is. Even four concurrent turns at the
# ceiling fit. This is a runaway guard, not a budget. Set
# WORKSHOP_TURN_MEMORY_MAX_MB=0 to disable it entirely.
_TURN_MEMORY_MAX_MB = int(os.environ.get("WORKSHOP_TURN_MEMORY_MAX_MB", "6144") or 0)
# Polling this is a /proc scan, so it is throttled well below the 0.15s
# supervision tick. A turn that balloons takes seconds to do it, not one tick.
_MEMORY_CHECK_EVERY_S = 5.0
_PAGE_MB = os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


def _pgid_rss_mb(pgid: int) -> float:
    """Resident MB across every process in `pgid`, the turn's process group.

    The group, not the pid: a turn's subagents and any tool it shells out to are
    its own memory as far as the box is concerned, and charging them to the turn
    is what makes the ceiling mean anything. Returns 0.0 if nothing matches (the
    group is already gone), so a missing group never trips the ceiling."""
    total_pages = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0.0
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                data = fh.read()
            # comm (field 2) is parenthesised and may contain spaces, so split
            # after the LAST ')'. rest[0] is field 3, hence pgrp=rest[2],
            # rss=rest[21] (fields 5 and 24).
            rest = data[data.rfind(b")") + 2:].split()
            if int(rest[2]) != pgid:
                continue
            total_pages += int(rest[21])
        except (OSError, IndexError, ValueError):
            continue   # the process exited mid-read, or /proc raced us
    return total_pages * _PAGE_MB


def _did_visible_work(segments: list[dict]) -> bool:
    """True if a turn produced anything the operator can actually see above the
    reply. A tool call or a subagent counts; so does non-empty interim text. A
    pending approval or a whitespace-only text segment does not."""
    for s in segments or []:
        if not isinstance(s, dict):
            continue
        t = s.get("t")
        if t in ("tool", "agent"):
            return True
        if t == "text" and (s.get("text") or "").strip():
            return True
    return False


def _drop_text_segments(segments: list[dict],
                        cut_head: list[str] | None = None) -> list[dict]:
    """The same turn with the model's prose removed and its work left alone.

    For the one turn whose prose is replaced wholesale rather than shown: an
    explicit `/compact`. Rewriting the committed `text` was not enough, because
    the transcript RENDERS `meta.segments` and only falls back to `text` when
    there are none. So the "## Context Usage" table went on being drawn from the
    segment it was streamed into, under a one-line confirmation that had already
    replaced it. The operator reported the table a third time on 2026-08-12 for
    exactly this reason.

    Tool and agent segments survive on purpose. `/compact` is a local command
    and never has any, but dropping work that did happen would be a worse lie
    than the dump this removes.

    `cut_head` is threaded through because a dropped text segment may have been
    the anchor for a mid-run message. A command turn refuses input, so it should
    never have one, but "should never" is what the invariant in carry_cuts
    exists to stop relying on."""
    kept = [s for s in segments or []
            if not (isinstance(s, dict) and s.get("t") == "text")]
    dropped = [s for s in segments or []
               if isinstance(s, dict) and s.get("t") == "text"]
    carry_cuts(dropped, kept, cut_head if cut_head is not None else [])
    return kept


def _clean_exit_text(segments: list[dict], *, unread: bool = False) -> str:
    """What to say for a turn that exited 0 and wrote no closing message.

    WHICH of the three cases this is matters, and saying the wrong one is what
    the operator kept reporting:

      never read it  -> we handed the message to the process and it never
                        acknowledged reading it. The model was not given the
                        message at all, so nothing about the answer is the
                        model's doing. This is the honest thing to say, and it
                        is the one case where sending it again is the remedy.
      it did work    -> the tools and subagents above ARE the result, and
                        pointing at them is true and useful.
      it did nothing -> there is nothing above to point at, so
                        "the steps above are the result" is a sentence about an
                        empty bubble.

    THESE USED TO FIRE FOR A REASON THAT WAS OURS. Both of the last two were the
    driver killing the CLI while it was answering — see services/turn_end.py,
    where that is now prevented. What is left is the genuine article: a turn
    whose model really did produce nothing.

    Say what actually happened, and nothing more (design rule 11: no advice
    nobody asked for, so this does not close by telling them to try again)."""
    if unread and not _did_visible_work(segments):
        return "Ended before this message reached the model. Nothing ran."
    if _did_visible_work(segments):
        return "Finished with no closing message. The steps above are the result."
    return "Ended without a reply. Nothing ran this turn."


def _failure_text(rc: int, turn_error: str, stderr_tail: str) -> str:
    """The message shown for a turn that produced nothing and failed.

    `turn_error` wins over `stderr_tail` because this module only sets it when
    THIS process is what failed (journal open, input channel, missing binary) —
    in those cases it knows the cause exactly and the child either never ran or
    never got to say anything. Whenever the CLI itself is what broke, this
    module has nothing to add and the child's stderr is the only real
    information available. Whatever the source, host paths are scrubbed before it
    is shown (see _scrub_paths)."""
    detail = _scrub_paths((turn_error or "").strip() or (stderr_tail or "").strip())
    if not detail:
        return f"Ended unexpectedly (exit {rc})."
    if _looks_like_auth_failure(detail):
        return (f"Ended unexpectedly (exit {rc}): {detail}\n\n"
                "That reads like a credentials problem. Settings → System shows "
                "whether the Claude and GitHub credentials are still good.")
    return f"Ended unexpectedly (exit {rc}): {detail}"


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
                     mcp_selection: list[str] | None = None,
                     tools_disabled: bool = False,
                     speaker: str | None = None,
                     run_id: str | None = None,
                     effort: str | None = None,
                     permission_mode: str | None = None,
                     attachments: list[dict] | None = None,
                     origin: list[dict] | None = None) -> Turn:
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
    this claims one itself, raising RunInFlight if the thread is busy.

    CHOKEPOINT 3 of the developer-mode gate (ADR 0014). This is the one funnel
    every chat and build turn passes through before the spawn, and the spawn
    runs `claude` with the full agentic toolset at `cwd`. So `cwd` is asked the
    single developer-mode question here: is the platform repo a permitted run
    target? Off, a turn whose cwd is the Workshop's own source tree is refused
    with 403 before anything is claimed or written, which is what stops doors 3,
    4 and 5 of the §2 survey — a `project:` row, a revived build agent, an
    `agent:` maker — from being three separate ways to edit the running app.

    A product capability boundary and a safety interlock, not a security
    boundary: this stops the app being used to rewrite itself, not a person with
    a shell from editing the source."""
    from services import app_runner

    app_runner.assert_run_target(cwd, "a chat turn")
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
        # The operator send(s) this prompt was assembled from: text, client id,
        # send time. Carried so a prompt the CLI never reads can be put back on
        # the thread queue as the messages it came from, rather than as this
        # assembled blob. In meta, so a turn re-attached after a restart can
        # still recover them. See _requeue_unread.
        "origin": list(origin or []),
        # REFS, not bytes. The journal is written to disk and read back on every
        # restart-recovery pass, so a 12MB base64 image in here would be paid for
        # on each one. The blocks are built from the store at delivery instead.
        "attachments": list(attachments or []),
        "timeout_s": timeout_s, "pid": None, "status": "running", "started": turn.started,
        "allowed_tools": allowed_tools, "disallowed_tools": disallowed_tools,
        "mcp_servers": mcp_servers,
        # The chat's own per-thread MCP choice, a FILTER over whatever the mode
        # would otherwise reach. Carried in meta for the same reason `effort` is:
        # a turn respawned after a restart must come back with the same server
        # surface it started with, not silently widen to the default. JSON keeps
        # `null` and `[]` distinct, and they mean different things here — None is
        # "never chosen" (inherit), `[]` is "chosen: none". An older journal
        # entry has no key at all, which reads as None: today's behavior.
        "mcp_selection": mcp_selection,
        "tools_disabled": bool(tools_disabled),
        # None = send no --effort, i.e. the model's own default. Carried in meta
        # so a turn resumed after a restart respawns at the level it was started
        # at rather than silently dropping to the default.
        "effort": effort,
        # How much this turn asks before acting. None = the platform default
        # (bypassPermissions), which is what every chat has always run at.
        "permission_mode": permission_mode,
    }
    _write_meta(meta)
    _drive_task(turn, meta, _spawn_and_drive(turn, meta))
    return turn


def _build_cmd(meta: dict) -> tuple[list[str], dict]:
    """The claude argv for one turn, plus any env the tool surface needs.

    Pure and total so the one coupling that cannot be got wrong — a permission
    mode that can ask, sent without the channel to ask on — is testable without
    spawning anything (tests/test_permission_channel.py)."""
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
    #
    # A full agentic turn reaches tool_flags too when the chat has made an MCP
    # choice (`mcp_selection is not None`). Without that it fell straight to the
    # bare `--permission-mode` below and emitted no `--strict-mcp-config` at all,
    # so the CLI inherited every server in `~/.claude.json` — Plaid, Google,
    # Discord, all on the operator's credentials — no matter what the chat had
    # selected. The per-thread MCP picker was therefore honored on the narrow
    # path and silently ignored on exactly the path with the widest tools.
    tool_env: dict = {}
    mode = meta.get("permission_mode") or model_caps.DEFAULT_PERMISSION_MODE
    if (meta.get("allowed_tools") is not None or meta.get("tools_disabled")
            or meta.get("mcp_selection") is not None):
        _tflags, tool_env, _block = llm_router.tool_flags(
            tools_disabled=bool(meta.get("tools_disabled")),
            allowed_tools=meta.get("allowed_tools"),
            disallowed_tools=meta.get("disallowed_tools"),
            mcp_servers=meta.get("mcp_servers"),
            permission_mode=mode,
            mcp_selection=meta.get("mcp_selection"),
            cwd=meta.get("cwd") or "",
        )
        cmd.extend(_tflags)
        # tool_flags' full-agentic branch returns the strict MCP flags ONLY — it
        # documents that the caller owns the permission mode there (call_cloud
        # keeps the CLI default; this path needs bypass, headless with no
        # approver). The narrow and tools-off branches emit their own, so adding
        # it unconditionally would duplicate the flag.
        if meta.get("allowed_tools") is None and not meta.get("tools_disabled"):
            cmd.extend(["--permission-mode", mode])
    else:
        cmd.extend(["--permission-mode", mode])
    # THE COUPLING THAT MATTERS. Every mode except bypass can stop and ask, and
    # without a channel to ask ON the CLI accepts the mode and then silently
    # auto-denies every gated call: the turn ends "success", the file is never
    # written, and the only trace is a permission_denials array nobody reads.
    # Measured against CLI 2.1.143. Never send one of these without the other.
    if model_caps.needs_approval_channel(mode):
        cmd.extend(["--permission-prompt-tool", "stdio"])
    if meta["session_id"] and meta["resume"]:
        cmd.extend(["--resume", meta["session_id"]])
    elif meta["session_id"]:
        cmd.extend(["--session-id", meta["session_id"]])
    return cmd, tool_env


async def _end_wedged(turn: Turn, meta: dict, detail: str) -> None:
    """End a run whose drive loop raised, instead of leaving it running forever.

    Nothing else was going to end it. `_drive_turn` had no try/finally and its
    task had no exception handler, so ANY raise inside it — an OSError from
    `_drain` when the journal goes away, a TypeError in `_segment_ingest` on a
    payload shape the CLI has not sent before — skipped `_finalize_turn`
    entirely. `turn.complete` and `turn.finished` were never set, `active_run_id`
    was never cleared, and the journal stayed `status: running`. There is no wall
    clock on a chat turn (CHAT_TIMEOUT_S = 0, deliberately), so nothing would
    ever time it out: the chat showed "Still running" with a ticking elapsed
    clock indefinitely, and only a backend restart or another message recovered
    it. The traceback surfaced, if at all, as a garbage-collection-time "Task
    exception was never retrieved".

    THE REPLY KEEPS WHAT IT HAD. Whatever the turn emitted before the crash is
    already in `turn.segments`, so it is salvaged as the answer and a plain
    sentence is appended saying the turn ended early and why. A crash with
    nothing produced falls through to the ordinary "Ended unexpectedly" text,
    which is the honest rendering of that case."""
    if turn.finished.is_set():
        # `_finalize_turn` already ran; its own `finally` guarantees that. The
        # raise happened after the run ended and there is nothing left to close.
        return
    turn.error = turn.error or detail
    _push(turn, "error", {"detail": turn.error})
    salvaged = _turn_content(turn)
    if salvaged:
        turn.segments.append({"t": "text", "text": (
            f"\n\nThis turn ended early: {detail}. "
            "Everything above is what it had produced by then."
        )})
        turn.final_text = _turn_content(turn)
    # Only when there IS a process. `_retire_turn_proc` polls for up to 2s
    # waiting for one to die, and a run that crashed before it spawned would pay
    # that wait for nothing while the operator watches the turn hang.
    if meta.get("pid") or turn.proc is not None:
        try:
            await _retire_turn_proc(meta.get("pid"), turn.proc)
        except Exception as e:  # pragma: no cover — best-effort
            log.debug("turn %s: could not retire process after crash: %s", turn.id, e)
    _cleanup_turn_input(turn.id)
    await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)


def _drive_task(turn: Turn, meta: dict, coro) -> "asyncio.Task":
    """Launch a turn's drive loop as a task that CANNOT wedge the thread.

    Two layers, because they fail differently:

      * the coroutine catches its own exceptions and routes them through
        `_end_wedged`, which is what lets the run commit what it had; and
      * a done-callback backstop releases anyone waiting even if that handling
        itself could not run — a cancellation landing inside `_end_wedged`, or a
        BaseException the `except` above does not cover. It cannot commit
        anything (there is no loop left to await on), so it only unblocks: the
        blocking POST /messages path waits on `turn.finished`, and a request held
        open forever is the worst of the possible endings."""
    async def _guarded() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            # A cancelled turn is an ended turn. Say so and re-raise, so the
            # cancellation is not swallowed from whoever asked for it.
            await _end_wedged(turn, meta, "the turn was cancelled")
            raise
        except Exception as e:
            log.exception("turn %s: drive loop failed", turn.id)
            await _end_wedged(turn, meta, f"{type(e).__name__}: {e}")

    task = asyncio.create_task(_guarded())

    def _backstop(t: "asyncio.Task") -> None:
        if not turn.finished.is_set():
            log.error("turn %s: drive task ended without finalizing (%r)", turn.id,
                      t.exception() if not t.cancelled() else "cancelled")
            turn.complete = True
            turn.new_event.set()
            turn.finished.set()

    task.add_done_callback(_backstop)
    return task


async def _spawn_and_drive(turn: Turn, meta: dict) -> None:
    """Spawn the turn's CLI process and drive it to completion.

    The whole body holds a slot from the turn pool (services/agent_slots), taken
    before the spawn and released when the run finishes, so a fan-out cannot put
    an unbounded number of CLI processes on the host. The limit is set well above
    normal interactive use; reaching it means something is looping, not that the
    operator is busy.
    """
    async with agent_slots.turn_slot():
        await _spawn_and_drive_inner(turn, meta)


async def _spawn_and_drive_inner(turn: Turn, meta: dict) -> None:
    # The prompt goes in over stdin as a stream-json message rather than as an
    # argv positional, because stdin then STAYS open for the rest of the run and
    # anything else the operator sends lands at the next step boundary. (It also
    # takes the assembled prompt off the argv length limit.)
    cmd, tool_env = _build_cmd(meta)

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
    # The control-protocol handshake goes FIRST, before the prompt, so the
    # channel is live before the model can reach a tool that needs it.
    if model_caps.needs_approval_channel(meta.get("permission_mode")):
        if not _write_line(turn.id, _initialize_line()):
            log.warning("turn %s: could not open the approval channel; "
                        "gated tool calls will be auto-denied", turn.id)
    # Deliver the prompt as the run's first message.
    #
    # A FAILED DELIVERY ENDS THE RUN. This warned and carried on, which produced
    # the worst available outcome: the CLI was spawned, sat on an open stdin with
    # nothing to answer, and idled. It emits no result line, so the drive loop
    # never breaks; the process stays alive, so the liveness check never fires;
    # and there is no wall clock on a chat turn, so nothing ends it. The chat
    # showed a turn running forever over a prompt the model never received, and
    # the only record was one log line. Treated like the spawn failures above it
    # instead: kill what we started, say so, and finalize.
    # Blocks are built HERE and not at start_turn, so the base64 never enters the
    # journal (see meta["attachments"]). The filename line is appended to the
    # prompt rather than to the stored message: an image block carries no name,
    # so without it the model cannot tell which of three files is meant, but the
    # transcript renders chips and must not also show the sentence.
    _atts = meta.get("attachments") or []
    att_blocks = chat_attachments.content_blocks(_atts, meta.get("owner_id"))
    _named = chat_attachments.describe(_atts)
    _prompt = f"{meta['prompt']}\n\n{_named}" if _named else meta["prompt"]
    if not write_to_turn(turn.id, _prompt, att_blocks, origin=meta.get("origin")):
        turn.error = "could not deliver the prompt to the turn's input channel"
        _push(turn, "error", {"detail": turn.error})
        # The write failed outright, so the message went nowhere at all. Same
        # loss as one the CLI never read, and the same remedy: record it as an
        # unread delivery so `_requeue_unread` puts it back on the queue and the
        # drain sends it. Nothing else would ever retry it.
        turn.unread.append({"origin": meta.get("origin") or []})
        await _retire_turn_proc(proc.pid, proc)
        _cleanup_turn_input(turn.id)
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
        return
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
    # Answers recorded on disk win over anything the journal replay implies.
    turn.answers.update(meta.get("answers") or {})
    # `replays` and `results` are what turn_end reads: how many of the prompts we
    # handed over the CLI has acknowledged reading, and how many requests it has
    # finished. `activity_at` is the silence clock that bounds the wait.
    # `seg_at_result` is the segment count as of the LATEST result. When it falls
    # behind `len(turn.segments)` the model has produced something since that
    # result, which means it is answering and must not be retired. -1 until the
    # first result, where the count is not yet meaningful.
    state = {"compacted": False, "saw_result": False, "last_ctx": 0,
             "replays": 0, "results": 0, "foreign_results": 0,
             "seg_at_result": -1,
             "activity_at": time.time(), "seg_count": 0}
    buf = ""
    offset = 0
    last_persist = 0.0

    def _persist() -> None:
        # The live snapshot, not the log. The journal on disk is the buffer and
        # this is the copy a reload reads; the record gets one write, at commit.
        try:
            chat_store.update_run(
                turn.thread_id, turn.id, turn.segments, _turn_content(turn),
                turn.cut_head)
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
            #
            # ONLY THIS TURN'S OWN CALLS. A subagent's assistant messages arrive
            # here too, carrying `parent`, and a delegate runs on its own fresh
            # context: a few tens of thousands of tokens against the parent's
            # hundreds. Without this check the last delegate to speak overwrote
            # the thread's reading, and the meter fell from 44% to 8% without
            # the conversation having shrunk by a single message. Spend is the
            # opposite case and is accumulated below for every call, delegates
            # included, because a delegate's tokens are really spent.
            if payload.get("parent") is None:
                ctx = _ctx_tokens_from_usage(payload.get("usage"))
                if ctx:
                    state["last_ctx"] = ctx
            # Spend accumulates across tool rounds: each assistant message is a
            # separate API call and is billed separately. (Contrast last_ctx,
            # which is overwritten because only the latest window size matters.)
            state["spend_sum"] = int(state.get("spend_sum") or 0) + _spend_tokens_from_usage(payload.get("usage"))
        elif kind == "result":
            state["saw_result"] = True
            # A RESULT THAT LANDS BEFORE THE CLI HAS READ ANY PROMPT OF OURS IS
            # NOT AN ANSWER TO ONE. It belongs to a prompt the CLI generated for
            # itself — on `--resume`, a `<task-notification>` for a background
            # agent left over from the previous process, delivered ahead of ours
            # and answered within ~90ms of spawn. Counting it made `results`
            # balance `replayed` against work that had not started, which is one
            # of the two ways this loop used to retire a turn mid-answer.
            # `turn_end` compares the two, so the miscount has to be fixed here,
            # where we can still see that no echo has arrived.
            if int(state["replays"]) > 0:
                state["results"] += 1
            else:
                state["foreign_results"] = int(state.get("foreign_results") or 0) + 1
            # NEW OUTPUT SINCE THE LATEST RESULT, cleared here and set by the
            # segment counter in the drive loop. See services/turn_end.py.
            state["seg_at_result"] = len(turn.segments)
            state["activity_at"] = time.time()
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
            if kind in ("text", "tool", "tool_result", "permission"):
                saw_tool = _segment_ingest(turn, kind, payload)
            elif kind == "meta" and payload.get("subtype") == "compact_boundary":
                state["compacted"] = True
            elif kind == "user_echo":
                # THE RECEIPT THAT THE CLI READ ONE OF OUR MESSAGES. Nothing is
                # rendered from it (see below), but it is the only evidence that
                # a prompt we wrote has actually been picked up, which is half of
                # knowing whether a `result` ends this turn. Sidechain echoes are
                # a subagent's own prompt and are not ours.
                if payload.get("parent") is None:
                    state["replays"] += 1
                    state["activity_at"] = time.time()
                    # FIFO: stdin is read in order, so the oldest outstanding
                    # delivery is the one this echo acknowledges. What is left
                    # at the end was never read (`_requeue_unread`).
                    if turn.unread:
                        turn.unread.pop(0)
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
    over_memory = False
    last_mem_check = time.time()
    while True:
        saw_tool = _drain()
        now = time.time()
        # Runaway-memory guard. See _pgid_rss_mb for why this is a watchdog
        # rather than an rlimit or a cgroup cap.
        if _TURN_MEMORY_MAX_MB > 0 and now - last_mem_check >= _MEMORY_CHECK_EVERY_S:
            last_mem_check = now
            group_pid = meta.get("pid") or (proc.pid if proc is not None else 0)
            if group_pid:
                used = _pgid_rss_mb(int(group_pid))
                if used > _TURN_MEMORY_MAX_MB:
                    over_memory = True
                    turn.error = (
                        f"Stopped: this turn's processes reached "
                        f"{used / 1024:.1f} GB of memory, over the "
                        f"{_TURN_MEMORY_MAX_MB / 1024:.1f} GB ceiling. Any file "
                        "changes it already made are saved."
                    )
                    log.warning("turn %s killed: %.0f MB > %d MB ceiling",
                                turn.id, used, _TURN_MEMORY_MAX_MB)
                    _kill_turn_proc(meta.get("pid"), proc)
                    _drain(final=True)
                    break
        # A tool event still gets a fast snapshot, because a tool chip appearing
        # is the most visible sign a turn is moving. It just gets a FLOOR now:
        # `saw_tool` alone was unthrottled, so a burst of tool events wrote the
        # draft on every pass of this loop. The floor is under the drain
        # interval, so a tool lands in the very next snapshot either way.
        gap = now - last_persist
        if (saw_tool and gap > 0.1) or (turn.segments and gap > 0.4):
            _persist(); last_persist = now
        if len(turn.segments) != state["seg_count"]:
            # NEW output, not "has output": the stuck-bound below measures
            # SILENCE, and reading `turn.segments` as a truthy value would reset
            # it on every pass for the rest of the turn, which is no bound at
            # all. A turn working through an injected message keeps resetting
            # this and is never touched by that bound; a wedged one stops.
            state["seg_count"] = len(turn.segments)
            state["activity_at"] = now
        if state["saw_result"] and turn_end.turn_is_over(turn_end.TurnProgress(
                delivered=turn.delivered,
                replayed=int(state["replays"]),
                results=int(state["results"]),
                produced=bool(turn.segments),
                is_command=(meta.get("prompt") or "").strip().startswith("/") or turn.injected_command,
                idle_s=now - float(state["activity_at"]),
                stopped=turn.stop_requested,
                produced_since_result=(
                    int(state["seg_at_result"]) >= 0
                    and len(turn.segments) > int(state["seg_at_result"])))):
            # THE FIRST RESULT IS NOT THE END OF THE TURN, only the end of one
            # request. Retiring on it killed whatever the CLI had started for a
            # second prompt — an injected message, or the task-notification the
            # resume path delivers ahead of ours. See services/turn_end.py.
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
                         stopped=stopped, over_memory=over_memory,
                         # A message the CLI never echoed back is one it never
                         # read. The reply says so rather than reporting it as
                         # the model having nothing to say.
                         unread=int(state["replays"]) < turn.delivered)


def _stale_pending_approvals(turn: Turn) -> None:
    """Any approval still pending when the turn ends is STALE, not pending.

    The process it was blocking is gone, so answering it can no longer do
    anything. Leaving the buttons live would offer the operator a decision that
    silently does nothing, which is the one outcome worse than not asking."""
    changed = False
    for seg in turn.segments:
        if seg.get("t") == "approval" and seg.get("state") == "pending":
            seg["state"] = "stale"
            changed = True
    if changed:
        _push(turn, "permission_resolved", {"stale": True})


def build_receipts(kind: str, labels: list[str],
                   refs: list[dict] | None = None) -> list[dict]:
    """Turn one write helper's outcome strings into typed receipts.

    A receipt is a durable effect on the world OUTSIDE the chat: a note written,
    a ticket filed, a preference captured. `refs` are what the write actually
    PRODUCED, paired positionally with the labels that describe them, and a
    receipt carrying one becomes a link, so "Saved a note" can be opened rather
    than only read.

    A warning never takes a ref. Failure strings are parenthesised by convention
    in every one of these helpers ("(note skipped: ...)"), and a refusal or a
    parse error produced nothing to link to. The helpers return an empty ref
    list in those cases too; this is the second guard, because a link to a note
    that was never written is worse than no link.

    Refs are trusted ONLY when they describe the labels one for one. Pairing by
    index across two lists of different lengths can attach a ref to the wrong
    label, and a receipt linking to the wrong note is a worse failure than one
    linking nowhere, so a mismatch drops every ref rather than guessing.

    Module level, not a closure inside _finalize_turn, so a test can exercise
    THIS function rather than a copy of it.
    """
    paired = refs if refs and len(refs) == len(labels or []) else []
    out: list[dict] = []
    for i, label in enumerate(labels or []):
        text = (label or "").strip()
        if not text:
            continue
        warn = text.startswith("(")
        receipt = {"kind": "warn" if warn else kind, "label": text}
        ref = paired[i] if not warn and i < len(paired) else None
        if ref:
            receipt["ref"] = ref
        out.append(receipt)
    return out


def _close_open_tools(turn: Turn) -> None:
    """Any tool call still open when the turn ends never reported a result.

    It is not a success and it is not a failure: the process it was running in
    is gone. `at` is dropped so nothing renders a duration measured against a
    clock that stopped, and `ok` is left UNSET, which the transcript reads as
    "outcome unknown" rather than as either outcome. Setting ok=False here would
    paint every step of an interrupted turn red.
    """
    for seg in turn.tool_segs.values():
        seg.pop("at", None)
    turn.tool_segs.clear()


def _close_open_agents(turn: Turn, *, stopped: bool, lost: bool) -> None:
    """Any subagent still open when the turn ends did NOT finish. Say which.

    A subagent segment is closed by the Agent tool's own `tool_result`. When the
    turn ends before that arrives — the operator pressed Stop, or the backend was
    restarted under a detached run — the result never comes, so `done` stayed
    False and the chip read "working" for the rest of the thread's life. The
    delegation was over; only the row was still claiming otherwise.

    Closed with the SAME reason the turn ended for, because that is what actually
    happened to the subagent: it was killed with its parent. `outcome` carries
    the reason to the collapsed chip so the row itself is honest without being
    opened, and `result` says it in words for when it is."""
    if stopped:
        outcome, said = "stopped", "Stopped with the turn, before it reported back."
    elif lost:
        outcome, said = "lost", "Lost with the turn, before it reported back."
    else:
        outcome, said = "unfinished", "The turn ended before it reported back."
    for seg in turn.agent_segs.values():
        if seg.get("done"):
            continue
        seg["done"] = True
        seg["outcome"] = outcome
        # Never overwrite partial text the subagent had already reported.
        if not seg.get("result"):
            seg["result"] = said


def resolve_permission(turn_id: str, request_id: str, allow: bool,
                       message: str | None = None) -> tuple[bool, str]:
    """Answer a pending approval. Returns (ok, detail).

    Order matters: the frame goes to the process FIRST, because that is the part
    that unblocks the turn, and only a write that landed is recorded as a
    decision. Recording first and failing to deliver would show the operator an
    approved action that never ran."""
    turn = TURNS.get(turn_id)
    seg = None
    if turn is not None:
        for s in turn.segments:
            if s.get("t") == "approval" and s.get("id") == request_id:
                seg = s
                break
        if seg is None:
            return False, "no such permission request on this turn"
        if seg.get("state") == "stale":
            return False, "that turn has ended; the request can no longer be answered"
        if seg.get("state") in ("allowed", "denied"):
            # Answering twice is a no-op, not an error: a double-click or a
            # retried request must not read as a failure.
            return True, seg["state"]
    updated_input = (seg or {}).get("input") if allow else None
    if not answer_permission(turn_id, request_id, allow, updated_input, message):
        return False, "the turn's input channel is gone; it can no longer be answered"
    state = "allowed" if allow else "denied"
    # Recorded on disk as well as in memory: the drive loop re-reads the journal
    # from the start after a restart, and would otherwise rebuild this as pending.
    try:
        meta = _read_meta(_journal_dir(turn_id)) or {}
        if meta.get("turn_id"):
            answers = dict(meta.get("answers") or {})
            answers[request_id] = state
            meta["answers"] = answers
            _write_meta(meta)
    except Exception as e:  # pragma: no cover — the answer already landed
        log.debug("turn %s: could not record answer %s: %s", turn_id, request_id, e)
    if turn is not None and seg is not None:
        turn.answers[request_id] = state
        seg["state"] = state
        _push(turn, "permission_resolved", {"id": request_id, "state": state})
        try:
            chat_store.update_run(turn.thread_id, turn.id, turn.segments,
                                  _turn_content(turn), turn.cut_head)
        except Exception as e:  # pragma: no cover — defensive
            log.debug("turn %s: approval persist failed: %s", turn_id, e)
    return True, state


def _requeue_unread(turn: Turn, *, stopped: bool) -> int:
    """Put back anything we wrote that the CLI never read.

    A message is the system's responsibility from the moment the server accepts
    it (services/chat_queue). Writing it to a process that then never reads it
    is not delivery, and until now it was the one path where an accepted message
    could still vanish: the bytes went into the FIFO, the write succeeded, and
    nothing ever noticed that the CLI had not picked it up. The turn ended, the
    process was retired, and the text existed only in the transcript with
    nothing coming for it.

    So it goes back on the thread's queue, under its ORIGINAL client id and send
    time. The log is idempotent on client id, so the drain re-sends it without
    writing it to the transcript a second time, and it keeps its place in the
    order by the time the operator hit enter rather than by the moment this
    failed. `_finalize_turn` drains the thread immediately after this.

    NOT AFTER A STOP. Stop is the operator taking the wheel back, and it holds
    the queue on purpose; re-queueing here would clear that hold and send, which
    is the opposite of what they just asked for. The message stays in the
    transcript where they can send it again.

    An entry with no client id is not requeued, because there would be no way to
    recognise it as the same message: the drain would append a second copy of it
    to the transcript, which is a worse failure than the one this fixes. Every
    send from the app carries one; this is the internal callers.
    """
    if stopped or not turn.unread:
        return 0
    pending, turn.unread = turn.unread, []
    sent = 0
    for delivery in pending:
        for entry in delivery.get("origin") or []:
            cid = (entry.get("client_id") or "").strip()
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            if not cid:
                log.warning("turn %s: a message the CLI never read has no client id; "
                            "it stays in the transcript unanswered", turn.id)
                continue
            try:
                chat_queue.enqueue(turn.thread_id, text=text, client_id=cid,
                                   ts=entry.get("ts") or None,
                                   attachments=entry.get("attachments") or None)
                sent += 1
            except Exception as e:  # pragma: no cover — the queue is a file write
                log.warning("turn %s: could not requeue an unread message: %s", turn.id, e)
    if sent:
        log.warning("turn %s: %d message(s) were never read by the CLI; "
                    "requeued for the next turn", turn.id, sent)
    return sent


async def _finalize_turn(turn: Turn, meta: dict, **kw) -> None:
    """Commit the run and wake whoever is waiting on it.

    Always wakes, even if the commit raised. The blocking POST /messages path
    awaits `turn.finished`, and a finalize that fails silently would leave that
    request hanging for as long as the client will hold the socket open."""
    _stale_pending_approvals(turn)
    _close_open_tools(turn)
    _close_open_agents(turn, stopped=bool(kw.get("stopped")), lost=bool(kw.get("lost")))
    # BEFORE the commit, because the drain fired in the `finally` below is what
    # sends these, and it must find them already on the queue.
    _requeue_unread(turn, stopped=bool(kw.get("stopped")))
    try:
        await _commit_turn(turn, meta, **kw)
    finally:
        turn.complete = True
        turn.finished_at = time.time()
        turn.new_event.set()
        turn.finished.set()
        # THE THREAD IS IDLE, SO ANYTHING WAITING GOES NOW. This is the moment
        # the browser's queue used to drain from — but only in a tab that was
        # open, bound to this thread, and awake. Delivery of a message the
        # operator already sent cannot depend on that; it happens here, where
        # the turn actually ends. Fire-and-forget: assembling the next turn's
        # prompt must not sit inside the commit that whoever is blocked on
        # `turn.finished` is waiting for. See services/chat_drain.
        chat_drain.drain_soon(turn.thread_id, reason="turn finished")


async def _commit_turn(turn: Turn, meta: dict, *, rc: int, timed_out: bool, compacted: bool,
                       context_tokens: int = 0, spend_tokens: int = 0,
                       subtype: str = "", lost: bool = False,
                       stopped: bool = False, over_memory: bool = False,
                       unread: bool = False) -> None:
    prompt = meta.get("prompt", "") or ""
    cwd = meta.get("cwd", "") or ""
    timeout_s = float(meta.get("timeout_s", 0) or 0)
    _push(turn, "done", {"exit_code": rc, "text_len": len(turn.final_text)})
    turn.complete = True
    if timed_out:
        turn.error = turn.error or f"timeout after {int(timeout_s)}s"

    # Same plain-language finalize as the original path: salvage any text; when
    # nothing came back, say WHY (timeout / compact / clean-no-summary / failure).
    # The turn's own prompt, OR a slash command injected into it after it began.
    # Without the second half a mid-turn `/context` (which the CLI runs locally
    # and answers without the model) commits as "Nothing ran this turn".
    is_command = prompt.strip().startswith("/") or turn.injected_command
    minutes = max(1, round(timeout_s / 60)) if timeout_s > 0 else 0
    # Neither cap is passed to the CLI any more (see start_turn): no step cap,
    # and by default no wall clock either. A turn ends when the model is done,
    # when the process dies, or when the operator stops it — never because it
    # took "too many" steps or "too long". Autonomous work is the point of this
    # platform; spend is bounded by the usage cap. The timeout branch below only
    # runs for a caller that deliberately asked for a budget.
    # A /compact turn says one line, whatever the CLI wrote.
    #
    # The CLI's answer to /compact is not an answer, it is a dump. Depending on
    # version it is either a 117-character sentence ending in advice nobody asked
    # for, or a "## Context Usage" markdown table of ~1900 characters listing
    # every token category. The operator reported the table twice; it is long
    # enough to be clamped, so it also arrives wearing a Show more control.
    #
    # None of it is wanted, because both facts it carries are already on screen
    # and in better places: the fold divider MessageList draws from the boundary
    # says how many messages folded, and the composer meter shows the window,
    # re-derived right here when a compact clears the stale reading. Printing a
    # table as well is the third telling of the same thing.
    #
    # ONLY the explicit command turn. `compacted` alone is not enough: the CLI
    # also auto-compacts mid-turn when the window fills, and that turn is real
    # work whose reply must never be replaced by this line.
    if compacted and prompt.strip().lower() == "/compact":
        text = "Compacted. Earlier messages are now a summary."
        is_error = False
        # The dump lives in the SEGMENTS too, and segments are what the
        # transcript draws. Replacing `text` alone left it fully on screen.
        turn.segments = _drop_text_segments(turn.segments, turn.cut_head)
    elif over_memory:
        # ABOVE final_text on purpose. A turn killed at the memory ceiling may
        # already have written a perfectly ordinary-looking reply, and showing
        # only that would hide the fact that the rest of the work was cut off.
        # Whatever it did write is kept, with the reason under it.
        text = (f"{turn.final_text}\n\n{turn.error}".strip()
                if turn.final_text else (turn.error or "Stopped at the memory ceiling."))
        is_error = True
    elif turn.final_text:
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
        # rc 0 WITH NOTHING TO SHOW IS NOT "it ran". Headless mode parses a
        # leading slash into a local-command block and executes nothing, so a
        # command the CLI only implements in its own terminal exits clean,
        # silent, and in about a second. "Command ran." over that is the chat
        # asserting an effect that did not happen: /compact reported it for
        # months while the live window it was supposed to empty stayed exactly
        # as full (2026-08-12T05:10:18Z, two seconds, no compact_boundary).
        #
        # /compact and /clear no longer come through here at all — they are
        # server-side commands now, see routes/chat.py:compact_thread. This is
        # for every other slash the operator can still type.
        text = "Command ran." if turn.segments else (
            "Nothing ran. In this mode the CLI records a leading slash as one of "
            "its own commands and executes it only in its own terminal. Send it "
            "without the slash if you meant it as a message."
        )
        is_error = False
    elif rc == 0:
        text = _clean_exit_text(turn.segments, unread=unread)
        is_error = False
    elif lost:
        # The operator saw "ended unexpectedly (exit -1)" over and over without
        # ever being told what it meant. This is that case, named: the backend
        # was restarted while the turn was in flight and the run did not survive.
        text = chat_store.LOST_RUN_TEXT
        is_error = False
    else:
        # The CLI's own stderr, which until now was captured and never read: a
        # turn that died of a bad credential said so on disk while the chat said
        # "exit 1". See `_stderr_tail`.
        text = _failure_text(rc, turn.error or "", _stderr_tail(turn.id))
        is_error = True
    # Provider actions (e.g. an app persona applying a plan-patch to budget.json).
    # Only providers that actually override apply_actions run here, so the general
    # chat and build turns pay nothing. Mirrors the blocking POST /messages path:
    # the action is applied server-side, its fence is stripped from the answer, and
    # any human-readable notes ("Updated the plan") ride along in meta.notes.
    notes: list[str] | None = None
    turn_receipts: list[dict] | None = None
    if not is_error and text:
        try:
            from services import chat_context
            thread = chat_store.get_thread(turn.thread_id)
            provider = chat_context.get_provider((thread or {}).get("context_key"))
            ctx_key = (thread or {}).get("context_key")
            new_text = text
            applied_notes: list[str] = []
            # TYPED RECEIPTS, built here rather than in the helpers.
            #
            # A receipt is a durable effect on the world OUTSIDE the chat: a note
            # written, a ticket filed, a preference captured. It is not a step
            # (which folds away with the rest of a turn's work) and not a status
            # (which is how the turn ended). All three used to render as the same
            # anonymous `.chat-note` div, so "Saved a note to workshop" and
            # "Stopped by operator" were indistinguishable.
            # See docs/decisions/0015-transcript-structure-and-content-provenance.md §4.
            #
            # The kind comes from WHICH helper produced the string, which this
            # function already knows, so no provider interface has to change.
            # Failure strings are parenthesised by convention in every one of
            # these helpers ("(note skipped: ...)"), and those are warnings, not
            # receipts of something that happened.
            receipts: list[dict] = []

            def _receipt(kind: str, labels: list[str],
                         refs: list[dict] | None = None) -> None:
                receipts.extend(build_receipts(kind, labels, refs))
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
                _receipt("action", pnotes or [])
            # O2: apply a task-patch block against this chat's task scope.
            from services import task_scope
            if _permitted("tasks"):
                new_text, tnotes = task_scope.apply_task_patch(
                    ctx_key, new_text, (thread or {}).get("id"), (thread or {}).get("mode"))
                applied_notes += tnotes
                _receipt("ticket", tnotes)
            else:
                new_text = task_scope.strip_task_patch(new_text)
                refusal = f"(a {_role or 'roleless'} agent may not file tickets)"
                applied_notes.append(refusal)
                _receipt("ticket", [refusal])
            # Phase 4: capture an explicit operator preference/correction emitted
            # as a ``learning-note`` block into the learnings store.
            if _permitted("learnings"):
                new_text, lnotes = task_scope.apply_learning_note(ctx_key, new_text)
                applied_notes += lnotes
                _receipt("learning", lnotes)
            # Phase N3: capture a durable scoped ``note`` into the knowledge store.
            if _permitted("notes"):
                # The note is stamped with WHO WROTE IT: an @mentioned agent's
                # note and the assistant's are otherwise byte-identical when
                # either is recalled later. See services/origin.
                from services import origin as _origin
                new_text, nnotes, nrefs = await task_scope.apply_note_block(
                    ctx_key, new_text,
                    author=(_origin.agent(_speaker) if _is_agent and _speaker else _origin.SELF),
                )
                applied_notes += nnotes
                _receipt("note", nnotes, nrefs)
            # A data-mode assistant is told it may ask for a skill by emitting a
            # fenced block. It has tools and reads the skill itself, so nothing
            # expands one; take the raw protocol off the reply.
            new_text = chat_context.strip_use_skill(new_text)
            if new_text != text or applied_notes:
                text = new_text or "(done)"
                notes = applied_notes or None
                turn_receipts = receipts or None
                # Replace the trailing text run with the cleaned answer so the
                # rendered segments match the applied result.
                cut = len(turn.segments)
                while cut > 0 and turn.segments[cut - 1].get("t") == "text":
                    cut -= 1
                kept = turn.segments[:cut]
                tail = [{"t": "text", "text": text}] if text else []
                # The dropped text may have been the anchor for a message that
                # arrived while it was being written. Move the position rather
                # than losing it; see carry_cuts.
                carry_cuts(turn.segments[cut:], kept + tail, turn.cut_head)
                turn.segments = kept + tail
        except Exception as e:
            log.warning("turn %s: provider apply_actions failed: %s", turn.id, e)
    # THE COMMIT. One message, appended at the tail, whether or not a client is
    # watching — so it lands below every message this run was handed and above
    # nothing. `lost` is recorded as such rather than as a normal finish.
    turn.notes = notes
    try:
        turn.committed = chat_store.commit_run(
            turn.thread_id, turn.id, turn.segments, text,
            error=is_error, timed_out=timed_out, notes=notes, receipts=turn_receipts,
            cut_head=turn.cut_head,
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
    # EXCEPT WHEN THE TURN PRODUCED NOTHING, where the journal is the only
    # evidence of why. Diagnosing the 2026-08-12 empty replies took reading the
    # CLI's own session files, because ours had been deleted seconds after the
    # failure. It is a few KB, it is kept only for the case that should now be
    # rare, and `status: done` keeps the restart-recovery pass off it.
    if not turn.segments and not (turn.final_text or "").strip() and not stopped:
        log.warning("turn %s produced nothing; keeping its journal at %s",
                    turn.id, _journal_dir(turn.id))
        return
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
            # Re-derived from the journal, not carried in memory: a run adopted
            # after a restart must still refuse messages it will never read.
            turn.accepts_input = not (meta.get("prompt") or "").strip().startswith("/")
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
            _drive_task(turn, meta, _drive_turn(turn, meta, proc=None))
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


def inject(turn_id: str, text: str, attachments: list[dict] | None = None,
           client_id: str | None = None, sent_at: str | None = None) -> bool:
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
    if not text and not attachments:
        return False
    # A COMMAND TURN NEVER READS ITS INPUT. `/compact` summarizes and exits; the
    # bytes would reach a process that has already stopped listening, and the
    # write would SUCCEED, which is what made this invisible. Refusing here is
    # what turns a swallowed message into a queued one.
    t = TURNS.get(turn_id)
    if t is not None and not t.accepts_input:
        return False
    blocks = chat_attachments.content_blocks(attachments or [])
    named = chat_attachments.describe(attachments or [])
    # `client_id` and `sent_at` are this message's own, so a delivery the CLI
    # never reads goes back on the queue as the message the operator sent rather
    # than as a new one. See _requeue_unread.
    ok = write_to_turn(turn_id, f"{text}\n\n{named}".strip() if named else text, blocks,
                       origin=[{"text": text, "client_id": client_id, "ts": sent_at,
                                "attachments": list(attachments or [])}])
    # Remember that a command went in, so a turn whose only work was a locally
    # run slash command is not reported as having done nothing. Only on a
    # successful write: a refused injection never reached the CLI.
    if ok and t is not None and text.startswith("/"):
        t.injected_command = True
    return ok


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
