"""Live dictation: the open stretch read whole and revised, pauses shortened
rather than ending it, settling only where it should, and the socket around it."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech_live
from services.speech import cuts, live, recordings
from services.speech.commands import Table
from services.speech.heard import Heard, Word
from services.speech.live import LiveDictation
from services.voice import protocol
from services.voice.detectors import SpeechFrame

RATE = 16000


class LoudIsSpeech:
    tier = "test"

    def __call__(self, frame: np.ndarray) -> SpeechFrame:
        p = float(np.abs(frame).mean() > 0.05)
        return SpeechFrame(p >= 0.5, p)


def speech(seconds: float) -> np.ndarray:
    return np.full(int(seconds * RATE), 8000, dtype=np.int16)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.int16)


def words_in(pcm: np.ndarray, stops: bool) -> Heard:
    """One word per half second of sound; with `stops`, the last word before
    each quiet ends a sentence."""
    loud = np.abs(pcm) > 0
    words: list[Word] = []
    at, n = 0, 0
    while at < pcm.shape[0]:
        if not loud[at]:
            at += 1
            continue
        end = at
        while end < pcm.shape[0] and loud[end]:
            end += 1
        for s in range(at, end, RATE // 2):
            n += 1
            last = s == range(at, end, RATE // 2)[-1]
            words.append(Word(f"w{n}" + ("." if stops and last else ""), s / RATE, min(end, s + RATE // 2) / RATE))
        at = end
    return Heard(" ".join(w.text for w in words), tuple(words))


class Run:
    def __init__(self, start: int = 0, stops: bool = False) -> None:
        self.reads: list[int] = []
        self.out: list[dict] = []
        self.stops = stops
        self.s = LiveDictation(self._transcribe, start=start, detector=LoudIsSpeech())
        self.at = start

    def _transcribe(self, pcm: np.ndarray) -> Heard:
        self.reads.append(pcm.shape[0])
        return words_in(pcm, self.stops)

    async def emit(self, msg: dict) -> None:
        self.out.append(msg)

    def feed(self, pcm: np.ndarray, frame: int = 320) -> None:
        for i in range(0, pcm.shape[0], frame):
            chunk = pcm[i:i + frame]
            self.s.write(self.at, chunk)
            self.at += chunk.shape[0]

    def tick(self) -> None:
        self.s._read_at = 0.0
        asyncio.run(self.s.tick(self.emit))

    def texts(self) -> list[dict]:
        return [m for m in self.out if m["t"] == "text"]

    def finals(self) -> list[dict]:
        return [m for m in self.out if m["t"] == "final"]


def test_text_while_speaking_is_the_whole_stretch_again():
    r = Run()
    r.feed(speech(1.0))
    r.tick()
    r.feed(speech(1.0))
    r.tick()
    first, second = r.texts()
    assert first["from"] == second["from"] == 0
    assert first["text"] == "w1 w2" and second["text"] == "w1 w2 w3 w4"
    assert r.finals() == []


def test_a_pause_does_not_settle_or_split_the_stretch():
    r = Run()
    r.feed(speech(1.0))
    r.tick()
    r.feed(silence(1.2))
    r.tick()
    r.feed(speech(1.0))
    r.tick()
    assert r.finals() == []
    assert r.texts()[-1] == {"t": "text", "text": "w1 w2 w3 w4", "from": 0, "to": r.at}


def test_a_long_pause_is_shortened_before_the_read():
    r = Run()
    r.feed(speech(1.0))
    r.feed(silence(10.0))
    r.feed(speech(1.0))
    r.tick()
    assert r.reads[-1] < int(3.0 * RATE)
    assert r.finals() == []


def test_nothing_new_said_means_no_read():
    r = Run()
    r.feed(speech(1.0))
    r.feed(silence(0.5))
    r.tick()
    reads = len(r.reads)
    r.feed(silence(2.0))
    r.tick()
    assert len(r.reads) == reads


def test_silence_alone_is_let_go_without_a_read():
    r = Run()
    r.feed(silence(4.0))
    r.tick()
    finals = r.finals()
    assert finals and finals[-1]["text"] == "" and finals[-1]["to"] > 0
    assert r.reads == []


def test_a_long_stretch_settles_its_earlier_sentences():
    r = Run(stops=True)
    for _ in range(5):
        r.feed(speech(2.5))
        r.feed(silence(0.4))
        r.tick()
        r.tick()
    finals = r.finals()
    final = finals[-1]
    assert final["text"].endswith(".") and final["rest"]
    assert finals[0]["from"] == 0 and final["to"] == r.s.open_from
    assert all(a["to"] == b["from"] for a, b in zip(finals, finals[1:]))
    words = len(final["text"].split())
    assert final["to"] - final["from"] > int(words * 0.5 * RATE)
    r.feed(speech(1.0))
    r.tick()
    assert r.texts()[-1]["from"] == final["to"]


def test_someone_who_never_stops_is_cut_at_the_widest_gap():
    r = Run()
    talk = np.concatenate([speech(live.LONGEST_MS / 1000 - 6), silence(0.4), speech(7)])
    r.feed(talk)
    r.tick()
    r.tick()
    final = r.finals()[-1]
    assert final["text"] and final["rest"]
    gap = int((live.LONGEST_MS / 1000 - 6) * RATE)
    assert gap - RATE // 2 < final["to"] < gap + int(0.4 * RATE)


def test_commit_starts_the_next_stretch_where_the_client_froze():
    r = Run()
    r.feed(speech(1.0))
    r.tick()
    shown = r.texts()[-1]
    asyncio.run(r.s.commit(shown["to"], r.emit))
    r.feed(speech(1.0))
    r.tick()
    nxt = r.texts()[-1]
    assert nxt["from"] == shown["to"] and nxt["text"] == "w1 w2"
    assert r.finals() == []


def test_text_ends_just_past_its_last_word_so_a_word_begun_is_not_cut():
    r = Run()
    r.s._transcribe = lambda pcm: Heard("One", (Word("One", 0.1, 0.3),))
    r.feed(speech(2.0))
    r.tick()
    assert abs(r.texts()[-1]["to"] - (0.3 + cuts.WORD_TAIL_MS / 1000) * RATE) <= 1


def test_a_word_heard_early_ends_where_its_speech_ends():
    r = Run()
    r.s._transcribe = lambda pcm: Heard("One", (Word("One", 0.1, 0.3),))
    r.feed(speech(0.7))
    r.feed(silence(0.5))
    r.feed(speech(1.0))
    r.tick()
    assert abs(r.texts()[-1]["to"] - int(0.8 * RATE)) <= 512


def test_commit_behind_a_settle_says_the_words_in_between_again():
    r = Run()
    r.feed(speech(1.0))
    r.tick()
    shown = r.texts()[-1]
    r.feed(speech(1.0))
    asyncio.run(r.s.finish(r.emit))
    asyncio.run(r.s.commit(shown["to"], r.emit))
    again = r.finals()[-1]
    assert again["from"] == shown["to"] and again["to"] == r.at and again["text"]


def test_stop_settles_the_rest():
    r = Run()
    r.feed(speech(0.8))
    asyncio.run(r.s.finish(r.emit))
    assert r.finals()[-1]["text"] == "w1 w2"
    assert r.s.open_from == r.at


def test_a_resent_frame_already_settled_is_ignored():
    r = Run()
    r.feed(speech(1.0))
    asyncio.run(r.s.finish(r.emit))
    settled = r.s.open_from
    r.s.write(0, speech(0.5))
    assert r.s.open_from == settled and r.s.timeline.end == settled


def test_reconnect_starts_where_the_client_says():
    r = Run(start=5 * RATE)
    r.feed(speech(1.0))
    asyncio.run(r.s.finish(r.emit))
    assert r.finals()[-1]["from"] == 5 * RATE
    assert r.reads == [RATE]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(speech_live, "socket_origin_ok", lambda ws: True)
    monkeypatch.setattr(speech_live, "socket_user", lambda ws: {"id": 6})
    monkeypatch.setattr(speech_live.ownership, "set_current_owner", lambda _id: None)
    monkeypatch.setattr(speech_live.engine, "available", lambda: True)
    monkeypatch.setattr(speech_live.engine, "warm", lambda: None)
    monkeypatch.setattr(speech_live.meaning, "warm", lambda *a: None)
    monkeypatch.setattr(speech_live.engine, "transcribe_timed", lambda pcm: Heard("hello there"))
    monkeypatch.setattr(live, "SpeechDetector", LoudIsSpeech)
    monkeypatch.setattr(speech_live, "load_table", lambda: Table([], []))
    monkeypatch.setattr(recordings, "root", lambda: tmp_path / "recordings")
    app = FastAPI()
    app.include_router(speech_live.router)
    return TestClient(app)


def test_socket_hello_audio_stop(client):
    with client.websocket_connect("/speech/live") as ws:
        ws.send_text(json.dumps({"t": "hello", "from": 0}))
        assert json.loads(ws.receive_text())["t"] == "ready"
        pcm = speech(0.8)
        for i in range(0, pcm.shape[0], 320):
            ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, i, pcm[i:i + 320]))
        ws.send_text(json.dumps({"t": "stop"}))
        got = []
        while not got or got[-1]["t"] != "done":
            got.append(json.loads(ws.receive_text()))
    finals = [m for m in got if m["t"] == "final"]
    assert finals[-1]["text"] == "hello there"
    assert finals[-1]["to"] == pcm.shape[0]


def test_socket_keeps_the_audio_under_the_dictation_id_and_answers_pause(client):
    dictation = "d" * 24
    with client.websocket_connect("/speech/live") as ws:
        ws.send_text(json.dumps({"t": "hello", "from": 0, "dictation": dictation}))
        assert json.loads(ws.receive_text())["t"] == "ready"
        pcm = speech(0.5)
        for i in range(0, pcm.shape[0], 320):
            ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, i, pcm[i:i + 320]))
        ws.send_text(json.dumps({"t": "pause", "paused": True}))
        got = []
        while not got or got[-1]["t"] != "paused":
            got.append(json.loads(ws.receive_text()))
        assert got[-1] == {"t": "paused", "paused": True}
        ws.send_text(json.dumps({"t": "stop"}))
        while got[-1]["t"] != "done":
            got.append(json.loads(ws.receive_text()))
    assert recordings.length_ms(dictation) == 500
    assert recordings.wav(dictation)[:4] == b"RIFF"


def test_socket_refuses_without_a_user(client, monkeypatch):
    monkeypatch.setattr(speech_live, "socket_user", lambda ws: None)
    with pytest.raises(Exception):
        with client.websocket_connect("/speech/live") as ws:
            ws.receive_text()
