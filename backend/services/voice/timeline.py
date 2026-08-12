"""The session's microphone audio as ONE continuous, sample-indexed stream.

This replaces cutting audio into files by stopping the recorder, which is the
choice that produced most of voice mode's bugs (see
docs/voice-mode/architecture-2026-08-12.md section 2.1). Under the old design a
"segment" was a MediaRecorder container, so:

  * closing one and opening the next left a real deaf gap at every pause,
  * un-hearing something meant throwing a buffer away,
  * the live caption re-decoded the whole utterance every tick, and
  * nothing could answer "what was said between these two instants", so every
    decision had to be taken against a mutable flag set by a timer.

Here a segment is a pair of sample offsets into a ring buffer. Cutting is
arithmetic, so it is lossless by construction and there is no device operation
to fail. `read(a, b)` is the one question everything downstream asks.

Offsets are ABSOLUTE and monotonic from the first sample of the session. They
never wrap even though storage does, so a range that has aged out of the buffer
is reported as such rather than silently returning the wrong audio.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Everything upstream of TTS runs at 16 kHz mono: Silero VAD, smart-turn and
# every Whisper family model take 16k, so resampling once at the edge (in the
# capture worklet) means no resampling anywhere else.
SAMPLE_RATE = 16000
FRAME_SAMPLES = 320          # 20 ms, the wire frame size
BUFFER_SECONDS = 180


class RangeExpired(Exception):
    """The requested range has been overwritten. Never guess: a caller that asks
    for audio the buffer no longer holds must be told, not handed neighbouring
    samples that happen to sit at the same physical index."""


@dataclass(frozen=True)
class Span:
    """A half-open range of absolute sample offsets, [start, end)."""

    start: int
    end: int

    @property
    def samples(self) -> int:
        return max(0, self.end - self.start)

    @property
    def ms(self) -> int:
        return int(self.samples * 1000 / SAMPLE_RATE)

    def clamp(self, lo: int, hi: int) -> "Span":
        return Span(max(self.start, lo), min(self.end, hi))


class AudioTimeline:
    """A fixed-capacity ring of int16 mono samples, addressed absolutely.

    Not thread-safe by itself. The runtime owns one per session and touches it
    only from the session's own asyncio task, so no lock is needed; if that ever
    changes the invariant to preserve is that `write` and `read` do not
    interleave.
    """

    def __init__(self, seconds: int = BUFFER_SECONDS) -> None:
        self._cap = seconds * SAMPLE_RATE
        self._buf = np.zeros(self._cap, dtype=np.int16)
        # Absolute offset one past the last sample written.
        self._end = 0

    # ---- writing -----------------------------------------------------------
    @property
    def end(self) -> int:
        """Absolute offset one past the newest sample."""
        return self._end

    @property
    def start(self) -> int:
        """Absolute offset of the oldest sample still held."""
        return max(0, self._end - self._cap)

    def append(self, pcm: np.ndarray) -> Span:
        """Append samples, returning the span they occupy."""
        if pcm.dtype != np.int16:
            pcm = pcm.astype(np.int16)
        n = int(pcm.shape[0])
        if n == 0:
            return Span(self._end, self._end)
        if n >= self._cap:
            # A single write larger than the whole ring: keep only the tail.
            pcm = pcm[-self._cap:]
            n = self._cap
        first = self._end
        pos = first % self._cap
        tail = min(n, self._cap - pos)
        self._buf[pos:pos + tail] = pcm[:tail]
        if tail < n:
            self._buf[:n - tail] = pcm[tail:]
        self._end = first + n
        return Span(first, self._end)

    def write_at(self, offset: int, pcm: np.ndarray) -> None:
        """Place a frame by its own offset rather than appending.

        A reordered or duplicated frame from the network lands where it belongs
        instead of corrupting the stream. A frame older than the buffer, or one
        that would leave a hole larger than the buffer, is dropped.
        """
        n = int(pcm.shape[0])
        if n == 0:
            return
        if offset == self._end:
            self.append(pcm)
            return
        if offset > self._end:
            # A gap: the frames between are genuinely lost, so zero them rather
            # than letting stale ring contents read as audio.
            gap = offset - self._end
            if gap >= self._cap:
                self._end = offset
                self.append(pcm)
                return
            self.append(np.zeros(gap, dtype=np.int16))
            self.append(pcm)
            return
        # offset < end: a late or duplicate frame. Only rewrite what is still held.
        if offset + n <= self.start:
            return
        for i in range(n):
            at = offset + i
            if at < self.start or at >= self._end:
                continue
            self._buf[at % self._cap] = pcm[i]

    # ---- reading -----------------------------------------------------------
    def read(self, span: Span) -> np.ndarray:
        """Samples for `span`, as int16. Raises RangeExpired if it has aged out."""
        lo, hi = span.start, min(span.end, self._end)
        if hi <= lo:
            return np.zeros(0, dtype=np.int16)
        if lo < self.start:
            raise RangeExpired(f"{lo} is older than {self.start}")
        n = hi - lo
        pos = lo % self._cap
        tail = min(n, self._cap - pos)
        if tail == n:
            return self._buf[pos:pos + n].copy()
        return np.concatenate([self._buf[pos:pos + tail], self._buf[:n - tail]])

    def read_float(self, span: Span) -> np.ndarray:
        """Samples for `span` as float32 in [-1, 1] — what the ONNX models take."""
        return self.read(span).astype(np.float32) / 32768.0

    def tail(self, ms: int) -> Span:
        """The most recent `ms` of audio, clamped to what is held."""
        want = int(ms * SAMPLE_RATE / 1000)
        return Span(max(self.start, self._end - want), self._end)


def to_wav(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """int16 mono -> WAV bytes. The ASR tier decodes containers, not raw PCM."""
    import io
    import wave

    if pcm.dtype != np.int16:
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
