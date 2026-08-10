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
from services.build_streaming import (
    session_alive, usage_scope_for_thread, _spend_tokens_from_usage,
    _summarize_tool_input,
)

log = logging.getLogger("workshop.voice_worker")

CLAUDE_CLI = llm_router.CLAUDE_CLI
STREAM_LINE_LIMIT = 64 * 1024 * 1024
IDLE_S = 600.0            # reap a conversational worker after this long with no turn
# Build workers hold the AGENT'S shared Claude session (so voice continues the
# text chat). Release them promptly once idle so a text-chat turn can resume that
# same session without two live processes writing the session file. Rapid
# back-to-back voice turns keep it warm; a pause frees it.
BUILD_IDLE_S = 30.0
TURN_TIMEOUT_S = 60.0     # a conversational voice reply slower than this is a failure
# A voice BUILD turn runs real tools (edits, commands, tests) and legitimately
# takes as long as the work takes — mirror the text build path, which has no
# wall clock at all (routes/chat.CHAT_TIMEOUT_S), not the 60s conversational
# one. 0 means no deadline. The conversational bound above stays: a SPOKEN
# reply that slow is a genuine failure, not autonomous work.
BUILD_TURN_TIMEOUT_S = 0.0
# Stable cwd so `--resume` can find the session on disk across respawns. Used for
# conversational (no-tool) workers; build workers use the agent's own cwd.
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

# Build-by-voice: the model has the full toolset and is actually editing the
# project, but the operator is LISTENING, not reading a transcript. The whole UX
# hinges on the model narrating as it works so there's never a long silent gap
# while tools run. This is the "smart phrasing for long turns" — the model speaks
# the plan and progress out loud instead of us bolting on synthetic filler.
_BUILD_VOICE_SYSTEM = (
    "You are pair-building with the operator by VOICE while you actually edit this "
    "project with your tools. The operator is LISTENING, not reading — so narrate, "
    "don't dump.\n"
    "- Speak in short, plain spoken sentences. Never read code, file contents, "
    "paths, or command syntax aloud; describe them in words a person would say.\n"
    "- Before a meaningful step, say in ONE sentence what you're about to do "
    "(\"Okay, I'll add a config file and wire it into startup\"). This keeps the "
    "line alive while the tool runs.\n"
    "- Work in small steps. After finishing, give a one or two sentence spoken "
    "summary of what changed and what you'd do next — not a list.\n"
    "- If a request is ambiguous or risky, ask one short spoken question before "
    "acting.\n"
    "- No markdown, no bullet lists, no emoji in what you say.\n"
    "Some user messages are prefixed with a bracketed system note ([note: ...], "
    "[correction: ...], or [context update]); treat those as guidance about the "
    "conversation state, not spoken words."
)


def _voice_system(provider, is_build: bool = False) -> str:
    base = ""
    try:
        base = provider.persona() or ""
    except Exception:
        base = ""
    speak_rules = _BUILD_VOICE_SYSTEM if is_build else _VOICE_SYSTEM
    return (base + "\n\n" + speak_rules).strip()


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
    # Ordered text/tool segments, same shape the build chat persists
    # (build_streaming._seg_text/_seg_tool -> chat_store meta["segments"]): a
    # {"t":"text","text":...} or {"t":"tool","name":...,"input":...} list. Lets a
    # voice BUILD turn keep its tool history in the thread transcript.
    segments: list[dict] = field(default_factory=list)
    text: str = ""            # accumulated visible reply text
    complete: bool = False
    committed: bool = False
    cancelled: bool = False
    # An explicit user cancel ("scrap what I just said") — the mis-heard utterance
    # and any partial reply are DROPPED, not written to the thread. (A barge-in is
    # different: reason="barge" keeps what was actually heard.)
    discarded: bool = False
    persisted: bool = False
    error: str | None = None
    heard_count: int | None = None
    started: float = field(default_factory=time.time)
    # marks and lifecycle
    first_delta_at: float | None = None
    completed_at: float | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    new_event: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, kind: str, payload: dict) -> None:
        self.events.append({"kind": kind, "payload": payload, "ts": time.time()})
        self.new_event.set()

    # Segment accumulators mirror build_streaming's _seg_text/_seg_tool so the
    # persisted meta["segments"] is identical in shape to a text build turn.
    def seg_text(self, s: str) -> None:
        if not s:
            return
        if self.segments and self.segments[-1].get("t") == "text":
            self.segments[-1]["text"] += s
        else:
            self.segments.append({"t": "text", "text": s})

    def seg_tool(self, name, inp) -> None:
        self.segments.append({"t": "tool", "name": name or "?", "input": inp or ""})


