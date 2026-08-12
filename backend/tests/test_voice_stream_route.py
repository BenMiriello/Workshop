"""The voice socket, through the real route: auth, framing, and a whole turn.

This exercises what the runtime tests cannot: the WebSocket handler, the
authentication that had to be reimplemented for a transport FastAPI's HTTP
dependency cannot cover, the binary frame format, and the fact that the pieces
are actually wired to each other.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.voice import protocol
from services.voice.timeline import SAMPLE_RATE


def speech(ms: int) -> np.ndarray:
    n = int(ms * SAMPLE_RATE / 1000)
    t = np.arange(n)
    w = np.sin(t * 0.11) * 0.3 + np.random.default_rng(0).normal(0, 0.02, n)
    return (np.clip(w, -1, 1) * 32767).astype(np.int16)


def quiet(ms: int) -> np.ndarray:
    n = int(ms * SAMPLE_RATE / 1000)
    return (np.random.default_rng(1).normal(0, 0.0005, n) * 32767).astype(np.int16)


@pytest.fixture
def app(monkeypatch, tmp_path):
    import main
    from services import ownership
    from routes import voice_stream

    # THE RUNTIME ROOT IS REDIRECTED BEFORE ANY OF THIS RUNS, and that is not
    # hygiene. The stub below authenticates as owner 6, which on this box is a
    # REAL operator, so without this every run of this file wrote real chats
    # into their real chat list: three per run, indistinguishable from the ones
    # a live socket used to leave behind. That is most of what made the empty-
    # "Voice"-chat report so hard to read, because the flood appeared to
    # continue after the bug behind it was fixed. A test that authenticates as
    # a real owner must never share that owner's storage.
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(
        voice_stream, "_user", lambda ws: {"id": 6, "email": "test@example.com"}
    )
    return main.app


@pytest.fixture
def stub_engines(monkeypatch):
    from services.voice import runtime as rt

    class FakeSTT:
        available = True

        def transcribe(self, wav, lang="en", preview=False):
            return "hel" if preview else "hello there"

    class FakeTTS:
        available = True

        def synth_wav(self, text, voice=None, speed=1.0):
            return b"\0" * 44 + np.zeros(2400, dtype="<i2").tobytes()

    monkeypatch.setattr(rt.voice_engine, "STT", FakeSTT())
    monkeypatch.setattr(rt.voice_engine, "STT_PREVIEW", FakeSTT())
    monkeypatch.setattr(rt.voice_engine, "TTS", FakeTTS())

    started = []

    async def ensure_worker(*a, **k):
        return None

    async def start_turn(*, context_key, thread_id, text, model=None, speculative=False):
        started.append(text)

        class T:
            id = "w1"

        return T()

    async def iter_events(turn, after=0):
        yield 1, {"kind": "text", "payload": {"content": "Hi there. Done now."}}

    async def cancel_turn(*a, **k):
        return True

    monkeypatch.setattr(rt.voice_worker, "ensure_worker", ensure_worker)
    monkeypatch.setattr(rt.voice_worker, "start_turn", start_turn)
    monkeypatch.setattr(rt.voice_worker, "iter_events", iter_events)
    monkeypatch.setattr(rt.voice_worker, "cancel_turn", cancel_turn)
    return started


_ping = [0]


def drain(ws, settle: float = 0.0):
    """Everything the server has to say, up to a round-trip barrier.

    `receive()` on the test client blocks with no timeout, so draining needs a
    message guaranteed to arrive. A ping is delivered after everything queued
    before it, which makes the collection deterministic instead of a race.
    """
    if settle:
        time.sleep(settle)
    _ping[0] += 1
    n = _ping[0]
    ws.send_text(json.dumps({"t": "ping", "n": n}))
    seen = []
    for _ in range(6000):
        msg = ws.receive()
        if msg.get("text"):
            payload = json.loads(msg["text"])
            if payload.get("t") == "pong" and payload.get("n") == n:
                return seen
            seen.append(payload)
        elif msg.get("bytes") is not None:
            seen.append({"t": "__audio__", "n": len(msg["bytes"])})
    return seen


def first(seen, kind):
    return next((m for m in seen if m.get("t") == kind), None)


def send_pcm(ws, cursor, pcm):
    for i in range(0, len(pcm), 320):
        chunk = pcm[i:i + 320]
        ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, cursor, chunk))
        cursor += len(chunk)
    return cursor


class TestAuth:
    def test_an_unauthenticated_socket_is_refused(self, monkeypatch):
        import main
        from routes import voice_stream

        monkeypatch.setattr(voice_stream, "_user", lambda ws: None)
        client = TestClient(main.app)
        with pytest.raises(Exception):
            with client.websocket_connect("/voice/stream"):
                pass

    def test_a_cross_site_origin_is_refused(self, app):
        # A WebSocket is not covered by the same-origin policy but does carry
        # cookies, so without this check any page could open one.
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/voice/stream", headers={"origin": "https://evil.example"}
            ):
                pass


PUBLIC_HOST = "workshop.invalid"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"


class TestOriginBehindTheProxy:
    """THE BUG THAT MADE VOICE COMPLETELY NON-FUNCTIONAL.

    Every real connection arrives through the public edge, which terminates the
    browser's socket and dials this process on 127.0.0.1:8000. Comparing the
    browser's Origin against THIS process's Host compares the public hostname
    against `127.0.0.1:8000`, so every genuine connection was refused. The
    operator got a spinner that never resolved while the backend log filled with
    `WebSocket /voice/stream 403`.
    """

    def test_a_proxied_connection_is_accepted(self, app, stub_engines):
        with TestClient(app).websocket_connect(
            "/voice/stream",
            headers={"origin": PUBLIC_ORIGIN, "x-forwarded-host": PUBLIC_HOST},
        ) as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            assert first(drain(ws), "ready") is not None

    def test_the_forwarded_host_still_has_to_match_the_origin(self, app):
        with pytest.raises(Exception):
            with TestClient(app).websocket_connect(
                "/voice/stream",
                headers={"origin": "https://elsewhere.invalid",
                         "x-forwarded-host": PUBLIC_HOST},
            ):
                pass

    def test_a_configured_public_origin_is_accepted(self, app, stub_engines, monkeypatch):
        monkeypatch.setenv("WORKSHOP_PUBLIC_ORIGIN", PUBLIC_ORIGIN)
        with TestClient(app).websocket_connect(
            "/voice/stream", headers={"origin": PUBLIC_ORIGIN}
        ) as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            assert first(drain(ws), "ready") is not None

    def test_a_direct_same_host_connection_still_works(self, app, stub_engines):
        with TestClient(app).websocket_connect(
            "/voice/stream", headers={"origin": "http://testserver"}
        ) as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            assert first(drain(ws), "ready") is not None


class TestASpokenTurn:
    def test_hello_reports_the_rates_without_creating_a_chat(self, app, stub_engines):
        # No thread until something is actually SAID. Otherwise every connect,
        # every reconnect and every mounted surface leaves an empty chat behind.
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            ready = first(drain(ws), "ready")
            assert ready is not None
            assert ready["micSampleRate"] == SAMPLE_RATE
            assert ready["threadId"] is None

    def test_the_thread_is_bound_once_something_is_said(self, app, stub_engines):
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            drain(ws)
            ws.send_text(json.dumps({"t": "text", "body": "hello"}))
            seen = drain(ws, settle=0.6)
            bound = [m.get("threadId") for m in seen
                     if m.get("t") == "state" and m.get("threadId")]
            assert bound, "the surface was never told which chat this is"

    def test_speaking_produces_a_turn_and_spoken_audio(self, app, stub_engines):
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            drain(ws)
            cursor = send_pcm(ws, 0, speech(1400))
            send_pcm(ws, cursor, quiet(2600))
            seen = drain(ws, settle=1.5)
            clause = first(seen, "clause")
            assert clause is not None, f"nothing was spoken; saw {[m.get('t') for m in seen]}"
            assert "Hi there." in clause["text"]
            assert stub_engines == ["hello there"]
            assert first(seen, "__audio__") is not None, "no PCM came back for the reply"

    def test_a_typed_message_takes_the_same_path(self, app, stub_engines):
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            drain(ws)
            ws.send_text(json.dumps({"t": "text", "body": "type this"}))
            seen = drain(ws, settle=0.6)
            assert stub_engines == ["type this"]
            assert any(m.get("lastUser") == "type this" for m in seen if m.get("t") == "state")

    def test_silence_alone_says_nothing_and_starts_nothing(self, app, stub_engines):
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            drain(ws)
            send_pcm(ws, 0, quiet(5000))
            seen = drain(ws, settle=1.0)
            assert stub_engines == [], "a quiet room started a turn"
            errors = [m.get("error") for m in seen if m.get("t") == "state" and m.get("error")]
            assert errors == [], f"a quiet room reported {errors}"

    def test_the_binary_frame_round_trips_by_sample_offset(self, app, stub_engines):
        # A reordered frame must land where it belongs rather than being appended
        # in arrival order, which is what the absolute cursor is for.
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello", "context_key": "general"}))
            drain(ws)
            pcm = speech(200)
            ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, 3200, pcm[:1600]))
            ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, 0, pcm[:3200]))
            seen = drain(ws, settle=0.3)
            assert first(seen, "state") is not None, "the socket died on a reordered frame"
