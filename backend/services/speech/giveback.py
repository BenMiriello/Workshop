"""Giving the speech processor worker's memory back when the machine needs it.

The worker holds both speech models (well over a gigabyte) for as long as it
lives, and `engine` keeps it alive once used. An installation that shares its
machine's memory with a heavy tenant calls `yield_now` while that tenant needs
the room, and `resume` after; for as long as the hold stands:

- an idle worker is stopped at once;
- a worker in use finishes the piece it is on and is stopped once it has been
  idle for `IDLE_S`, so a long job's consecutive pieces share one load;
- a call that finds no worker starts one cold, so dictation and read-aloud
  still work, and the worker leaves again after the last of them.

After the hold, speech is as it was: the next use starts a worker and it stays.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from services import proc_memory
from services.speech.workers import Worker

IDLE_S = 20.0
POLL_S = 1.0


class GiveBack:
    def __init__(self, worker: Worker, idle_s: float = IDLE_S, poll_s: float = POLL_S):
        self.worker = worker
        self.idle_s = idle_s
        self.poll_s = poll_s
        self.yielded = False
        self._using = 0
        self._lock = threading.Lock()
        self._watcher: threading.Thread | None = None

    @contextmanager
    def using(self):
        """Wrap every use of the worker, from asking for it to its reply, so it
        is never stopped under a piece that is on its way to it."""
        with self._lock:
            self._using += 1
        try:
            yield
        finally:
            with self._lock:
                self._using -= 1

    def memory(self) -> tuple[int, int]:
        """`(resident, swapped)` MiB of the worker, zero when none is running."""
        pid = self.worker.pid
        return (proc_memory.rollup_mib(pid) if pid else None) or (0, 0)

    def in_use(self) -> bool:
        with self._lock:
            return self._using > 0

    def _stop_if_idle(self) -> bool:
        with self._lock:
            if self._using:
                return False
            if self.worker.alive:
                self.worker.stop()
            return True

    def yield_now(self) -> dict:
        """Stop the worker if nothing is using it; either way stay yielded."""
        self.yielded = True
        before = sum(self.memory())
        stopped = self._stop_if_idle()
        after = sum(self.memory())
        self._watch()
        busy = not stopped
        return {"given_mib": max(0, before - after), "kept_mib": after, "busy": busy,
                "retry_after_s": self.idle_s if busy else None,
                "detail": ("speech worker in use, stops when idle" if busy
                           else "speech worker stopped" if before else "no speech worker running")}

    def resume(self) -> None:
        self.yielded = False

    def _watch(self) -> None:
        with self._lock:
            if self._watcher is not None and self._watcher.is_alive():
                return
            self._watcher = threading.Thread(
                target=self._watch_loop, daemon=True, name="speech-giveback")
            self._watcher.start()

    def _watch_loop(self) -> None:
        while self.yielded:
            time.sleep(self.poll_s)
            idle = time.time() - self.worker.last_used > self.idle_s
            if self.worker.alive and idle:
                self._stop_if_idle()
