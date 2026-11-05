"""The speech models themselves: fetch, load, run.

Self-contained on purpose. `worker.py` imports this from a separate venv (the
GPU one) where none of the backend's packages exist, so nothing here may import
from the rest of the backend.

  Parakeet TDT 0.6B v3  speech to text, 25 European languages, via onnx-asr.
                        int8 on the processor; full precision on the card,
                        where int8 kernels are slower than float.
  Kokoro-82M            text to speech, 24 kHz, via kokoro-onnx.
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

STT_MODEL = "nemo-parakeet-tdt-0.6b-v3"
STT_DIR = "parakeet-tdt-0.6b-v3"
STT_RATE = 16000

TTS_DIR = "kokoro"
TTS_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
TTS_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
DEFAULT_VOICE = "af_heart"


def quantization_for(device: str) -> str | None:
    return None if device == "cuda" else "int8"


def stt_dir(models: Path, device: str) -> Path:
    """Each precision in its own directory: onnx-asr fetches only into a
    directory that does not exist yet."""
    return models / (STT_DIR if device != "cuda" else f"{STT_DIR}-fp32")


def _providers(device: str) -> list[str]:
    if device == "cuda":
        import onnxruntime as ort

        # CUDA and cuDNN come from pip wheels, not the system; onnxruntime does
        # not find them on its own and would quietly run on the processor.
        ort.preload_dlls()
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def on_card(tts) -> bool:
    """Whether a loaded model actually runs on the card."""
    return "CUDAExecutionProvider" in tts.sess.get_providers()


def _session_options(threads: int):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    if threads > 0:
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
    return opts


def fetch_tts(models: Path) -> None:
    dest = models / TTS_DIR
    dest.mkdir(parents=True, exist_ok=True)
    for name in TTS_FILES:
        path = dest / name
        if path.exists() and path.stat().st_size > 0:
            continue
        part = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(TTS_URL + name, part)
        part.rename(path)


def load_stt(models: Path, device: str, threads: int):
    import onnx_asr

    return onnx_asr.load_model(
        STT_MODEL,
        stt_dir(models, device),
        quantization=quantization_for(device),
        sess_options=_session_options(threads),
        providers=_providers(device),
    )


def load_tts(models: Path, device: str, threads: int):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    fetch_tts(models)
    session = ort.InferenceSession(
        str(models / TTS_DIR / TTS_FILES[0]),
        sess_options=_session_options(threads),
        providers=_providers(device),
    )
    return Kokoro.from_session(session, str(models / TTS_DIR / TTS_FILES[1]))


def transcribe(stt, samples) -> str:
    """`samples` is float32 mono at STT_RATE."""
    return (stt.recognize(samples, sample_rate=STT_RATE) or "").strip()


def transcribe_timed(stt, samples) -> dict:
    """The text, its pieces, and when each piece was heard (seconds)."""
    r = stt.with_timestamps().recognize(samples, sample_rate=STT_RATE)
    return {"text": (r.text or "").strip(), "tokens": list(r.tokens or []),
            "stamps": [float(t) for t in (r.timestamps or [])]}


def speak(tts, text: str, voice: str, speed: float):
    """Float32 mono samples and their rate."""
    return tts.create(text, voice=voice or DEFAULT_VOICE, speed=float(speed), lang="en-us")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch the speech models.")
    ap.add_argument("--fetch", type=Path, required=True, help="models directory")
    ap.add_argument("--quantization", choices=("int8", "none"), default="int8")
    args = ap.parse_args()
    device = "cpu" if args.quantization == "int8" else "cuda"
    import onnx_asr

    onnx_asr.load_model(STT_MODEL, stt_dir(args.fetch, device),
                        quantization=quantization_for(device),
                        providers=["CPUExecutionProvider"])
    fetch_tts(args.fetch)
    print(f"speech models ready in {args.fetch}")


if __name__ == "__main__":
    main()
