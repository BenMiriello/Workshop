"""Speech routing: borrow the card while no job claims it, finish on the
processor when one does, and never lose a piece in the hand-off."""
import time

import numpy as np
import pytest

from services import gpu_claims
from services.speech import audio, engine
from services.speech.card_turns import CardTurns
from services.speech.workers import WorkerError


class FakeWorker:
    def __init__(self, device, fail=False):
        self.device, self.fail = device, fail
        self.alive = self.ready = False
        self.busy = False
        self.calls = []
        self.last_used = time.time()
        self.error = None

    def start(self):
        self.alive = self.ready = True

    def wait_ready(self, timeout):
        return self.ready

    def call(self, op, **payload):
        if self.fail or not self.ready:
            raise WorkerError("gone")
        self.calls.append(op)
        return {"text": self.device}

    def stop(self):
        self.alive = self.ready = False


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_claims, "store_path", lambda: tmp_path / "claims.json")
    monkeypatch.setattr(engine, "WATCH_S", 0.01)
    p = engine.Local.__new__(engine.Local)
    p.mode, p.gpu_index = "auto", 0
    p.cpu, p.gpu = FakeWorker("cpu"), FakeWorker("cuda")
    p._lock = __import__("threading").Lock()
    p._draining, p._watcher = False, None
    p.turns = CardTurns(settle_s=0)
    yield p
    if p.gpu:
        p.gpu.stop()


def _settle(p):
    if p._watcher:
        p._watcher.join(1)


def test_free_card_is_borrowed_under_a_guest_claim(local):
    assert local.run("transcribe") == {"text": "cuda"}
    assert gpu_claims.snapshot()[0]["kind"] == "guest"


def test_job_claim_moves_work_to_cpu_and_frees_card(local):
    local.run("transcribe")
    gpu_claims.claim("renders", "job")
    _settle(local)
    assert not local.gpu.alive
    assert local.run("transcribe") == {"text": "cpu"}
    assert [c["name"] for c in gpu_claims.snapshot()] == ["renders"]


def test_card_is_taken_only_after_a_minute_free_and_not_again_within_one(local):
    local.turns = CardTurns()
    now = [1000.0]
    real_seen, real_may = local.turns.seen, local.turns.may_start
    local.turns.seen = lambda free: real_seen(free, now[0])
    local.turns.may_start = lambda: real_may(now[0])
    local.turns.started = lambda real=local.turns.started: real(now[0])
    assert local.run("transcribe") == {"text": "cpu"}
    assert gpu_claims.snapshot() == []
    for _ in range(29):
        now[0] += 2.0
        assert local.run("transcribe") == {"text": "cpu"}
    now[0] += 2.0
    assert local.run("transcribe") == {"text": "cuda"}
    gpu_claims.claim("renders", "job", ttl_s=1)
    _settle(local)
    assert not local.gpu.alive
    time.sleep(1.1)                       # the job's claim lapses: the card is free
    for _ in range(20):
        now[0] += 2.0
        assert local.run("transcribe") == {"text": "cpu"}
    now[0] += 30.0                        # an unseen half minute counts for nothing
    assert local.run("transcribe") == {"text": "cpu"}


def test_card_turns_count_a_minute_seen_free_and_rest_after_a_start():
    t = CardTurns(settle_s=60)
    t.seen(True, 0)
    assert not t.may_start(30)
    for at in range(4, 64, 4):
        t.seen(True, at)
    assert t.may_start(60)
    t.started(60)
    for at in range(64, 120, 4):           # left idle, the card still free
        t.seen(True, at)
    assert not t.may_start(116)
    t.seen(False, 121)                     # a job took it
    for at in range(124, 190, 4):
        t.seen(True, at)
    assert not t.may_start(180)
    assert t.may_start(184)


def test_card_failure_mid_piece_is_redone_on_cpu(local):
    local.gpu.fail = True
    assert local.run("transcribe") == {"text": "cpu"}


def test_cpu_mode_never_touches_the_card(local):
    local.mode, local.gpu = "cpu", None
    assert local.run("transcribe") == {"text": "cpu"}
    assert gpu_claims.snapshot() == []


def test_pieces_cut_long_audio_at_quiet_points():
    rate = audio.STT_RATE
    loud = (np.sin(np.arange(rate * 70) / 3) * 20000).astype(np.int16)
    loud[int(rate * 28.0):int(rate * 28.3)] = 0
    parts = audio.pieces(loud, rate)
    assert sum(p.size for p in parts) == loud.size
    assert all(p.size <= 30 * rate for p in parts)
    assert 27.9 * rate < parts[0].size < 28.4 * rate


def test_wav_round_trip_and_resample():
    pcm = (np.arange(16000) % 200).astype(np.int16)
    back, rate = audio.read_wav(audio.to_wav(pcm, 16000))
    assert rate == 16000 and np.array_equal(back, pcm)
    assert audio.resample(pcm, 16000, 24000).size == 24000


def test_split_sentences_merges_fragments():
    assert audio.split_sentences("Yes. That works well for everyone here. Ok.") == [
        "Yes.", "That works well for everyone here. Ok."]
