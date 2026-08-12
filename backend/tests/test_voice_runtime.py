"""The server pipeline end to end: PCM in, a turn out, speech back.

Drives the real runtime with synthetic audio and stubbed engines, so it covers
the wiring the pure reducer tests cannot: the VAD loop, pre-roll, incremental
commits, the endpoint, generation and the speech-out queue.
"""
from __future__ import annotations

import asyncio
import functools
import types

import numpy as np
import pytest

from services.voice import machine as m
from services.voice import runtime as rt
from services.voice.timeline import SAMPLE_RATE


def speech(ms: int, *, level: float = 0.3) -> np.ndarray:
    """Something the VAD will call speech: a loud-ish noisy tone."""
    n = int(ms * SAMPLE_RATE / 1000)
    t = np.arange(n)
    wave = np.sin(t * 0.11) * level + np.random.default_rng(0).normal(0, 0.02, n)
    return (np.clip(wave, -1, 1) * 32767).astype(np.int16)


def quiet(ms: int) -> np.ndarray:
    n = int(ms * SAMPLE_RATE / 1000)
    return (np.random.default_rng(1).normal(0, 0.0005, n) * 32767).astype(np.int16)


class Harness:
    def __init__(self):
        self.json: list[dict] = []
        self.audio: list[tuple[int, int]] = []
        self.cursor = 0

    async def send_json(self, payload):
        self.json.append(payload)

    async def send_audio(self, token, pcm):
        self.audio.append((token, len(pcm)))

    async def feed(self, runtime, pcm):
        """Push audio in 20ms frames, like the client does."""
        step = 320
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            await runtime.on_audio(self.cursor, chunk)
            self.cursor += len(chunk)
        await asyncio.sleep(0)

    async def settle(self, runtime, ms=1800, *, until=None):
        """Feed silence while letting the runtime tick, which is what a real
        client does: it never stops sending. Silence duration is measured from
        the AUDIO, not the wall clock, so a test that stops feeding freezes it."""
        for _ in range(int(ms / 20)):
            await runtime.on_audio(self.cursor, quiet(20))
            self.cursor += 320
            await asyncio.sleep(0.002)
            if until and until():
                return


def _aio(fn):
    """Run an async test body. pytest-asyncio is not installed on this box and
    cannot be fetched, so the loop is driven here instead of by a plugin."""

    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


@pytest.fixture
def stubbed(monkeypatch):
    """Stub the three slow things: ASR, TTS and the model worker."""
    transcripts = {"value": ["hello there"]}

    class FakeSTT:
        available = True

        def transcribe(self, wav, lang="en", preview=False):
            if preview:
                return "hel"
            return transcripts["value"].pop(0) if transcripts["value"] else ""

    class FakeTTS:
        available = True

        def synth_wav(self, text, voice=None, speed=1.0):
            # 44-byte header then 100ms of silence, matching the real shape.
            return b"\0" * 44 + np.zeros(2400, dtype="<i2").tobytes()

    monkeypatch.setattr(rt.voice_engine, "STT", FakeSTT())
    monkeypatch.setattr(rt.voice_engine, "STT_PREVIEW", FakeSTT())
    monkeypatch.setattr(rt.voice_engine, "TTS", FakeTTS())

    started: list[str] = []
    cancelled: list[tuple[str, str]] = []

    async def ensure_worker(*a, **k):
        return None

    async def start_turn(*, context_key, thread_id, text, model=None, speculative=False):
        started.append(text)
        return types.SimpleNamespace(id=f"w{len(started)}")

    async def iter_events(turn, after=0):
        for ev in (
            {"kind": "text", "payload": {"content": "Hi there. "}},
            {"kind": "text", "payload": {"content": "All done now."}},
        ):
            yield 1, ev
            await asyncio.sleep(0)

    async def cancel_turn(turn_id, *, reason="superseded", heard=None, heard_text=None):
        cancelled.append((turn_id, reason))
        return True

    monkeypatch.setattr(rt.voice_worker, "ensure_worker", ensure_worker)
    monkeypatch.setattr(rt.voice_worker, "start_turn", start_turn)
    monkeypatch.setattr(rt.voice_worker, "iter_events", iter_events)
    monkeypatch.setattr(rt.voice_worker, "cancel_turn", cancel_turn)
    return types.SimpleNamespace(
        started=started, cancelled=cancelled, transcripts=transcripts
    )


