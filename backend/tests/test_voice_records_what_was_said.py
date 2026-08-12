"""What the operator says out loud reaches the thread, and stays there.

This is the property that was missing, and its absence is what cost him a whole
dictated message. Voice used to hand the utterance to the worker and persist it
only when a reply COMPLETED, so a cancel, a failed reply, a dropped socket or a
turn that never ended erased words he had already watched appear on screen.

These tests drive the real runtime against a real chat store in a temp root, and
assert the message is on the thread in exactly the cases that used to lose it.
"""
from __future__ import annotations

import asyncio
import functools
import types

import pytest

from services.voice import machine as m
from services.voice import runtime as rt


def _aio(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))
    return wrapper


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A real chat store, in a temp root. See test_voice_stream_route: this file
    must never write into a real operator's chat list."""
    from services import ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    # Pin an owner so the personal stores resolve. Safe ONLY because the root
    # above is a temp dir: the same call against the real root is what wrote
    # junk chats into the operator's own list.
    ownership.set_current_owner(6)
    from services import chat_store
    return chat_store


def messages(store, thread_id):
    return [(msg.get("role"), (msg.get("content") or "").strip())
            for msg in (store.get_thread(thread_id) or {}).get("messages") or []]


async def _runtime(store, *, on_start=None):
    sent: list[dict] = []

    async def send_json(p): sent.append(p)
    async def send_audio(t, p): pass

    th = store.create_thread(context_key="general", title="Voice")["id"]
    r = rt.VoiceRuntime(thread_id=th, context_key="general", model=None,
                        send_json=send_json, send_audio=send_audio)
    return r, th


@pytest.fixture
def worker(monkeypatch):
    """A model worker that can be told to fail, hang or answer."""
    behaviour = {"mode": "reply"}

    async def start_turn(*, context_key, thread_id, text, model=None,
                         speculative=False, user_client_id=None):
        if behaviour["mode"] == "raise_on_start":
            raise RuntimeError("worker is down")
        return types.SimpleNamespace(id="w1")

    async def iter_events(turn, after=0):
        if behaviour["mode"] == "never_answers":
            return
        if behaviour["mode"] == "error":
            yield 1, {"kind": "error", "payload": {"detail": "model exploded"}}
            return
        yield 1, {"kind": "text", "payload": {"content": "Sure."}}

    async def cancel_turn(turn_id, **k): return True
    async def ensure_worker(*a, **k): return None

    monkeypatch.setattr(rt.voice_worker, "start_turn", start_turn)
    monkeypatch.setattr(rt.voice_worker, "iter_events", iter_events)
    monkeypatch.setattr(rt.voice_worker, "cancel_turn", cancel_turn)
    monkeypatch.setattr(rt.voice_worker, "ensure_worker", ensure_worker)

    class FakeTTS:
        available = False
    monkeypatch.setattr(rt.voice_engine, "TTS", FakeTTS())
    return behaviour


@_aio
async def _say(r, text):
    """Speak `text` and let the endpoint fire, without any audio or ASR."""
    await r.dispatch(m.Started())
    await r.dispatch(m.PartialTranscript(text))
    await r.dispatch(m.Endpoint(complete=True, had_speech=True))
    await asyncio.sleep(0.05)


class TestTheWordsReachTheThread:
    def test_a_spoken_message_is_on_the_thread(self, store, worker):
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.dispatch(m.PartialTranscript("what is on my calendar"))
            await r.dispatch(m.Endpoint(complete=True, had_speech=True))
            await asyncio.sleep(0.05)
            return th
        th = asyncio.run(body())
        assert ("user", "what is on my calendar") in messages(store, th)

    def test_it_is_there_when_the_reply_fails(self, store, worker):
        worker["mode"] = "error"
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.dispatch(m.PartialTranscript("this must not vanish"))
            await r.dispatch(m.Endpoint(complete=True, had_speech=True))
            await asyncio.sleep(0.05)
            return th
        th = asyncio.run(body())
        assert ("user", "this must not vanish") in messages(store, th)

    def test_it_is_there_when_the_worker_cannot_start(self, store, worker):
        worker["mode"] = "raise_on_start"
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.dispatch(m.PartialTranscript("worker is down but I still spoke"))
            await r.dispatch(m.Endpoint(complete=True, had_speech=True))
            await asyncio.sleep(0.05)
            return th
        th = asyncio.run(body())
        assert ("user", "worker is down but I still spoke") in messages(store, th)

    def test_it_is_there_when_the_reply_never_comes(self, store, worker):
        worker["mode"] = "never_answers"
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.dispatch(m.PartialTranscript("silence from the model"))
            await r.dispatch(m.Endpoint(complete=True, had_speech=True))
            await asyncio.sleep(0.05)
            return th
        th = asyncio.run(body())
        assert ("user", "silence from the model") in messages(store, th)

    def test_it_survives_the_session_being_torn_down_mid_turn(self, store, worker):
        worker["mode"] = "never_answers"
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.dispatch(m.PartialTranscript("closing the tab now"))
            await r.dispatch(m.Endpoint(complete=True, had_speech=True))
            await asyncio.sleep(0.02)
            await r.close()      # what turning voice off does
            return th
        th = asyncio.run(body())
        assert ("user", "closing the tab now") in messages(store, th)

    def test_it_is_written_once_even_if_recorded_twice(self, store, worker):
        """The idempotency key is the turn id, so a retry appends nothing."""
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r._record_utterance("turn-1", "say it once")
            await r._record_utterance("turn-1", "say it once")
            return th
        th = asyncio.run(body())
        said = [t for role, t in messages(store, th) if role == "user"]
        assert said.count("say it once") == 1


class TestSendNowAlwaysDoesSomething:
    """The manual send is the last thing between a missed turn-end and losing a
    thought. It used to return silently unless the VAD had opened an utterance,
    which is precisely what has failed when you reach for it."""

    def test_it_sends_even_when_the_detector_never_opened_an_utterance(
        self, store, worker, monkeypatch
    ):
        import numpy as np

        class FakeSTT:
            available = True
            def transcribe(self, wav, lang="en", preview=False):
                return "the vad never heard me"

        monkeypatch.setattr(rt.voice_engine, "STT", FakeSTT())
        monkeypatch.setattr(rt.voice_engine, "STT_PREVIEW", FakeSTT())

        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            # Audio on the timeline, but no onset was ever reported: this is the
            # deaf-detector case, reproduced by writing the timeline directly.
            r.timeline.append(np.zeros(16000 * 2, dtype=np.int16))
            assert r._utt_start is None          # the precondition that used to bail
            await r.force_endpoint()
            await asyncio.sleep(0.05)
            return th

        th = asyncio.run(body())
        assert ("user", "the vad never heard me") in messages(store, th)

    def test_it_stays_a_no_op_when_there_is_genuinely_no_audio(self, store, worker):
        async def body():
            r, th = await _runtime(store)
            await r.dispatch(m.Started())
            await r.force_endpoint()
            await asyncio.sleep(0.02)
            return th
        th = asyncio.run(body())
        assert [t for role, t in messages(store, th) if role == "user"] == []
