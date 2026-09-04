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

from services import (agent_slots, attachment_carry, build_agents,
                      chat_attachments, chat_drain,
                      chat_log, chat_queue, chat_store, chat_worktree, leases,
                      llm_router, model_caps, ownership, reply_guard, turn_end,
                      worktrees)

log = logging.getLogger("workshop.build_streaming")

CLAUDE_CLI = llm_router.CLAUDE_CLI

def _stdin_fifo(turn_id: str) -> Path:
    """The turn's realtime input channel.

    A turn runs `--input-format stream-json`, so its stdin stays open for the
    life of the turn and a message sent while the agent is working reaches the
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
    # The CLI keys the session dir on the cwd IT sees. Under scoped exec that is
    # the mountpoint (/workspace), not the real path, so a session must be found
    # in that dir for --resume to succeed. Checking the real-cwd dir (or globbing
    # every dir) would find a session the CLI, chdir'd to /workspace, cannot then
    # resume, and the turn would fail. So when scoped, look only where the CLI
    # will look; the failed-resume self-heal is the backstop, not the norm.
    from services import scoped_exec
    # Under the uid layer the CLI runs (and writes its session files) as the
    # turn-user, HOME'd at its own directory (turn_env()), not the host user's -- and
    # the host user's ~/.claude is not even bound in for that case (see _grants()).
    # A soft check: an unavailable turn-user here just means "no session found
    # yet" (the fresh-session path), not a hard failure -- the spawn itself is
    # what raises on a real misconfiguration.
    try:
        user = scoped_exec.turn_user(os.path.realpath(cwd) if cwd else None)
    except scoped_exec.TurnUserUnavailable:
        user = None
    home = scoped_exec.turn_home() if user else os.path.expanduser("~")
    scoped = scoped_exec.enabled()
    eff_cwd = scoped_exec.MOUNTPOINT if scoped else cwd
    encoded = eff_cwd.rstrip("/").replace("/", "-")
    primary = os.path.join(home, ".claude", "projects", encoded, f"{session_id}.jsonl")
    if os.path.exists(primary):
        return True
    if scoped:
        # A session minted before the flip lives under the old real-cwd dir the
        # CLI can no longer reach. Treat it as gone so the next turn mints a fresh
        # one and re-sends the assembled history, seamlessly, instead of a doomed
        # --resume.
        return False
    # Un-scoped: encoding could shift across CLI versions; the id is a unique
    # uuid, so a glob across all projects is a safe fallback.
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
    # The prompt size the model last reported for THIS turn, so the composer's
    # meter can move while the turn runs instead of showing the previous turn's
    # number for however long it takes. Pushed on the wire as a `context` event
    # AND persisted into the live snapshot, because the wire only reaches a tab
    # that is streaming right now.
    context_tokens: int = 0
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
    # Set once this turn's message has been committed to the thread, with the
    # committed message in `committed`. This is what the blocking POST /messages
    # path awaits, so there is ONE finalize pipeline instead of a streaming one
    # and a blocking one that drift apart.
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    committed: dict | None = None
    notes: list[str] | None = None
    # True once the subprocess exists and its input channel is open. A turn is
    # CLAIMED before its prompt is assembled, so there is a window where it owns
    # the thread but cannot be delivered to yet; that is not the same thing as a
    # turn whose channel has been cleaned up because it is finishing, and the
    # injection path has to tell them apart.
    spawned: bool = False
    # The log seq through which this turn's prompt includes the thread, stamped
    # in the same synchronous stretch that records the trigger message and the
    # turn marker (routes/chat._turn_for_message). None until that stretch runs.
    # This is what makes "did the prompt see this message" a comparison instead
    # of a guess: a message filed with a higher seq while the turn was claimed
    # but not yet deliverable is provably NOT in the prompt, and the send path
    # queues it rather than dropping it into the gap.
    prompt_through_seq: int | None = None
    # The turn's process, for the one caller that has to end it early (stop_turn).
    # `pid` is set on the re-attach path too, where this process never held a
    # handle; `proc` only when we spawned it ourselves.
    pid: int | None = None
    proc: object | None = None
    # Mirrors meta["turn_user"] / meta["pid_starttime"] so stop_turn (which
    # only has this object, not `meta`) can still signal correctly across the
    # uid layer -- see scoped_exec.signal_pg and _proc_alive.
    turn_user: str | None = None
    pid_starttime: str | None = None
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
    # The §10.3 offer this turn's first blocked write produced, once. Set by
    # `_maybe_write_blocked` when a tool result comes back EROFS against a
    # read-only mount ([B1]); None on every turn where nothing was blocked,
    # which is every turn while WORKSHOP_TREE_LEASE_MOUNT is off. Kept ON THE
    # TURN rather than in a module-level set so it is collected with the turn
    # and cannot outlive it, and so "once" means once per turn rather than once
    # per process.
    write_offer: dict | None = None


# In-memory turn registry. Build sessions are short-lived enough that this is
# acceptable; a future upgrade can spill to disk for cross-process resume.
TURNS: dict[str, Turn] = {}

# A RESTART IS A PAUSE, NOT AN ENDING.
#
# The agent process is deliberately detached (start_new_session=True) and the
# unit sets KillMode=process, so systemd's SIGTERM reaches uvicorn alone and the
# child keeps working across a restart. But that guarantee was being defeated
# from INSIDE this process: on shutdown, asyncio cancels every remaining task,
# the drive loop's CancelledError handler ran _end_wedged, and _end_wedged calls
# _retire_turn_proc -- os.killpg(SIGTERM) on the very child systemd had been
# careful not to touch. It then committed "This turn ended early", so a restart
# that was supposed to be invisible instead killed the operator's agent mid-work
# and wrote that into the transcript.
#
# Cancellation therefore has to mean two different things, and only the process
# knows which: "this turn was abandoned" (end it, say so) versus "the process is
# going away" (let go; the child outlives us and reattach_running_turns adopts
# it on the way back up). This flag is that distinction. main.py's lifespan sets
# it before cancelling anything.
SHUTTING_DOWN = False


def begin_shutdown() -> None:
    """Mark the process as going down so in-flight turns are released, not ended.

    Idempotent, and safe to call even if nothing is running."""
    global SHUTTING_DOWN
    SHUTTING_DOWN = True

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
    a turn that finished more than the TTL ago qualifies. `turn_is_live` already
    answers False for anything complete, so no caller's answer changes."""
    cutoff = (now or time.time()) - FINISHED_TURN_TTL_S
    stale = [tid for tid, t in TURNS.items()
             if t.finished_at is not None and t.finished_at < cutoff]
    for tid in stale:
        del TURNS[tid]
    if stale:
        log.debug("pruned %d finished turn(s) from the registry", len(stale))
    return len(stale)


class TurnInFlight(RuntimeError):
    """A turn is already open on this thread; join it instead of starting a rival.

    Carries the id of the turn that owns the thread. Raised by `start_turn` when
    it claims for itself and loses the race, which is the backstop for two POSTs
    landing in the same event-loop tick."""

    def __init__(self, turn_id: str):
        super().__init__(f"a turn is already in flight on this thread: {turn_id}")
        self.turn_id = turn_id


def _make_turn(thread_id: str, agent_slug: str, turn_id: str | None = None,
               accepts_input: bool = True) -> Turn:
    # The registry only grows here, so this is the only place it has to shrink.
    _prune_turns()
    # A claimed turn is already registered (see claim_turn); adopt it rather than
    # replacing it, so nothing that looked it up between the claim and the spawn
    # is holding an object this one has orphaned.
    t = TURNS.get(turn_id or "")
    if t is None:
        t = Turn(id=turn_id or new_turn_id(), thread_id=thread_id, agent_slug=agent_slug)
        TURNS[t.id] = t
    else:
        t.agent_slug = agent_slug
        t.started = time.time()
    # Only ever narrows. A turn that cannot be spoken to is decided at claim
    # time, and the spawn path re-makes the same object without knowing why.
    if not accepts_input:
        t.accepts_input = False
    return t


def new_turn_id() -> str:
    return str(uuid.uuid4())


def turn_is_live(turn_id: str | None) -> bool:
    """Is this process driving this turn right now?

    A turn this process does not hold is not live however the thread's meta reads:
    it belongs to a previous boot, and the startup reconcile owns it. A complete
    one is not live either. Both are cases where `active_turn_id` outlives the
    thing it names, and the point of one predicate is that every caller agrees
    about them."""
    if not turn_id:
        return False
    t = TURNS.get(turn_id)
    return t is not None and not t.complete


def turn_accepts_input(turn_id: str | None) -> bool:
    """May a message be delivered into the turn holding this thread?

    True for a turn this process is not driving at all: nothing is known against
    it, and the injection path has its own answer for that case. See
    Turn.accepts_input for the two kinds that say no."""
    t = TURNS.get(turn_id or "")
    if t is None or t.complete:
        return True
    return t.accepts_input


def turn_is_ending(turn_id: str | None) -> bool:
    """Has this turn spawned and lost its input channel?

    That is the one state where a message cannot be delivered and never will be:
    the drive loop has ended and the finalize is running. A turn that has been
    claimed but not yet spawned looks the same to `write_to_turn` (no FIFO) and
    is the opposite situation — it is about to start — so the two must not be
    treated alike, or a message sent in the startup window would kill the turn it
    was meant for."""
    t = TURNS.get(turn_id or "")
    return t is not None and t.spawned and not _stdin_fifo(turn_id or "").exists()


async def claim_turn(thread_id: str, turn_id: str, *, model: str | None = None,
                     agent: str | None = None, force: bool = False,
                     accepts_input: bool = True, announce: bool = True) -> str | None:
    """Take this thread for `turn_id`. Returns the turn that already owns it (join
    that one), or None when the claim succeeded.

    Held under the thread's WRITE LOCK across the whole check-and-act, so two
    POSTs in the same event-loop tick cannot both open a turn: the second takes
    the lock, sees `active_turn_id`, and is told to join. That is structural, not
    a timing accident — there is no await between the read and the write for the
    other coroutine to slip into.

    `force` takes over a thread whose active turn has just refused a delivery.
    Only the injection path uses it, and only after a write to that turn's input
    channel has actually failed.

    A turn this process is not driving gets a `lost` message on the way out:
    nothing else is ever going to answer for it, and leaving the chat waiting on
    it is the "chats keep stalling" report."""
    async with chat_log.lock_for(thread_id):
        active = chat_store.active_turn_id(thread_id)
        if active and active != turn_id:
            if turn_is_live(active) and not force:
                return active
            if active not in TURNS:
                chat_store.lose_turn(thread_id, active)
        chat_store.begin_turn(thread_id, turn_id, model=model, agent=agent,
                              announce=announce)
        # Register the turn BEFORE returning. Prompt assembly awaits, and until
        # the turn is in TURNS a message arriving during that window would see an
        # `active_turn_id` naming a turn nothing is driving and take the thread
        # over — starting the rival turn the claim exists to prevent.
        _make_turn(thread_id, "", turn_id=turn_id, accepts_input=accepts_input)
        return None


def release_external_turn(thread_id: str, turn_id: str) -> None:
    """Give the thread back after a turn this module did not drive.

    Voice turns claim a thread so the rest of the system can see them — "a turn in
    flight is always visible" was simply false for voice, and a message typed
    during a spoken turn used to open a second, rival turn on the same session.
    They persist their own reply, so there is nothing here to commit; the claim
    just has to end, and the registry entry has to become prunable."""
    t = TURNS.get(turn_id)
    if t is not None:
        t.complete = True
        t.finished_at = time.time()
    try:
        chat_store.release_turn(thread_id, turn_id)
    except Exception:  # pragma: no cover — the reconcile owns whatever is left
        log.debug("could not release external turn %s on %s", turn_id, thread_id)
    # A voice turn ending leaves the thread idle exactly as a text turn does, and
    # a message typed during one is queued behind it in the same way.
    chat_drain.drain_soon(thread_id, reason="external turn released")


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


