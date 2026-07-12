"""Resident `claude -p` worker per voice session.

Phase 1 of the voice-mode v2 latency plan (docs/voice-mode/design.md section 3):
one long-lived `claude -p --input-format stream-json --output-format stream-json
--include-partial-messages` process per voice session, fed user messages over
stdin, streaming token deltas back over stdout. This removes BOTH latency sources
of the old path (cold subprocess spawn per turn, and blocking for the whole reply
before speaking):

  - No spawn cost: the process is prewarmed on surface mount and stays resident.
  - Token streaming: text deltas are pushed as they arrive, so the client can cut
    the first sentence and start TTS while the rest is still generating.

Verified locally: the CLI keeps the process alive across stdin turns and emits
`stream_event` -> `content_block_delta` (text_delta) partials; a warm second turn
returned in ~1.2s with no spawn.

Turns are serialized per worker (one in flight at a time) via an asyncio.Lock; a
single stdout pump routes lines to the current turn. Crash/EOF marks the worker
dead and the next ensure() respawns with `--resume <session_id>` so conversational
memory survives. Speculative drafts (deferred persistence) and cancel/interrupt are
supported here so phases 2-3 wire onto them without a rewrite.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from services import chat_context, chat_store, llm_router
from services.build_streaming import session_alive

log = logging.getLogger("workshop.voice_worker")

CLAUDE_CLI = llm_router.CLAUDE_CLI
STREAM_LINE_LIMIT = 64 * 1024 * 1024
IDLE_S = 600.0            # reap a worker after this long with no turn
TURN_TIMEOUT_S = 60.0     # a voice reply slower than this is a failure
# Stable cwd so `--resume` can find the session on disk across respawns.
VOICE_CWD = str(Path(__file__).resolve().parents[2])

_VOICE_SYSTEM = (
    "You are in a live VOICE conversation with the operator. Keep replies short and "
    "speakable: usually one to three sentences, never markdown, lists, code blocks, "
    "or emoji. Write numbers and units the way you would say them out loud. If the "
    "user asks for more depth, give it, but default to brief.\n"
    "Some user messages are prefixed with a bracketed system note ([note: ...], "
    "[correction: ...], or [context update]). Those are guidance about the "
    "conversation state, not the user's spoken words; read them and respond to the "
    "actual request."
)


def _voice_system(provider) -> str:
    base = ""
    try:
        base = provider.persona() or ""
    except Exception:
        base = ""
    return (base + "\n\n" + _VOICE_SYSTEM).strip()


# ---------------------------------------------------------------- turn record

@dataclass
class VoiceTurn:
    id: str
    session_key: str
    thread_id: str
    user_text: str
    speculative: bool = False
    deferred_persist: bool = False
    # Ordered event log the SSE endpoint replays/streams. Each: {kind, payload, ts}.
    events: list[dict] = field(default_factory=list)
    text: str = ""            # accumulated visible reply text
    complete: bool = False
    committed: bool = False
    cancelled: bool = False
    persisted: bool = False
    error: str | None = None
    heard_count: int | None = None
    started: float = field(default_factory=time.time)
    # marks and lifecycle
    first_delta_at: float | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    new_event: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, kind: str, payload: dict) -> None:
        self.events.append({"kind": kind, "payload": payload, "ts": time.time()})
        self.new_event.set()


TURNS: dict[str, VoiceTurn] = {}


# ---------------------------------------------------------------- worker

@dataclass
class VoiceWorker:
    session_key: str
    thread_id: str
    session_id: str
    model: str
    context_key: str
    proc: asyncio.subprocess.Process | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current: VoiceTurn | None = None
    dead: bool = False
    last_activity: float = field(default_factory=time.time)
    context_hash: str = ""
    # Interruption note to prepend to the next turn (truncate analogue): tells the
    # model what the user actually heard before barging in.
    pending_note: str = ""
    pump_task: asyncio.Task | None = None

    def touch(self) -> None:
        self.last_activity = time.time()


WORKERS: dict[str, VoiceWorker] = {}
_reaper_started = False


def _umsg(text: str) -> bytes:
    return (json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n").encode()


async def _spawn_proc(worker: VoiceWorker, resume: bool) -> None:
    provider = chat_context.get_provider(worker.context_key)
    cmd = [
        CLAUDE_CLI, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", worker.model,
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disallowed-tools", "*",
        "--append-system-prompt", _voice_system(provider),
    ]
    if resume and session_alive(worker.session_id, VOICE_CWD):
        cmd.extend(["--resume", worker.session_id])
    else:
        cmd.extend(["--session-id", worker.session_id])
    env = llm_router._claude_env()
    worker.proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=VOICE_CWD, env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_LINE_LIMIT,
    )
    worker.dead = False
    worker.pump_task = asyncio.create_task(_pump(worker))
    log.info("voice worker %s spawned (resume=%s, pid=%s)", worker.session_key,
             resume, worker.proc.pid if worker.proc else "?")


async def _pump(worker: VoiceWorker) -> None:
    """Continuously read the worker's stdout and route events to the current turn."""
    proc = worker.proc
    assert proc and proc.stdout
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            turn = worker.current
            if turn is None or turn.complete:
                continue
            t = ev.get("type")
            if t == "stream_event":
                e = ev.get("event", {})
                if e.get("type") == "content_block_delta":
                    d = e.get("delta", {})
                    if d.get("type") == "text_delta":
                        chunk = d.get("text", "")
                        if chunk:
                            if turn.first_delta_at is None:
                                turn.first_delta_at = time.time()
                            turn.text += chunk
                            turn.push("text", {"content": chunk})
            elif t == "result":
                _finish_turn(turn, error=None)
            elif t == "assistant":
                # Fallback: if no deltas arrived (partials disabled/edge), salvage
                # the complete assistant text.
                if not turn.text:
                    for b in ev.get("message", {}).get("content", []) or []:
                        if isinstance(b, dict) and b.get("type") == "text":
                            turn.text += b.get("text", "")
    except Exception as e:  # pragma: no cover — defensive
        log.warning("voice pump %s died: %s", worker.session_key, e)
    # stdout closed -> process gone.
    worker.dead = True
    if worker.current and not worker.current.complete:
        _finish_turn(worker.current, error="worker exited")


