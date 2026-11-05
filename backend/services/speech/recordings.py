"""The audio of each live dictation, kept for an hour so an app subscribed to
`dictated` can copy it (docs/dictation-and-clipboard.md, "Events for apps").

Kept under the id the client named the dictation with, an unguessable token,
as raw 16 kHz mono int16 samples in stream order, so a reconnect that resends
audio already kept adds only what is new. Served as WAV, on loopback only
(routes/speech_dictations.py). Older recordings are deleted whenever a new
one starts or one is read.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from services.speech.audio import to_wav
from services.voice.timeline import SAMPLE_RATE
from wsplatform import paths

KEEP_S = 3600
LONGEST_S = 3600
# A gap in the stream up to this long is kept as silence; past it, skipped.
FILL_GAP_S = 60
ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def root() -> Path:
    return paths.runtime_dir() / "speech" / "recordings"


def valid(dictation: str) -> bool:
    return bool(ID.match(dictation or ""))


def sweep(now: float | None = None) -> None:
    cutoff = (now or time.time()) - KEEP_S
    try:
        for p in root().glob("*.pcm"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def _file(dictation: str) -> Path | None:
    if not valid(dictation):
        return None
    sweep()
    p = root() / f"{dictation}.pcm"
    return p if p.is_file() else None


def length_ms(dictation: str) -> int | None:
    p = _file(dictation)
    return None if p is None else int(p.stat().st_size // 2 * 1000 / SAMPLE_RATE)


def wav(dictation: str) -> bytes | None:
    p = _file(dictation)
    if p is None:
        return None
    pcm = np.frombuffer(p.read_bytes()[: p.stat().st_size // 2 * 2], dtype="<i2")
    return to_wav(pcm, SAMPLE_RATE)


class Recorder:
    """Appends a dictation's microphone frames to its recording."""

    def __init__(self, dictation: str) -> None:
        sweep()
        p = root() / f"{dictation}.pcm"
        p.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(p, "ab")
        self._end = p.stat().st_size // 2

    def write(self, offset: int, pcm: np.ndarray) -> None:
        if offset + pcm.shape[0] <= self._end or self._end >= LONGEST_S * SAMPLE_RATE:
            return
        if offset > self._end:
            gap = offset - self._end
            if gap <= FILL_GAP_S * SAMPLE_RATE:
                self._f.write(np.zeros(gap, dtype="<i2").tobytes())
            self._end = offset
        else:
            pcm = pcm[self._end - offset:]
        self._f.write(pcm.astype("<i2", copy=False).tobytes())
        self._f.flush()
        self._end += pcm.shape[0]

    def close(self) -> None:
        self._f.close()