def _current_ref(meta: dict) -> str:
    """Which ref the blocked write was aimed at -- the base of the branch that
    would be offered.

    The turn meta carries one once a session's ref is bound to it (B5/B8); until
    then the tree itself is the only thing that knows, so it is asked. That is a
    `git rev-parse` in a path that runs at most once per turn, and only when a
    write has ALREADY failed, so it is nowhere near the hot loop. "HEAD" is the
    honest last resort: it is what the offer would branch from anyway.
    """
    named = meta.get("branch") or meta.get("base_ref")
    if named:
        return str(named)
    cwd = meta.get("cwd")
    if not cwd:
        return "HEAD"
    try:
        from services import git_service
        return git_service.rev_parse(cwd, "HEAD", abbrev_ref=True) or "HEAD"
    except Exception:
        return "HEAD"


# --- the worktree this turn holds (connection C-3) ---------------------------

def _renew_tree(meta: dict, state: dict) -> None:
    """Renew the lease and presence on the tree this turn is writing in.

    SECTION 6'S RENEWAL CHANNEL FOR AN INTERACTIVE HOLDER, driven from the one
    loop that runs for exactly as long as the turn does. A lease TTL is 90s and
    a beat is every 30s (`services/leases.py`), so two consecutive beats can be
    lost to a slow disk without the tree lapsing under a chat that is plainly
    working.

    A turn that holds nothing pays one dict lookup here and nothing else, which
    is every chat that was never pinned to a branch.

    LOSING THE LEASE DOES NOT KILL THE TURN. Being fenced means someone
    preempted this chat, and `services/control.py` already stopped the turn
    itself as part of that; all this does is stop claiming a tree that is no
    longer ours, so the next spawn reads the truth.
    """
    tree = meta.get("tree")
    if not tree:
        return
    now = time.time()
    if now - float(state.get("tree_beat_at") or 0) < leases.HEARTBEAT_SEC:
        return
    state["tree_beat_at"] = now
    try:
        held = chat_worktree.TreeState.from_dict(tree)
        if held is None or not chat_worktree.beat(
                str(meta.get("thread_id") or ""), held,
                activity=f"a chat turn in {held.branch or held.cwd}"):
            log.info("turn %s: the tree lease is no longer ours; letting it go",
                     meta.get("turn_id"))
            meta["tree"] = None
    except Exception as exc:  # pragma: no cover - bookkeeping, never a turn
        log.debug("turn %s: could not renew the tree lease: %s",
                  meta.get("turn_id"), exc)


def _release_tree(meta: dict) -> None:
    """The turn is over, so the tree is free. Section 7: sessions are disposable.

    Withdrawing presence is the whole of it -- `presence.release` already
    releases the leases the holder had, which is section 6's join rule, and
    calling `leases.release` here as well would be a second implementation of
    the same rule.
    """
    if not meta.get("tree"):
        return
    try:
        chat_worktree.leave(str(meta.get("thread_id") or ""))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("turn %s: could not release the tree: %s", meta.get("turn_id"), exc)


def _auto_create_worktree_for_chat(thread_id: str) -> dict | None:
    """Create and pin a worktree for a project chat on its first write.

    Returns {branch_slug, display_name, project_slug} on success, None when
    the chat is not a project chat, is already pinned, or creation fails.
    """
    if not thread_id:
        return None
    try:
        thread = chat_store.get_thread(thread_id)
        if not thread:
            return None
        if thread.get("branch_slug"):
            return None
        # ASK `chat_worktree`, DO NOT RE-DERIVE. This used to read
        # `context_key.startswith("project:")` and slice the slug off the key,
        # which is a bug `chat_worktree._project_of` already documents at length
        # and already fixed: `DevelopmentContextProvider` is a project provider
        # that deliberately keys itself `development` rather than
        # `project:development` (ADR 0014), so the key test excluded the one
        # chat that cuts more branches than any other -- the platform's own.
        #
        # The whole point of the write-blocked path is that a chat which writes
        # gets a worktree WITHOUT BEING ASKED. Excluding the operator's own chat
        # from it meant the one place a person would meet this feature was the
        # manual picker the design exists to delete, and it is a large part of
        # why `WORKSHOP_TREE_LEASE_MOUNT` could not be armed.
        #
        # Same rule fixed in two places, re-broken in the second, which is what
        # re-deriving instead of asking costs.
        from services import chat_context, chat_worktree
        provider = chat_context.get_provider(thread.get("context_key"))
        project_slug = chat_worktree.project_of(provider)
        if not project_slug:
            return None
        title = (thread.get("title") or "").strip() or None
        created = worktrees.allocate_task_worktree(
            project_slug, thread_id, title=title,
            allocated_by=f"chat:{thread_id}")
        branch_slug = created["branch_slug"]
        chat_store.set_thread_branch(thread_id, branch_slug)
        return {
            "branch_slug": branch_slug,
            "display_name": created.get("display_name") or title or branch_slug,
            "project_slug": project_slug,
        }
    except Exception as exc:
        log.debug("auto-create worktree for chat %s failed: %s", thread_id, exc)
        return None


