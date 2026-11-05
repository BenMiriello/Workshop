"""Voice mode's view of the speech engine.

Voice mode was written against three objects, `STT`, `STT_PREVIEW` and `TTS`,
each taking and returning WAV bytes. They stay, as a thin face over
services/speech/engine.py, which is the one engine every speech feature in the
Workshop uses. The live partial and the committed text share one model: on the
processor Parakeet reads a few seconds of speech in a fraction of a second, so
a smaller second model for previews no longer earns its memory.
"""
from __future__ import annotations

import numpy as np

from services.speech import audio, engine
from services.speech.models import STT_DIR

TTS_SAMPLE_RATE = 24000
DEFAULT_VOICE = engine.voice()
split_sentences = audio.split_sentences


class _STT:
    @property
    def available(self) -> bool:
        return engine.available()

    def transcribe(self, wav: bytes, language: str | None = None, preview: bool = False) -> str:
        return engine.transcribe(audio.decode(wav))


class _TTS:
    @property
    def available(self) -> bool:
        return engine.available()

    def voices(self) -> list[str]:
        return engine.voices() if self.available else []

    def synth_wav(self, text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
        pcm, rate = engine.speak(text, voice, speed)
        pcm = audio.resample(np.asarray(pcm, dtype=np.int16), rate, TTS_SAMPLE_RATE)
        return audio.to_wav(pcm, TTS_SAMPLE_RATE)


STT = _STT()
STT_PREVIEW = _STT()
TTS = _TTS()


def health() -> dict:
    h = engine.health()
    error = h["error"] if h["available"] else "speech packages not installed"
    return {
        "stt": {"available": h["available"], "model": STT_DIR, "preview_model": STT_DIR,
                "error": error},
        "tts": {"available": h["available"], "voice": h["voice"], "error": error,
                "sample_rate": TTS_SAMPLE_RATE},
        "device": h["device"],
        "device_pref": h["mode"],
    }
