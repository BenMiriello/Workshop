"""The speech processor worker leaves while the machine's RAM room is held,
comes back cold for the work that cannot wait, and is normal afterwards."""
import threading
import time

import pytest

from services import proc_memory
from services.speech import engine
from services.speech.giveback import GiveBack


class FakeWorker:
    def __init__(self):
        self.alive = self.ready = False
        self.pid = None
        self.busy = False
        self.error = None
        self.last_used = time.time()
        self.starts = self.stops = 0

    def start(self):
        if not self.alive:
            self.alive = self.ready = True
            self.pid = 900
            self.starts += 1

    def wait_ready(self, timeout):
        return self.ready

    def call(self, op, **payload):
        self.last_used = time.time()
        return {"text": "heard"}

    def stop(self):
        self.alive = self.ready = False
        self.pid = None
        self.stops += 1


def until(condition, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def memory(monkeypatch):
    monkeypatch.setattr(proc_memory, "rollup_mib",
                        lambda pid, proc="/proc": (1200, 300) if pid == 900 else None)


@pytest.fixture
def local():
    p = engine.Local.__new__(engine.Local)
    p.mode, p.gpu_index, p.gpu = "cpu", 0, None
    p.cpu = FakeWorker()
    p._lock = threading.Lock()
    p._draining, p._watcher = False, None
    p.__dict__["giveback"] = GiveBack(p.cpu, idle_s=0.05, poll_s=0.01)
    yield p
    p.giveback.resume()


def test_an_idle_worker_is_stopped_and_what_it_gave_is_measured(local):
    local.cpu.start()
    out = local.giveback.yield_now()
    assert not local.cpu.alive
    assert out["given_mib"] == 1500 and out["kept_mib"] == 0 and not out["busy"]
    assert out["retry_after_s"] is None


def test_no_worker_running_gives_nothing_and_is_not_busy(local):
    out = local.giveback.yield_now()
    assert out == {"given_mib": 0, "kept_mib": 0, "busy": False, "retry_after_s": None,
                   "detail": "no speech worker running"}


def test_a_worker_in_use_answers_busy_and_leaves_when_idle(local):
    local.cpu.start()
    with local.giveback.using():
        out = local.giveback.yield_now()
        assert out["busy"] and out["given_mib"] == 0 and out["kept_mib"] == 1500
        assert out["retry_after_s"] == 0.05 and local.cpu.alive
    local.cpu.last_used = time.time()
    assert until(lambda: not local.cpu.alive)


def test_work_while_yielded_starts_a_cold_worker_that_then_leaves(local):
    local.giveback.yield_now()
    assert local.run("transcribe") == {"text": "heard"}
    assert local.cpu.starts == 1
    assert until(lambda: not local.cpu.alive)


def test_consecutive_pieces_share_one_load(local):
    local.giveback.idle_s = 0.3
    local.giveback.yield_now()
    local.run("transcribe")
    local.run("transcribe")
    local.run("transcribe")
    assert local.cpu.starts == 1


def test_a_worker_is_never_stopped_under_a_piece_on_its_way_to_it(local):
    local.cpu.start()
    local.cpu.last_used -= 100
    with local.giveback.using():
        local.giveback.yield_now()
        time.sleep(0.1)
        assert local.cpu.alive


def test_after_the_hold_speech_is_normal(local):
    local.giveback.yield_now()
    local.giveback.resume()
    local.run("transcribe")
    time.sleep(0.2)
    assert local.cpu.alive and local.cpu.stops == 0


def test_warming_is_skipped_while_yielded(local):
    local.giveback.yield_now()
    local.warm()
    assert not local.cpu.alive
    local.giveback.resume()
    local.warm()
    assert local.cpu.alive
