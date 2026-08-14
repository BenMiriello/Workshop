"""Tenant isolation is the security model, so it is tested, not assumed.

Every assertion here failed at least once during development against a database
whose per-table RLS audit reported no problems. The role the application
connects as was a superuser, and a superuser ignores row security no matter what
the tables say. Read `db/bootstrap.py` before changing any of this.

Skipped when there is no database, so the suite still runs on a machine that has
not provisioned one.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402


@pytest.fixture
def two_orgs():
    """Two organizations, each with a project, a state and one ticket.

    Torn down through `organization`, which is not RLS-protected and which every
    tenant table cascades from, so a failed test cannot leave rows behind that a
    tenant-scoped session would be unable to see in order to delete.
    """
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Organization, Project, Ticket, UserAccount, WorkflowState

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    ids: dict = {}
    try:
        # Organizations and users are provisioned on the OWNER connection: the
        # application role deliberately has no INSERT on either (migration
        # 7bf9ae20fe0d), because a request handler must never be able to create
        # or destroy a tenant.
        with owner_session() as s:
            for name in ("a", "b"):
                org = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}{name}-{tag}",
                                   name=f"Test {name}")
                s.add(org)
                s.flush()
                ids[name] = org.id
            user = UserAccount(id=uuid7(), email=f"t-{tag}@example.test", name="T")
            s.add(user)
            s.flush()
            ids["user"] = user.id

        for name in ("a", "b"):
            org = ids[name]
            with tenant_session(org) as s:
                p = Project(id=uuid7(), org_id=org, slug=f"p-{tag}", name="P",
                            key=f"P{tag[:3]}")
                s.add(p)
                s.flush()
                st = WorkflowState(id=uuid7(), org_id=org, project_id=p.id, name="Todo",
                                   category="unstarted", is_default=True)
                s.add(st)
                s.flush()
                t = Ticket(id=uuid7(), org_id=org, project_id=p.id, number=1,
                           title=f"{name} secret", state_id=st.id)
                s.add(t)
                s.flush()
                ids[f"project_{name}"] = p.id
                ids[f"state_{name}"] = st.id
                ids[f"ticket_{name}"] = t.id

        yield ids
    finally:
        # try/finally, not a plain teardown after the yield: a fixture that
        # raises PART WAY through setup never reaches the yield, so a bare
        # teardown never runs and the rows it already created leak. That is how
        # stale organizations accumulated in this database.
        _delete_test_orgs([ids.get("a"), ids.get("b")])
        if ids.get("user"):
            with owner_session() as s:
                s.execute(text("DELETE FROM user_account WHERE id = :u"),
                          {"u": ids["user"]})


def test_the_app_role_is_not_privileged():
    """The check that would have caught the original failure immediately.

    ENABLE + FORCE + a policy on every table is not sufficient: a SUPERUSER or a
    BYPASSRLS role ignores all of it. This asserts the connection itself.
    """
    from db.engine import admin_session

    with admin_session() as s:
        role, is_super, bypass = tuple(s.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user")).one())
    assert not is_super, f"{role} is a superuser; RLS does not apply to it"
    assert not bypass, f"{role} holds BYPASSRLS; RLS does not apply to it"


def test_every_tenant_table_is_protected():
    from db.engine import admin_session, verify_rls

    with admin_session() as s:
        assert verify_rls(s) == []


def test_a_tenant_reads_only_its_own_rows(two_orgs):
    from db.engine import tenant_session

    for name in ("a", "b"):
        with tenant_session(two_orgs[name]) as s:
            titles = [r[0] for r in s.execute(text("SELECT title FROM ticket")).all()]
        assert titles == [f"{name} secret"]


def test_a_tenant_cannot_read_another_by_primary_key(two_orgs):
    """The predicate-free read. An application bug that forgets `WHERE org_id`
    still must not cross the boundary; that is the entire reason RLS is here
    rather than a convention."""
    from db.engine import tenant_session

    with tenant_session(two_orgs["a"]) as s:
        row = s.execute(text("SELECT title FROM ticket WHERE id = :i"),
                        {"i": two_orgs["ticket_b"]}).one_or_none()
        total = s.execute(text("SELECT count(*) FROM ticket")).scalar()
    assert row is None
    assert total == 1


def test_a_tenant_cannot_insert_into_another_org(two_orgs):
    """WITH CHECK, not just USING. A policy that filters reads but permits
    writes labelled with someone else's org_id is only half a boundary."""
    from db.engine import tenant_session
    from db.ids import uuid7

    with pytest.raises(Exception) as err:
        with tenant_session(two_orgs["a"]) as s:
            s.execute(text(
                "INSERT INTO ticket (id, org_id, project_id, number, title, state_id) "
                "VALUES (:i, :o, :p, 99, 'smuggled', :st)"),
                {"i": uuid7(), "o": two_orgs["b"], "p": two_orgs["project_a"],
                 "st": two_orgs["state_a"]})
    assert "row-level security" in str(err.value)