def _finish_turn(turn: VoiceTurn, *, error: str | None) -> None:
    if turn.complete:
        return
    turn.error = error
    turn.complete = True
    if error:
        turn.push("error", {"detail": error})
    turn.push("done", {"text_len": len(turn.text), "error": bool(error)})
    turn.done_event.set()
    turn.new_event.set()


async def ensure_worker(context_key: str, thread_id: str, model: str | None) -> VoiceWorker:
    """Get (spawning/prewarming if needed) the resident worker for this session."""
    _ensure_reaper()
    key = thread_id
    worker = WORKERS.get(key)
    resolved_model = model or chat_context.get_provider(context_key).default_model
    if worker and not worker.dead and worker.proc and worker.proc.returncode is None:
        worker.touch()
        return worker
    if worker is None:
        worker = VoiceWorker(
            session_key=key, thread_id=thread_id,
            session_id=str(uuid.uuid4()), model=resolved_model,
            context_key=context_key,
        )
        WORKERS[key] = worker
        await _spawn_proc(worker, resume=False)
    else:
        # Respawn a dead/reaped worker, resuming its session for memory.
        worker.model = resolved_model
        await _spawn_proc(worker, resume=True)
    worker.touch()
    return worker


async def start_turn(*, context_key: str, thread_id: str, text: str,
                     model: str | None = None, speculative: bool = False) -> VoiceTurn:
    """Begin a streaming reply turn. Returns immediately; the SSE endpoint reads
    the turn's event log as it fills."""
    worker = await ensure_worker(context_key, thread_id, model)
    turn = VoiceTurn(
        id=str(uuid.uuid4()), session_key=worker.session_key, thread_id=thread_id,
        user_text=text, speculative=speculative, deferred_persist=speculative,
    )
    TURNS[turn.id] = turn
    asyncio.create_task(_run_turn(worker, turn))
    return turn


