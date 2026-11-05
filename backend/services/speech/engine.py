"""Speech for the whole Workshop: one engine behind dictation, read-aloud and
voice mode.

WHERE IT RUNS. Three providers, chosen by configuration:

  local, cpu   a worker process on the processor. Always there once used, so
               speech never waits on anything else the machine is doing.
  local, gpu   a second worker on the card, BORROWED through a guest claim
               (services/gpu_claims.py): taken only once no job claim has been
               live for a minute (services/speech/card_turns.py), given back
               the moment one appears. The piece in flight gets two
               seconds to finish (else it is redone on the processor), the
               worker is stopped so its memory on the card is returned, and
               the rest of the work carries on on the processor.
  remote       any server speaking the OpenAI audio API (a speech server on
               another machine, or a hosted service).

Work is cut into pieces (under 30 s of audio, or one sentence of text) and the
device is chosen again for every piece, which is what lets a long job move to
the processor halfway through.

Configuration, all optional (environment, e.g. the installation's instance.env):

  WORKSHOP_SPEECH_DEVICE   auto (default): borrow the card when the GPU
                           environment exists and no job claims it; cpu; gpu
                           (use the card without asking)
  WORKSHOP_SPEECH_THREADS  processor threads per model (default: a quarter of
                           the cores, at least 2, at most 8)
  WORKSHOP_SPEECH_CPUS     affinity list for the processor worker, e.g. 0-11
  WORKSHOP_SPEECH_GPU      CUDA device index (default 0)
  WORKSHOP_SPEECH_VOICE    default voice (default af_heart)
  WORKSHOP_SPEECH_URL      OpenAI-compatible base URL including /v1; when set,
                           every request goes there instead
  WORKSHOP_SPEECH_API_KEY, WORKSHOP_SPEECH_STT_MODEL, WORKSHOP_SPEECH_TTS_MODEL
"""
from __future__ import annotations

import functools
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from services import gpu_claims
from services.speech import audio, heard, lag
from services.speech.card_turns import CardTurns
from services.speech.giveback import GiveBack
from services.speech.models import DEFAULT_VOICE
from services.speech.remote import Remote
from services.speech.workers import Worker, WorkerError, decode_pcm, encode_pcm
from wsplatform import paths

log = lag.log

