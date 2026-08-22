"""Schema semantics that are easy to get wrong and silent when you do.

Skipped when there is no database, like the tenancy tests.
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
def org():
    """A throwaway organization with a project and one state."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Organization, Project, WorkflowState

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}s-{tag}",
                             name="Schema test")
            s.add(o)
            s.flush()
            oid = o.id

        with tenant_session(oid) as s:
            p = Project(id=uuid7(), org_id=oid, slug=f"p-{tag}", name="P",
                        key=f"S{tag[:3]}")
            s.add(p)
            s.flush()
            st = WorkflowState(id=uuid7(), org_id=oid, project_id=p.id, name="Todo",
                               category="unstarted", is_default=True)
            s.add(st)
            s.flush()
            ctx = {"org": oid, "project": p.id, "state": st.id}

        yield ctx
    finally:
        _delete_test_orgs([oid])


def _ticket(session, ctx, number, priority, title="t"):
    from db.ids import uuid7
    from db.models import Ticket
    t = Ticket(id=uuid7(), org_id=ctx["org"], project_id=ctx["project"],
               number=number, title=title, state_id=ctx["state"], priority=priority)
    session.add(t)
    session.flush()
    return t.id


def test_priority_sorts_by_severity_not_alphabetically(org):
    """The whole reason `priority` is a NATIVE enum rather than a CHECK.

    Alphabetically these five sort high, low, medium, none, urgent, which is
    meaningless and would look entirely plausible in a queue. A Postgres enum
    sorts by declaration order, so plain `ORDER BY priority` is correct with no
    CASE expression and no rank column for a caller to forget.
    """
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        for i, p in enumerate(("low", "urgent", "none", "high", "medium")):
            _ticket(s, org, i + 1, p, title=p)
        got = [r[0] for r in s.execute(text(
            "SELECT priority::text AS p FROM ticket ORDER BY priority")).all()]

    assert got == ["urgent", "high", "medium", "low", "none"]
    assert got != sorted(got), "if this equals the alphabetical sort, the enum is gone"


def test_casting_priority_to_text_under_its_own_name_breaks_the_sort(org):
    """A trap found while writing the test above, pinned so it stays known.

    `ORDER BY` resolves an unqualified name against the SELECT's OUTPUT columns
    before the table's columns. So `SELECT priority::text AS priority ... ORDER
    BY priority` sorts the TEXT, alphabetically, and quietly reintroduces
    exactly the bug the enum was chosen to prevent. The result still looks like
    a plausible queue.

    Two ways to stay safe, both asserted here: do not reuse the column's name
    for the cast, or qualify the ORDER BY with the table alias.
    """
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        for i, p in enumerate(("low", "urgent", "none", "high", "medium")):
            _ticket(s, org, 100 + i, p, title=p)

        shadowed = [r[0] for r in s.execute(text(
            "SELECT priority::text AS priority FROM ticket ORDER BY priority")).all()]
        qualified = [r[0] for r in s.execute(text(
            "SELECT t.priority::text AS priority FROM ticket t ORDER BY t.priority")).all()]

    assert shadowed == sorted(shadowed), (
        "expected the documented footgun: the text alias shadows the enum column")
    assert qualified[0] == "urgent", "a table-qualified ORDER BY keeps enum ordering"


def test_priority_rejects_a_value_outside_the_enum(org):
    from db.engine import tenant_session

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text(
                "INSERT INTO ticket (id, org_id, project_id, number, title, state_id, priority) "
                "VALUES (gen_random_uuid(), :o, :p, 900, 'x', :st, 'critical')"),
                {"o": org["org"], "p": org["project"], "st": org["state"]})
    assert "ticket_priority" in str(err.value)


def test_priority_defaults_to_none(org):
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        s.execute(text(
            "INSERT INTO ticket (id, org_id, project_id, number, title, state_id) "
            "VALUES (gen_random_uuid(), :o, :p, 901, 'x', :st)"),
            {"o": org["org"], "p": org["project"], "st": org["state"]})
        got = s.execute(text(
            "SELECT priority::text FROM ticket WHERE number = 901")).scalar()
    assert got == "none"


def test_ticket_number_is_unique_within_a_project(org):
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        _ticket(s, org, 500, "none")

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            _ticket(s, org, 500, "none")
    assert "uq_ticket_number" in str(err.value)


def test_a_ticket_cannot_be_its_own_parent(org):
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        tid = _ticket(s, org, 501, "none")

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text("UPDATE ticket SET parent_id = id WHERE id = :i"), {"i": tid})
    assert "ck_ticket_not_own_parent" in str(err.value)


