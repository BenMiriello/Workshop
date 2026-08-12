"""The voice session runtime: turns the world into events, performs effects.

This is the impure half of the design. It owns the audio timeline, runs the
detectors, drives ASR, talks to the resident `claude -p` worker and synthesises
speech. It holds NO policy: every decision is `machine.reduce`'s, and everything
here either produces an `Event` or performs an `Effect`.

Two things worth reading before changing anything.

**Transcription is incremental, and that is not an optimisation.** The old
client re-transcribed the whole growing utterance every 900ms to draw a caption,
so a ten second utterance decoded about 55 seconds of audio and then decoded all
ten again at the endpoint. That is quadratic, it ran on the same cores as
Kokoro, and it is why voice felt worst exactly when it was busiest. Here the
utterance is committed piecewise at micro-pauses (`_maybe_commit`), so total ASR
work is linear in the length of what was said, and the endpoint usually has only
a few hundred milliseconds left to decode.

**The client decides what was heard.** `ClauseAudible` / `ClauseHeard` come from
the browser, because only the browser knows when a sample actually left the
speaker. That matters: whether the next thing the operator says is a
continuation or an interruption turns on it (machine.py), and the server only
knows what it sent, which is not the same thing.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from services import chat_store, voice_engine, voice_worker

from . import machine as m
from .clauses import ClauseStream
from .detectors import SpeechDetector, TurnEndDetector, VAD_WINDOW
from .timeline import SAMPLE_RATE, AudioTimeline, RangeExpired, Span, to_wav

log = logging.getLogger("workshop.voice.runtime")

# Look this far BACK from the moment the VAD trips, so the first phoneme is not
# clipped. A ring buffer can do this and a recorder that is started on demand
# never could: by the time you know someone is talking, they already are.
PREROLL_MS = 320
# A pause long enough to be a safe cut point for incremental ASR. Well below any
# endpoint decision, so committing here never pre-empts the turn-end model.
MICRO_PAUSE_MS = 240
# Do not bother committing less than this; the seam costs more than it saves.
MIN_COMMIT_MS = 900
# Live caption cadence, and the most audio one caption pass will ever look at.
# Bounded, so the caption cannot become the expensive thing on the box.
PARTIAL_EVERY_MS = 550
PARTIAL_MAX_MS = 5000
# How often the runtime re-asks its own questions (commit / caption / endpoint).
# Independent of the frame rate on purpose; see _tick_loop.
TICK_MS = 40
# How far back "Send now" reaches when the detector never opened an utterance.
# Long enough to hold a whole thought said into a deaf VAD, short enough that it
# cannot sweep up a previous turn's audio.
FORCE_WINDOW_SAMPLES = 30 * 16000
# Shortest burst that can fairly be called an attempt to say something. Below
# it, an empty transcript is noise and must not be reported as a failure to hear.
REAL_SPEECH_MS = 350

TTS_SAMPLE_RATE = voice_engine.TTS_SAMPLE_RATE
TTS_FRAME = 1200                     # 50ms at 24k


@dataclass
class Clause:
    turn_id: str
    index: int
    text: str
    token: int


class VoiceRuntime:
    """One live voice conversation.

    `send_json` and `send_audio` are the transport. Everything else is owned
    here, and the whole object dies with the socket.
    """

    def __init__(
        self,
        *,
        thread_id: str | None,
        context_key: str,
        model: str | None,
        send_json: Callable[[dict], Awaitable[None]],
        send_audio: Callable[[int, np.ndarray], Awaitable[None]],
    ) -> None:
        # None until the operator says something. See `_thread`.
        self.thread_id = thread_id
        self.context_key = context_key
        self.model = model
        self._send_json = send_json
        self._send_audio = send_audio

        self.timeline = AudioTimeline()
        self.speech = SpeechDetector()
        self.turn_end = TurnEndDetector()
        self.state = m.State()
        self.patience = 1.0

        # ---- perception bookkeeping (not policy) --------------------------
        self._analysed_to = 0            # VAD has run up to here
        self._carry = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._utt_start: int | None = None
        self._committed_to = 0           # transcript covers up to here
        self._silence_from: int | None = None
        self._speech_samples = 0
        self._had_speech = False
        self._last_partial = 0.0
        self._armed = False
        self._stt_failed = False
        self._typing = False

        # ---- generation / speech out --------------------------------------
        self._gen_tasks: dict[str, asyncio.Task] = {}
        # Machine turn id -> worker turn id. Per INSTANCE: as a class attribute
        # this would be one dict shared by every open session on the box.
        self._worker_turns: dict[str, str] = {}
        # Turns whose spoken message is already on the thread. The worker is told
        # so it does not write it a second time, and so its context replay leaves
        # it out of the history it seeds the model with.
        self._recorded: set[str] = set()
        self.voice: str | None = None
        self.speed: float = 1.0
        self._clause_q: asyncio.Queue[Clause | None] = asyncio.Queue()
        self._speaker_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None
        self._speaking_turn: str | None = None
        self._token = 0
        self._asr_lock = asyncio.Lock()
        self._closed = False

    # =====================================================================
    # lifecycle
    # =====================================================================
    async def start(self) -> None:
        self._speaker_task = asyncio.create_task(self._speaker_loop())
        self._tick_task = asyncio.create_task(self._tick_loop())
        await self.dispatch(m.Started())
        # The worker is warmed here only for a session that already HAS a
        # thread, because the worker is keyed by thread: there is nothing to
        # warm for a conversation that does not exist yet. A brand-new voice
        # chat pays the spawn on its first utterance, which is the cost of not
        # leaving a chat behind for every socket that opened and said nothing.
        if self.thread_id:
            await voice_worker.ensure_worker(
                self.context_key, self.thread_id, self.model)
        await self.emit()

    async def close(self, *, release: bool = True) -> None:
        """Tear the session down.

        `release` hands the resident `claude -p` process back. It is TRUE for a
        deliberate close (voice switched off, tab closed) and FALSE for an
        unexpected disconnect, where the client is about to reconnect and killing
        the worker would cost a respawn for a network blip. The idle reaper still
        collects it if the client never comes back.
        """
        if self._closed:
            return
        self._closed = True
        await self.dispatch(m.Stopped())
        if release and self.thread_id:
            try:
                await voice_worker.stop_session(self.thread_id)
            except Exception:                           # noqa: BLE001
                log.warning("could not release the voice worker", exc_info=True)
        for t in list(self._gen_tasks.values()):
            t.cancel()
        await self._clause_q.put(None)
        if self._speaker_task:
            self._speaker_task.cancel()
        if self._tick_task:
            self._tick_task.cancel()

    # =====================================================================
    # events in
    # =====================================================================
    async def dispatch(self, event: m.Event) -> None:
        out = m.reduce(self.state, event, turn_id=uuid.uuid4().hex[:12])
        changed = out.state != self.state
        self.state = out.state
        for fx in out.effects:
            await self._perform(fx)
        if changed or out.effects:
            await self.emit()

    async def on_audio(self, offset: int, pcm: np.ndarray) -> None:
        """A 20ms frame from the client. The only entry point for microphone
        audio, and the thing that drives the whole perception loop."""
        if self._closed:
            return
        self.timeline.write_at(offset, pcm)
        if not self._armed or not self.state.mic_on:
            # Not listening: keep the timeline fed (so the buffer stays warm and
            # a re-arm has history) but take no decisions from it.
            self._analysed_to = self.timeline.end
            return
        await self._analyse()

    # =====================================================================
    # perception
    # =====================================================================
    async def _analyse(self) -> None:
        end = self.timeline.end
        if end <= self._analysed_to:
            return
        try:
            fresh = self.timeline.read_float(Span(self._analysed_to, end))
        except RangeExpired:
            self._analysed_to = self.timeline.start
            return
        self._analysed_to = end
        buf = np.concatenate([self._carry, fresh]) if self._carry.size else fresh
        n_windows = buf.shape[0] // VAD_WINDOW
        self._carry = buf[n_windows * VAD_WINDOW:]
        base = end - buf.shape[0]

        for w in range(n_windows):
            frame = buf[w * VAD_WINDOW:(w + 1) * VAD_WINDOW]
            at = base + w * VAD_WINDOW
            verdict = self.speech(frame)
            if verdict.speech:
                self._speech_samples += VAD_WINDOW
                if not self._in_speech:
                    self._in_speech = True
                    self._silence_from = None
                    if self._utt_start is None:
                        # Reach BACKWARDS for the pre-roll. Only possible because
                        # the timeline is continuous.
                        pre = int(PREROLL_MS * SAMPLE_RATE / 1000)
                        self._utt_start = max(self.timeline.start, at - pre)
                        self._committed_to = self._utt_start
                        self._had_speech = False
                        self._stt_failed = False
                        self._speech_samples = VAD_WINDOW
                    await self.dispatch(m.SpeechStarted(at))
                if self._speech_samples * 1000 / SAMPLE_RATE >= REAL_SPEECH_MS:
                    self._had_speech = True
            else:
                if self._in_speech:
                    self._in_speech = False
                    self._silence_from = at
                    await self.dispatch(m.SpeechEnded(at))

    # ---- the decision tick -------------------------------------------------
    #
    # Committing, captioning and the turn-end question run on a CLOCK, not on
    # frame arrival. Driving them from `on_audio` looked equivalent (frames come
    # at 50fps) and was not: with the client stalled, throttled by a background
    # tab, or simply between frames, the endpoint was never re-evaluated and the
    # utterance hung with nothing to un-hang it. Decision cadence and transport
    # cadence are different things and coupling them is a wedge waiting to
    # happen.
    async def _tick_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(TICK_MS / 1000)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:                       # noqa: BLE001
                log.exception("voice tick failed")

    async def _tick(self) -> None:
        if not self._armed or not self.state.mic_on or self._utt_start is None:
            return
        end = self.timeline.end
        now = time.monotonic()
        if self._in_speech:
            if (now - self._last_partial) * 1000 >= PARTIAL_EVERY_MS:
                self._last_partial = now
                await self._partial()
            return
        silence_ms = int((end - (self._silence_from or end)) * 1000 / SAMPLE_RATE)
        if silence_ms >= MICRO_PAUSE_MS:
            await self._maybe_commit(self._silence_from or end)
        await self._consider_endpoint(silence_ms)

    async def _maybe_commit(self, upto: int) -> None:
        """Transcribe a settled piece of the utterance at a micro-pause.

        This is what keeps ASR linear. The cut always lands inside real silence,
        so no word is split at a seam.
        """
        span = Span(self._committed_to, upto)
        if span.ms < MIN_COMMIT_MS:
            return
        if self._asr_lock.locked():
            return
        self._committed_to = upto
        text = await self._transcribe(span)
        if text:
            await self.dispatch(m.SegmentTranscribed(text))

    # ---- manual and held endpoints -----------------------------------------
    def set_typing(self, active: bool) -> None:
        """Typing holds the turn open.

        Someone typing mid-utterance has not finished talking, and endpointing
        under them sends half of what they meant. This is the one input that is
        allowed to override the turn-end model, because it is evidence the model
        cannot have: it is not in the audio.
        """
        self._typing = active

    async def force_endpoint(self) -> None:
        """The operator said they are done. No model gets a vote.

        THIS IS THE ESCAPE HATCH, so it must not be able to do nothing. It used
        to return silently whenever no utterance was in progress, which sounds
        like an impossible case and is not: an utterance only starts when the VAD
        reports onset, and with no Silero weights on the box that is a hand-rolled
        energy heuristic that goes deaf on a quiet voice or a noisy room. So the
        one control meant to rescue a missed turn-end failed in exactly the
        situation that produces a missed turn-end, and the press did nothing at
        all -- no send, no error, no explanation.

        The audio is on the timeline either way. If the detector never opened an
        utterance, open one over what was actually captured and send that.
        """
        if self._utt_start is None:
            end = self.timeline.end
            start = max(self._committed_to, end - FORCE_WINDOW_SAMPLES)
            if end - start < int(0.2 * SAMPLE_RATE):
                return                      # genuinely nothing there
            self._utt_start = start
            self._committed_to = start
            self._silence_from = None
            self._had_speech = True
        await self._endpoint()

    async def test_mic(self) -> str:
        """Transcribe the last few seconds of the REAL timeline.

        Every layer this touches is the one a live turn uses, which is what lets
        one press answer "is it me, the mic, the model, or the network".
        """
        return await self._transcribe(self.timeline.tail(4000))

    async def _consider_endpoint(self, silence_ms: int) -> None:
        if self._utt_start is None or self._in_speech or self._typing:
            return
        text = " ".join(self.state.segments).strip()
        tail = self.timeline.tail(8000)
        try:
            audio = self.timeline.read_float(tail)
        except RangeExpired:
            audio = np.zeros(0, dtype=np.float32)
        verdict = self.turn_end(audio, text, silence_ms, patience=self.patience)
        if not verdict.complete:
            return
        await self._endpoint()

    async def _endpoint(self) -> None:
        """The operator has finished. Commit the tail, then hand it to the machine."""
        end = self._silence_from or self.timeline.end
        span = Span(self._committed_to, end)
        if span.ms > 120:
            text = await self._transcribe(span)
            if text:
                await self.dispatch(m.SegmentTranscribed(text))
        had_speech = self._had_speech
        stt_failed = self._stt_failed
        self._reset_utterance()
        await self.dispatch(
            m.Endpoint(complete=True, had_speech=had_speech, stt_failed=stt_failed)
        )

    def _reset_utterance(self) -> None:
        self._utt_start = None
        self._committed_to = self.timeline.end
        self._silence_from = None
        self._speech_samples = 0
        self._had_speech = False
        self._in_speech = False
        self.speech.reset()

    # ---- ASR ---------------------------------------------------------------
    async def _transcribe(self, span: Span, *, preview: bool = False) -> str:
        if span.samples <= 0:
            return ""
        engine = voice_engine.STT_PREVIEW if preview else voice_engine.STT
        if not engine.available:
            if not preview:
                self._stt_failed = True
            return ""
        try:
            pcm = self.timeline.read(span)
        except RangeExpired:
            return ""
        wav = to_wav(pcm)
        try:
            async with self._asr_lock:
                return await asyncio.to_thread(engine.transcribe, wav, "en", preview)
        except Exception as e:                          # noqa: BLE001
            if not preview:
                self._stt_failed = True
            log.warning("stt failed: %s", e)
            return ""

    async def _partial(self) -> None:
        """The live caption. Display only, and BOUNDED: it never looks at more
        than PARTIAL_MAX_MS, so it cannot grow into the expensive thing."""
        if self._utt_start is None or self._asr_lock.locked():
            return
        end = self.timeline.end
        start = max(self._committed_to, end - int(PARTIAL_MAX_MS * SAMPLE_RATE / 1000))
        span = Span(start, end)
        if span.ms < 300:
            return
        text = await self._transcribe(span, preview=True)
        if text and self._utt_start is not None:
            await self.dispatch(m.PartialTranscript(text))

    # =====================================================================
    # effects out
    # =====================================================================
    async def _perform(self, fx: m.Effect) -> None:
        if isinstance(fx, m.ArmCapture):
            self._armed = fx.on
            if fx.on:
                self._reset_utterance()
                self._analysed_to = self.timeline.end
                self._carry = np.zeros(0, dtype=np.float32)
            return

        if isinstance(fx, m.RecordUtterance):
            # Awaited, not spawned: this must be on the thread before the worker
            # reads it back as context and before any reply can be abandoned.
            await self._record_utterance(fx.turn_id, fx.text)
            return

        if isinstance(fx, m.StartGeneration):
            self._gen_tasks[fx.turn_id] = asyncio.create_task(
                self._generate(fx.turn_id, fx.text)
            )
            return

        if isinstance(fx, m.AbandonGeneration):
            task = self._gen_tasks.pop(fx.turn_id, None)
            if task:
                task.cancel()
            worker_turn = self._worker_turns.pop(fx.turn_id, None)
            if worker_turn:
                await voice_worker.cancel_turn(
                    worker_turn, reason=fx.reason.value,
                    heard=fx.heard, heard_text=fx.heard_text or None,
                )
            return

        if isinstance(fx, m.Speak):
            self._token += 1
            await self._send_json({
                "t": "clause", "token": self._token,
                "turn": fx.turn_id, "index": fx.index, "text": fx.text,
            })
            await self._clause_q.put(Clause(fx.turn_id, fx.index, fx.text, self._token))
            return

        if isinstance(fx, m.StopSpeaking):
            # Drain anything queued for this turn and tell the client to empty
            # its jitter buffer. Barge-in is instant, not "after this sentence".
            self._drain_clauses(fx.turn_id)
            self._speaking_turn = None
            await self._send_json({"t": "flush", "turn": fx.turn_id})
            return

    def _drain_clauses(self, turn_id: str) -> None:
        keep: list[Clause] = []
        while True:
            try:
                item = self._clause_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                keep.append(item)          # type: ignore[arg-type]
            elif item.turn_id != turn_id:
                keep.append(item)
        for k in keep:
            self._clause_q.put_nowait(k)

    # ---- generation --------------------------------------------------------
    async def _thread(self) -> str:
        """This session's chat, created on first use.

        A voice session opens with no thread when the surface it started from
        had no conversation yet, and it stays that way until something is
        actually said. That is what keeps a connect (or a reconnect, or five
        mounted surfaces) from leaving an empty chat behind. The title is a
        placeholder; `voice_worker.start_turn` replaces it with the first
        utterance on the same turn that creates it.

        The client is told the id through the state snapshot, which carries
        `threadId` on every emit, so the surface adopts the thread the moment it
        exists and the transcript never forks.
        """
        if not self.thread_id:
            self.thread_id = chat_store.create_thread(
                context_key=self.context_key, title="Voice")["id"]
            await self.emit()
        return self.thread_id

    async def _record_utterance(self, turn_id: str, text: str) -> None:
        """Append the spoken message to the thread the moment it is captured.

        Failure here is LOUD and does not stop the turn. A thread that cannot be
        written is a real problem, but refusing to answer on top of it would
        lose the words twice over.
        """
        thread_id = await self._thread()
        try:
            # Inline on the loop, like create_thread above: chat_log refuses a
            # write from a worker thread while the loop is live, so to_thread
            # would be rejected rather than merely slower.
            chat_store.append_message(
                thread_id, "user", text,
                meta={"voice": True},
                client_id=turn_id,        # idempotency key
            )
            self._recorded.add(turn_id)
        except Exception as e:            # noqa: BLE001
            log.error("spoken message not recorded on thread %s: %s", thread_id, e)
        await self.emit()

    async def _generate(self, turn_id: str, text: str) -> None:
        stream = ClauseStream()
        full = ""
        try:
            turn = await voice_worker.start_turn(
                context_key=self.context_key, thread_id=await self._thread(),
                text=text, model=self.model, user_client_id=turn_id,
            )
            self._worker_turns[turn_id] = turn.id
            async for _off, ev in voice_worker.iter_events(turn):
                kind = ev.get("kind")
                if kind == "text":
                    full += (ev.get("payload") or {}).get("content", "")
                    for c in stream.push(full):
                        await self.dispatch(m.ReplyClause(turn_id, c))
                elif kind == "error":
                    detail = (ev.get("payload") or {}).get("detail", "reply error")
                    await self.dispatch(m.ReplyFailed(turn_id, str(detail)))
                    return
                elif kind == "cancelled":
                    return
            for c in stream.flush(full):
                await self.dispatch(m.ReplyClause(turn_id, c))
            await self.dispatch(m.ReplyDone(turn_id))
        except asyncio.CancelledError:
            raise
        except Exception as e:                          # noqa: BLE001
            log.exception("generation failed")
            await self.dispatch(m.ReplyFailed(turn_id, str(e)))
        finally:
            self._gen_tasks.pop(turn_id, None)
            self._worker_turns.pop(turn_id, None)

    # ---- speech out --------------------------------------------------------
    async def _speaker_loop(self) -> None:
        """Synthesise queued clauses and stream them as PCM.

        One clause is synthesised while the previous one is still playing on the
        client, so there is no dead air between them and no per-clause round trip
        the operator can hear.
        """
        while True:
            item = await self._clause_q.get()
            if item is None:
                return
            if not voice_engine.TTS.available:
                # No engine: the caption is the delivery. Mark it heard so the
                # turn completes rather than wedging in "speaking".
                await self.dispatch(m.ClauseAudible(item.turn_id, item.index))
                await self.dispatch(m.ClauseHeard(item.turn_id, item.index))
                await self._maybe_finish(item.turn_id)
                continue
            try:
                wav = await asyncio.to_thread(
                    voice_engine.TTS.synth_wav, item.text, self.voice, self.speed
                )
            except Exception as e:                      # noqa: BLE001
                log.warning("tts failed: %s", e)
                await self.dispatch(m.ClauseHeard(item.turn_id, item.index))
                await self._maybe_finish(item.turn_id)
                continue
            # The turn may have been abandoned while this was synthesising.
            if self.state.turn is None or self.state.turn.id != item.turn_id:
                continue
            pcm = np.frombuffer(wav[44:], dtype="<i2")
            for i in range(0, pcm.shape[0], TTS_FRAME):
                if self.state.turn is None or self.state.turn.id != item.turn_id:
                    break
                await self._send_audio(item.token, pcm[i:i + TTS_FRAME])
            await self._send_json({"t": "clause_end", "token": item.token})
            await self._maybe_finish(item.turn_id)

    async def _maybe_finish(self, turn_id: str) -> None:
        """Close the turn once generation is done and nothing is left to say."""
        t = self.state.turn
        if t is None or t.id != turn_id:
            return
        if t.generating or self._clause_q.qsize():
            return
        if t.heard >= len(t.clauses):
            await self.dispatch(m.SpeechOutDone(turn_id))

    # ---- settings ----------------------------------------------------------
    def apply_prefs(self, prefs: dict) -> None:
        self.voice = prefs.get("voice") or None
        try:
            self.speed = float(prefs.get("speed") or 1.0)
        except (TypeError, ValueError):
            self.speed = 1.0
        # ONE patience number replaces endpoint_silence_ms, eager_endpoint,
        # eager_silence_ms and barge_sensitivity. Higher waits longer.
        self.patience = {"snappy": 0.7, "natural": 1.0, "patient": 1.5}.get(
            str(prefs.get("reply_pace") or "natural"), 1.0
        )

    # ---- snapshot ----------------------------------------------------------
    def snapshot(self) -> dict:
        s = self.state
        t = s.turn
        return {
            "t": "state",
            "phase": s.phase.value,
            "partial": s.partial,
            "pendingUser": s.pending_user,
            "lastUser": s.last_user,
            "sentences": [
                {"text": c.text, "status": c.status.value} for c in (t.clauses if t else ())
            ],
            "spokenCount": t.heard if t else 0,
            "error": s.error,
            "micOn": s.mic_on,
            "speakOn": s.speak_on,
            "deviceOk": s.device_ok,
            "userSpeaking": s.user_speaking,
            "interruptRate": (s.interrupts / s.turns) if s.turns else 0.0,
            "threadId": self.thread_id,
            "tiers": {"vad": self.speech.tier, "turnEnd": self.turn_end.tier},
        }

    async def emit(self) -> None:
        await self._send_json(self.snapshot())
