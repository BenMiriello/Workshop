"""The JSON-to-relational ticket mirror (ADR 0019, P3 first half).

Each test pins something that either broke while this was being built or would
silently lose data if it regressed. The mirror runs on EVERY ticket write, so a
fault here is a fault in filing a ticket.

Skipped without a database, like the other db tests.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)

pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402


@pytest.fixture
def scope(monkeypatch, tmp_path):
    """An org with a project the mirror will recognise, rooted at a tmp_path.

    `scope_for_root` resolves a working tree to a project via `backfill.SCOPES`,
    so the fixture points that table at tmp_path rather than inventing a second
    resolution path the real code does not use.
    """
    from db import backfill
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Organization, Project, TicketType, WorkflowState
    from db.legacy_map import STATES, TYPES

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    slug = f"mirror-{tag}"
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}m-{tag}",
                             name="Mirror test")
            s.add(o)
            s.flush()
            oid = o.id
        with tenant_session(oid) as s:
            p = Project(id=uuid7(), org_id=oid, slug=slug, name="M",
                        key=f"M{tag[:3]}")
            s.add(p)
            s.flush()
            for token, name, category, pos in STATES:
                s.add(WorkflowState(id=uuid7(), org_id=oid, project_id=p.id,
                                    name=name, category=category,
                                    position=pos, is_default=(token == "todo")))
            for _t, name, executable, desc in TYPES:
                s.add(TicketType(id=uuid7(), org_id=oid, project_id=p.id,
                                 name=name, agent_executable=executable,
                                 description=desc))
            s.flush()

        monkeypatch.setattr(backfill, "SCOPES",
                            [(slug, "M", "M", tmp_path)])
        yield {"org": oid, "slug": slug, "root": tmp_path}
    finally:
        _delete_test_orgs([oid])


def _task(tid, **kw):
    base = {"id": tid, "title": "A ticket", "body": "", "status": "todo",
            "priority": 0}
    base.update(kw)
    return base


def _mirror(scope, tasks):
    from db import ticket_sync
    return ticket_sync._mirror(scope["org"], None, scope["slug"], tasks,
                               {"inserted": 0, "updated": 0, "deleted": 0,
                                "events": 0, "skipped": 0})


def _rows(scope):
    from db import repo
    return {t["id"]: t for t in repo.list_tickets(scope["org"],
                                                  scope=scope["slug"])}


def test_a_new_ticket_is_inserted_and_a_second_pass_changes_nothing(scope):
    """Idempotence is the property the write path depends on: `_persist` runs
    this on every save, and most saves change one ticket out of a hundred."""
    tasks = [_task("t1"), _task("t2", title="Another")]
    first = _mirror(scope, tasks)
    assert first["inserted"] == 2
    second = _mirror(scope, tasks)
    assert (second["inserted"], second["updated"], second["deleted"]) == (0, 0, 0)
    assert set(_rows(scope)) == {"t1", "t2"}


def test_a_changed_field_is_updated_and_recorded_as_history(scope):
    """The capability the relational store adds. "Who changed the priority" was
    structurally unrecoverable in the JSON file."""
    from db.engine import tenant_session

    _mirror(scope, [_task("t1")])
    stats = _mirror(scope, [_task("t1", status="doing", priority=120)])
    assert stats["updated"] == 1

    row = _rows(scope)["t1"]
    assert row["status"] == "doing"
    assert row["priority"] == "urgent"

    with tenant_session(scope["org"]) as s:
        events = s.execute(text(
            "SELECT field, old_value, new_value FROM ticket_event "
            " WHERE verb = 'changed' ORDER BY field")).all()
    assert [(e[0], e[1], e[2]) for e in events] == [
        ("priority", "none", "urgent"), ("status", "todo", "doing")]


def test_priority_is_compared_as_a_band_not_as_an_integer(scope):
    """87 and 92 are both `high`. Comparing the raw value would report an update
    (and write a history row) on every save for a field nobody changed."""
    _mirror(scope, [_task("t1", priority=87)])
    stats = _mirror(scope, [_task("t1", priority=92)])
    assert stats["updated"] == 0
    assert stats["events"] == 0


def test_a_removed_ticket_is_soft_deleted_not_erased(scope):
    """Its runs, comments and history stay readable. That is the whole reason
    `deleted_at` exists rather than a DELETE."""
    from db.engine import tenant_session

    _mirror(scope, [_task("t1"), _task("t2")])
    stats = _mirror(scope, [_task("t1")])
    assert stats["deleted"] == 1
    assert set(_rows(scope)) == {"t1"}

    with tenant_session(scope["org"]) as s:
        still_there = s.execute(text(
            "SELECT count(*) FROM ticket WHERE external_id = 't2' "
            "AND deleted_at IS NOT NULL")).scalar()
    assert still_there == 1


def test_parents_and_links_resolve_when_the_target_comes_later(scope):
    """Forward references are the normal case in a list sorted by anything but
    dependency order, which is why the mirror needs a second pass."""
    tasks = [_task("child", parent="parent", depends_on=["parent"]),
             _task("parent")]
    _mirror(scope, tasks)
    row = _rows(scope)["child"]
    assert row["parent"] == "parent"
    assert row["depends_on"] == ["parent"]


def test_a_dangling_dependency_is_skipped_not_invented(scope):
    """The JSON store allowed an edge to a ticket that no longer exists. The
    schema cannot represent one, and fabricating a target would be worse than
    dropping it."""
    _mirror(scope, [_task("t1", depends_on=["nope"])])
    assert _rows(scope)["t1"]["depends_on"] == []


def test_comments_round_trip_by_their_legacy_id(scope):
    """`delete_comment` addresses a comment by that id. Without it comments
    render but can never be deleted, which reads as a UI bug."""
    c = {"id": "c1", "author": "assistant", "text": "a note"}
    _mirror(scope, [_task("t1", comments=[c])])
    got = _rows(scope)["t1"]["comments"]
    assert [(x["id"], x["text"]) for x in got] == [("c1", "a note")]

    stats = _mirror(scope, [_task("t1", comments=[c])])
    assert stats["comments"] == 0


def test_an_assistant_comment_is_system_not_a_user(scope):
    """`ck_comment_actor_is_consistent` forbids an `agent` author with no agent
    id, and the JSON store names no agent. `user` would claim a person wrote
    it, which is the one answer that is simply false."""
    from db.engine import tenant_session

    _mirror(scope, [_task("t1", comments=[
        {"id": "c1", "author": "assistant", "text": "x"}])])
    with tenant_session(scope["org"]) as s:
        row = s.execute(text(
            "SELECT author_type, author_user_id, author_agent_id FROM comment "
            " WHERE external_id = 'c1'")).one()
    assert row.author_type == "system"
    assert row.author_user_id is None and row.author_agent_id is None


def test_an_unknown_root_is_skipped_rather_than_guessed(tmp_path):
    """A checkout this installation does not know about must not have its
    tickets mirrored into whichever project happened to be first."""
    from db import ticket_sync
    assert ticket_sync.scope_for_root(tmp_path / "nowhere") is None


def test_the_mirror_never_raises_into_the_write_path(monkeypatch, tmp_path):
    """`.tasks.json` has already been written by the time this runs. A mirror
    failure must be logged, not raised, or a database outage stops the operator
    filing a ticket."""
    from db import ticket_sync
    from services import tenancy

    monkeypatch.setattr(tenancy, "available", lambda: True)
    monkeypatch.setattr(tenancy, "org_and_user",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ticket_sync, "scope_for_root", lambda _r: "anything")
    stats = ticket_sync.mirror(tmp_path, [_task("t1")])
    assert stats["skipped"] == 1


# --- the fields that got columns in d7b3e0c5f981 -----------------------------

def test_a_chat_source_becomes_a_queryable_link(scope):
    """THE POINT OF THE `source` COLUMNS. `{kind: chat, ref: <thread>}` was an
    unindexed blob, which is why "which tickets did this conversation file" has
    never been a query."""
    from db.engine import tenant_session

    thread = "a" * 32
    _mirror(scope, [_task("t1", source={"kind": "chat", "ref": thread}),
                    _task("t2", source={"kind": "operator", "ref": None})])
    with tenant_session(scope["org"]) as s:
        got = s.execute(text(
            "SELECT external_id FROM ticket WHERE source_kind = 'chat' "
            "AND source_ref = :r AND deleted_at IS NULL"), {"r": thread}).scalars().all()
    assert got == ["t1"]
    assert _rows(scope)["t2"]["source"] == {"kind": "operator", "ref": None}


def test_a_scope_key_is_not_stored_as_a_thread_id(scope):
    """19 live tickets carry `ref: "general"`, a SCOPE name from before the
    convention settled. Storing it as a thread reference yields a link that
    resolves to whatever chat that scope currently shows, which is worse than no
    link because it looks right. The kind is kept, the ref is not."""
    _mirror(scope, [_task("t1", source={"kind": "chat", "ref": "general"})])
    assert _rows(scope)["t1"]["source"] == {"kind": "chat", "ref": None}


def test_an_unknown_source_kind_does_not_lose_the_ticket(scope):
    """The CHECK would refuse it. Dropping a provenance label is the right
    trade against failing the whole write."""
    _mirror(scope, [_task("t1", source={"kind": "carrier-pigeon", "ref": "x"})])
    assert _rows(scope)["t1"]["source"]["kind"] is None
    assert _rows(scope)["t1"]["title"] == "A ticket"


def test_the_preserved_fields_round_trip(scope):
    """Every one of these was silently discarded by the first backfill. They are
    populated on live tickets: touches 35, blocked_reason 23, provenance 56."""
    acc = {"kind": "command", "spec": "file exists",
           "criteria": [{"desc": "d", "cmd": "test -f x", "expect": "exit0"}]}
    _mirror(scope, [_task("t1", touches=["a/b.py", "c.py"], provenance="ab" * 32,
                          blocked_reason="waiting on the operator",
                          acceptance=acc, claim_token=7)])
    row = _rows(scope)["t1"]
    assert row["touches"] == ["a/b.py", "c.py"]
    assert row["provenance"] == "ab" * 32
    assert row["blocked_reason"] == "waiting on the operator"
    assert row["acceptance"] == acc
    assert row["claim_token"] == 7


def test_blocked_reason_changing_is_recorded_but_a_claim_token_bump_is_not(scope):
    """History is a feed a person reads. A claim bump is execution bookkeeping
    that `run` records properly, and putting it here would bury the changes that
    matter."""
    from db.engine import tenant_session

    _mirror(scope, [_task("t1", blocked_reason="one", claim_token=1)])
    _mirror(scope, [_task("t1", blocked_reason="two", claim_token=2)])
    with tenant_session(scope["org"]) as s:
        fields = s.execute(text(
            "SELECT field FROM ticket_event WHERE verb = 'changed'")).scalars().all()
    assert "blocked_reason" in fields
    assert "claim_token" not in fields
    assert _rows(scope)["t1"]["claim_token"] == 2


def test_discovered_from_is_an_edge_and_accepts_a_bare_id(scope):
    """It was a scalar on the JSON record and is a list here, because an edge
    table has no reason to special-case one."""
    _mirror(scope, [_task("child", discovered_from="parent"), _task("parent")])
    from db.engine import tenant_session
    with tenant_session(scope["org"]) as s:
        n = s.execute(text(
            "SELECT count(*) FROM ticket_link WHERE kind = 'discovered_from' "
            "AND deleted_at IS NULL")).scalar()
    assert n == 1
