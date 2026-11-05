"""The speech routes speak WAV both ways and say plainly when speech is absent."""
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech
from services.speech import audio, engine


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "transcribe", lambda pcm: f"{pcm.size} samples")
    monkeypatch.setattr(engine, "speak_wav",
                        lambda text, voice, speed: audio.to_wav(np.zeros(240, np.int16), 24000))
    app = FastAPI()
    app.include_router(speech.router)
    return TestClient(app)


def test_transcribe_reads_a_wav_at_any_rate(client):
    wav = audio.to_wav(np.zeros(48000, np.int16), 48000)
    r = client.post("/speech/transcribe", content=wav, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.json() == {"text": "16000 samples"}


def test_speak_returns_wav(client):
    r = client.post("/speech/speak", json={"text": "Hello."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"


def test_empty_audio_is_refused(client):
    assert client.post("/speech/transcribe", content=b"").status_code == 400


def test_absent_engine_is_503_not_an_error(client, monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: False)
    assert client.post("/speech/speak", json={"text": "x"}).status_code == 503
    assert client.get("/speech/voices").json()["voices"] == []


def test_voices_are_labeled_and_grouped_by_language(client, monkeypatch):
    monkeypatch.setattr(engine, "voices", lambda: ["bm_george", "af_sky", "zf_xiaobei", "am_adam", "qx_odd", "plain"])
    monkeypatch.setattr(engine, "voice", lambda: "af_sky")
    body = client.get("/speech/voices").json()
    assert body["default"] == "af_sky"
    assert body["voices"] == [
        {"id": "am_adam", "name": "Adam", "language": "American English", "gender": "male"},
        {"id": "af_sky", "name": "Sky", "language": "American English", "gender": "female"},
        {"id": "bm_george", "name": "George", "language": "British English", "gender": "male"},
        {"id": "zf_xiaobei", "name": "Xiaobei", "language": "Mandarin Chinese", "gender": "female"},
        {"id": "qx_odd", "name": "Odd", "language": "Other", "gender": None},
        {"id": "plain", "name": "plain", "language": "Other", "gender": None},
    ]
