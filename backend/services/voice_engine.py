"""Local speech engine for Workshop voice mode — STT + TTS, fully self-hosted.

Two lazily-loaded model singletons:

  * STT  — faster-whisper (`base.en` int8). Decodes any browser-recorded blob
           (webm/opus, wav, mp4) via PyAV and returns a transcript.
  * TTS  — Kokoro-82M via `kokoro-onnx`. Turns text into 24 kHz audio, one
           sentence at a time so the client can start speaking immediately.

Nothing loads at import time. If the model packages or weights are missing the
engine reports `available=False` and the routes answer 503, so the frontend
falls back to the browser speech APIs and voice mode still works.

Device: CPU by default (24 cores handle short utterances comfortably). With
`VOICE_DEVICE=auto` the engine opportunistically loads onto CUDA when the GPU
broker grants VRAM (see `gpu_warmhold`); it never blocks and always falls back
to CPU. `VOICE_DEVICE=cpu|cuda` forces the choice.
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import wave
from pathlib import Path

log = logging.getLogger("workshop.voice_engine")

_RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", "/home/clawd-a/workshop/.runtime"))
_MODELS_DIR = Path(os.environ.get("VOICE_MODELS_DIR", str(_RUNTIME / "voice" / "models")))

# --- config -----------------------------------------------------------------
STT_MODEL = os.environ.get("VOICE_STT_MODEL", "base.en")
# A second, smaller model used ONLY for the live partial transcript the client
# shows while you are still speaking. It runs against a growing window of the
# current segment, so it must finish well inside the endpoint silence; base.en
# (RTF ~0.3 on CPU) falls behind on a long utterance, tiny.en (~0.1) does not.
# Its output is display-only and never becomes the committed turn text.
STT_PREVIEW_MODEL = os.environ.get("VOICE_STT_PREVIEW_MODEL", "tiny.en")
TTS_ONNX = os.environ.get("VOICE_TTS_ONNX", str(_MODELS_DIR / "kokoro-v1.0.onnx"))
TTS_VOICES = os.environ.get("VOICE_TTS_VOICES", str(_MODELS_DIR / "voices-v1.0.bin"))
DEFAULT_VOICE = os.environ.get("VOICE_TTS_VOICE", "af_heart")
TTS_SAMPLE_RATE = 24000


def _want_device() -> str:
    """cpu | cuda | auto — from VOICE_DEVICE (default cpu)."""
    return (os.environ.get("VOICE_DEVICE") or "cpu").strip().lower()


def _cuda_ok() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _resolve_device() -> str:
    want = _want_device()
    if want == "cuda":
        return "cuda" if _cuda_ok() else "cpu"
    if want == "auto":
        # Opportunistic: only take the GPU if it's actually usable. The broker
        # warm-hold (touched by the routes) keeps the lease alive across a
        # session; here we just check the device is present.
        return "cuda" if _cuda_ok() else "cpu"
    return "cpu"


# ============================================================================
# Speech-to-text (faster-whisper)
# ============================================================================
class _STT:
    def __init__(self, model_name: str = STT_MODEL) -> None:
        self._model = None
        self._name = model_name
        self._lock = threading.Lock()
        self._device = "cpu"
        self._err: str | None = None

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception as e:
            self._err = f"faster-whisper not installed: {e}"
            return False

    def _ensure(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel  # type: ignore

            device = _resolve_device()
            compute = "int8_float16" if device == "cuda" else "int8"
            log.info("loading faster-whisper %s on %s (%s)", self._name, device, compute)
            self._model = WhisperModel(self._name, device=device, compute_type=compute)
            self._device = device
            return self._model

    def transcribe(self, audio: bytes, language: str | None = "en", preview: bool = False) -> str:
        """Decode `audio` (any browser container — PyAV handles it) to text.

        `preview=True` transcribes a growing window of an utterance that is still
        in progress, for the live partial the client displays. It drops the VAD
        filter (a mid-word tail is not silence to be trimmed, and trimming it
        makes the partial flicker) and never raises for an empty result."""
        model = self._ensure()
        # faster-whisper decodes the container itself (PyAV) from a file-like.
        segments, _info = model.transcribe(
            io.BytesIO(audio),
            language=language,
            vad_filter=not preview,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()


# ============================================================================
# Text-to-speech (Kokoro-82M via kokoro-onnx)
# ============================================================================
def _patch_speed_dtype(model) -> None:
    """kokoro-onnx 0.5.0 feeds `speed` as int32 on the `input_ids` code path, but
    the onnx-community Kokoro-v1.0 export expects float. Rebind `_create_audio`
    on this instance with the corrected dtype. Uses only the model's own
    tokenizer/session, so it stays valid across the library's chunking logic."""
    import time as _time

    import numpy as np  # noqa

    input_names = [i.name for i in model.sess.get_inputs()]
    if "input_ids" not in input_names:
        return  # old export path already sends float speed — nothing to fix.

    from kokoro_onnx.config import MAX_PHONEME_LENGTH, SAMPLE_RATE  # type: ignore

    def _create_audio(self, phonemes, voice, speed):
        phonemes = phonemes[:MAX_PHONEME_LENGTH]
        _t = _time.time()
        tokens = np.array(self.tokenizer.tokenize(phonemes), dtype=np.int64)
        voice = voice[len(tokens)]
        tokens = [[0, *tokens, 0]]
        inputs = {
            "input_ids": tokens,
            "style": np.array(voice, dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),  # was int32 — the bug
        }
        audio = self.sess.run(None, inputs)[0]
        _ = _t
        return audio, SAMPLE_RATE

    import types

    model._create_audio = types.MethodType(_create_audio, model)


