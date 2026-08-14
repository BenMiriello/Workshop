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
    assert "ck_ticket_link_not_self" in str(err.value)


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