TURNS: dict[str, VoiceTurn] = {}


# ---------------------------------------------------------------- worker

@dataclass
class VoiceWorker:
    session_key: str
    thread_id: str
    session_id: str
    model: str
    context_key: str
    # Build workers run the full toolset in the agent's working tree; conversational
    # workers are no-tool and run in VOICE_CWD. Resolved once at creation.
    cwd: str = VOICE_CWD
    is_build: bool = False
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
    ]
    if worker.is_build:
        # Voice-driven building: full agentic toolset in the agent's working tree,
        # exactly like the text build path, so the model can actually read / write /
        # run. bypassPermissions because a headless voice turn has no interactive
        # approver (same rationale as the data-tool path in llm_router).
        cmd += ["--permission-mode", "bypassPermissions"]
    else:
        # Conversational voice: no tools, isolated cwd.
        cmd += [
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disallowed-tools", "*",
        ]
    cmd += ["--append-system-prompt", _voice_system(provider, worker.is_build)]
    if resume and session_alive(worker.session_id, worker.cwd):
        cmd.extend(["--resume", worker.session_id])
    else:
        cmd.extend(["--session-id", worker.session_id])
    env = llm_router._claude_env()
    worker.proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=worker.cwd, env=env,
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
                elif e.get("type") == "content_block_start":
                    # Build turns run tools. Surface each tool as it starts (name
                    # only — input arrives with the full assistant message) so the
                    # UI can show "what it's doing" while the model narrates aloud.
                    blk = e.get("content_block", {})
                    if blk.get("type") == "tool_use":
                        turn.push("tool", {"name": blk.get("name", "tool"), "input": ""})
            elif t == "result":
                _record_spend(turn, ev.get("usage"))
                _finish_turn(turn, error=None)
            elif t == "assistant":
                # The completed assistant message carries the authoritative,
                # ORDERED content blocks (text + tool_use), exactly like the
                # source build_streaming builds its segments from. Accumulate
                # them into turn.segments so a voice BUILD turn persists its tool
                # history in the same shape as a text build turn. (A build turn
                # emits several assistant messages, tool_use -> tool_result ->
                # assistant ...; each contributes its blocks in arrival order.)
                for b in ev.get("message", {}).get("content", []) or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        txt = b.get("text", "")
                        # Fallback: if no deltas arrived (partials disabled/edge),
                        # salvage the complete assistant text into the reply.
                        if not turn.text:
                            turn.text += txt
                        turn.seg_text(txt)
                    elif b.get("type") == "tool_use":
                        # Enrich the tool chip with a one-line input summary.
                        inp = _summarize_tool_input(b.get("input", {}) or {})
                        turn.push("tool", {"name": b.get("name", "tool"), "input": inp})
                        turn.seg_tool(b.get("name", "tool"), inp)
    except Exception as e:  # pragma: no cover — defensive
        log.warning("voice pump %s died: %s", worker.session_key, e)
    # stdout closed -> process gone.
    worker.dead = True
    if worker.current and not worker.current.complete:
        _finish_turn(worker.current, error="worker exited")


def _record_spend(turn: VoiceTurn, usage: object) -> None:
    """Meter a spoken turn's SPEND, exactly as the text path does.

    A voice turn is a chat turn: same account, same model, same money. It was the
    one turn kind that spent nothing on the record — build_streaming has written
    every typed turn to the ledger for months and this module had no reference to
    it at all, so talking instead of typing made the cost disappear from /usage.

    Same shape as the text path deliberately: role="chat" (chat spend stays
    separable from autonomous worker spend downstream), scope from the thread,
    and best-effort — a ledger hiccup must never cost the operator a reply they
    have already heard."""
    tokens = _spend_tokens_from_usage(usage)
    if tokens <= 0:
        return
    try:
        from services import usage_ledger
        usage_ledger.record(
            usage_scope_for_thread(turn.thread_id),
            tokens,
            role="chat",
            run=turn.id,
            chat=turn.thread_id,
        )
    except Exception as e:  # pragma: no cover — best-effort
        log.debug("voice turn %s: usage record failed: %s", turn.id, e)


