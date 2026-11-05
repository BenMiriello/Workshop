"""Speech from a server speaking the OpenAI audio API (WORKSHOP_SPEECH_URL).

Any failure there is reported as a WorkerError, the same as a local worker
that cannot answer, so the routes say "not available" rather than crashing.
"""
from __future__ import annotations

import os

import numpy as np

from services.speech import audio, heard
from services.speech.workers import WorkerError


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class Remote:
    def __init__(self, url: str, default_voice: str) -> None:
        self.url = url.rstrip("/")
        self.default_voice = default_voice
        key = _env("WORKSHOP_SPEECH_API_KEY")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}

    def _post(self, path: str, **kw):
        import httpx

        try:
            r = httpx.post(f"{self.url}{path}", headers=self.headers, timeout=120, **kw)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise WorkerError(f"speech server: {e}") from e
        return r

    def transcribe(self, pcm: np.ndarray) -> str:
        r = self._post("/audio/transcriptions",
                       files={"file": ("audio.wav", audio.to_wav(pcm, audio.STT_RATE), "audio/wav")},
                       data={"model": _env("WORKSHOP_SPEECH_STT_MODEL", "whisper-1")})
        return (r.json().get("text") or "").strip()

    def transcribe_timed(self, pcm: np.ndarray) -> heard.Heard:
        """Word times when the server reports them; text alone otherwise."""
        r = self._post("/audio/transcriptions",
                       files={"file": ("audio.wav", audio.to_wav(pcm, audio.STT_RATE), "audio/wav")},
                       data={"model": _env("WORKSHOP_SPEECH_STT_MODEL", "whisper-1"),
                             "response_format": "verbose_json",
                             "timestamp_granularities[]": "word"})
        body = r.json()
        return heard.from_spans(body.get("text") or "", body.get("words") or [])

    def speak(self, text: str, voice_name: str, speed: float) -> tuple[np.ndarray, int]:
        r = self._post("/audio/speech",
                       json={"model": _env("WORKSHOP_SPEECH_TTS_MODEL", "tts-1"), "input": text,
                             "voice": voice_name, "speed": speed, "response_format": "wav"})
        return audio.read_wav(r.content)

    def voices(self) -> list[str]:
        return [self.default_voice]

    def warm(self) -> None:
        return None

    def state(self) -> dict:
        return {"provider": "remote", "state": "ready", "error": None, "device": "remote",
                "gpu_available": False, "mode": "remote"}