def test_a_link_cannot_point_at_itself(org):
    """Two independent guards cover this and the TRIGGER wins the race.

    `ck_ticket_link_not_self` is a CHECK constraint; `ticket_link_cycle_guard`
    is a BEFORE INSERT trigger, and BEFORE triggers run first, so a self-link is
    reported as a one-node cycle rather than as the CHECK. Either message is a
    correct refusal, so this asserts the refusal and that it names one of the
    two guards, rather than pinning whichever happens to fire first.
    """
    from db.engine import tenant_session
    from db.ids import uuid7

    with tenant_session(org["org"]) as s:
        tid = _ticket(s, org, 502, "none")

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text(
                "INSERT INTO ticket_link (id, org_id, from_ticket_id, to_ticket_id, kind) "
                "VALUES (:i, :o, :t, :t, 'blocked_by')"),
                {"i": uuid7(), "o": org["org"], "t": tid})
    message = str(err.value)
    assert "ck_ticket_link_not_self" in message or "cycle" in message, message


def test_a_dependency_cycle_is_refused(org):
    """A `blocked_by` cycle makes every ticket in it permanently ineligible for
    the scheduler, with no error and no diagnostic. Nothing prevented it."""
    from db.engine import tenant_session
    from db.ids import uuid7

    with tenant_session(org["org"]) as s:
        a = _ticket(s, org, 601, "none", title="A")
        b = _ticket(s, org, 602, "none", title="B")

    def link(frm, to):
        with tenant_session(org["org"]) as s:
            s.execute(text(
                "INSERT INTO ticket_link (id, org_id, from_ticket_id, to_ticket_id, kind) "
                "VALUES (:i, :o, :f, :t, 'blocked_by')"),
                {"i": uuid7(), "o": org["org"], "f": frm, "t": to})

    link(a, b)
    with pytest.raises(Exception) as err:
        link(b, a)
    assert "cycle" in str(err.value)


def test_sub_ticket_depth_is_capped_at_two(org):
    """ADR 0001 ratified a depth cap of 2 and nothing enforced it. A parent may
    not itself have a parent, which also makes hierarchy cycles impossible
    without a recursive walk."""
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        a = _ticket(s, org, 611, "none", title="A")
        b = _ticket(s, org, 612, "none", title="B")
        c = _ticket(s, org, 613, "none", title="C")

    with tenant_session(org["org"]) as s:
        s.execute(text("UPDATE ticket SET parent_id = :p WHERE id = :i"), {"p": a, "i": b})

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text("UPDATE ticket SET parent_id = :p WHERE id = :i"),
                      {"p": b, "i": c})
    assert "depth is capped" in str(err.value)


def test_updated_at_fires_for_raw_sql(org):
    """`onupdate=func.now()` is an ORM-level hook and does not fire for a raw
    UPDATE. `ix_ticket_updated` and the default list order both depend on this
    column being honest, so it is a database trigger now."""
    import time

    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        tid = _ticket(s, org, 620, "none")
        before = s.execute(text("SELECT updated_at FROM ticket WHERE id = :i"),
                           {"i": tid}).scalar()

    time.sleep(0.02)
    with tenant_session(org["org"]) as s:
        s.execute(text("UPDATE ticket SET title = 'changed' WHERE id = :i"), {"i": tid})
    with tenant_session(org["org"]) as s:
        after = s.execute(text("SELECT updated_at FROM ticket WHERE id = :i"),
                          {"i": tid}).scalar()

    assert after > before


def test_only_one_default_state_per_project(org):
    """Ticket creation needs a defined fallback. A project could carry zero or
    five defaults."""
    from db.engine import tenant_session
    from db.ids import uuid7

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text(
                "INSERT INTO workflow_state (id, org_id, project_id, name, category, is_default) "
                "VALUES (:i, :o, :p, 'Second default', 'unstarted', true)"),
                {"i": uuid7(), "o": org["org"], "p": org["project"]})
    assert "uq_workflow_state_one_default" in str(err.value)


def test_a_state_in_use_cannot_be_deleted(org):
    """`ondelete=RESTRICT` on ticket.state_id. A tenant editing its workflow must
    not be able to orphan every ticket in a column."""
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        _ticket(s, org, 503, "none")

    with pytest.raises(Exception) as err:
        with tenant_session(org["org"]) as s:
            s.execute(text("DELETE FROM workflow_state WHERE id = :i"), {"i": org["state"]})
    assert "foreign key" in str(err.value).lower()


