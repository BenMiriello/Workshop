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
    from db.engine import admin_session, tenant_session
    from db.ids import uuid7
    from db.models import Organization, Project, Ticket, UserAccount, WorkflowState

    tag = uuid.uuid4().hex[:8]
    made = {}
    with admin_session() as s:
        for name in ("a", "b"):
            org = Organization(id=uuid7(), slug=f"t-{name}-{tag}", name=f"Test {name}")
            s.add(org)
            made[name] = org
        user = UserAccount(id=uuid7(), email=f"t-{tag}@example.test", name="T")
        s.add(user)
        s.flush()
        ids = {k: v.id for k, v in made.items()}
        ids["user"] = user.id

    for name in ("a", "b"):
        org = ids[name]
        with tenant_session(org) as s:
            p = Project(id=uuid7(), org_id=org, slug=f"p-{tag}", name="P", key=f"P{tag[:3]}")
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

    with admin_session() as s:
        s.execute(text("DELETE FROM organization WHERE id = ANY(:ids)"),
                  {"ids": [ids["a"], ids["b"]]})
        s.execute(text("DELETE FROM user_account WHERE id = :u"), {"u": ids["user"]})


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
