"""A guest gets the card only while no job wants it, and is told to let go."""
import time

import pytest

from services import gpu_claims


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_claims, "store_path", lambda: tmp_path / "claims.json")


def test_guest_granted_when_no_job():
    r = gpu_claims.claim("speech", "guest")
    assert r["granted"] and not r["yield"]


def test_job_refuses_guest_and_lists_itself():
    gpu_claims.claim("renders", "job")
    r = gpu_claims.claim("speech", "guest")
    assert not r["granted"] and r["yield"] and r["jobs"] == ["renders"]
    assert [c["name"] for c in gpu_claims.snapshot()] == ["renders"]


def test_job_arriving_takes_card_from_live_guest():
    assert gpu_claims.claim("speech", "guest")["granted"]
    job = gpu_claims.claim("renders", "job")
    assert job["granted"] and job["guests"] == ["speech"]
    assert not gpu_claims.claim("speech", "guest")["granted"]


def test_jobs_on_another_device_do_not_matter():
    gpu_claims.claim("renders", "job", device=1)
    assert gpu_claims.claim("speech", "guest", device=0)["granted"]


def test_expired_job_frees_the_card(monkeypatch):
    gpu_claims.claim("renders", "job", ttl_s=1)
    later = time.time() + 5
    monkeypatch.setattr(gpu_claims.time, "time", lambda: later)
    assert gpu_claims.claim("speech", "guest")["granted"]


def test_release_and_bad_kind():
    gpu_claims.claim("renders", "job")
    assert gpu_claims.release("renders")
    assert not gpu_claims.release("renders")
    with pytest.raises(ValueError):
        gpu_claims.claim("x", "owner")


def test_claims_are_shared_through_the_store(tmp_path, monkeypatch):
    gpu_claims.claim("renders", "job")
    raw = (tmp_path / "claims.json").read_text()
    assert '"renders"' in raw


def test_a_coordinator_can_refuse_a_guest_but_never_a_job(monkeypatch):
    import types
    from services import resources
    held = types.SimpleNamespace(guest_refused=lambda device: "model" if device == 0 else None)
    monkeypatch.setattr(resources, "backend", lambda: held)
    refused = gpu_claims.claim("speech", "guest")
    assert not refused["granted"] and refused["jobs"] == ["model"]
    assert gpu_claims.claim("speech", "guest", device=1)["granted"]
    assert gpu_claims.claim("renders", "job")["granted"]


def test_the_built_in_coordinator_refuses_no_guest(monkeypatch):
    from services import resources
    monkeypatch.delenv(resources.ENV, raising=False)
    assert gpu_claims.claim("speech", "guest")["granted"]
