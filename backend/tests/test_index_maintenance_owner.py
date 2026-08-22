"""Only ONE process rebuilds the semantic index, and it is the long-lived one.

THE REGRESSION. The rebuild used to start in whatever process called `search()`
first, and that is nearly always a chat turn's own MCP server child
(backend/mcp/workshop_query_server.py). So an 8-minute, multi-gigabyte batch job
ran inside a turn's process group and was charged to that turn.

Measured 2026-08-22: a chat turn called `search`, found the index empty, started
the rebuild in-process, and was killed by the turn memory ceiling at 9.2 GB for
the crime of searching. A healthy query server is 11 MB; fastembed runs ONNX
Runtime in-process and its arena over the whole corpus is the balloon.

The index is platform state and rebuilding it is a platform background job, so
the backend owns it. A process that does not own maintenance must still SEE that
the index needs catching up and say so, because `search` labels its hits "from
what is on disk right now" off that flag.
"""
from __future__ import annotations

import pytest

from services import semantic_index as si


@pytest.fixture
def stale(monkeypatch):
    """An index that wants rebuilding, without touching the real one."""
    monkeypatch.setattr(si, "is_stale", lambda: True)
    monkeypatch.setattr(si, "_owns_maintenance", False)
    monkeypatch.setattr(si, "_rebuild_threads", {})
    yield
    monkeypatch.setattr(si, "_rebuild_threads", {})


def test_a_process_that_does_not_own_maintenance_starts_no_rebuild(stale):
    """The whole fix: the MCP server inside a turn must not do this work."""
    assert si._background_rebuild_if_stale() is True   # still reports the need
    assert si._rebuild_threads == {}                   # and starts nothing


def test_the_owner_does_start_one(stale, monkeypatch, tmp_path):
    """The backend, having claimed maintenance, still self-heals."""
    monkeypatch.setattr(si, "_owns_maintenance", True)
    # The single-flight key is the owner's index dir, and resolving it needs a
    # pinned owner. Neither is what this test is about.
    monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
    monkeypatch.setattr(si, "pin_owner", lambda *a, **k: None)
    built: list[tuple] = []
    monkeypatch.setattr(si, "build", lambda kinds, **kw: built.append(kinds) or {"ok": True})
    monkeypatch.setattr(si, "rebuild_kinds", lambda: ("note",))

    assert si._background_rebuild_if_stale() is True
    assert len(si._rebuild_threads) == 1
    for t in si._rebuild_threads.values():
        t.join(timeout=10)
    assert built == [("note",)]


def test_a_fresh_index_starts_nothing_either_way(monkeypatch):
    monkeypatch.setattr(si, "is_stale", lambda: False)
    monkeypatch.setattr(si, "_rebuild_threads", {})
    monkeypatch.setattr(si, "_owns_maintenance", True)
    assert si._background_rebuild_if_stale() is False
    assert si._rebuild_threads == {}


def test_enable_maintenance_is_idempotent(monkeypatch):
    monkeypatch.setattr(si, "_owns_maintenance", False)
    si.enable_maintenance()
    si.enable_maintenance()
    assert si.owns_maintenance() is True


def test_a_maintenance_pass_rebuilds_only_when_stale(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(si, "pin_owner", lambda *a, **k: None)
    monkeypatch.setattr(si, "rebuild_if_stale", lambda: calls.append("ran") or None)
    si._maintenance_pass()
    assert calls == ["ran"]
