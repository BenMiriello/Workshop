"""A dictation's end published as `dictated` with its recording, the recording
kept an hour and served on loopback only, and refused at the public edge."""
from __future__ import annotations

import os
import time
import types

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech_dictations
from services import app_events, app_runner, installation
from services.speech import recordings

ID = "0123456789abcdef0123"
HOOK = {"dictated": {"service": "api", "path": "/hooks/dictated"}}


@pytest.fixture(autouse=True)
def kept(tmp_path, monkeypatch):
    monkeypatch.setattr(recordings, "root", lambda: tmp_path / "recordings")
    monkeypatch.setattr(installation, "backend_port", lambda: 8123)


def record(dictation: str, seconds: float) -> None:
    rec = recordings.Recorder(dictation)
    pcm = np.full(int(seconds * 16000), 1000, dtype=np.int16)
    for i in range(0, pcm.shape[0], 320):
        rec.write(i, pcm[i:i + 320])
    rec.write(0, pcm[:320])
    rec.close()


def test_a_reconnect_adds_only_what_is_new_and_a_gap_is_silence():
    record(ID, 0.5)
    rec = recordings.Recorder(ID)
    rec.write(7200, np.full(1600, 5, dtype=np.int16))
    rec.write(8000, np.full(1600, 5, dtype=np.int16))
    rec.close()
    assert recordings.length_ms(ID) == 600


def test_recordings_older_than_an_hour_are_deleted():
    record(ID, 0.2)
    old = time.time() - recordings.KEEP_S - 5
    os.utime(recordings.root() / f"{ID}.pcm", (old, old))
    assert recordings.wav(ID) is None
    assert not (recordings.root() / f"{ID}.pcm").exists()


def test_a_guessable_or_odd_id_is_never_a_file():
    assert not recordings.valid("short") and not recordings.valid("../" + "a" * 20)
    assert recordings.wav("../../etc/passwd" + "a" * 10) is None


@pytest.fixture
def subscribers(monkeypatch):
    sent: list[dict] = []
    codes = {8811: 204, 8812: 500}
    monkeypatch.setattr(app_runner, "_STATE", {"clip": {}, "other": {}})
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: slug)
    monkeypatch.setattr(app_runner, "_read_manifest_file", lambda d: {"events": HOOK})
    monkeypatch.setattr(app_runner, "service_target",
                        lambda slug, svc: ("running", 8811 if slug == "clip" else 8812))

    def post(url, *, json, headers, timeout):
        sent.append(json)
        return types.SimpleNamespace(status_code=codes[int(url.split(":")[2].split("/")[0])])

    monkeypatch.setattr(app_events.httpx, "post", post)
    return sent


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(speech_dictations.router)
    app.include_router(speech_dictations.recordings_router)
    return TestClient(app)


BODY = {"id": ID, "text": "hello there", "started_at": "2026-10-28T10:00:00Z",
        "ended_at": "2026-10-28T10:00:05Z", "status": "done",
        "places": [{"kind": "chat", "name": "Plans", "path": "/chat/x"}]}


def test_dictation_end_is_published_with_its_audio_and_counted(client, subscribers):
    record(ID, 1.0)
    r = client.post("/speech/dictations", json=BODY)
    assert r.json() == {"delivered": 1, "subscribers": 2}
    sent = subscribers[0]
    assert sent["event"] == "dictated" and sent["text"] == "hello there" and not sent["imported"]
    assert sent["places"] == BODY["places"]
    assert sent["audio"] == {"url": f"http://127.0.0.1:8123/api/speech/recordings/{ID}",
                             "mime": "audio/wav", "ms": 1000}
    wav = client.get(f"/speech/recordings/{ID}")
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav"
    assert len(wav.content) == 44 + 32000


def test_imported_history_carries_no_audio(client, subscribers):
    record(ID, 0.5)
    client.post("/speech/dictations", json={**BODY, "imported": True})
    assert subscribers[0]["audio"] is None and subscribers[0]["imported"] is True


def test_no_recording_means_no_audio_and_none_found(client, subscribers):
    client.post("/speech/dictations", json=BODY)
    assert subscribers[0]["audio"] is None
    assert client.get(f"/speech/recordings/{ID}").status_code == 404


def test_no_subscriber_delivers_to_none(client, monkeypatch):
    monkeypatch.setattr(app_runner, "_STATE", {})
    assert client.post("/speech/dictations", json=BODY).json() == {"delivered": 0, "subscribers": 0}


def test_recordings_are_loopback_only_and_blocked_at_the_edge():
    import main
    import workshop_public_app
    route = next(r for r in main.app.routes if getattr(r, "path", "") == "/speech/recordings/{dictation}")
    assert any(d.dependency.__name__ == "require_loopback" for d in route.dependencies)
    assert "/speech/recordings" in workshop_public_app._BLOCKED_PREFIXES