class _TTS:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self._voices: list[str] = []
        self._err: str | None = None

    @property
    def available(self) -> bool:
        try:
            import kokoro_onnx  # noqa: F401
        except Exception as e:
            self._err = f"kokoro-onnx not installed: {e}"
            return False
        if not (Path(TTS_ONNX).exists() and Path(TTS_VOICES).exists()):
            self._err = f"kokoro weights missing ({TTS_ONNX} / {TTS_VOICES})"
            return False
        return True

    def _ensure(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from kokoro_onnx import Kokoro  # type: ignore

            log.info("loading Kokoro TTS (%s)", TTS_ONNX)
            self._model = Kokoro(TTS_ONNX, TTS_VOICES)
            _patch_speed_dtype(self._model)
            try:
                self._voices = sorted(self._model.get_voices())
            except Exception:
                self._voices = [DEFAULT_VOICE]
            return self._model

    def voices(self) -> list[str]:
        if self._voices:
            return self._voices
        if not self.available:
            return []
        try:
            self._ensure()
        except Exception as e:
            log.warning("could not enumerate voices: %s", e)
        return self._voices

    def synth_wav(self, text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
        """Render `text` to a single mono 16-bit PCM WAV (bytes)."""
        model = self._ensure()
        voice = voice or DEFAULT_VOICE
        samples, sr = model.create(text, voice=voice, speed=float(speed), lang="en-us")
        return _float_to_wav(samples, sr)


def _float_to_wav(samples, sample_rate: int) -> bytes:
    """float32 [-1,1] numpy array -> 16-bit PCM mono WAV bytes (stdlib only)."""
    import numpy as np  # numpy ships with faster-whisper/kokoro

    arr = np.asarray(samples, dtype=np.float32)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    return buf.getvalue()


# ============================================================================
# Sentence splitting (for streaming TTS + heard-vs-generated tracking)
# ============================================================================
_SENT_RE = re.compile(r"[^.!?…\n]+[.!?…]*", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    """Coarse sentence split tuned for speech: keeps terminal punctuation,
    merges very short fragments so we don't utter one-word chunks."""
    parts = [m.group(0).strip() for m in _SENT_RE.finditer(text or "")]
    parts = [p for p in parts if p]
    out: list[str] = []
    for p in parts:
        if out and len(p) < 24 and not out[-1].endswith(("\n",)):
            out[-1] = f"{out[-1]} {p}".strip()
        else:
            out.append(p)
    return out


# --- singletons -------------------------------------------------------------
STT = _STT(STT_MODEL)
# Loaded lazily on the first preview request, so a session that never shows a
# partial (browser/native STT tiers) never pays for it.
STT_PREVIEW = _STT(STT_PREVIEW_MODEL)
TTS = _TTS()


def health() -> dict:
    return {
        "stt": {
            "available": STT.available,
            "model": STT_MODEL,
            "preview_model": STT_PREVIEW_MODEL,
            "error": STT._err,
        },
        "tts": {
            "available": TTS.available,
            "voice": DEFAULT_VOICE,
            "error": TTS._err,
            "sample_rate": TTS_SAMPLE_RATE,
        },
        "device": _resolve_device(),
        "device_pref": _want_device(),
    }
