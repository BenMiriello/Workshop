"""The speech worker wrapper against a real child process: load, call, a call
after a stop (the GPU hand-back race), and a child that dies while loading."""
from __future__ import annotations

import sys
import time

import pytest

from services.speech.workers import Worker, WorkerError

ECHO = """
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    print(json.dumps({"id": req["id"], "ok": True, "echo": req["op"]}), flush=True)
"""

DIES = "import sys; sys.exit(3)"


def worker(tmp_path, code: str) -> Worker:
    w = Worker(sys.executable, "cpu", tmp_path, 1, "", 0, tmp_path / "w.log")
    w._argv = [sys.executable, "-c", code]
    return w


def test_call_round_trip(tmp_path):
    w = worker(tmp_path, ECHO)
    w.start()
    assert w.wait_ready(10)
    assert w.call("transcribe", timeout=10)["echo"] == "transcribe"
    w.stop()
    assert not w.alive


def test_a_call_after_stop_is_a_worker_error(tmp_path):
    w = worker(tmp_path, ECHO)
    w.start()
    assert w.wait_ready(10)
    w.stop()
    with pytest.raises(WorkerError):
        w.call("transcribe", timeout=5)


def test_a_pipe_closed_under_a_call_is_a_worker_error(tmp_path):
    w = worker(tmp_path, ECHO)
    w.start()
    assert w.wait_ready(10)
    w._proc.stdin.close()
    with pytest.raises(WorkerError):
        w.call("transcribe", timeout=5)
    w.stop()


def test_a_child_that_dies_while_loading_says_so(tmp_path):
    w = worker(tmp_path, DIES)
    w.start()
    assert not w.wait_ready(10)
    end = time.time() + 5
    while w.error is None and time.time() < end:
        time.sleep(0.02)
    with pytest.raises(WorkerError, match="exited while loading"):
        w.call("transcribe", timeout=5)