async def _run_turn(worker: VoiceWorker, turn: VoiceTurn) -> None:
    async with worker.lock:
        if worker.dead or not worker.proc or worker.proc.stdin is None:
            _finish_turn(turn, error="worker unavailable")
            return
        worker.current = turn
        worker.touch()
        turn.push("started", {"model": worker.model, "speculative": turn.speculative})
        # Build the wire message: live-context prefix on first turn / when it
        # changes, plus the user's utterance.
        wire = _with_context(worker, turn.user_text)
        try:
            worker.proc.stdin.write(_umsg(wire))
            await worker.proc.stdin.drain()
        except Exception as e:
            _finish_turn(turn, error=f"write failed: {e}")
            worker.dead = True
            return
        try:
            await asyncio.wait_for(turn.done_event.wait(), timeout=TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            await _interrupt(worker)
            _finish_turn(turn, error=f"timeout after {int(TURN_TIMEOUT_S)}s")
        worker.current = None
        worker.touch()
    # Persist outside the lock (unless deferred for a speculative draft).
    if not turn.deferred_persist:
        _persist_turn(turn)


def _with_context(worker: VoiceWorker, text: str) -> str:
    prefix = ""
    if worker.pending_note:
        prefix += worker.pending_note + "\n"
        worker.pending_note = ""
    try:
        ctx = chat_context.get_provider(worker.context_key).live_context() or ""
    except Exception:
        ctx = ""
    h = hashlib.sha1(ctx.encode()).hexdigest()
    if ctx and h != worker.context_hash:
        worker.context_hash = h
        prefix += f"[context update]\n{ctx}\n\n"
    return prefix + text if prefix else text


def _persist_turn(turn: VoiceTurn) -> None:
    """Append the user + assistant messages to the thread. Assistant meta carries
    heard/total so the transcript records what was actually spoken. Idempotent:
    _run_turn's tail and commit_turn can both reach here for a committed draft."""
    if turn.persisted:
        return
    turn.persisted = True
    try:
        chat_store.append_message(turn.thread_id, "user", turn.user_text)
        meta: dict = {"voice": True}
        if turn.heard_count is not None:
            meta["heard"] = turn.heard_count
        chat_store.append_message(
            turn.thread_id, "assistant", turn.text.strip(),
            meta=meta if meta else None,
        )
    except Exception as e:  # pragma: no cover — defensive
        log.warning("voice turn %s persist failed: %s", turn.id, e)


async def _interrupt(worker: VoiceWorker) -> None:
    """Ask the resident process to abandon the in-flight generation. Uses the
    streaming-input control channel; falls back to respawn on the next turn if the
    process does not honor it."""
    if not worker.proc or worker.proc.stdin is None or worker.dead:
        return
    ctrl = json.dumps({
        "type": "control_request",
        "request_id": str(uuid.uuid4()),
        "request": {"subtype": "interrupt"},
    }) + "\n"
    try:
        worker.proc.stdin.write(ctrl.encode())
        await worker.proc.stdin.drain()
    except Exception:
        pass


async def cancel_turn(turn_id: str, *, reason: str = "superseded",
                      heard: int | None = None, heard_text: str | None = None) -> bool:
    """Cancel an in-flight turn (barge-in or speculative supersede). Interrupts the
    worker's generation and finishes the turn. Persistence for a committed turn is
    handled by commit; a superseded speculative draft is dropped."""
    turn = TURNS.get(turn_id)
    if not turn:
        return False
    if heard is not None:
        turn.heard_count = heard
    worker = WORKERS.get(turn.session_key)
    if worker:
        await _interrupt(worker)
        # Barge-in on a real (spoken) turn: tell the model what actually landed so
        # its memory matches what the user heard (OpenAI Realtime truncate analogue).
        if reason == "barge" and heard_text:
            worker.pending_note = (
                "[note: you were interrupted; the last sentence I heard was: "
                f"\"{heard_text.strip()}\". Anything after that was not spoken, so "
                "do not assume I heard it.]"
            )
    turn.cancelled = True
    if not turn.complete:
        _finish_turn(turn, error=None)
        turn.push("cancelled", {"reason": reason})
    return True


async def commit_turn(turn_id: str) -> bool:
    """Promote a speculative draft to a real turn: flush its deferred persistence."""
    turn = TURNS.get(turn_id)
    if not turn:
        return False
    turn.committed = True
    if turn.deferred_persist:
        turn.deferred_persist = False
        _persist_turn(turn)
    return True


def get_turn(turn_id: str) -> VoiceTurn | None:
    return TURNS.get(turn_id)


async def stop_session(thread_id: str) -> None:
    worker = WORKERS.pop(thread_id, None)
    if not worker:
        return
    if worker.pump_task:
        worker.pump_task.cancel()
    if worker.proc and worker.proc.returncode is None:
        try:
            worker.proc.kill()
            await worker.proc.wait()
        except Exception:
            pass


async def iter_events(turn: VoiceTurn, after: int = 0):
    """Yield (offset, event) pairs from `after`; suspend until new events; end when
    the turn is complete and drained. Mirrors build_streaming.iter_events so the SSE
    endpoint framing (id: <offset>, Last-Event-ID resume) is identical."""
    while True:
        while after < len(turn.events):
            yield after + 1, turn.events[after]
            after += 1
        if turn.complete:
            return
        turn.new_event.clear()
        try:
            await asyncio.wait_for(turn.new_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            yield after, {"kind": "ping", "payload": {}, "ts": time.time()}


def _ensure_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True
    asyncio.create_task(_reaper())


async def _reaper() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for key, worker in list(WORKERS.items()):
            if now - worker.last_activity > IDLE_S and (worker.current is None):
                log.info("voice worker %s idle-reaped", key)
                if worker.pump_task:
                    worker.pump_task.cancel()
                if worker.proc and worker.proc.returncode is None:
                    try:
                        worker.proc.kill()
                        await worker.proc.wait()
                    except Exception:
                        pass
                worker.dead = True
                # Keep the record so a later turn respawns with --resume (memory).