#: Per process: each backend process owns its own workers and its own claim.
GUEST = f"workshop-speech-{os.getpid()}"
GPU_IDLE_S = 120.0
# A card that failed to load is tried again after this long, not never.
GPU_RETRY_S = 300.0
CPU_LOAD_S = 900.0
WATCH_S = 0.5


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _threads() -> int:
    raw = _env("WORKSHOP_SPEECH_THREADS")
    if raw.isdigit():
        return int(raw)
    return max(2, min(8, (os.cpu_count() or 4) // 4))


def speech_dir() -> Path:
    return paths.runtime_dir() / "speech"


def voice() -> str:
    return _env("WORKSHOP_SPEECH_VOICE", DEFAULT_VOICE)


class Local:
    def __init__(self) -> None:
        root = speech_dir()
        models = root / "models"
        self.mode = _env("WORKSHOP_SPEECH_DEVICE", "auto").lower()
        self.gpu_index = int(_env("WORKSHOP_SPEECH_GPU", "0") or 0)
        self.cpu = Worker(sys.executable, "cpu", models, _threads(),
                          _env("WORKSHOP_SPEECH_CPUS"), 0, root / "worker-cpu.log")
        gpu_python = root / "gpu-env" / "bin" / "python"
        self.gpu = (Worker(str(gpu_python), "cuda", models, 0, "", 5, root / "worker-gpu.log")
                    if self.mode != "cpu" and gpu_python.exists() else None)
        self._lock = threading.Lock()
        self._draining = False
        self._watcher: threading.Thread | None = None
        self._gpu_failed_at: float | None = None
        self.turns = CardTurns()

    @property
    def installed(self) -> bool:
        return all(importlib.util.find_spec(m) for m in ("onnx_asr", "kokoro_onnx"))

    @functools.cached_property
    def giveback(self) -> GiveBack:
        return GiveBack(self.cpu)

    def warm(self) -> None:
        if self.giveback.yielded:
            return
        with self._lock:
            self.cpu.start()

    def _cpu(self) -> Worker:
        with self._lock:
            self.cpu.start()
        if not self.cpu.wait_ready(CPU_LOAD_S):
            raise WorkerError(self.cpu.error or "speech worker failed to start")
        return self.cpu

    def _borrow(self) -> Worker | None:
        """The GPU worker, if this piece may run on the card."""
        gpu = self.gpu
        if gpu is None or self._draining or not self._gpu_usable(gpu):
            return None
        if self.mode == "auto":
            granted = gpu_claims.claim(GUEST, "guest", ttl_s=10, device=self.gpu_index,
                                       label="speech")["granted"]
            self.turns.seen(granted)
            if not granted:
                return None
        with self._lock:
            if not gpu.alive:
                if self.mode == "auto" and not self.turns.may_start():
                    gpu_claims.release(GUEST)
                    return None
                gpu.start()
                self.turns.started()
                self._watch()
        return gpu if gpu.ready else None

    def _gpu_usable(self, gpu: Worker) -> bool:
        if not gpu.error:
            self._gpu_failed_at = None
            return True
        now = time.time()
        if self._gpu_failed_at is None:
            self._gpu_failed_at = now
            log.warning("speech could not load on the card, using the processor: %s", gpu.error)
            return False
        if now - self._gpu_failed_at < GPU_RETRY_S:
            return False
        self._gpu_failed_at = None
        return True

    def _watch(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True,
                                         name="speech-gpu-watch")
        self._watcher.start()

    def _watch_loop(self) -> None:
        gpu = self.gpu
        assert gpu is not None
        while gpu.alive:
            time.sleep(WATCH_S)
            idle = not gpu.busy and time.time() - gpu.last_used > GPU_IDLE_S
            wanted = self.mode == "auto" and not gpu_claims.claim(
                GUEST, "guest", ttl_s=10, device=self.gpu_index, label="speech")["granted"]
            self.turns.seen(not wanted)
            if wanted or idle:
                self._draining = True
                end = time.time() + 2.0
                while gpu.busy and time.time() < end:
                    time.sleep(0.05)
                gpu.stop()
                log.info("speech left the card (%s)", "a job claimed it" if wanted else "idle")
        gpu_claims.release(GUEST)
        self._draining = False

    def run(self, op: str, **payload) -> dict:
        gpu = self._borrow()
        if gpu is not None:
            try:
                return gpu.call(op, **payload)
            except WorkerError as e:
                log.info("speech piece moved to the processor: %s", e)
        with self.giveback.using():
            return self._cpu().call(op, **payload)

    def transcribe(self, pcm: np.ndarray) -> str:
        return self.run("transcribe", pcm=encode_pcm(pcm))["text"]

    def transcribe_timed(self, pcm: np.ndarray) -> heard.Heard:
        r = self.run("transcribe", pcm=encode_pcm(pcm), timed=True)
        return heard.from_tokens(r.get("tokens") or [], r.get("stamps") or [])

    def speak(self, text: str, voice_name: str, speed: float) -> tuple[np.ndarray, int]:
        r = self.run("speak", text=text, voice=voice_name, speed=speed)
        return decode_pcm(r["pcm"]), int(r["rate"])

    def voices(self) -> list[str]:
        with self.giveback.using():
            return self._cpu().call("voices")["voices"]

    def state(self) -> dict:
        w = self.cpu
        status = ("missing" if not self.installed else "ready" if w.ready
                  else "error" if w.error else "loading" if w.alive else "idle")
        gpu = self.gpu
        return {"provider": "local", "state": status, "error": w.error,
                "device": "gpu" if gpu is not None and gpu.ready else "cpu",
                "gpu_available": gpu is not None, "mode": self.mode}


@functools.lru_cache(maxsize=1)
def provider() -> Local | Remote:
    """This process's provider. Its workers are children of this process, so
    the handle is per process by nature; the GPU claim it takes is not."""
    url = _env("WORKSHOP_SPEECH_URL")
    return Remote(url, voice()) if url else Local()


def available() -> bool:
    p = provider()
    return not isinstance(p, Local) or p.installed


def transcribe(pcm: np.ndarray) -> str:
    """Text for 16 kHz PCM of any length, worked through piece by piece."""
    p = provider()
    texts = [p.transcribe(piece) for piece in audio.pieces(pcm)]
    return " ".join(t for t in texts if t).strip()


def transcribe_timed(pcm: np.ndarray) -> heard.Heard:
    """Text and word times for 16 kHz PCM no longer than one piece (audio.PIECE_S)."""
    return provider().transcribe_timed(pcm[:int(audio.PIECE_S * audio.STT_RATE)])


def transcribe_audio(data: bytes) -> str:
    return transcribe(audio.decode(data))


def speak(text: str, voice_name: str | None = None, speed: float = 1.0) -> tuple[np.ndarray, int]:
    return provider().speak(text, voice_name or voice(), speed)


def speak_wav(text: str, voice_name: str | None = None, speed: float = 1.0) -> bytes:
    pcm, rate = speak(text, voice_name, speed)
    return audio.to_wav(pcm, rate)


def voices() -> list[str]:
    return provider().voices()


def warm() -> None:
    p = provider()
    if available():
        p.warm()


def health() -> dict:
    return {**provider().state(), "available": available(), "voice": voice()}
