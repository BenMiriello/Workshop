"""A relational scope writes Postgres, and never a file behind its back.

ADR 0019 stage 2. Reads moved to Postgres before writes did, which left the
authoritative write on a file no reader consulted. Two live faults came out of
that inversion in one evening:

  - a mirror that skipped left a ticket filed and invisible (swallowed by a bare
    `except: pass` at the write path);
  - the shell CLI, run under an interpreter without sqlalchemy, wrote `done` to
    a file while the app kept reading `todo` from Postgres (e7b758a).

Both are the same defect. These pin the rule that removes it: for a root whose
tickets BELONG in Postgres, the write goes to Postgres or it raises. It never
silently lands somewhere nothing reads.

No database is required here: the rule is about which branch is taken and what
happens when the write cannot land, so the writer is stubbed.
"""
from __future__ import annotations

import json

import pytest

from db import ticket_sync
from services import tasks


@pytest.fixture
def relational(monkeypatch):
    """Make tmp_path look like a scope whose tickets live in Postgres."""
    monkeypatch.setattr(ticket_sync, "scope_for_root", lambda root: "development")


def test_a_file_backed_root_still_writes_its_file(tmp_path):
    tasks.add(tmp_path, text="A project ticket")
    assert (tmp_path / ".tasks.json").exists()
    assert json.loads((tmp_path / ".tasks.json").read_text())["tasks"]


def test_a_relational_scope_does_not_write_the_json_store(tmp_path, relational, monkeypatch):
    seen = {}

    def fake_mirror(root, tasks_, *, required=False):
        seen["required"] = required
        seen["count"] = len(tasks_)
        return {"inserted": len(tasks_), "skipped": 0, "deleted_ids": []}

    monkeypatch.setattr(ticket_sync, "mirror", fake_mirror)
    tasks.add(tmp_path, text="A relational ticket")
    assert not (tmp_path / ".tasks.json").exists(), \
        "a relational scope must not leave a second store on disk"
    assert seen["required"] is True, "the write must be required, not best effort"
    assert seen["count"] == 1


def test_the_export_is_still_rendered_for_a_relational_scope(tmp_path, relational, monkeypatch):
    """TODO.md survives as a one-way export; that is the whole of its remaining
    job."""
    monkeypatch.setattr(ticket_sync, "mirror",
                        lambda root, t, *, required=False: {"skipped": 0, "deleted_ids": []})
    tasks.add(tmp_path, text="A relational ticket")
    assert "A relational ticket" in (tmp_path / "TODO.md").read_text()


def test_a_write_that_cannot_reach_postgres_raises(tmp_path, relational, monkeypatch):
    """Fail closed. The alternative is the divergence this stage exists to end:
    the write lands on disk, the database comes back, and reads resume from rows
    that never saw it."""
    def boom(root, t, *, required=False):
        raise ticket_sync.WriteFailed("database is not reachable")

    monkeypatch.setattr(ticket_sync, "mirror", boom)
    with pytest.raises(ticket_sync.WriteFailed):
        tasks.add(tmp_path, text="A ticket nobody may lose")
    assert not (tmp_path / ".tasks.json").exists(), \
        "a failed relational write must not fall back to the file"


def test_belonging_is_static_and_does_not_ask_whether_the_database_is_up(tmp_path, relational, monkeypatch):
    """`is_relational` conflates 'belongs in Postgres' with 'Postgres answered'.
    The writer must ask the first, or an outage silently reroutes writes to a
    file that later readers ignore."""
    from services import tenancy
    monkeypatch.setattr(tenancy, "available", lambda: False)
    assert tasks.is_relational(tmp_path) is False       # the reader's question
    assert tasks.is_relational_scope(tmp_path) is True  # the writer's question


def test_a_required_write_reports_a_skip_as_a_failure():
    """A skip used to be indistinguishable from success in the counts, which is
    how a filed ticket went missing quietly."""
    from services import tenancy
    import types

    original = tenancy.available
    tenancy.available = lambda: False
    try:
        with pytest.raises(ticket_sync.WriteFailed):
            ticket_sync.mirror(types.SimpleNamespace(), [{"id": "a"}], required=True)
    finally:
        tenancy.available = original


def test_the_same_call_without_required_still_never_raises():
    """Every legacy caller keeps its old contract."""
    from services import tenancy
    import types

    original = tenancy.available
    tenancy.available = lambda: False
    try:
        stats = ticket_sync.mirror(types.SimpleNamespace(), [{"id": "a"}])
        assert stats["skipped"] == 1
    finally:
        tenancy.available = original
