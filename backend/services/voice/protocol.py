"""The wire format between the voice client and the runtime.

ONE bidirectional socket carries everything: binary frames for audio, JSON text
frames for control and state. Keeping both on one connection is what makes the
ordering questions go away, and it is why reconnect can be a single resume
rather than three independent reconnections that can each half-succeed.

Audio is raw little-endian int16 mono PCM with an 8-byte header. No container,
no codec negotiation, and no Safari special case: the reason the old design
needed `pickRecorderMime`, `extForMime` and a fragmented-mp4 path is that it was
shipping MediaRecorder containers, which is also what forced audio to be cut by
stopping the device.

    header  u8  kind        1 = mic (client -> server), 2 = tts (server -> client)
            u8  flags       reserved, 0
            u16 reserved    0
            u32 cursor      mic: absolute sample offset. tts: clause token
    body    int16le samples

The mic cursor is what makes the stream resumable and what lets the server place
a reordered frame where it belongs rather than appending it in arrival order.
"""
from __future__ import annotations

import struct

import numpy as np

HEADER = struct.Struct("<BBHI")
HEADER_BYTES = HEADER.size          # 8

KIND_MIC = 1
KIND_TTS = 2


def encode_audio(kind: int, cursor: int, pcm: np.ndarray) -> bytes:
    if pcm.dtype != np.int16:
        pcm = pcm.astype(np.int16)
    return HEADER.pack(kind, 0, 0, cursor & 0xFFFFFFFF) + pcm.tobytes()


def decode_audio(data: bytes) -> tuple[int, int, np.ndarray]:
    """(kind, cursor, samples). Raises ValueError on a frame that is not one."""
    if len(data) < HEADER_BYTES:
        raise ValueError("short frame")
    kind, _flags, _res, cursor = HEADER.unpack_from(data, 0)
    body = data[HEADER_BYTES:]
    if len(body) % 2:
        raise ValueError("odd pcm length")
    return kind, cursor, np.frombuffer(body, dtype="<i2")


class Cursor:
    """A 32-bit wire cursor unwrapped into the absolute offset the timeline uses.

    A session that ran long enough would wrap `u32` at about 74 hours of audio,
    which is not a real risk, but a wrapped cursor would place a frame 2^32
    samples in the past and silently corrupt the stream. Unwrapping costs
    nothing and removes the failure mode.
    """

    def __init__(self) -> None:
        self._high = 0
        self._last = 0

    def absolute(self, cursor: int) -> int:
        if cursor < self._last and (self._last - cursor) > (1 << 31):
            self._high += 1 << 32
        self._last = cursor
        return self._high + cursor