def _finish_turn(turn: VoiceTurn, *, error: str | None) -> None:
    if turn.complete:
        return
    turn.error = error
    turn.complete = True
    turn.completed_at = time.time()
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
    provider = chat_context.get_provider(context_key)
    resolved_model = model or provider.default_model
    if worker and not worker.dead and worker.proc and worker.proc.returncode is None:
        worker.touch()
        return worker
    if worker is None:
        is_build = chat_context.is_build_provider(provider)
        # Build workers run in the agent's own tree (so edits land in the project
        # and `--resume` finds the session under that cwd); conversational workers
        # stay in the stable VOICE_CWD sandbox.
        cwd = str(provider.dir) if is_build else VOICE_CWD
        # Voice is another INTERFACE to the same chat, not a parallel one. For a
        # build-agent chat, share the agent's own Claude session so talking
        # continues the exact conversation you were typing — same memory, same
        # working tree — instead of a fresh brain. Resume it if it already exists
        # on disk. (Projects have no per-agent session record, so they still get a
        # fresh session.) The idle reaper releases this worker quickly (see
        # _reaper) so the text path can resume the same session without two live
        # processes fighting over the session file.
        session_id = str(uuid.uuid4())
        resume = False
        if context_key.startswith("build:"):
            try:
                from services import build_agents as _ba
                sid, is_new = _ba.ensure_session_id(context_key.split(":", 1)[1])
                session_id = sid
                resume = (not is_new) and session_alive(sid, cwd)
            except Exception:  # agent gone / registry hiccup — fall back to fresh session
                session_id = str(uuid.uuid4())
                resume = False
        worker = VoiceWorker(
            session_key=key, thread_id=thread_id,
            session_id=session_id, model=resolved_model,
            context_key=context_key, cwd=cwd, is_build=is_build,
        )
        WORKERS[key] = worker
        await _spawn_proc(worker, resume=resume)
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
    # Title the auto-created voice thread from the first utterance instead of the
    # permanent "Voice" placeholder (routes/voice.py voice_session). Only on the
    # first turn (no messages yet) and only while still the placeholder, so a
    # later turn never clobbers a real title.
    try:
        th = chat_store.get_thread(thread_id)
        if th and th.get("title") == "Voice" and not th.get("messages"):
            first_line = " ".join(text.split())[:40].strip()
            if first_line:
                chat_store.rename_thread(thread_id, first_line)
    except Exception:  # pragma: no cover — titling is cosmetic, never block a turn
        pass
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
        timeout_s = BUILD_TURN_TIMEOUT_S if worker.is_build else TURN_TIMEOUT_S
        # 0 means no deadline; asyncio.wait_for spells that None.
        try:
            await asyncio.wait_for(turn.done_event.wait(),
                                   timeout=timeout_s if timeout_s > 0 else None)
        except asyncio.TimeoutError:
            await _interrupt(worker)
            _finish_turn(turn, error=f"timeout after {int(timeout_s)}s")
        worker.current = None
        worker.touch()
    # Persist outside the lock — unless it was a speculative draft (deferred) or
    # the user explicitly cancelled ("scrap that"), in which case the utterance is
    # dropped so a mis-heard send never lands in the transcript.
    if not turn.deferred_persist and not turn.discarded:
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
        # A voice BUILD turn ran tools; persist the ordered text/tool segments in
        # the SAME shape the text build chat stores (chat_store meta["segments"])
        # so the thread keeps its tool history. Conversational turns have only a
        # single text segment redundant with the content, so skip meta bloat there
        # and attach segments only when a tool actually ran.
        if any(s.get("t") == "tool" for s in turn.segments):
            meta["segments"] = turn.segments
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
        # Speculative supersede: the just-cancelled draft answered an incomplete
        # fragment that was never spoken. Tell the model to answer the full,
        # corrected utterance fresh instead of continuing from the dropped draft
        # (design.md section 4 / D2 correction-marker).
        elif reason == "superseded":
            worker.pending_note = (
                "[correction: my previous message was an incomplete fragment of "
                "the next one and your reply was never spoken; answer the full "
                "message fresh.]"
            )
    turn.cancelled = True
    # An explicit user cancel discards the utterance entirely (see _run_turn).
    if reason in ("cancelled", "discard"):
        turn.discarded = True
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
        await asyncio.sleep(20)
        now = time.time()
        for key, worker in list(WORKERS.items()):
            idle_limit = BUILD_IDLE_S if worker.is_build else IDLE_S
            if now - worker.last_activity > idle_limit and (worker.current is None):
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
        # Prune completed turn records older than 10 minutes. TURNS otherwise
        # grows unbounded until restart (D6 backend-hygiene). A live SSE stream
        # holds its own reference to the turn object, so dropping the dict entry
        # never interrupts an in-flight reader; only fully-finished turns qualify.
        for tid, turn in list(TURNS.items()):
            if turn.completed_at is not None and now - turn.completed_at > 600:
                del TURNS[tid]
