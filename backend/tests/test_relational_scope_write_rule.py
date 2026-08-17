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
from services import task_scope, tasks, tenancy


@pytest.fixture
def relational(monkeypatch):
    """Make tmp_path look like a scope whose tickets live in Postgres, on a box
    that has no database.

    The seam is `task_scope.relational_scope_for_root`, which is where the
    root-to-scope mapping lives now. It used to be spelled in `ticket_sync`,
    behind an import of sqlalchemy, which is what made the answer depend on the
    running interpreter (see `test_belonging_does_not_depend_on_the_interpreter`).

    `configured`/`available` are stubbed off because these tests are about which
    branch the WRITE takes; with a database configured, the read that precedes
    the write fails closed rather than serving a file, which is the point of
    `test_a_read_of_a_relational_scope_never_falls_back_to_the_file` below.
    """
    monkeypatch.setattr(task_scope, "relational_scope_for_root",
                        lambda root: "development")
    monkeypatch.setattr(tenancy, "configured", lambda: False)
    monkeypatch.setattr(tenancy, "available", lambda: False)


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
    monkeypatch.setattr(tenancy, "available", lambda: False)
    assert tasks.is_relational(tmp_path) is False       # the reader's question
    assert tasks.is_relational_scope(tmp_path) is True  # the writer's question


def test_belonging_does_not_depend_on_the_interpreter(tmp_path, monkeypatch):
    """The regression of 2026-08-17.

    `is_relational_scope` used to reach `db.ticket_sync`, which imports
    sqlalchemy at module scope, inside a bare `except Exception: return False`.
    Run under an interpreter without the driver -- `python3 scripts/...` rather
    than `backend/.venv/bin/python` -- it answered "file-backed" for the repo
    root. `_persist` then wrote `.tasks.json`, `reconcile` re-imported
    `TODO.md`, and four tickets that already existed in Postgres were re-minted
    with fresh ids in a store the app had stopped reading.

    Simulated by making the db package unimportable, which is what a missing
    driver looks like from here.
    """
    import builtins
    real_import = builtins.__import__

    def no_db(name, *a, **kw):
        if name.startswith("db.") or name == "db":
            raise ModuleNotFoundError("No module named 'sqlalchemy'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(task_scope, "WORKSHOP_ROOT", tmp_path)
    monkeypatch.setattr(builtins, "__import__", no_db)
    assert tasks.is_relational_scope(tmp_path) is True, \
        "a root's tickets belong where they belong; a missing driver is not an answer"


def test_a_read_of_a_relational_scope_never_falls_back_to_the_file(tmp_path, monkeypatch):
    """Fail closed on the READ too, or the retired store quietly serves.

    `scripts/root_task_counts.sh` read without a pinned owner, the relational
    read raised `OwnerUnset`, and the old code logged a warning and returned the
    file's tickets. Two cron digests reported those counts to the operator for
    days, one ticket in the wrong status column.
    """
    monkeypatch.setattr(task_scope, "relational_scope_for_root",
                        lambda root: "development")
    monkeypatch.setattr(tenancy, "configured", lambda: True)
    monkeypatch.setattr(tenancy, "available", lambda: False)
    (tmp_path / ".tasks.json").write_text(json.dumps(
        {"version": 2, "tasks": [{"id": "stale", "title": "From the retired store",
                                  "status": "done"}]}))
    with pytest.raises(tasks.RelationalStoreUnavailable):
        tasks.list_tasks(tmp_path)


def test_a_relational_scope_does_not_re_import_its_own_export(tmp_path, monkeypatch):
    """`reconcile` is inert for a relational scope even with the database down.

    Guarding it on `is_relational` (which asks reachability) meant an outage
    turned the one-way export back into an inbox: every line that did not match
    the stale file was imported as a NEW ticket. That is how the four duplicates
    were minted.
    """
    monkeypatch.setattr(task_scope, "relational_scope_for_root",
                        lambda root: "development")
    monkeypatch.setattr(tenancy, "configured", lambda: False)
    monkeypatch.setattr(tenancy, "available", lambda: False)
    (tmp_path / "TODO.md").write_text("# TODO\n\n## operator\n\n- [ ] A line nobody filed\n")
    assert tasks.reconcile(tmp_path) is False
    assert not (tmp_path / ".tasks.json").exists()


def test_there_is_no_way_to_import_an_export(tmp_path):
    """The guard above was the fallback. The import DIRECTION is now gone.

    `tasks.import_markdown` and the `PUT /projects/{slug}/todo` route that
    reached it are deleted (2026-08-17), so no credential and no database state
    can turn a rendering back into store input. This asserts the absence,
    because a guard is only as good as the next caller that reaches around it --
    and one did.
    """
    import inspect

    from routes import projects

    assert not hasattr(tasks, "import_markdown"), \
        "the TODO.md import bridge is back"
    routes = {(r.path, m) for r in projects.router.routes
              for m in getattr(r, "methods", ())}
    assert ("/{slug}/todo", "PUT") not in routes, \
        "PUT /projects/{slug}/todo is back; it re-derived the store from markdown"
    assert ("/{slug}/todo", "GET") in routes, \
        "reading the rendering is fine and should still work"


def test_a_file_backed_export_edit_is_reverted_not_absorbed(tmp_path):
    """The other half of the same rule, for a root that IS file-backed.

    `reconcile` used to heal a hand edit into the store: a checkbox flip moved
    the ticket's status, and a line matching nothing was minted as a new ticket.
    It now runs one way. An edit behind the store's back is overwritten by the
    store's own rendering, and invents nothing.
    """
    kept = tasks.add(tmp_path, text="Real work")
    (tmp_path / "TODO.md").write_text(
        "# TODO\n\n## operator\n\n- [x] Real work\n- [ ] A line nobody filed\n")

    assert tasks.reconcile(tmp_path) is True  # the export was rewritten

    titles = {t["title"]: t["status"] for t in tasks.list_tasks(tmp_path)}
    assert titles == {"Real work": "todo"}, \
        "the export edit changed the store: status healed in, or a ticket minted"
    assert "A line nobody filed" not in (tmp_path / "TODO.md").read_text()
    assert tasks.get_task(tmp_path, kept["id"])["status"] == "todo"


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
