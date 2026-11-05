"""A speech worker process seen from the backend: start, call, stop.

One request is in flight per worker at a time; `call` blocks the calling
thread (routes reach it through `asyncio.to_thread`). Each worker's stderr goes
to its own log under the runtime dir, since a load failure is only ever
explained there.
"""
from __future__ import annotations

import base64
import itertools
import json
import logging
import subprocess
import threading
import time
from pathlib import Path

from services.speech import lag

log = logging.getLogger("workshop.speech.workers")

BACKEND_DIR = Path(__file__).resolve().parents[2]


class WorkerError(RuntimeError):
    pass


class Worker:
    def __init__(self, python: str, device: str, models: Path, threads: int,
                 cpus: str, nice: int, log_path: Path) -> None:
        self.device = device
        self._argv = ["nice", "-n", str(nice), python, "-m", "services.speech.worker",
                      str(models), device, str(threads), cpus]
        self._log_path = log_path
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._call_lock = threading.Lock()
        self._reply: dict | None = None
        self._replied = threading.Event()
        self._ids = itertools.count(1)
        self.error: str | None = None
        self.last_used = time.time()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self.alive and self._proc is not None else None

    @property
    def ready(self) -> bool:
        return self.alive and self._ready.is_set()

    @property
    def busy(self) -> bool:
        return self._call_lock.locked()

    def start(self) -> None:
        if self.alive:
            return
        self._ready.clear()
        self.error = None
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self._log_path, "ab", buffering=0)
        logf.write(f"\n--- start {self.device} @ {time.ctime()} ---\n".encode())
        self._proc = subprocess.Popen(
            self._argv, cwd=str(BACKEND_DIR), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=logf, text=True, bufsize=1)
        logf.close()
        threading.Thread(target=self._read, args=(self._proc,), daemon=True,
                         name=f"speech-{self.device}").start()

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        loaded = False
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ready" in msg:
                loaded = bool(msg["ready"])
                if loaded:
                    self._ready.set()
                else:
                    self.error = msg.get("error") or "failed to load"
                continue
            self._reply = msg
            self._replied.set()
        if self.error is None and not loaded and proc is self._proc:
            self.error = f"exited while loading (see {self._log_path})"
        self._replied.set()

    def wait_ready(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self._ready.wait(0.25):
                return self.alive
            if not self.alive:
                return False
        return False

    def call(self, op: str, timeout: float = 120.0, **payload) -> dict:
        asked = time.monotonic()
        with self._call_lock:
            lag.note(self.device, (time.monotonic() - asked) * 1000)
            # `stop` may run on another thread at any moment (the GPU drain),
            # so hold one handle and treat a pipe closed under us as gone.
            proc = self._proc
            if not self.ready or proc is None or proc.stdin is None:
                raise WorkerError(self.error or f"{self.device} worker not ready")
            req_id = next(self._ids)
            self._reply = None
            self._replied.clear()
            try:
                proc.stdin.write(json.dumps({"id": req_id, "op": op, **payload}) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise WorkerError(f"{self.device} worker gone: {e}") from e
            if not self._replied.wait(timeout):
                self.stop()
                raise WorkerError(f"{self.device} worker timed out on {op}")
            reply = self._reply
            self.last_used = time.time()
        if reply is None or reply.get("id") != req_id:
            raise WorkerError(f"{self.device} worker stopped during {op}")
        if not reply.get("ok"):
            raise WorkerError(reply.get("error") or f"{op} failed")
        return reply

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._ready.clear()
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()


def encode_pcm(pcm) -> str:
    return base64.b64encode(pcm.astype("<i2").tobytes()).decode()


def decode_pcm(data: str):
    import numpy as np

    return np.frombuffer(base64.b64decode(data), dtype="<i2")
