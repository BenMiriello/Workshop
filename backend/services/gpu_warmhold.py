"""Session-scoped GPU warm-hold.

The operator's model: a chat/voice session should be slow to load only *once*,
at the start. We achieve that by acquiring a GPU-broker lease on the first turn
and re-arming it with a short sliding TTL on every subsequent turn, so the GPU
stays held until ~30s after the last turn and then lapses on its own (the broker
expires the TTL — there is no release timer to babysit).

Everything here is non-blocking and tolerates the broker being down: if a lease
can't be had, callers simply run on CPU. This is a personal single-user box, so
there is no queueing or fairness machinery — just "hold the card while I'm
talking, let it go when I stop."

Used by the voice routes today; the text chat route can call `touch()` on the
same key to keep the GPU warm across a text session too.
"""
from __future__ import annotations

import logging
import os
import time

from . import gpubroker_client as broker

log = logging.getLogger("workshop.gpu_warmhold")

# Sliding TTL: held this long past the last turn, then the broker drops it.
HOLD_TTL_S = int(os.environ.get("VOICE_GPU_HOLD_TTL_S", "45"))
# Voice models are small; ask for a modest slice so ollama/autonomy can coexist.
VOICE_VRAM_MIB = int(os.environ.get("VOICE_GPU_VRAM_MIB", "2500"))
# Re-arm at most this often (a burst of turns shouldn't hammer the broker).
_REARM_MIN_S = 5.0


class GpuWarmHold:
    def __init__(self, vram_mib: int = VOICE_VRAM_MIB, ttl_s: int = HOLD_TTL_S):
        self.vram_mib = vram_mib
        self.ttl_s = ttl_s
        self._lease_id: str | None = None
        self._last_touch = 0.0

    @property
    def held(self) -> bool:
        return self._lease_id is not None

    async def touch(self, reason: str = "voice session") -> bool:
        """Ensure the GPU lease is armed for another `ttl_s`. Non-blocking:
        returns True if we (believe we) hold the GPU, False to run on CPU."""
        if os.environ.get("VOICE_DEVICE", "cpu").strip().lower() == "cpu":
            return False  # GPU path disabled; nothing to hold.
        now = time.monotonic()
        if self._lease_id and (now - self._last_touch) < _REARM_MIN_S:
            return True
        try:
            res = await broker.acquire(
                vram_budget_mib=self.vram_mib,
                priority=broker.DEFAULT_PRIORITY,
                reason=reason,
                ttl_s=self.ttl_s,
                blocking=False,
            )
        except broker.BrokerUnavailable:
            return self._lease_id is not None  # broker down: keep whatever we had
        self._last_touch = now
        lease_id = (res or {}).get("lease_id") or (res or {}).get("id")
        granted = bool((res or {}).get("granted", lease_id is not None))
        if granted and lease_id:
            self._lease_id = lease_id
            return True
        # Not granted (GPU busy with something higher priority) → CPU this turn.
        return False

    async def release(self) -> None:
        if self._lease_id:
            try:
                await broker.release(self._lease_id)
            except Exception:
                pass
            self._lease_id = None


# One process-wide hold for the voice models (they are shared singletons).
VOICE_HOLD = GpuWarmHold()