def test_a_tenant_cannot_move_a_row_to_another_org(two_orgs):
    from db.engine import tenant_session

    with pytest.raises(Exception) as err:
        with tenant_session(two_orgs["a"]) as s:
            s.execute(text("UPDATE ticket SET org_id = :b"), {"b": two_orgs["b"]})
    assert "row-level security" in str(err.value)


def test_a_session_with_no_tenant_sees_nothing(two_orgs):
    """`admin_session` is not a back door. With no `app.current_org` set the
    policy compares against NULL, which excludes every row."""
    from db.engine import admin_session

    with admin_session() as s:
        assert s.execute(text("SELECT count(*) FROM ticket")).scalar() == 0


def test_the_login_bootstrap_can_read_its_own_memberships(two_orgs):
    """A user who has just authenticated has no organization yet. If membership
    were filtered on org_id alone, the rows saying which organizations they may
    enter would be invisible and every user would be locked out."""
    from db.engine import tenant_session, user_session
    from db.models import Membership

    # Written inside the org's own tenant session, which is also the answer to
    # "who creates the first membership at signup": the provisioning flow knows
    # the org id it just created, so it never needs a privileged write.
    with tenant_session(two_orgs["a"]) as s:
        s.add(Membership(org_id=two_orgs["a"], user_id=two_orgs["user"], role="owner"))

    with user_session(two_orgs["user"]) as s:
        orgs = [r[0] for r in s.execute(text("SELECT org_id FROM membership")).all()]
        tickets = s.execute(text("SELECT count(*) FROM ticket")).scalar()

    assert orgs == [two_orgs["a"]]
    assert tickets == 0, "a user session with no org must not see tenant data"


def test_tenant_session_refuses_a_missing_org():
    from db.engine import TenantUnset, tenant_session

    with pytest.raises(TenantUnset):
        with tenant_session(None):
            pass


def test_the_tenant_setting_does_not_follow_a_pooled_connection(two_orgs):
    """`engine.py`'s headline claim, which nothing tested.

    `SET LOCAL` is transaction-scoped. A plain `SET` would survive the request
    and the next caller to check out that pooled connection would inherit the
    previous tenant's context. Since the pool is small, the connection used here
    is very likely the same one, which is exactly the case that matters.
    """
    from db.engine import admin_session, tenant_session

    with tenant_session(two_orgs["a"]) as s:
        assert s.execute(text("SELECT current_setting('app.current_org', true)")
                         ).scalar() == str(two_orgs["a"])

    with admin_session() as s:
        leaked = s.execute(text("SELECT current_setting('app.current_org', true)")).scalar()
        visible = s.execute(text("SELECT count(*) FROM ticket")).scalar()

    assert leaked in (None, ""), f"tenant context survived the transaction: {leaked!r}"
    assert visible == 0


