"""Shared machine resources: who holds them, and a message that waits for them.

Pins: a lease is visible until dropped, until its process exits, or until it
expires; a chat's own turns never make it wait on itself; a model that runs
elsewhere never waits; the "when it is free" rule comes true when the other
holder lets go; the installation can name its own coordinator.
"""
from __future__ import annotations

import subprocess
import sys
import types

import pytest

from services import chat_queue, ownership, resource_leases, resources, send_rules
from wsplatform import paths


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path)
    monkeypatch.delenv(resources.ENV, raising=False)
    ownership.set_current_owner(1)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


def test_a_lease_is_seen_until_dropped(owned):
    resource_leases.take("render-1", "gpu", holder="app:x", label="Render")
    assert [h["id"] for h in resource_leases.holders("gpu")] == ["render-1"]
    assert resource_leases.holders("other") == []
    assert resource_leases.drop("render-1")
    assert resource_leases.holders("gpu") == []


def test_a_lease_ends_with_its_process(owned):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    resource_leases.take("gone", "gpu", holder="app:x", pid=proc.pid)
    assert resource_leases.holders("gpu") == []


def test_a_lease_ends_when_it_expires(owned, monkeypatch):
    resource_leases.take("short", "gpu", holder="app:x", ttl_s=10)
    now = __import__("time").time()
    monkeypatch.setattr(resource_leases.time, "time", lambda: now + 11)
    assert resource_leases.holders("gpu") == []


def _needs(monkeypatch, needs):
    monkeypatch.setattr(resources, "needs_for_thread", lambda tid: needs)


def test_resources_free_waits_for_the_other_holder(owned, monkeypatch):
    tid = "a" * 32
    _needs(monkeypatch, ("gpu",))
    entry = {"when": send_rules.normalize({"kind": "resources_free"})}
    resource_leases.take("render-1", "gpu", holder="app:x")
    assert not send_rules.is_ready(entry, thread_id=tid)
    resource_leases.drop("render-1")
    assert send_rules.is_ready(entry, thread_id=tid)


def test_a_chat_never_waits_on_its_own_turn(owned, monkeypatch):
    tid = "b" * 32
    _needs(monkeypatch, ("gpu",))
    monkeypatch.setattr(resources, "needs_for_model", lambda m: ("gpu",))
    resources.take_for_turn(tid, "turn-1", None, "local-model", label="Chat")
    assert resources.free_for_thread(tid)
    assert not resources.free_for_thread("c" * 32)
    resources.release_for_turn("turn-1", "local-model")
    assert resource_leases.holders("gpu") == []


def test_a_model_that_runs_elsewhere_never_waits(owned, monkeypatch):
    _needs(monkeypatch, ())
    resource_leases.take("render-1", "gpu", holder="app:x")
    assert resources.free_for_thread("d" * 32)


def test_the_queue_holds_it_until_free(owned, monkeypatch):
    tid = "e" * 32
    _needs(monkeypatch, ("gpu",))
    resource_leases.take("render-1", "gpu", holder="app:x")
    chat_queue.enqueue(tid, text="later", client_id="c1",
                       when={"kind": "resources_free"})
    assert chat_queue.next_batch(tid) == []
    assert chat_queue.needs_poll(tid)
    resource_leases.drop("render-1")
    assert [e["client_id"] for e in chat_queue.next_batch(tid)] == ["c1"]


def test_an_installation_can_name_its_own_coordinator(owned, monkeypatch):
    mod = types.ModuleType("fake_coordinator")
    mod.NAME = "fake"
    mod.holders = lambda r: [{"id": "x", "resource": r, "holder": "broker"}]
    mod.take = lambda *a, **k: {}
    mod.drop = lambda i: True
    monkeypatch.setitem(sys.modules, "fake_coordinator", mod)
    monkeypatch.setenv(resources.ENV, "module:fake_coordinator")
    assert resources.coordinator_name() == "fake"
    assert resources.busy_with(("gpu",))[0]["holder"] == "broker"