async def make(h: Harness):
    r = rt.VoiceRuntime(
        thread_id="th1", context_key="general", model=None,
        send_json=h.send_json, send_audio=h.send_audio,
    )
    await r.start()
    return r


@_aio
async def test_speaking_produces_a_turn(stubbed):
    h = Harness()
    r = await make(h)
    await h.feed(r, speech(1400))
    await h.settle(r, 2600)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if stubbed.started:
            break
    assert stubbed.started == ["hello there"], "an utterance did not become a turn"
    await r.close()


@_aio
async def test_the_reply_is_synthesised_and_streamed(stubbed):
    h = Harness()
    r = await make(h)
    await h.feed(r, speech(1400))
    await h.settle(r, 2600)
    for _ in range(80):
        await asyncio.sleep(0.01)
        if h.audio:
            break
    assert h.audio, "no PCM was sent for the reply"
    clauses = [j for j in h.json if j.get("t") == "clause"]
    assert clauses, "no clause was announced to the client"
    # Clause-level, not sentence-level: the short reply must not wait for done.
    assert any("Hi there." in c["text"] for c in clauses)
    await r.close()


@_aio
async def test_silence_alone_never_starts_a_turn(stubbed):
    h = Harness()
    r = await make(h)
    await h.settle(r, 6000)
    for _ in range(20):
        await asyncio.sleep(0.01)
    assert stubbed.started == []
    assert r.state.error is None, "a quiet room reported a fault"
    await r.close()


@_aio
async def test_a_mid_thought_pause_does_not_split_the_message(stubbed):
    # The heuristic tier keeps listening after "and", which is the whole point:
    # a single timer at 700ms sent this as two messages.
    stubbed.transcripts["value"] = ["make it blue and", "move it left"]
    h = Harness()
    r = await make(h)
    await h.feed(r, speech(1400))
    await h.settle(r, 1200)               # not long enough to end an unfinished line
    await h.feed(r, speech(1200))
    await h.settle(r, 2600)
    for _ in range(80):
        await asyncio.sleep(0.01)
        if stubbed.started:
            break
    assert len(stubbed.started) == 1, f"split into {stubbed.started}"
    assert "blue" in stubbed.started[0] and "left" in stubbed.started[0]
    await r.close()


@_aio
async def test_the_preroll_reaches_back_before_the_vad_tripped(stubbed):
    # A ring buffer can look backwards; a recorder started on demand never could,
    # which is why the first phoneme used to be clipped.
    h = Harness()
    r = await make(h)
    await h.feed(r, quiet(800))
    await h.feed(r, speech(1200))
    assert r._utt_start is not None
    onset_gap = r.timeline.end - r._utt_start
    assert onset_gap > int(1.2 * SAMPLE_RATE), "no pre-roll was captured"
    await r.close()


@_aio
async def test_mic_off_takes_no_decisions_but_keeps_the_timeline(stubbed):
    h = Harness()
    r = await make(h)
    await r.dispatch(m.SetLanes(mic_on=False))
    await h.feed(r, speech(2000))
    await h.settle(r, 2600)
    for _ in range(20):
        await asyncio.sleep(0.01)
    assert stubbed.started == []
    assert r.timeline.end > 0, "the buffer went cold while muted"
    await r.close()


@_aio
async def test_transcription_work_is_linear_not_quadratic(stubbed, monkeypatch):
    """The old caption loop re-decoded the whole utterance every tick.

    Here the total audio handed to ASR must stay close to the audio actually
    spoken, however long the utterance runs.
    """
    stubbed.transcripts["value"] = ["one"] * 30
    seen: list[int] = []
    real = rt.VoiceRuntime._transcribe

    async def counting(self, span, *, preview=False):
        seen.append(span.samples)
        return await real(self, span, preview=preview)

    monkeypatch.setattr(rt.VoiceRuntime, "_transcribe", counting)
    h = Harness()
    r = await make(h)
    for _ in range(6):
        await h.feed(r, speech(1200))
        await h.settle(r, 400)   # micro-pauses: commit points
    await h.settle(r, 2600)
    for _ in range(40):
        await asyncio.sleep(0.01)
    spoken = 6 * 1.2 * SAMPLE_RATE
    assert sum(seen) < spoken * 4, (
        f"ASR looked at {sum(seen)/SAMPLE_RATE:.1f}s for {spoken/SAMPLE_RATE:.1f}s of speech"
    )
    await r.close()