def test_a_tenant_cannot_reference_another_orgs_rows(two_orgs):
    """Referential-integrity checks run with row security OFF, so `WITH CHECK`
    constrains the label on a row and says nothing about what it points at.
    Before the composite foreign keys, all of these succeeded, which gave an
    existence oracle for other tenants' UUIDs and let one tenant pin a RESTRICT
    on another tenant's workflow state.
    """
    from db.engine import tenant_session
    from db.ids import uuid7

    attempts = {
        "ticket -> other org's project": (
            "INSERT INTO ticket (id, org_id, project_id, number, title, state_id) "
            "VALUES (:i, :o, :p, 700, 'x', :st)",
            {"p": two_orgs["project_b"], "st": two_orgs["state_a"]}),
        "ticket -> other org's state": (
            "INSERT INTO ticket (id, org_id, project_id, number, title, state_id) "
            "VALUES (:i, :o, :p, 701, 'x', :st)",
            {"p": two_orgs["project_a"], "st": two_orgs["state_b"]}),
        "link -> other org's ticket": (
            "INSERT INTO ticket_link (id, org_id, from_ticket_id, to_ticket_id, kind) "
            "VALUES (:i, :o, :f, :t, 'blocked_by')",
            {"f": two_orgs["ticket_a"], "t": two_orgs["ticket_b"]}),
        "comment on other org's ticket": (
            "INSERT INTO comment (id, org_id, ticket_id, author_type, body) "
            "VALUES (:i, :o, :t, 'system', 'x')",
            {"t": two_orgs["ticket_b"]}),
    }
    for label, (sql, extra) in attempts.items():
        with pytest.raises(Exception) as err:
            with tenant_session(two_orgs["a"]) as s:
                s.execute(text(sql), {"i": uuid7(), "o": two_orgs["a"], **extra})
        assert "foreign key" in str(err.value).lower(), f"{label} was allowed"


def test_a_tenant_cannot_enumerate_other_organizations_or_users(two_orgs):
    """`organization` and `user_account` carry no `org_id`, so they fall outside
    the derived tenant-table list and originally had no policy at all: every
    tenant could read every other tenant's name and every customer's email."""
    from db.engine import tenant_session

    with tenant_session(two_orgs["a"], two_orgs["user"]) as s:
        orgs = [r[0] for r in s.execute(text("SELECT id FROM organization")).all()]
        emails = s.execute(text("SELECT count(*) FROM user_account")).scalar()

    assert two_orgs["b"] not in orgs, "another tenant's organization was visible"
    assert emails <= 1, "the customer list was enumerable from inside a tenant"


def test_the_audit_log_cannot_be_rewritten_by_the_application(two_orgs):
    """`models.py` says an audit row that can be edited or removed is not an
    audit row. `GRANT ... ON ALL TABLES` had made that false."""
    from db.engine import tenant_session

    for sql in ("UPDATE ticket_event SET new_value = 'rewritten history'",
                "DELETE FROM ticket_event"):
        with pytest.raises(Exception) as err:
            with tenant_session(two_orgs["a"]) as s:
                s.execute(text(sql))
        assert "permission denied" in str(err.value).lower()


def test_verify_rls_actually_detects_a_broken_table():
    """A negative test. `verify_rls` returning `[]` is only reassuring if it can
    return something else; a regression that made it unconditionally empty would
    otherwise pass every check in this file."""
    from db.engine import owner_session, verify_rls

    with owner_session() as s:
        s.execute(text("ALTER TABLE ticket_label NO FORCE ROW LEVEL SECURITY"))
        try:
            problems = verify_rls(s)
        finally:
            s.execute(text("ALTER TABLE ticket_label FORCE ROW LEVEL SECURITY"))

    assert any("ticket_label" in p and "FORCED" in p for p in problems), problems

    with owner_session() as s:
        assert not [p for p in verify_rls(s) if "ticket_label" in p]