def _maybe_write_blocked(turn: Turn, meta: dict, payload: dict) -> None:
    """The `EROFS` event: first write creates the worktree, without asking.

    THIS IS THE WRITE-DETECTION MECHANISM §14.1 OF THE BRANCHES DOC NEEDED AND
    DID NOT HAVE. "Branch on write" was unbuildable as written: a turn is a
    detached process inside bwrap, the backend cannot observe individual writes,
    and "first write" is not even classifiable because the harness writes
    session and temp files constantly. Against a read-only mount the first
    GENUINE write comes back as a failed tool result carrying errno 30, which is
    exact, cheap and observable -- and it arrives here, on the ordinary stream,
    with no new channel.

    What it does now is §10.3's default taken automatically: create a worktree
    named from the chat's title, pin the chat to it, and inject one message so
    the model knows to continue rather than retry in the same turn. Write access
    applies from the NEXT turn (the bwrap mount is fixed for the current one).

    ONCE PER TURN. A blocked write is usually followed by several more as the
    model retries; one creation is the event, and five would be noise about the
    same fact.

    FALLBACK. When auto-creation is not possible (not a project chat, already
    pinned, project missing), the old offer is pushed for manual handling.

    SILENT AND CHEAP WHILE THE FLAG IS OFF. `tree_lease_mounts()` is a single
    env read and the very first thing checked, so a turn on today's read-write
    mount does no work here at all -- including no import of the lease layer.
    Any failure below is swallowed: a turn must not die because the platform
    could not phrase an offer about it.
    """
    try:
        from services import scoped_exec
        if turn.write_offer is not None or not scoped_exec.tree_lease_mounts():
            return
        from services import tree_access
        if not tree_access.detect_erofs(payload.get("text")):
            return
        thread_id = str(meta.get("thread_id") or "")
        result = _auto_create_worktree_for_chat(thread_id)
        if result is not None:
            name = result.get("display_name") or result.get("branch_slug") or "new worktree"
            turn.write_offer = result
            write_to_turn(
                turn.id,
                f"Working in new worktree: {name}. "
                "Your write was blocked on the shared read-only tree. "
                "Write access applies from the next turn. "
                "Acknowledge in one sentence only.",
            )
            return
        # Fallback: the chat is not a project chat or is already pinned.
        offer = tree_access.first_write_offer(
            payload.get("text"), current_ref=_current_ref(meta),
            ticket_id=meta.get("ticket_id"))
        if offer is None:
            return
        turn.write_offer = offer
        _push(turn, "write_blocked", offer)
    except Exception as exc:  # pragma: no cover -- defensive, see docstring
        log.debug("turn %s: could not handle write block: %s", turn.id, exc)


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
                    # Output-side confidentiality guard (defence-in-depth behind
                    # the L0 kernel): strip any leaked platform-prompt region from
                    # the live chunk. The committed final_text is scrubbed again
                    # in _turn_content as the guaranteed-clean backstop.
                    out.append(("text", {
                        "content": reply_guard.scrub(
                            _rewrite_cli_copy(block.get("text", ""))),
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
            # THE ONLY PLACE THIS TURN'S SPEND IS ACTUALLY REPORTED.
            # `usage` on a result envelope is all zeros on this CLI build —
            # measured 2026-09-04 over every surviving turn journal, four of
            # four — while `modelUsage` beside it carries the real per-model
            # counts, including the real output count that the assistant frames
            # only ever show as a message_start placeholder. Carried through so
            # `_finalize_turn` can record components instead of a frame sum.
            "modelUsage": ev.get("modelUsage"),
        })
    if t == "system":
        return ("meta", {"subtype": ev.get("subtype")})
    # A PARTIAL-MESSAGE FRAME (from --include-partial-messages). The whole reason
    # we ask for these is the model's reasoning: the final `assistant` message
    # carries the thinking block with its text redacted to "", so the only place
    # the reasoning text lives is the `thinking_delta` chunks here. We take those
    # and NOTHING ELSE from this stream — text_delta and input_json_delta would
    # duplicate what the whole `assistant` message already delivers, so they fall
    # through to `meta` and are ignored. `parent` rides along so a subagent's
    # reasoning nests under its chip rather than joining the parent's work.
    if t == "stream_event":
        se = ev.get("event") or {}
        if se.get("type") == "content_block_delta":
            d = se.get("delta") or {}
            if d.get("type") == "thinking_delta":
                txt = d.get("thinking") or ""
                if txt:
                    return ("thinking", {"content": txt, "parent": parent})
        return ("meta", {"type": t})
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
    if turn.segments and turn.segments[-1].get("t") in ("text", "thinking"):
        turn.segments[-1]["sealed"] = True
    for host in turn.agent_segs.values():
        steps = host.get("steps") or ()
        if steps and steps[-1].get("t") in ("text", "thinking"):
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


def _seg_thinking(turn: "Turn", s: str) -> None:
    """One run of the model's reasoning, coalesced exactly as text is. A thinking
    segment is its OWN kind, so it never merges with the answer text beside it and
    never reaches _turn_content: reasoning is a foldable channel, not the reply."""
    if not s:
        return
    if turn.segments and turn.segments[-1].get("t") == "thinking" \
            and not turn.segments[-1].get("sealed"):
        turn.segments[-1]["text"] += s
    else:
        turn.segments.append({"t": "thinking", "text": s})


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


# THE HARNESS ANSWERS OUR STOP BY REJECTING THE CALL IN FLIGHT, in the same
# words it uses when the OPERATOR refuses one at an approval prompt. The two are
# indistinguishable in the text, and they are opposite facts:
#
#   a refusal   the operator considered this action and declined it. The
#               transcript marks it, because the operator is the remedy.
#   a stop      the operator ended the turn. This call was never considered at
#               all; it was simply cut off, and there is nothing to approve.
#
# Only the backend can tell them apart, because only the backend knows it sent
# the interrupt. Reported 2026-08-16: pressing Stop left the last step reading
# "needs approval", which invites an answer to a question nobody asked.
_STOP_REJECTION = re.compile(
    r"user doesn't want to proceed|tool use was rejected|request interrupted",
    re.I,
)


def _stop_interrupted(turn: "Turn", text: str, is_error: bool) -> bool:
    """Is this result the harness cutting a call off for OUR stop, rather than a
    refusal the operator actually made? Both conditions are required: after a
    stop, an ordinary failure is still an ordinary failure."""
    return bool(is_error and turn.stop_requested and _STOP_REJECTION.search(text or ""))


def _close_tool(turn: "Turn", tool_id: str, text: str, is_error: bool,
                *, interrupted: bool = False) -> bool:
    """Close a top-level tool segment with its outcome and how long it took.

    A FAILED step is never merged into a group and never collapses, so `ok` is
    the single field the whole disclosure state machine turns on. `ms` is
    dropped when the result arrives without a recorded start (a journal tailed
    from mid-stream), rather than being invented as zero.

    `interrupted` is the stop case, and it is closed EXACTLY as `_close_open_tools`
    closes a call that never reported at all: `at` dropped so nothing ticks
    against a clock that stopped, and `ok` left UNSET, which every reader takes
    as "outcome unknown" rather than as either outcome. That the two stop paths
    (no result, and a result that is the stop's own rejection) end up identical
    is the point: both mean the turn was stopped here, and a step should not
    look different depending on which side of the interrupt its result landed.
    The failure text is dropped with it. It says the user rejected the call,
    which is not what happened, and how the TURN ended is already said once, by
    the turn (`meta.stopped`, stopReason.ts).
    """
    seg = turn.tool_segs.pop(tool_id, None)
    if seg is None:
        return False
    if interrupted:
        seg.pop("at", None)
        return True
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
    t = seg.get("t")
    if t in ("text", "thinking") and steps and steps[-1].get("t") == t \
            and not steps[-1].get("sealed"):
        steps[-1]["text"] += seg.get("text") or ""
    else:
        steps.append(seg)
    return True


def answer_segments(segments: list[dict]) -> list[dict]:
    """The TRAILING run of text: what the turn finally said.

    THE TRANSCRIPT HAS TWO KINDS OF `text` AND ONLY ONE OF THEM IS THE ANSWER.
    A turn narrates as it works ("Taking the concrete ones first...", "Both
    landed, starting the next thread") and then, at the end, answers. Both
    arrive as `t: "text"`, and the ONLY thing that distinguishes them is
    position: narration has work after it, the answer does not.

    That rule was implemented in the renderer and nowhere else, so every other
    consumer re-derived it or skipped it. This is the one definition; callers
    use it rather than filtering on `t == "text"` themselves.

    Mirrors AssistantTurn's split in frontend/src/components/chat/Transcript.tsx
    exactly. The two must agree: the same turn is rendered from the client's
    segments while it streams and from these after a reload."""
    cut = len(segments)
    while cut > 0 and segments[cut - 1].get("t") == "text":
        cut -= 1
    return segments[cut:]


def content_from_segments(segments: list[dict]) -> str:
    """The stored reply body for a turn with these segments.

    Split out from `_turn_content` so a caller that has segments but no live
    `Turn` can derive the SAME body: the 2026-08-28 backfill of already-stored
    replies is the reason it exists. A migration that reimplemented this rule
    would be a second definition of "what did the turn say", which is exactly
    the failure mode this whole change is undoing.

    See `_turn_content` for what the rule is and what it replaced."""
    answer = answer_segments(segments)
    joined = "".join(s["text"] for s in answer if s.get("t") == "text").strip()
    if not joined:
        # A TURN THAT ENDED ON A TOOL NEVER ANSWERED, and 12.5% of the operator's
        # replies are that shape: stopped, interrupted, or out of budget mid-step.
        # Storing "" for those would be honest about the answer and useless
        # everywhere content is read as a summary -- the chat list would show a
        # blank row for every interrupted turn.
        #
        # So fall back to the LAST thing the turn said, which is the most recent
        # narration and the best available account of where it got to. One
        # segment, not all of them joined: the bug being fixed here is the
        # joining, and reintroducing it in the fallback would put it back on
        # exactly the replies most likely to be long.
        said = [s for s in segments if s.get("t") == "text" and s.get("text", "").strip()]
        joined = said[-1]["text"].strip() if said else ""
    return reply_guard.scrub(joined)


def _turn_content(turn: "Turn") -> str:
    """The operator-visible answer: the TRAILING run of top-level text.

    NOT every text segment. It used to be all of them joined, which silently
    made the stored reply body the answer with all of the turn's interim
    narration concatenated onto the front of it. Measured 2026-08-27 over the
    operator's own history: 535 of 632 replies (85%) carried narration they
    should not have, 1.82 million characters of it.

    The renderer was never fooled (it splits at the trailing run and always
    has), so this was invisible on screen and wrong everywhere else `content` is
    read: the copy button, the chat-list preview, search, notifications, and the
    context fed back to the next turn. Reported repeatedly as the final response
    "including all of the text that was printed out along the way".

    A subagent's interim text is excluded too, as before: it lives in its own
    segment's steps and is not part of the reply.

    Scrubbed through reply_guard: the guaranteed-clean backstop for the stored
    transcript, so a leaked platform-prompt region never persists even if the
    live per-chunk guard missed a split banner."""
    return content_from_segments(turn.segments)


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
    if kind == "thinking":
        text = payload.get("content", "")
        if not (nested and _agent_step(turn, nested, {"t": "thinking", "text": text})):
            _seg_thinking(turn, text)
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
        if rid:
            text = payload.get("text") or ""
            is_error = bool(payload.get("is_error"))
            interrupted = _stop_interrupted(turn, text, is_error)
            if interrupted:
                # ON THE WIRE TOO. The streaming tab closes its own copy of this
                # step from the frame below, and it cannot make this call: it
                # does not know a stop was requested. Deciding it once, here,
                # is what keeps the live view and the reloaded one from
                # disagreeing about the same step.
                payload["interrupted"] = True
            if _close_tool(turn, rid, text, is_error, interrupted=interrupted):
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


# How often a mid-turn reading is written through to the thread record. The
# reading changes on every model step and an agentic turn here makes hundreds,
# but the thread record is a whole-file rewrite, so writing each one would turn
# the meter into a disk load. 60s is far finer than the gap it closes (a turn
# runs for an hour) and far coarser than the step rate.
_THREAD_CTX_WRITE_EVERY_S = 60.0


def _persist_thread_context(turn: "Turn", meta: dict, state: dict,
                            ctx: int) -> None:
    """Write a fresh main-conversation reading onto the THREAD, mid-turn.

    WHY THIS IS NOT ONLY A FINALIZE CONCERN, measured 2026-09-04 on the
    operator's own thread. `GET /context` reads three sources in order: the
    RUNNING turn's live snapshot, then the thread's stored `context_tokens`,
    then an estimate from the stored transcript. The stored value was written
    only at commit. So:

      - a compaction turn ends with no usable reading and correctly CLEARS the
        stored value (a pre-compact number pinned the meter at its old figure,
        which is what made /compact read as a no-op);
      - nothing then restores it until the NEXT turn commits;
      - between those two events the meter falls all the way through to the
        transcript estimate, which undercounts badly.

    Measured on that thread: the estimate said 21,242 tokens (2%) while the
    model was reporting 234,014 (23%) on the same conversation — 11x low, and
    the operator asked, reasonably, whether context was working at all. Two of
    its three completed turns were compactions, so it had been in that hole
    since the first of them.

    Writing through mid-turn closes it: the very next model call after a
    compaction restores a real number within seconds, and it stays right while
    the turn runs rather than only after it lands.

    The session fence still applies — a measurement is a fact about one live
    window, and a fold that detached that window mid-turn wins. What is new is
    that losing the fence is now LOGGED by chat_store rather than dropped
    silently.
    """
    now = time.time()
    last = state.get("thread_ctx_written_at")
    if last is not None and (now - float(last)) < _THREAD_CTX_WRITE_EVERY_S:
        return
    state["thread_ctx_written_at"] = now
    try:
        chat_store.set_thread_context_tokens(
            turn.thread_id, ctx, measured_session=meta.get("session_id"))
    except Exception as e:  # pragma: no cover — the meter must not cost a turn
        log.debug("turn %s: mid-turn context persist failed: %s", turn.id, e)


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


def _stderr_tail(turn_id: str) -> str:
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
    limit = _STDERR_TAIL_CHARS
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


# Signatures that a `--resume` failed because the SESSION is gone or unusable
# (missing on disk, written under a cwd the CLI can no longer reach, corrupted),
# as opposed to a transient failure a perfectly good session should survive. The
# CLI reports a missing session as "No conversation found with session ID" (see
# session_alive). This is the only class where detaching the thread's session is
# the right self-heal; on anything else, detaching throws away a durable
# transcript the operator was mid-conversation with.
_RESUME_MISS_HINTS = (
    "no conversation found",
    "conversation found with session",
    "could not resume",
    "couldn't resume",
    "unable to resume",
    "failed to resume",
    "session not found",
    "no such session",
)

# Signatures of a TRANSIENT failure: a rate limit, an overloaded upstream, an
# expired credential, a dropped socket. None of these is the session's fault, so
# the session must be kept and retried, not discarded. Auth hints are handled by
# `_looks_like_auth_failure` and folded in below.
_TRANSIENT_HINTS = (
    "overloaded", "rate limit", "rate_limit", "too many requests",
    "429", "529", "503", "502", "500 ", "timeout", "timed out",
    "temporarily", "try again", "session limit", "hit your",
    "connection", "network", "econnreset", "socket hang up", "reset by peer",
)


def _looks_like_resume_miss(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _RESUME_MISS_HINTS)


def _looks_like_transient(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _TRANSIENT_HINTS)


def _resume_failure_is_session_fault(detail: str, produced_output: bool) -> bool:
    """Whether a FAILED `--resume` turn should detach the thread's session.

    True only when the session itself is the suspect. The old rule detached on
    ANY error, which meant a single 529, an expired token, or a dropped socket
    threw away a durable transcript and forced a lossy summary+tail rebuild on
    the next turn. Context must not leave before a compaction, so:

      - an explicit resume-miss signature ("No conversation found") -> the
        session is gone, detach;
      - a recognized transient (rate limit / overload / auth / network) -> the
        session is fine, keep it;
      - no signature either way: a resume that produced NO output died at
        startup, which points at the session (missing or corrupt) -> detach; one
        that produced output loaded the session fine and failed later for some
        other reason -> keep. The no-output arm preserves the original anti-loop
        guard (a genuinely broken session is not retried forever) without
        nuking a good session on a transient error.
    """
    if _looks_like_resume_miss(detail):
        return True
    if _looks_like_auth_failure(detail) or _looks_like_transient(detail):
        return False
    return not produced_output


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
    the anchor for a mid-turn message. A command turn refuses input, so it should
    never have one, but "should never" is what the invariant in carry_cuts
    exists to stop relying on."""
    kept = [s for s in segments or []
            if not (isinstance(s, dict) and s.get("t") == "text")]
    dropped = [s for s in segments or []
               if isinstance(s, dict) and s.get("t") == "text"]
    carry_cuts(dropped, kept, cut_head if cut_head is not None else [])
    return kept


# THE PROVIDER'S CONTEXT-USAGE REPORT, RECOGNISED BY ITS SHAPE.
#
# `_drop_text_segments` above removes the dump from ONE turn, the one whose whole
# prompt is the literal string "/compact". That hinge is too narrow and it is why
# the table came back: a compaction is not only that turn. The CLI compacts
# mid-turn on its own when the window fills (29 `trigger:auto` boundaries on
# record), a queued or carried `/compact` does not always arrive with a prompt
# that compares equal to "/compact", and a local command can answer on a turn
# whose boundary event never reached us. On every one of those the prompt test
# fails, nothing is dropped, and the CLI's own text is committed as the reply.
#
# So the second guard hinges on the TEXT instead: this is never something a model
# wrote, it is the CLI answering locally, and it is recognisable. Same knowledge
# `services/context_report` parses the panel out of, used here to refuse it.
_LOCAL_STDOUT_OPEN = "<local-command-stdout>"
_LOCAL_STDOUT_CLOSE = "</local-command-stdout>"
# The heading the CLI prints the table under, and the older one-sentence form it
# printed before the table existed. Both were reported by the operator.
_CONTEXT_REPORT_HEAD = "## context usage"
_COMPACT_SENTENCE = "conversation compacted"


def _unwrap_local_stdout(text: str) -> str:
    """A local command's output with the CLI's own wrapper taken off.

    The CLI hands local-command output back inside `<local-command-stdout>`,
    both in the session file and (as a bare-string `user` event) on the stream.
    Recognising the payload means looking past the wrapper first."""
    s = (text or "").strip()
    if s.startswith(_LOCAL_STDOUT_OPEN):
        s = s[len(_LOCAL_STDOUT_OPEN):]
        if s.rstrip().endswith(_LOCAL_STDOUT_CLOSE):
            s = s.rstrip()[: -len(_LOCAL_STDOUT_CLOSE)]
    return s.strip()


def _is_context_report(text: str) -> bool:
    """Is this text the CLI's context-usage dump rather than something said?

    DELIBERATELY TIGHT, because the alternative failure is worse. A turn that
    merely MENTIONS the report — this project has whole replies about this bug —
    must not lose its text. So the report has to BE the message: it starts with
    the heading (or the older sentence) and, for the table, carries the token
    line that makes it a reading rather than prose under a borrowed heading."""
    s = _unwrap_local_stdout(text).lower()
    if not s:
        return False
    if s.startswith(_COMPACT_SENTENCE):
        return True
    if not s.startswith(_CONTEXT_REPORT_HEAD):
        return False
    # A reading, not a heading someone reused: the CLI always prints the totals
    # line, and a category table under it.
    return "**tokens:**" in s or "| category |" in s


def _drop_context_report(segments: list[dict],
                         cut_head: list[str] | None = None) -> list[dict]:
    """The same turn with the CLI's context-usage dump removed and everything
    else — prose, tools, subagents — left exactly where it was.

    Narrower than `_drop_text_segments` on purpose. That one runs on a turn whose
    reply is replaced wholesale and can afford to take every text segment; this
    one runs on turns that may be REAL WORK which happened to compact along the
    way, so it removes only the segments that are the dump itself."""
    kept = [s for s in segments or []
            if not (isinstance(s, dict) and s.get("t") == "text"
                    and _is_context_report(s.get("text", "")))]
    dropped = [s for s in segments or []
               if isinstance(s, dict) and s.get("t") == "text"
               and _is_context_report(s.get("text", ""))]
    if dropped:
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


def _proc_alive(pid: int | None, *, starttime: str | None = None) -> bool:
    """Is this turn's agent process still running?

    IS IT STILL *OURS*. `os.kill(pid, 0)` alone answers "does some process have
    this number", and pids are recycled. Turn journals outlive the process that
    wrote them -- across a backend restart by design, and across a reboot by
    accident -- so a recycled pid made a long-dead turn read as live. The drive
    loop then tails a journal nothing is writing, and because `turn_end` is only
    consulted after a `result` line that will never come, and chat turns run with
    no deadline, it spins at 0.15s intervals forever while the thread shows
    "Still running".

    Checking the command line costs one small read and makes the answer specific:
    a recycled pid belongs to something else, and that is not our turn.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists but belongs to another uid -- expected under the uid
        # layer (WORKSHOP_TURN_USER): the host user cannot signal a
        # `workshop-turn`-owned process, but "cannot signal" is not "gone".
        # `starttime` is what still tells "my turn, still running" apart from
        # "something else now holds this number": two processes never share
        # both a pid and a start time, and unlike a signal it is a plain file
        # read, so it works regardless of which uid owns the target (verified:
        # /proc/<pid>/stat is mode 444 on this host). Answering False here
        # unconditionally (the pre-uid-layer behaviour) would make every
        # scoped turn's restart-adoption read as "already dead" the moment
        # isolation actually worked.
        if starttime is not None:
            return _proc_starttime(pid) == starttime
        return True
    if starttime is not None:
        return _proc_starttime(pid) == starttime
    return _is_agent_proc(pid, unknown=True)


# THE CLI'S OWN BACKGROUND-TASK LIFECYCLE, as `system` subtypes. Observed
# directly on 2026-09-01 with this driver's flags: a background Agent emits
# `task_started` at launch and `task_notification` when its result comes back
# (`background_tasks_changed` and `task_updated` ride along and say nothing this
# needs). Named here so the driver and `turn_end` agree on one vocabulary.
_BG_TASK_EVENTS = frozenset({"task_started", "task_notification"})

_AGENT_COMMS = frozenset({"claude", "bwrap", "sudo", "turn-exec"})


def _is_agent_proc(pid: int, *, unknown: bool = False) -> bool:
    """Is this pid an agent process (rather than whatever recycled its number)?

    `/proc/<pid>/comm` is the executable name. Matching on the COMMAND LINE
    instead is too loose: any process whose argv happens to mention claude
    counts, and on this box that includes every shell whose snapshot path
    contains `.claude/`. That false positive turned up immediately in testing.

    Accepts more than "claude" itself: under Layer 1 (bwrap) the pid this
    module records is bwrap's own, not the CLI's -- bwrap forks rather than
    exec-replacing itself (confirmed live: the recorded pid's child, not the
    pid itself, is the "claude" process) -- and under the uid layer the chain
    is sudo -> turn-exec -> bwrap -> claude, any of which this module's
    recorded pid might legitimately be depending on where in the exec chain a
    check lands.

    `unknown` is the answer when procfs cannot say -- True where the caller is
    asking "is my known child still alive" (do not invent a death), False where
    it is scanning for a match (do not invent a process).
    """
    try:
        with open(f"/proc/{pid}/comm", "r") as fh:
            return fh.read().strip() in _AGENT_COMMS
    except OSError:
        return unknown


def _proc_starttime(pid: int) -> str | None:
    """Field 22 of `/proc/<pid>/stat` (start time in clock ticks since boot),
    read verbatim and compared as a string. Two processes never share both a
    pid and a start time, so this is an exact identity check across a pid
    recycle -- and unlike `os.kill(pid, 0)`, reading it is a plain file read
    that does not depend on which uid owns the target (verified: mode 444 on
    this host, readable regardless of owner), which is what makes it the
    mechanism that works under the uid layer.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    # comm (stat field 2) is parenthesised and may itself contain ')' or a
    # space, so split on the LAST ')' rather than tokenizing the whole line.
    parts = raw.rsplit(b")", 1)
    if len(parts) != 2:
        return None
    fields = parts[1].split()
    # fields[0] here is state (stat field 3); starttime is stat field 22, i.e.
    # index 22 - 3 = 19 into this remainder.
    if len(fields) <= 19:
        return None
    return fields[19].decode()


async def _retire_turn_proc(pid: int | None, proc=None, *,
                             user: str | None = None,
                             starttime: str | None = None) -> None:
    """End a turn's process once its `result` line has landed.

    Under `--input-format stream-json` the CLI does not exit when it finishes a
    turn: stdin is still open, so it idles waiting for the next message. The
    result line is the end of THIS turn, so somebody has to retire it or every
    turn leaks a claude process holding its session.

    SIGTERM the whole group first and give it a moment. The group matters
    because the CLI owns children (MCP servers, tool subprocesses) and this now
    runs on every single turn rather than only on a timeout, so orphaning them
    would accumulate fast. SIGKILL is the fallback if it does not go.

    `user` is the uid the spawn actually used (`meta["turn_user"]`, recorded
    then -- not recomputed now, so a self-scope turn carved out of the uid
    layer at spawn is still signalled directly even if the flag happens to be
    set by the time this runs). CROSS-UID, this must go through
    `scoped_exec.signal_pg` and ONLY that: `proc.terminate()` still targets the
    real pid via a plain `os.kill` under the hood and raises PermissionError
    from the host user onto a `workshop-turn`-owned process, and the bare
    `except Exception: pass` this used to fall into would swallow that and
    silently do nothing -- leaking the process and leaving Stop a no-op.
    """
    if user is not None:
        if pid:
            from services import scoped_exec
            try:
                pgid = os.getpgid(pid)
            except OSError:
                pgid = None
            try:
                scoped_exec.signal_pg(pid, signal.SIGTERM, user=user, pgid=pgid)
            except Exception:
                pass
    else:
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
        # waitpid-based, so this still reports correctly regardless of which
        # uid the child actually ran as -- only SIGNALLING it is uid-gated.
        if proc is not None and proc.returncode is not None:
            return
        if pid and not _proc_alive(pid, starttime=starttime):
            return
    _kill_turn_proc(pid, proc, user=user, starttime=starttime)


def _cleanup_turn_input(turn_id: str) -> None:
    """Drop the turn's input channel. Its absence is also the signal that makes
    a later injection fail fast and fall back to the queue instead of hanging."""
    try:
        _stdin_fifo(turn_id).unlink(missing_ok=True)
    except OSError as e:
        log.debug("turn %s: input channel cleanup failed: %s", turn_id, e)


def _kill_turn_proc(pid: int | None, proc=None, *,
                     user: str | None = None,
                     starttime: str | None = None) -> None:
    if user is not None:
        # See `_retire_turn_proc`: proc.kill() is not safe to try cross-uid,
        # so this does not fall through to it on any failure here.
        if pid:
            from services import scoped_exec
            try:
                pgid = os.getpgid(pid)
            except OSError:
                pgid = None
            try:
                scoped_exec.signal_pg(pid, signal.SIGKILL, user=user, pgid=pgid)
            except Exception:
                pass
        return
    # THE GROUP FIRST, exactly as `_retire_turn_proc` does, and for the reason
    # its docstring gives: the CLI owns children (MCP servers, tool
    # subprocesses) and killing only the direct child orphans them.
    #
    # This used to call `proc.kill()` and RETURN, which made the killpg below
    # unreachable in precisely the case that matters — a live `proc` is the
    # normal state when the timeout or the memory ceiling fires. The direct
    # child is the group LEADER (bwrap/timeout), so killing it alone left the
    # `claude` process and its MCP server running, reparented to systemd.
    #
    # Measured 2026-08-22: a turn killed at the 6 GB ceiling left an orphaned
    # pair holding 9.0 GB for over seven hours. The guard fired, reported the
    # turn stopped, and reclaimed none of the memory it fired over — so the
    # ceiling did not do the one thing it exists to do.
    try:
        pgid = os.getpgid(pid) if pid else None
    except OSError:
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        elif proc is not None and proc.returncode is None:
            proc.kill()
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
                     turn_id: str | None = None,
                     effort: str | None = None,
                     permission_mode: str | None = None,
                     attachments: list[dict] | None = None,
                     tree: dict | None = None,
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

    `turn_id` is a turn the caller already claimed (`claim_turn`). Passing one is
    how the route holds the thread from before it starts assembling a prompt —
    which takes awaits, and therefore ticks — through to the spawn. Omit it and
    this claims one itself, raising TurnInFlight if the thread is busy.

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
    from wsplatform import dev_mode

    try:
        dev_mode.assert_run_target(cwd, "a chat turn")
    except dev_mode.DevModeRefusal as e:
        # local: this service module has no other reason to depend on FastAPI,
        # so the import stays here rather than at module scope. Same convention
        # `services/app_runner.py` used before this refusal moved out of it —
        # see wsplatform/dev_mode.py's header for why the dependency could not
        # just move with it.
        from fastapi import HTTPException
        raise HTTPException(e.status, e.message)
    turn = _make_turn(thread_id, agent_slug, turn_id=turn_id)
    if turn_id is None:
        joined = await claim_turn(thread_id, turn.id, model=model, agent=speaker)
        if joined is not None:
            TURNS.pop(turn.id, None)
            raise TurnInFlight(joined)
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
        # THE WORKTREE THIS TURN HOLDS, when it holds one
        # (`services/chat_worktree.enter`, connection C-3 of the leases design).
        # None for every chat that is not pinned to a branch, which is the
        # common case and is byte-for-byte today's behaviour. In meta rather
        # than in a process-local map so a turn adopted after a restart goes on
        # renewing the lease it took rather than silently abandoning a tree it
        # is still writing to.
        "tree": tree or None,
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

    Total, and CHEAP, but no longer pure. It used to say "pure and total"; the
    full-agentic branch now builds the workshop MCP config, which reads the auth
    db and scans the app catalog (measured: 220ms cold, 0.31ms warm). It still
    spawns nothing and still returns the same argv for the same inputs, which is
    what `tests/test_permission_channel.py` actually leans on: the one coupling
    that cannot be got wrong, a permission mode that can ask sent without the
    channel to ask on, stays testable without a process.

    The one caller-visible precondition: the full-agentic branch attaches the
    search server only when an owner is pinned. See that branch for why."""
    cmd: list[str] = [
        CLAUDE_CLI, "-p",
        "--model", meta["model"],
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        # Stream the model's reasoning. The CLI emits a thinking block whose
        # `thinking` field is EMPTY in the final assembled `assistant` message
        # (only a signature survives), so the reasoning text exists only as
        # `thinking_delta` chunks on the partial-message stream. Without this
        # flag those chunks are never sent and the reasoning is lost. With it,
        # the extra `stream_event` frames are additive: text and tool still come
        # from the whole `assistant` message exactly as before, and _parse_stream_event
        # mines these frames ONLY for thinking_delta (everything else is `meta`,
        # ignored), so nothing is double-counted. See _parse_stream_event.
        "--include-partial-messages",
        # Re-emit stdin user messages on stdout once read. This is what turns
        # "the bytes reached the process" into "the turn has read it" — the
        # difference between a chat that is waiting and one that looks broken.
        "--replay-user-messages",
        # NO SUBAGENT HOOK HERE ANY MORE, and that is the fix rather than a
        # loosening. A PreToolUse hook used to deny any Agent/Task call that was
        # not explicitly foreground, because a backgrounded subagent's result
        # never arrived. The cause was never the CLI: it was `_retire_turn_proc`
        # SIGTERMing the process GROUP on the parent's first result, with the
        # subagent inside that group. `turn_end` now counts pending background
        # tasks and holds the turn open until they report, so the thing the hook
        # was preventing cannot happen, and denying the call would only remove a
        # capability that works. Measured end to end 2026-09-01; see turn_end.
    ]
    # WHICH SCOPE this turn's search tool reaches (memory-and-documentation-
    # model-2026-08-14.md sec C). Pinned here, once, before EITHER tool-surface
    # branch below might spawn the workshop MCP server -- the narrow branch via
    # `llm_router.tool_flags`, the full-agentic branch via
    # `llm_router.mcp_config_json` -- exactly where the owner is pinned for the
    # same reason. `_platform_mcp_servers` used to read this straight out of
    # the BACKEND PROCESS's own environment, which nothing ever set, so every
    # turn searched as `workshop` regardless of which chat it actually was; see
    # `app_registry.set_current_query_scope`'s docstring.
    #
    # `chat_store.get_thread` failing (a thread deleted between claiming the
    # run and building this argv) leaves `ctx_key` None, which is exactly the
    # "no chat behind this" case the pin already handles: the env var is
    # omitted and the subprocess falls back to `workshop`, never `development`.
    from services import app_registry, chat_store
    try:
        ctx_key = (chat_store.get_thread(meta.get("thread_id")) or {}).get("context_key")
    except Exception:
        ctx_key = None
    app_registry.set_current_query_scope(ctx_key)
    # And WHICH chat, so the workshop MCP server can offer this thread a
    # background agent (spawn_background) bound to it. Same seam, same reason.
    app_registry.set_current_query_thread(meta.get("thread_id"))
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
        # FULL AGENTIC, NO SELECTION: give it the workshop search server.
        #
        # This branch sends no MCP flags at all and inherits whatever the CLI's
        # own configuration provides — and the workshop server is in neither
        # `~/.claude.json` nor a `<cwd>/.mcp.json`, because it is spawned by us
        # rather than installed. So a project or build chat, the turn with the
        # WIDEST tool surface in the system, was the one that could not search
        # the workshop's own notes, tickets and chats.
        #
        # Additive, never strict: `--strict-mcp-config` would drop every other
        # server the tree legitimately configures. And emitted BEFORE
        # `--permission-mode` because `--mcp-config` is variadic and keeps
        # eating following NON-flag tokens (2026-08-10: `--mcp-config '{...}' hi`
        # sent the CLI looking for a config file called `hi`); a flag right
        # after it terminates the group.
        #
        # Never fatal. A missing launch spec means this turn runs without search,
        # exactly as it did before, rather than failing to start at all.
        # The owner is pinned to THIS CALLER, and when there is no caller the
        # server is simply not attached.
        #
        # This passed `owner_id=ownership.owner_or_none()` and claimed to pin the
        # caller, which was false in exactly the case it was guarding: `None`
        # means "use the default", and `mcp_config_json`'s default is
        # `auth_store.operator_id()`. So an unowned turn got a server scoped to
        # the OPERATOR's notes, tickets and chats, and `mcp_config_json` then
        # called `set_current_owner` as a side effect, converting the documented
        # fail-closed `OwnerUnset` into a silent read of the operator's stores
        # for the rest of that context.
        #
        # A turn with no owner pinned does not get a search tool. That is the
        # only answer that cannot serve one account's data to another, and the
        # cost of being wrong is a turn without search, not a leak.
        from services import ownership
        _owner = ownership.owner_or_none()
        if _owner is None:
            log.debug("no owner pinned for this turn; not attaching the workshop server")
        else:
            try:
                cmd.extend(["--mcp-config", llm_router.mcp_config_json(
                    ["workshop"], owner_id=_owner, context_key=ctx_key)])
            except Exception as e:  # noqa: BLE001 - a search tool is not worth a dead turn
                log.warning("workshop MCP config unavailable for this turn: %s", e)
        cmd.extend(["--permission-mode", mode])
        # This branch never touches tool_flags, so the baseline deny list (which
        # takes AskUserQuestion away from a headless turn — see llm_router) has
        # to be added here too. The widest-surface turn in the system was the one
        # path that could still reach for the interactive tool and lose its own
        # question. One --disallowedTools group only; nothing else adds it here.
        cmd.extend(["--disallowedTools", *llm_router.BASELINE_DISALLOWED_TOOLS])
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
    """End a turn whose drive loop raised, instead of leaving it running forever.

    Nothing else was going to end it. `_drive_turn` had no try/finally and its
    task had no exception handler, so ANY raise inside it — an OSError from
    `_drain` when the journal goes away, a TypeError in `_segment_ingest` on a
    payload shape the CLI has not sent before — skipped `_finalize_turn`
    entirely. `turn.complete` and `turn.finished` were never set, `active_turn_id`
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
        # raise happened after the turn ended and there is nothing left to close.
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
    # waiting for one to die, and a turn that crashed before it spawned would pay
    # that wait for nothing while the operator watches the turn hang.
    if meta.get("pid") or turn.proc is not None:
        try:
            await _retire_turn_proc(meta.get("pid"), turn.proc,
                                     user=meta.get("turn_user"),
                                     starttime=meta.get("pid_starttime"))
        except Exception as e:  # pragma: no cover — best-effort
            log.debug("turn %s: could not retire process after crash: %s", turn.id, e)
    _cleanup_turn_input(turn.id)
    await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)


def _drive_task(turn: Turn, meta: dict, coro) -> "asyncio.Task":
    """Launch a turn's drive loop as a task that CANNOT wedge the thread.

    Two layers, because they fail differently:

      * the coroutine catches its own exceptions and routes them through
        `_end_wedged`, which is what lets the turn commit what it had; and
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
            if SHUTTING_DOWN:
                # NOT an ended turn: a restart. The child is detached and still
                # writing to its journal, which still says "running" -- exactly
                # the state reattach_running_turns() is built to adopt. Ending
                # it here would kill that child (see SHUTTING_DOWN above) and
                # write "this turn ended early" over work that is still going.
                # Let go instead, and let the next process pick it back up.
                log.info("turn %s: releasing across shutdown (child keeps running)", turn.id)
                raise
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
    before the spawn and released when the turn finishes, so a fan-out cannot put
    an unbounded number of CLI processes on the host. The limit is set well above
    normal interactive use; reaching it means something is looping, not that the
    operator is busy.
    """
    async with agent_slots.turn_slot():
        await _spawn_and_drive_inner(turn, meta)


async def _spawn_and_drive_inner(turn: Turn, meta: dict) -> None:
    # The prompt goes in over stdin as a stream-json message rather than as an
    # argv positional, because stdin then STAYS open for the rest of the turn and
    # anything else the operator sends lands at the next step boundary. (It also
    # takes the assembled prompt off the argv length limit.)
    cmd, tool_env = _build_cmd(meta)

    _push(turn, "started", {"cwd": meta["cwd"], "model": meta["model"],
                            "resume": meta["resume"], "effort": meta.get("effort")})
    # NO PLACEHOLDER. The turn is already claimed on the thread (`claim_turn`) and
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
    # see EOF the moment the first injection closed and would end the turn.
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
    # Layer 1 (managed-file-references-and-path-scoping-2026-08-18.md): confine
    # the turn to its scope, mounted at /workspace. On by default where bwrap
    # is functional (WORKSHOP_SCOPED_EXEC unset); =0/=1 force it off/on. cwd
    # stays the real path: that is where bwrap itself starts before entering
    # the namespace. The uid layer (spawn_argv, WORKSHOP_TURN_USER) composes on
    # top and is off by default -- see scoped_exec's module docstring.
    from services import scoped_exec
    try:
        # `session_id` is the lease layer's ([B1]) "who is asking": with
        # WORKSHOP_TREE_LEASE_MOUNT on, this tree is mounted read-write only if
        # this chat holds its worktree lease, and read-only otherwise
        # (workspace-ownership-and-leases-2026-08-22.md §4). The flag is OFF by
        # default and the argv is byte-identical while it is, so passing the id
        # here changes nothing today and means no second edit to this file at
        # the moment the operator turns it on.
        spawn_cmd = scoped_exec.spawn_argv(cmd, meta["cwd"],
                                           session_id=meta.get("thread_id"),
                                           holder_kind="chat")
    except (scoped_exec.TurnUserUnavailable, scoped_exec.ConfinementUnavailable) as e:
        # FAIL-CLOSED: the turn cannot be confined, so it is refused, NOT spawned
        # with host access. Both the filesystem layer (bwrap) and the uid layer
        # raise here rather than degrade to an un-isolated spawn.
        out_f.close(); err_f.close(); os.close(in_fd)
        turn.error = f"turn cannot run confined, refused: {e}"
        _push(turn, "error", {"detail": turn.error})
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
        return
    turn_user = scoped_exec.turn_user(os.path.realpath(meta["cwd"])) if meta.get("cwd") else None
    env = scoped_exec.turn_env(env, meta.get("cwd"))
    try:
        proc = await asyncio.create_subprocess_exec(
            *spawn_cmd, cwd=meta["cwd"], env=env,
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
    # Hand the process to the Turn as well as to the journal: Stop has a turn id
    # and nothing else, and reading the pid back off disk to signal it would be a
    # second source of truth for something this process is holding.
    turn.proc, turn.pid = proc, proc.pid
    # The control-protocol handshake goes FIRST, before the prompt, so the
    # channel is live before the model can reach a tool that needs it.
    if model_caps.needs_approval_channel(meta.get("permission_mode")):
        if not _write_line(turn.id, _initialize_line()):
            log.warning("turn %s: could not open the approval channel; "
                        "gated tool calls will be auto-denied", turn.id)
    # Deliver the prompt as the turn's first message.
    #
    # A FAILED DELIVERY ENDS THE TURN. This warned and carried on, which produced
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
    #
    # AND THE IMAGES A COMPACTION TOOK COME BACK HERE. A compaction replaces the
    # session's history with a text summary, and a summary cannot carry a
    # picture -- measured, see services/attachment_carry, which also has the
    # experiment that shows an ORDINARY turn boundary loses nothing. `due` is
    # `[]` on every turn but the first one after a compaction, so this costs one
    # meta read in the normal case. The carried blocks go FIRST, ahead of
    # anything attached to this message, and `carry_note` says so, because an
    # image block carries no name and the model maps blocks to names by order.
    _atts = meta.get("attachments") or []
    _carry = attachment_carry.due(meta.get("thread_id") or "", meta["prompt"],
                                  meta.get("owner_id"))
    att_blocks = (chat_attachments.content_blocks(_carry, meta.get("owner_id"))
                  + chat_attachments.content_blocks(_atts, meta.get("owner_id")))
    _named = chat_attachments.describe(_atts)
    _carry_note = attachment_carry.describe(_carry)
    _prompt = "\n\n".join([meta["prompt"], *(x for x in (_carry_note, _named) if x)])
    if not write_to_turn(turn.id, _prompt, att_blocks, origin=meta.get("origin")):
        turn.error = "could not deliver the prompt to the turn's input channel"
        _push(turn, "error", {"detail": turn.error})
        # The write failed outright, so the message went nowhere at all. Same
        # loss as one the CLI never read, and the same remedy: record it as an
        # unread delivery so `_requeue_unread` puts it back on the queue and the
        # drain sends it. Nothing else would ever retry it.
        turn.unread.append({"origin": meta.get("origin") or []})
        await _retire_turn_proc(proc.pid, proc, user=turn_user,
                                 starttime=_proc_starttime(proc.pid))
        _cleanup_turn_input(turn.id)
        await _finalize_turn(turn, meta, rc=-1, timed_out=False, compacted=False)
        return
    # ONLY ONCE THE BLOCKS ARE ACTUALLY ON THE CHANNEL. Clearing the mark before
    # the write would spend the one re-send on a delivery that never happened,
    # and the images would then be owed forever with nothing left to notice it.
    if _carry:
        attachment_carry.note_carried(meta.get("thread_id") or "")
    _preempt = (meta.get("tree") or {}).get("preempted")
    if _preempt:
        _push(turn, "preempt_notice", _preempt)
    meta["pid"] = proc.pid
    # RECORDED, not recomputed later: `turn_user` is the uid the spawn actually
    # used (None on the self-scope carve-out even with the flag set), so a
    # later signal/liveness check uses what happened at spawn time rather than
    # re-deriving it from the flag, which could answer differently by then.
    # `pid_starttime` is what makes a later liveness check exact under the uid
    # layer, where `os.kill(pid, 0)` can no longer prove identity by itself
    # (see `_proc_alive`).
    meta["turn_user"] = turn_user
    meta["pid_starttime"] = _proc_starttime(proc.pid)
    turn.turn_user = turn_user
    turn.pid_starttime = meta["pid_starttime"]
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
             # Background subagents launched by this turn, and how many have
             # reported. The difference is what `turn_end` waits on.
             "bg_started": 0, "bg_reported": 0,
             # Seeded at NOW, not at 0: `chat_worktree.enter` wrote the first beat
             # when it took the lease, so the first renewal is due one window
             # from the start of the turn rather than on the first loop pass.
             "tree_beat_at": time.time(),
             "activity_at": time.time(), "seg_count": 0}
    buf = ""
    # RESUME THE JOURNAL, DO NOT REPLAY IT.
    #
    # This was a flat `offset = 0`, so a turn adopted after a restart re-read its
    # whole journal and rebuilt the assistant message from scratch, then
    # persisted the half-rebuilt version over the stored one. From the chat that
    # looks like the conversation spontaneously rewinding mid-reply, and it also
    # discarded `cut_head` (the anchors for messages the operator sent mid-turn,
    # which live only in memory), so those messages lost their place in the
    # reply.
    #
    # The draft already carries the segments; it now carries the journal offset
    # they were built from, written in the SAME call, so the pair is always
    # consistent. Anything after that offset is re-read and appended, which is
    # exactly right because the segments stop there.
    offset = _resume_from(turn)
    last_persist = 0.0

    # RESUMED PAST A FINISHED TURN. A turn that wrote its `result` before a
    # restart is adopted here with `proc is None`, and if the draft resumed us to
    # the end of the journal we will never re-read that result: the tail loop
    # sees no new bytes, `saw_result` stays false, and the idle process (stdin
    # still open) never dies, so nothing ever retires the turn and it spins
    # "running" forever over a reply that is already written. The result is on
    # disk; read it and let the first loop pass finalize from the restored draft.
    #
    # Scoped tightly so it cannot end a turn that is still working: only on the
    # re-attach path (proc is None), only when we resumed to/past end-of-file (any
    # unread bytes are drained normally, which sets saw_result the ordinary way),
    # and only when the journal's LAST event is a result (a turn mid-answer on an
    # injected prompt has later output, so its tail is not a result). See
    # docs/platform-redesign/turn-recovery-terminal-journal-2026-08-17.md.
    if proc is None and offset > 0:
        try:
            _term_size = (_journal_dir(turn.id) / "stream.jsonl").stat().st_size
        except OSError:
            _term_size = -1
        if offset >= _term_size >= 0:
            term = _journal_terminal_result(turn.id)
            if term is not None:
                log.info("turn %s: journal already terminal on re-attach; "
                         "committing the finished reply without re-driving", turn.id)
                state["saw_result"] = True
                state["result_subtype"] = term.get("subtype") or ""
                state["spend_result"] = _spend_tokens_from_usage(term.get("usage"))
                if not state["last_ctx"]:
                    _ctx = _ctx_tokens_from_usage(term.get("usage"))
                    if _ctx:
                        state["last_ctx"] = _ctx

    def _persist() -> None:
        # The live snapshot, not the log. The journal on disk is the buffer and
        # this is the copy a reload reads; the record gets one write, at commit.
        #
        # `offset` rides along so a re-attach can resume instead of replaying.
        # It MUST be written with the segments it corresponds to; two separate
        # writes would let a restart land between them and resume at a position
        # the segments do not reach (dropping output) or overshoot (duplicating).
        try:
            chat_store.update_turn(
                turn.thread_id, turn.id, turn.segments, _turn_content(turn),
                turn.cut_head, journal_offset=offset,
                context_tokens=turn.context_tokens)
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
                if ctx and ctx != state["last_ctx"]:
                    state["last_ctx"] = ctx
                    turn.context_tokens = ctx
                    # The meter's live feed. Without this the reading exists
                    # only in `state` until finalize, so the composer meter
                    # cannot move during a turn no matter what the client does:
                    # GET /context serves the LAST turn's number. One small
                    # event per model step, only when the number changed.
                    _push(turn, "context", {"tokens": ctx})
                    # AND ONTO THE THREAD, which is what the meter reads BETWEEN
                    # turns. See _persist_thread_context: the finalize-only write
                    # leaves a real hole after a compaction.
                    _persist_thread_context(turn, meta, state, ctx)
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
            # The per-model components, which is what the ledger row stores and
            # what a dollar figure needs. `spend_result` above stays as the
            # backward-compatible integer for the `tokens` column, but on this
            # CLI build it is ZERO (see the modelUsage comment in
            # `_parse_stream_event`), which is why the `or` chain below it has
            # always fallen through to the inflated frame sum.
            if isinstance(payload.get("modelUsage"), dict):
                state["model_usage"] = payload["modelUsage"]
            if payload.get("total_cost_usd") is not None:
                state["vendor_cost_usd"] = payload["total_cost_usd"]
            # Fallback only: the result usage can be a per-turn SUM across tool
            # rounds, so it overcounts the window. Use it only if no assistant
            # message reported usage.
            if not state.get("last_ctx"):
                ctx = _ctx_tokens_from_usage(payload.get("usage"))
                if ctx:
                    state["last_ctx"] = ctx
            _push(turn, "result", payload)
        else:
            if kind in ("text", "tool", "tool_result", "permission", "thinking"):
                saw_tool = _segment_ingest(turn, kind, payload)
                if kind == "tool_result" and payload.get("is_error"):
                    _maybe_write_blocked(turn, meta, payload)
            elif kind == "meta" and payload.get("subtype") == "compact_boundary":
                state["compacted"] = True
            elif kind == "meta" and payload.get("subtype") in _BG_TASK_EVENTS:
                # A BACKGROUND SUBAGENT IS WORK THIS TURN IS STILL OWED, and
                # these are the CLI's own words for its lifecycle. Verified
                # against the real CLI 2026-09-01: `task_started` on launch,
                # `task_notification` when it reports back. Between them the
                # parent's own `result` can land, and retiring on it kills the
                # subagent, because it lives in this turn's process group.
                # `turn_end` reads the difference and holds the turn open.
                sub = payload.get("subtype")
                if sub == "task_started":
                    state["bg_started"] += 1
                else:
                    state["bg_reported"] += 1
                # A notification is real activity, so it resets the silence clock
                # the background bound measures.
                state["activity_at"] = time.time()
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
                    _kill_turn_proc(meta.get("pid"), proc, user=meta.get("turn_user"),
                                    starttime=meta.get("pid_starttime"))
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
                    and len(turn.segments) > int(state["seg_at_result"])),
                # Clamped: a `task_notification` can belong to a task started by
                # a PREVIOUS process (the resume case turn_end's header
                # describes), which would otherwise drive this negative.
                background_pending=max(
                    0, int(state["bg_started"]) - int(state["bg_reported"])))):
            # THE FIRST RESULT IS NOT THE END OF THE TURN, only the end of one
            # request. Retiring on it killed whatever the CLI had started for a
            # second prompt — an injected message, or the task-notification the
            # resume path delivers ahead of ours. See services/turn_end.py.
            await _retire_turn_proc(meta.get("pid"), proc, user=meta.get("turn_user"),
                                     starttime=meta.get("pid_starttime"))
            break
        if turn.stop_requested and not turn.stop_signalled:
            # The operator stopped a turn whose process did not exist yet when
            # they pressed it (the claim window: a turn owns the thread from
            # before its prompt is assembled). stop_turn had nothing to signal
            # then, so the loop signals it now — same helper, same interlock, so
            # the group is signalled exactly once either way.
            turn.stop_signalled = True
            await _retire_turn_proc(meta.get("pid"), proc, user=meta.get("turn_user"),
                                     starttime=meta.get("pid_starttime"))
        alive = (proc.returncode is None) if proc is not None else \
            _proc_alive(meta.get("pid"), starttime=meta.get("pid_starttime"))
        if not alive:
            _drain(final=True)
            _persist()
            break
        if deadline is not None and time.time() > deadline:
            timed_out = True
            _kill_turn_proc(meta.get("pid"), proc, user=meta.get("turn_user"),
                             starttime=meta.get("pid_starttime"))
            _drain(final=True)
            break
        _renew_tree(meta, state)
        await asyncio.sleep(0.15)

    _cleanup_turn_input(turn.id)
    turn.final_text = _turn_content(turn)
    # THE STOP IS READ AFTER THE FINAL DRAIN, never before it. Everything the
    # process wrote before the signal reached it is on disk, and the drain above
    # is what turns those bytes into the segments this turn commits.
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
        # the turn is simply gone, which is a different thing from a crash and
        # deserves to be named as one. Unless we ended it ourselves: a turn the
        # operator stopped is accounted for, and calling it lost would tell them
        # the backend dropped something they were holding the button on.
        rc = -1
        lost = not stopped
    await _finalize_turn(turn, meta, rc=rc, timed_out=timed_out, compacted=state["compacted"],
                         context_tokens=int(state.get("last_ctx") or 0),
                         spend_tokens=int(state.get("spend_result") or state.get("spend_sum") or 0),
                         model_usage=state.get("model_usage"),
                         vendor_cost_usd=state.get("vendor_cost_usd"),
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
    restarted under a detached turn — the result never comes, so `done` stayed
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
            chat_store.update_turn(turn.thread_id, turn.id, turn.segments,
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
                                   attachments=entry.get("attachments") or None,
                                   sender=entry.get("sender") or None)
                sent += 1
            except Exception as e:  # pragma: no cover — the queue is a file write
                log.warning("turn %s: could not requeue an unread message: %s", turn.id, e)
    if sent:
        log.warning("turn %s: %d message(s) were never read by the CLI; "
                    "requeued for the next turn", turn.id, sent)
    return sent


async def _finalize_turn(turn: Turn, meta: dict, **kw) -> None:
    """Commit the turn and wake whoever is waiting on it.

    Always wakes, even if the commit raised. The blocking POST /messages path
    awaits `turn.finished`, and a finalize that fails silently would leave that
    request hanging for as long as the client will hold the socket open."""
    # BEFORE anything that can raise. A tree left held by a finished turn is
    # the orphaned worktree this whole design exists to remove, and the commit
    # below is the part with failure modes.
    _release_tree(meta)
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
                       unread: bool = False,
                       model_usage: dict | None = None,
                       vendor_cost_usd: float | None = None) -> None:
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
    # FIRST, AND WHATEVER BRANCH BELOW ENDS UP RUNNING: the dump is not a reply
    # and is never committed as one.
    #
    # The branch below replaces the reply of ONE turn, the one whose prompt is
    # exactly "/compact". That is the right rule for the SENTENCE it writes, and
    # the wrong rule for throwing the dump away, and the difference is why the
    # operator reported the table again on 2026-09-01. `/compact` stopped being a
    # Workshop-side fold and became a provider turn again on 2026-08-29
    # (`5be52599`), which put the CLI's own output back on the wire after eleven
    # weeks in which no dump could reach the transcript and nothing exercised the
    # narrow guard. Every compaction that is not that exact prompt — the CLI's
    # own mid-turn auto-compaction, a `/compact` whose prompt was carried or
    # queued, one whose boundary event never arrived — walked straight past it
    # into `elif turn.final_text`, and the dump was committed verbatim.
    #
    # So it is removed HERE, by what it is rather than by which prompt asked for
    # it, from the segments the transcript draws AND from the text it falls back
    # to. Scoped to turns where the CLI answered locally (a compaction happened,
    # or the prompt was a slash command) so an ordinary reply is never touched.
    if compacted or is_command:
        turn.segments = _drop_context_report(turn.segments, turn.cut_head)
        if _is_context_report(turn.final_text):
            turn.final_text = ""
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
        # TWO DIFFERENT SLASH-COMMAND CASES share this branch.
        #
        # INJECTED: a slash command was delivered INTO a running turn whose
        # original prompt was NOT a slash command (turn.injected_command = True
        # and prompt does not start with "/"). The CLI handles the injected
        # command locally. Any segments are from the original prompt's model
        # work, not from the slash command. "Command ran." is correct — the
        # command did run — and "send it without the slash" is wrong because
        # the original prompt was not a slash command.
        #
        # NON-INJECTED: the prompt itself started with a slash. Headless mode
        # parses a leading slash into a local-command block, and whether the
        # command then runs depends on the command: /compact does run (measured
        # 2026-08-27, see routes/chat.py:compact_thread), /autocompact answers
        # "isn't available in this environment", and either way the model is
        # not called, so rc 0 WITH NOTHING TO SHOW does not mean "it ran in
        # this chat" and must not be reported as a model reply.
        # /compact and /clear no longer come through here — they are
        # server-side now (routes/chat.py:compact_thread). This handles every
        # other slash the operator can still type.
        injected_only = turn.injected_command and not prompt.strip().startswith("/")
        if turn.segments or injected_only:
            text = "Command ran."
        else:
            # Non-injected slash: the CLI executed the command in its own
            # terminal and never called the model. "Nothing ran" was wrong
            # (the command did run) and "send without slash" was wrong advice.
            text = "The CLI ran this as a local command. Output went to your terminal, not here."
        is_error = False
    elif rc == 0:
        text = _clean_exit_text(turn.segments, unread=unread)
        is_error = False
    elif lost:
        # The operator saw "ended unexpectedly (exit -1)" over and over without
        # ever being told what it meant. This is that case, named: the backend
        # was restarted while the turn was in flight and the turn did not survive.
        text = chat_store.LOST_TURN_TEXT
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
            from services import task_scope
            # A REPLAY MUST NOT FILE THE TICKET TWICE.
            #
            # These blocks are the turn's writes to the world outside the chat.
            # After a restart the reply is re-derived from the journal, so it
            # carries the same task-patch, learning-note and note blocks it
            # carried the first time -- and re-running them files a duplicate
            # ticket and writes a duplicate note, silently.
            #
            # PER KIND, AND RECORDED AS EACH ONE LANDS. A single "blocks_applied"
            # flag written after all four would still replay whatever had already
            # been applied when a crash landed in the middle of them. Each helper
            # uses `.search`, so a reply carries at most one block of each kind:
            # marking the kind the moment it succeeds is therefore exact, not an
            # approximation, and there is no window left between them.
            #
            # The receipts are stored alongside so a replayed reply still says
            # "Saved a note to workshop" and still links to the note that was
            # actually written, rather than losing the record of its own effects.
            _done_kinds = set(meta.get("applied_kinds") or [])
            # Older journals carry the previous whole-block flag; honour it as
            # "all of them", so a turn in flight across this deploy is not
            # re-applied.
            if meta.get("blocks_applied"):
                _done_kinds |= {"actions", "tasks", "learnings", "notes"}
            if _done_kinds:
                applied_notes += list(meta.get("applied_notes") or [])
                receipts.extend(meta.get("applied_receipts") or [])

            def _mark_kind(kind: str) -> None:
                """Record that this kind's write is done, before the next runs."""
                _done_kinds.add(kind)
                try:
                    meta["applied_kinds"] = sorted(_done_kinds)
                    meta["applied_notes"] = list(applied_notes)
                    meta["applied_receipts"] = list(receipts)
                    _write_meta(meta)
                except Exception as e:  # pragma: no cover - best effort
                    log.debug("turn %s: could not record applied %s: %s", turn.id, kind, e)

            if "actions" in _done_kinds:
                pass
            elif type(provider).apply_actions is not chat_context.ContextProvider.apply_actions:
                new_text, pnotes = provider.apply_actions(new_text)
                applied_notes += (pnotes or [])
                _receipt("action", pnotes or [])
                _mark_kind("actions")
            # O2: apply a task-patch block against this chat's task scope.
            if "tasks" in _done_kinds:
                new_text = task_scope.strip_block_kind(new_text, "tasks")
            elif _permitted("tasks"):
                new_text, tnotes = task_scope.apply_task_patch(
                    ctx_key, new_text, (thread or {}).get("id"), (thread or {}).get("mode"))
                applied_notes += tnotes
                _receipt("ticket", tnotes)
                _mark_kind("tasks")
            else:
                new_text = task_scope.strip_task_patch(new_text)
                refusal = f"(a {_role or 'roleless'} agent may not file tickets)"
                applied_notes.append(refusal)
                _receipt("ticket", [refusal])
                _mark_kind("tasks")
            # Phase 4: capture an explicit operator preference/correction emitted
            # as a ``learning-note`` block into the learnings store.
            if "learnings" in _done_kinds:
                new_text = task_scope.strip_block_kind(new_text, "learnings")
            elif _permitted("learnings"):
                new_text, lnotes = task_scope.apply_learning_note(ctx_key, new_text)
                applied_notes += lnotes
                _receipt("learning", lnotes)
                _mark_kind("learnings")
            # Phase N3: capture a durable scoped ``note`` into the knowledge store.
            if "notes" in _done_kinds:
                new_text = task_scope.strip_block_kind(new_text, "notes")
            elif _permitted("notes"):
                # The note is stamped with WHO WROTE IT: an @mentioned agent's
                # note and the assistant's are otherwise byte-identical when
                # either is recalled later. See services/origin.
                from services import origin as _origin
                new_text, nnotes, nrefs = await task_scope.apply_note_block(
                    ctx_key, new_text,
                    author=(_origin.agent(_speaker) if _is_agent and _speaker else _origin.SELF),
                    thread_id=(thread or {}).get("id"),
                )
                applied_notes += nnotes
                _receipt("note", nnotes, nrefs)
                _mark_kind("notes")
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
    # watching — so it lands below every message this turn was handed and above
    # nothing. `lost` is recorded as such rather than as a normal finish.
    turn.notes = notes
    try:
        turn.committed = chat_store.commit_turn(
            turn.thread_id, turn.id, turn.segments, text,
            error=is_error, timed_out=timed_out, notes=notes, receipts=turn_receipts,
            cut_head=turn.cut_head,
            agent=meta.get("speaker"), state=("lost" if lost else "finished"),
            # A stopped turn is FINISHED, not lost: it produced what it produced
            # and nothing is coming later. `stopped` is what tells the operator
            # apart from a restart in the transcript.
            stopped=(chat_store.STOPPED_BY_OPERATOR if stopped else None),
        )
    except Exception as e:
        log.warning("turn %s: chat_store commit failed: %s", turn.id, e)
    # A native compact seen mid-turn (`compact_boundary`, above) gets the same
    # divider a manual compact gets -- see chat_store.note_auto_compact. Only
    # once the commit above confirms the thread still exists, and best-effort:
    # a marker write must never fail a turn the operator already received.
    if compacted and turn.committed:
        try:
            chat_store.note_auto_compact(turn.thread_id)
        except Exception as e:
            log.warning("turn %s: auto-compact marker failed: %s", turn.id, e)
    # Self-heal a bad resume, but ONLY when the session is actually the fault.
    # A turn resuming this thread's session that fails is not proof the session
    # is broken: a rate limit, an overloaded upstream, an expired token, or a
    # dropped socket fails the turn while the transcript on disk is perfectly
    # good. Detaching on every error (the old rule) discarded that transcript and
    # forced a lossy summary+tail rebuild next turn, which is context leaving
    # before a compaction. So detach only on a genuine resume miss (or a startup
    # failure that produced nothing and carries no transient signature); keep the
    # session on a transient failure and retry it. The original anti-loop guard
    # (a truly broken session is not resumed forever) is preserved by the
    # no-output arm of _resume_failure_is_session_fault.
    if is_error and meta.get("resume") and meta.get("session_id"):
        detail = f"{turn.error or ''} {_stderr_tail(turn.id)}"
        produced = bool(turn.final_text) or _did_visible_work(turn.segments)
        if _resume_failure_is_session_fault(detail, produced):
            try:
                thread = chat_store.get_thread(turn.thread_id)
                if thread and thread.get("claude_session") == meta["session_id"]:
                    chat_store.clear_thread_session(turn.thread_id)
                    log.info("turn %s: detached failed session %s (session fault)",
                             turn.id, meta["session_id"])
            except Exception as e:
                log.debug("turn %s: session detach failed: %s", turn.id, e)
        else:
            log.info("turn %s: resume errored but the session is not the fault "
                     "(transient or post-load failure); keeping session %s",
                     turn.id, meta["session_id"])
    # Record the real context occupancy reported by the model this turn, so the
    # composer meter reflects the live window (and its post-compaction drop)
    # instead of the ever-growing stored transcript. Only on a successful turn
    # with a real reading; a failed/timed-out turn keeps the prior value.
    if context_tokens > 0 and not is_error:
        try:
            # Fenced on the session the number describes: a fold or /clear that
            # detached the window mid-turn wins over a measurement of the window
            # it detached. chat_store.set_thread_context_tokens has the story.
            chat_store.set_thread_context_tokens(
                turn.thread_id, context_tokens,
                measured_session=meta.get("session_id"))
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
    #
    # COMPONENTS, NOT ONE INTEGER, when the turn reported them. The `tokens`
    # column has held three incompatible quantities at once — this writer summed
    # raw usage over stream FRAMES (duplicated once per content block, and
    # counting cache reads at parity), the worker weighted cache reads at 0.1,
    # and compaction stored a chars/4 estimate — and window_usage added them
    # together. `modelUsage` fixes the source (real, deduplicated, per model,
    # with the real output count) and storing the components fixes the unit,
    # because a row that keeps them can be re-expressed later and a row that
    # keeps only a total never can.
    try:
        from services import token_usage, usage_ledger
        parsed = usage_ledger.usage_from_envelope(
            {"type": "result", "modelUsage": model_usage,
             "total_cost_usd": vendor_cost_usd})
        comp = parsed["components"]
        tokens = spend_tokens
        if comp:
            # The rate-limit-shaped figure, so this row is finally in the SAME
            # unit as the worker rows it sits beside in one window.
            tokens = token_usage.effective(token_usage.Usage(**comp))
        if tokens > 0 or comp:
            usage_ledger.record(
                usage_scope_for_thread(turn.thread_id),
                tokens,
                role="chat",
                run=turn.id,
                chat=turn.thread_id,
                components=comp,
                models=parsed["models"],
                model=parsed["model"],
                source=parsed["source"],
                vendor_cost_usd=parsed["vendor_cost_usd"],
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

    # A SECOND CHANCE, NOT THE FIRST ONE. Naming is primarily triggered when the
    # operator's message lands (chat_store.append_message), because that is when
    # the subject arrives; this covers the case where that attempt found no topic
    # and the reply has since supplied one.
    #
    # NO is_error GUARD. There used to be one, and it made the two ways a turn
    # can fail behave oppositely: an errored turn never named, while a turn the
    # operator STOPPED did, because a stop commits with is_error False. Neither
    # was intended and nothing said so here. The guard was also pointless: the
    # namer reads the thread itself and titles it from the operator's message,
    # which is present and perfectly nameable whether or not the reply worked.
    # Refusing to name a chat because its first turn failed is precisely when a
    # name is most useful, since that chat is the one being reopened.
    try:
        from services import chat_naming
        chat_naming.schedule(turn.thread_id)
    except Exception as e:  # pragma: no cover — best-effort
        log.debug("turn %s: auto-name schedule failed: %s", turn.id, e)

    # Auto-checkpoint the working tree (never the self/platform monorepo).
    #
    # is_IN_self_repo, not is_self_dir. The old check asked whether cwd WAS the
    # repo root, but `git add -A` is repo-wide from any subdirectory, so a turn
    # running in backend/, shared/, apps/ or branches/ passed the check and then
    # committed the whole monorepo -- other agents' unfinished files included.
    try:
        from services import checkpoints
        from wsplatform import dev_mode
        if cwd and not dev_mode.is_in_self_repo(cwd):
            paths = _written_paths(turn.id, cwd)
            sha = await asyncio.to_thread(
                checkpoints.checkpoint, cwd, f"{turn.agent_slug} turn", paths
            )
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


def _recover_pid(session_id: str | None) -> int | None:
    """Find a still-running agent process by the session id it was spawned with.

    Closes the gap between the child being spawned and its pid reaching the
    journal. If the backend dies in there, the journal says `pid: null` while a
    real agent is running and writing to it -- and the re-attach then read that
    as "died before it started", finalised the turn as lost, and deleted the
    journal out from under a process that was still working. The turn's output
    went nowhere and the agent was left orphaned, holding the session the next
    turn would try to resume.

    The session id is in the journal and on the process's command line, so the
    orphan is identifiable. Returns None when there is no match, which lands on
    the old behaviour: treat it as dead.
    """
    if not session_id:
        return None
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            # Identity first (cheap, and exact), then the session id. Testing
            # the command line alone matched this scan's own shell.
            if not _is_agent_proc(pid):
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue  # exited between the two reads
            if session_id in cmd:
                return pid
    except OSError:
        return None
    return None


def _journal_terminal_result(turn_id: str) -> dict | None:
    """The turn's own terminal `result` payload if its journal ends on one.

    A finished turn's LAST journal event is its `result` line: the CLI emits one
    per prompt it reads and does not exit afterwards (stdin stays open). So the
    last non-empty line parsing as a `result` means the model is done and only
    the idle process remains. Reads the tail only -- a long journal is not read
    into memory to look at its final line.

    Returns None on any doubt (empty, unreadable, last line is not a result),
    because the caller uses this to END a turn and every uncertainty must resolve
    to "keep waiting", not "stop".
    """
    p = _journal_dir(turn_id) / "stream.jsonl"
    try:
        size = p.stat().st_size
        if not size:
            return None
        with open(p, "rb") as fh:
            # The last event is one line; 64KB is generous headroom for it.
            if size > 65536:
                fh.seek(size - 65536)
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    line = ""
    for cand in tail.splitlines():
        if cand.strip():
            line = cand
    if not line:
        return None
    try:
        ev = json.loads(line)
    except Exception:
        return None
    return ev if isinstance(ev, dict) and ev.get("type") == "result" else None


def _resume_from(turn: "Turn") -> int:
    """Where in the journal this turn's already-rendered segments end.

    Returns 0 -- today's full replay -- unless the stored draft gives a complete,
    self-consistent place to resume from. Every uncertainty resolves to 0,
    because replaying is merely ugly while resuming from a wrong offset drops or
    duplicates part of the reply.

    Only meaningful on re-attach: a fresh turn has no draft and starts at 0
    anyway. Restores the segments and the mid-turn cut anchors alongside the
    offset, since the offset is only correct RELATIVE to them.
    """
    try:
        from services import chat_log
        live = chat_log.read_live(turn.thread_id)
    except Exception:
        return 0
    if not live or live.get("turn_id") != turn.id:
        return 0
    off = live.get("journal_offset")
    segs = (live.get("meta") or {}).get("segments")
    # An older draft, written before offsets were recorded, cannot be resumed
    # from: its segments are real but we do not know where they stop.
    if not isinstance(off, int) or off <= 0 or not isinstance(segs, list) or not segs:
        return 0
    try:
        if off > _journal_dir(turn.id).joinpath("stream.jsonl").stat().st_size:
            return 0  # journal shorter than the offset: not the file we indexed
    except OSError:
        return 0
    turn.segments = list(segs)
    ch = (live.get("meta") or {}).get("cut_head")
    if isinstance(ch, list):
        turn.cut_head = list(ch)
    log.info("turn %s: resuming journal at byte %d (%d segments restored)",
             turn.id, off, len(segs))
    return off


_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _written_paths(turn_id: str, cwd: str) -> list[str]:
    """The files THIS turn wrote, repo-relative, read back from its journal.

    A checkpoint should stage the turn's own work and nothing else. The journal
    already records every tool call, so the set is recoverable without threading
    state through the drive loop -- and it is still on disk here, because the
    journal is not removed until after the commit.

    Returns [] when it cannot tell, which the caller treats as "fall back to
    staging everything": a messy checkpoint is recoverable, a missing one is not.
    """
    out: list[str] = []
    try:
        base = Path(cwd).resolve()
        seen = set()
        with open(_journal_dir(turn_id) / "stream.jsonl", encoding="utf-8") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                for block in (ev.get("message") or {}).get("content", []) or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in _WRITE_TOOLS:
                        continue
                    fp = (block.get("input") or {}).get("file_path")
                    if not fp:
                        continue
                    try:
                        rel = Path(fp).resolve().relative_to(base)
                    except Exception:
                        continue  # wrote outside this tree; not ours to commit
                    if str(rel) not in seen:
                        seen.add(str(rel))
                        out.append(str(rel))
    except Exception:
        return []
    return out


def _already_committed(meta: dict) -> bool:
    """Did this turn's reply already land on its thread before we went down?

    One field, read from disk: a thread holds `active_turn_id` from the moment a
    turn is claimed until `commit_turn` appends the reply and clears it. So the
    field still naming this turn means the commit had NOT happened; anything else
    means it had (or the turn was explicitly released), and re-driving would
    duplicate it. Falls back to False -- re-drive, which is recoverable -- when
    the answer cannot be read, because the alternative is silently dropping a
    reply that never committed.
    """
    thread_id = meta.get("thread_id") or ""
    turn_id = meta.get("turn_id") or ""
    if not thread_id or not turn_id:
        return False
    try:
        return chat_store.active_turn_id(thread_id) != turn_id
    except Exception:
        return False


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
                # thread still points at this turn; reconcile_threads() closes it
                # out with a `lost` message right after this pass.
                shutil.rmtree(d, ignore_errors=True)
                continue
            try:
                ownership.set_current_owner(int(owner_id))
                if not chat_store.get_thread(meta.get("thread_id") or ""):
                    shutil.rmtree(d, ignore_errors=True)
                    continue
                # THE REPLY MAY ALREADY BE ON THE THREAD. commit_turn appends the
                # reply AND clears active_turn_id, but the journal is only marked
                # done afterwards -- and the git checkpoint that runs in between
                # takes seconds. A restart inside that window leaves a journal
                # still reading "running" for a turn whose reply is committed.
                #
                # This pass used to check only status and thread existence, so it
                # re-drove that turn, replayed the journal, and committed AGAIN:
                # a duplicate assistant message, plus a second application of
                # every task-patch, note and learning block the turn carried.
                # That is the one restart outcome that is not recoverable by
                # waiting, so it is checked before anything else is done.
                #
                # The thread no longer naming this turn is the durable record that
                # the commit won. A genuinely interrupted turn (window A) still
                # holds the field and is still adopted below.
                if _already_committed(meta):
                    log.info(
                        "turn %s: reply already committed before shutdown; "
                        "retiring journal instead of re-driving", tid,
                    )
                    _mark_meta(meta, "done")
                    shutil.rmtree(d, ignore_errors=True)
                    continue
            except Exception:
                continue
            turn = Turn(id=tid, thread_id=meta["thread_id"], agent_slug=meta.get("agent_slug", ""))
            turn.started = meta.get("started", time.time())
            # Re-derived from the journal, not carried in memory: a turn adopted
            # after a restart must still refuse messages it will never read.
            turn.accepts_input = not (meta.get("prompt") or "").strip().startswith("/")
            # This process did not spawn it, but the process it belongs to did:
            # its input channel is either on disk or gone for good, which is the
            # distinction `turn_is_ending` needs to make. The pid comes off the
            # journal for the same reason — an adopted turn must still be
            # stoppable, and it is a process group like any other.
            turn.spawned = True
            # No pid recorded, but the agent may well be running: the backend can
            # go down between spawning the child and journalling its pid. Look for
            # it by session id before concluding it is dead, because concluding
            # that deletes the journal a live agent is writing to.
            if not meta.get("pid"):
                found = _recover_pid(meta.get("session_id"))
                if found:
                    log.info("turn %s: recovered orphaned agent pid %s by session id", tid, found)
                    meta["pid"] = found
                    try:
                        _write_meta(meta)
                    except Exception:
                        pass
            turn.pid = meta.get("pid")
            turn.turn_user = meta.get("turn_user")
            turn.pid_starttime = meta.get("pid_starttime")
            TURNS[tid] = turn
            alive = _proc_alive(meta.get("pid"), starttime=meta.get("pid_starttime"))
            log.info("re-attaching build turn %s (pid=%s alive=%s)", tid, meta.get("pid"), alive)
            _drive_task(turn, meta, _drive_turn(turn, meta, proc=None))
    except Exception as e:  # pragma: no cover — never block startup
        log.warning("reattach_running_turns failed: %s", e)


def reconcile_threads() -> None:
    """Startup hook, second pass: close out any thread still pointing at a turn.

    `meta.active_turn_id` is the whole reattachment story now — one field, not a
    backward scan for a streaming flag. So a thread is stuck if and only if that
    field names a turn this process is not driving, and the fix is to say so in
    the transcript: a `turn: lost` marker and a message in the same plain wording
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
                    turn_id = chat_store.active_turn_id(thread_id)
                    if not turn_id or turn_is_live(turn_id):
                        continue
                    log.info("thread %s: turn %s did not survive; recording it as lost",
                             thread_id, turn_id)
                    chat_store.lose_turn(thread_id, turn_id)
        finally:
            if prior is None:
                ownership.clear_current_owner()
            else:
                ownership.set_current_owner(prior)
    except Exception as e:  # pragma: no cover — never block startup
        log.warning("reconcile_threads failed: %s", e)


def get_turn(turn_id: str) -> Turn | None:
    return TURNS.get(turn_id)


# How long a stop waits for the turn to commit before answering anyway. The kill
# itself is bounded (_retire_turn_proc: ~2s of SIGTERM, then SIGKILL) and the
# drive loop polls at 0.15s, so this is slack, not a budget. Answering late is
# fine; answering before the commit would tell the client the turn is over while
# the events endpoint still reports it active.
STOP_WAIT_S = 20.0


async def stop_turn(turn_id: str) -> dict:
    """END a turn: signal its process group, commit what it produced, release the
    thread. What the Stop button does, and the only thing that does it.

    ONE PIPELINE. This does not write a message. It kills the process and lets
    the drive loop finish the turn the way every other ending finishes it — final
    drain, segments folded, `chat_store.commit_turn` once at the tail — with
    `meta.stopped` naming the operator. A second finalize path here is exactly
    what the rebuild collapsed, and it would lose the partial output on the way.

    IDEMPOTENT. A turn that already finished, one this process never held (a
    restart orphan, which the startup reconcile owns), and one another request is
    already stopping all return without raising: `stopped` says whether this call
    found a turn to end, and neither answer is an error.

    The check-and-claim is under the thread's WRITE LOCK, like every other turn
    transition, and holds no await — so two Stops cannot both claim the kill, and
    a Stop cannot land between a rival send's check and its claim. The kill and
    the wait happen outside the lock: they take seconds, and nothing else on the
    thread should block on them."""
    turn = TURNS.get(turn_id)
    if turn is None:
        return {"turn_id": turn_id, "stopped": False,
                "reason": "no turn by that id is going here"}
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
        user, starttime = turn.turn_user, turn.pid_starttime
    if signal_now:
        log.info("turn %s: stopped by the operator; signalling pid %s", turn_id, pid)
        await _retire_turn_proc(pid, proc, user=user, starttime=starttime)
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
        if SHUTTING_DOWN:
            # Let go of the stream so the process can actually go down.
            #
            # This generator otherwise only ends when the turn does, so every
            # open chat tab pinned uvicorn's graceful shutdown open: with no
            # --timeout-graceful-shutdown set, uvicorn waits forever and systemd
            # SIGKILLed it at TimeoutStopSec (90s). A "quick restart" therefore
            # took a minute and a half of dead backend whenever anyone had the
            # chat open, which is most of the time.
            #
            # Ending here is safe and invisible: the client reconnects (see
            # useTurnStream's handleDrop, 1.2s backoff) and the turn itself is
            # untouched -- its child is still running and the next process adopts
            # it. The stream is a view of the turn, not the turn.
            return
        # Wait for the next push or a 1s heartbeat (so disconnected clients
        # get noticed eventually).
        turn.new_event.clear()
        try:
            await asyncio.wait_for(turn.new_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            # Heartbeat tick; loop and check turn.complete + emit a keepalive.
            yield after, TurnEvent(kind="ping", payload={})
