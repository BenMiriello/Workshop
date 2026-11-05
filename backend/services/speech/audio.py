"""Audio plumbing: decode, resample, wrap as WAV, cut into pieces, split text.

PCM throughout is mono signed 16-bit as a numpy int16 array.
"""
from __future__ import annotations

import io
import re
import subprocess
import wave

import numpy as np

STT_RATE = 16000
PIECE_S = 30.0
LOOK_S = 4.0
FRAME_S = 0.1


class AudioError(ValueError):
    pass


def resample(pcm: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or pcm.size == 0:
        return pcm
    n = int(round(pcm.size * dst / src))
    x = np.linspace(0, pcm.size - 1, n)
    return np.interp(x, np.arange(pcm.size), pcm.astype(np.float32)).astype(np.int16)


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise AudioError("only 16-bit WAV is read directly")
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return pcm, rate


def decode(data: bytes, rate: int = STT_RATE) -> np.ndarray:
    """Any audio the browser can record, as PCM at `rate`."""
    if data[:4] == b"RIFF":
        try:
            pcm, src = read_wav(data)
            return resample(pcm, src, rate)
        except (AudioError, wave.Error, EOFError):
            pass
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1",
             "-ar", str(rate), "pipe:1"],
            input=data, capture_output=True, timeout=120, check=True)
    except FileNotFoundError as e:
        raise AudioError("send 16-bit WAV, or install ffmpeg to decode other formats") from e
    except subprocess.SubprocessError as e:  # failed, or ran past the timeout
        said = e.stderr.decode(errors="replace")[:200] if isinstance(e.stderr, bytes) else ""
        raise AudioError(f"could not decode audio: {said or 'it took too long'}") from e
    return np.frombuffer(out.stdout, dtype="<i2")


def to_wav(pcm: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.astype("<i2").tobytes())
    return buf.getvalue()


def pieces(pcm: np.ndarray, rate: int = STT_RATE, max_s: float = PIECE_S) -> list[np.ndarray]:
    """Cut long audio into pieces no longer than `max_s`, each cut made at the
    quietest moment of the last few seconds before the limit, so no word is
    split. Each piece is one unit of work a worker can be switched between."""
    limit = int(max_s * rate)
    frame = int(FRAME_S * rate)
    look = int(LOOK_S * rate)
    out: list[np.ndarray] = []
    start = 0
    while pcm.size - start > limit:
        lo, hi = start + limit - look, start + limit
        window = pcm[lo:hi].astype(np.float32)
        frames = window[: window.size // frame * frame].reshape(-1, frame)
        quiet = int(np.argmin((frames ** 2).mean(axis=1)))
        cut = lo + quiet * frame + frame // 2
        out.append(pcm[start:cut])
        start = cut
    out.append(pcm[start:])
    return [p for p in out if p.size]


_SENT_RE = re.compile(r"[^.!?…\n]+[.!?…]*", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    """Sentences for speaking one at a time; very short fragments are merged
    into the one before so nothing is said as a lone word."""
    parts = [m.group(0).strip() for m in _SENT_RE.finditer(text or "")]
    out: list[str] = []
    for p in (p for p in parts if p):
        if out and len(p) < 24:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out