def test_no_composite_set_null_nulls_its_discriminator():
    """A composite `ON DELETE SET NULL` must name the column it nulls.

    THE BUG THIS PINS. Postgres nulls EVERY column of a composite foreign key on
    `SET NULL` unless the action names them. Every composite FK here pairs a
    nullable pointer with a NOT NULL discriminator (`org_id`, or `thread_id` on
    the chat tables), so the plain form tried to null that too and raised at
    delete time. Eleven constraints were affected; `chat_thread`'s tried to null
    the thread's own primary key.

    Written as a query over the live catalogue rather than over the models,
    because these constraints are created by raw DDL in migrations and a test
    that read the models would not have seen any of them.
    """
    from db.engine import owner_session

    with owner_session() as s:
        bad = s.execute(text("""
            SELECT conrelid::regclass || '.' || conname
              FROM pg_constraint
             WHERE contype = 'f'
               AND confdeltype = 'n'
               AND array_length(conkey, 1) > 1
               AND coalesce(array_length(confdelsetcols, 1), 0) = 0
        """)).scalars().all()
    assert bad == [], (
        "composite SET NULL without a column list; these will raise "
        f"NotNullViolation on delete: {bad}")


def test_agent_table_is_gone(org):
    """`agent` was dropped outright by T4 (coherence-overhaul-2026-08-16,
    migration def2cbf58df8): zero reads, zero writes ever. Pinned so a future
    migration cannot silently resurrect it without a reader to justify the cost.
    `run` was also dropped by T4, but a NEW `run` table was added by
    f3b8d2a17e94 shaped from the live .runtime/jobs.jsonl record; that table's
    existence is tested in test_db_run.py."""
    from db.engine import tenant_session

    with tenant_session(org["org"]) as s:
        names = s.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'agent'"
        )).scalars().all()
    assert names == []


def test_the_claim_fields_live_on_the_ticket_now(org):
    """`job_ids`/`lease_until`/`last_heartbeat`/`claim_pid` used to be derived
    from the (now-deleted) `run` table in the compatibility read shape
    (`db/repo.py`); they are plain columns on `ticket` as of def2cbf58df8,
    the same move e9c4a71f0d38 made for `attempts`/`awaiting`/`assignee`."""
    from db.engine import tenant_session
    from datetime import datetime, timezone

    with tenant_session(org["org"]) as s:
        tid = _ticket(s, org, 904, "none")
        now = datetime.now(timezone.utc)
        s.execute(text(
            "UPDATE ticket SET job_ids = :j, lease_until = :lu, "
            "last_heartbeat = :lh, claim_pid = :p WHERE id = :i"),
            {"j": ["job-a", "job-b"], "lu": now, "lh": now, "p": 4242, "i": tid})
        row = s.execute(text(
            "SELECT job_ids, lease_until, last_heartbeat, claim_pid "
            "FROM ticket WHERE id = :i"), {"i": tid}).one()
    assert list(row.job_ids) == ["job-a", "job-b"]
    assert row.lease_until is not None and row.last_heartbeat is not None
    assert row.claim_pid == 4242


def test_a_parked_ticket_is_never_scheduled(org):
    """PARKING IS WHERE WORK GOES TO STOP, and the scheduler must honour that.

    `parked` is the dead-letter state a run lands in at the attempt cap. An
    earlier version of `repo.next_ready` accepted the whole `backlog` category
    and excluded only `Draft` by name, which made every parked ticket eligible
    again and put a root-privileged parked ticket at the head of the queue.
    `services/tasks.is_ready` has always required status == "todo" exactly.
    """
    from db import repo
    from db.engine import tenant_session

    states = {}
    with tenant_session(org["org"]) as s:
        for name in ("Parked", "Draft"):
            states[name] = s.execute(text(
                "INSERT INTO workflow_state (id, org_id, project_id, name, "
                "category, position) VALUES (:i, :o, :p, :n, 'backlog', 9) "
                "RETURNING id"),
                {"i": uuid7_(), "o": org["org"], "p": org["project"],
                 "n": name}).scalar()
        _ticket(s, org, 940, "urgent", title="parked and urgent")
        s.execute(text("UPDATE ticket SET state_id = :st WHERE number = 940"),
                  {"st": states["Parked"]})
        _ticket(s, org, 941, "urgent", title="a draft")
        s.execute(text("UPDATE ticket SET state_id = :st WHERE number = 941"),
                  {"st": states["Draft"]})
        _ticket(s, org, 942, "low", title="the only real candidate")

    ready = [t["title"] for t in repo.next_ready(org["org"], limit=10)]
    assert ready == ["the only real candidate"], (
        f"backlog states leaked into the queue: {ready}")


def uuid7_():
    from db.ids import uuid7
    return uuid7()
