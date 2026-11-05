"""One speech worker process: both models loaded, one request at a time.

    python -m services.speech.worker MODELS_DIR DEVICE THREADS [CPUS]

DEVICE is cpu or cuda. CPUS is an optional affinity list ("0-11,14-23").
Requests and replies are JSON lines on stdin and stdout:

    {"id": 1, "op": "transcribe", "pcm": <base64 s16le mono 16 kHz>}
    {"id": 1, "op": "transcribe", "pcm": ..., "timed": true}   also tokens, stamps
    {"id": 2, "op": "speak", "text": "...", "voice": "af_heart", "speed": 1.0}
    {"id": 3, "op": "voices"}

The first line written is {"ready": ...} once both models are loaded. The
process exits when stdin closes, so it never outlives the backend that started
it: a restart that leaves the parent's children running still closes the pipe.
Run in its own process so a GPU copy can be stopped outright, which is the
only way its memory on the card is actually returned.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.speech import models  # noqa: E402


def _cpus(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return out


def _keep_in_memory() -> None:
    """Hold the loaded models in RAM. Live dictation reads on the processor
    while other work fills the machine's memory, and a model paged out to swap
    turns a read of a fraction of a second into seconds. What is mapped now is
    locked page by page as it is touched (MCL_ONFAULT), so nothing merely
    mapped is pulled in, and buffers a read allocates later are not locked.
    The worker is stopped outright when its memory is owed back
    (services/speech/giveback.py), which unlocks it."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.mlockall(1 | 4) != 0:       # MCL_CURRENT | MCL_ONFAULT
        print(f"speech worker: models not locked in memory: {os.strerror(ctypes.get_errno())}",
              file=sys.stderr, flush=True)


def _handle(req: dict, stt, tts) -> dict:
    import numpy as np

    op = req.get("op")
    if op == "transcribe":
        pcm = np.frombuffer(base64.b64decode(req["pcm"]), dtype="<i2")
        samples = pcm.astype(np.float32) / 32768.0
        if not samples.size:
            return {"text": "", "tokens": [], "stamps": []}
        if req.get("timed"):
            return models.transcribe_timed(stt, samples)
        return {"text": models.transcribe(stt, samples)}
    if op == "speak":
        samples, rate = models.speak(tts, req["text"], req.get("voice") or "",
                                     req.get("speed") or 1.0)
        arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        pcm = (arr * 32767.0).astype("<i2").tobytes()
        return {"pcm": base64.b64encode(pcm).decode(), "rate": int(rate)}
    if op == "voices":
        return {"voices": sorted(tts.get_voices())}
    raise ValueError(f"unknown op {op!r}")


def main() -> None:
    models_dir, device, threads = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    if len(sys.argv) > 4 and sys.argv[4]:
        os.sched_setaffinity(0, _cpus(sys.argv[4]))
    # Libraries print to stdout; the protocol owns it. Keep a private copy of
    # the real stdout and point fd 1 at stderr for everything else.
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)

    def send(msg: dict) -> None:
        out.write(json.dumps(msg) + "\n")
        out.flush()

    try:
        stt = models.load_stt(models_dir, device, threads)
        tts = models.load_tts(models_dir, device, threads)
        if device == "cuda" and not models.on_card(tts):
            raise RuntimeError("CUDA is not usable here; see the onnxruntime errors above")
    except Exception as e:  # noqa: BLE001
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return
    if device == "cpu" and sys.platform == "linux":
        _keep_in_memory()
    send({"ready": True, "device": device})
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        try:
            send({"id": req.get("id"), "ok": True, **_handle(req, stt, tts)})
        except Exception as e:  # noqa: BLE001
            send({"id": req.get("id"), "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
