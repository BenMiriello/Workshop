"""The `app` table itself: RLS isolation and the repo functions
(app-is-the-entity-2026-08-20.md stage A), the same level `test_db_chat.py`
and `test_db_ticket_mirror.py` pin their tables at.

Skipped when there is no database, like the other db tests.
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


@pytest.fixture
def two_orgs():
    """Two throwaway orgs, so a cross-org read can be proven empty."""
    from db.engine import owner_session
    from db.ids import uuid7
    from db.models import Organization

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    ids = []
    try:
        with owner_session() as s:
            for n in ("a", "b"):
                o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}apps-{tag}-{n}",
                                 name=f"App RLS test {n}")
                s.add(o)
                s.flush()
                ids.append(o.id)
        yield tuple(ids)
    finally:
        _delete_test_orgs(ids)


def test_an_app_upserts_by_slug_not_by_duplicating(two_orgs):
    from db import app_repo
    from db.engine import tenant_session

    org_a, _ = two_orgs
    with tenant_session(org_a) as s:
        first = app_repo.upsert_app(s, org_a, slug="thing", name="Thing")
        again = app_repo.upsert_app(s, org_a, slug="thing", name="Thing Renamed",
                                    icon="Box")
        assert first["id"] == again["id"]
        assert again["name"] == "Thing Renamed"
        assert again["icon"] == "Box"
        rows = app_repo.list_apps(s, org_a)
        assert sum(1 for r in rows if r["slug"] == "thing") == 1


def test_soft_delete_hides_the_app_but_not_its_row(two_orgs):
    from db import app_repo
    from db.engine import tenant_session

    org_a, _ = two_orgs
    with tenant_session(org_a) as s:
        row = app_repo.upsert_app(s, org_a, slug="gone", name="Gone")
        assert app_repo.delete_app(s, org_a, row["id"]) is True
        assert app_repo.get_app(s, org_a, row["id"]) is None
        assert app_repo.delete_app(s, org_a, row["id"]) is False  # already gone


def test_an_app_in_org_a_is_invisible_to_org_b(two_orgs):
    """Row-level security, not just an application WHERE clause: org B's
    session cannot see org A's row even by primary key, matching the property
    `test_db_tenancy.py` pins for every other table."""
    from db import app_repo
    from db.engine import tenant_session

    org_a, org_b = two_orgs
    with tenant_session(org_a) as s:
        row = app_repo.upsert_app(s, org_a, slug="secret", name="Secret")
    with tenant_session(org_b) as s:
        assert app_repo.get_app(s, org_b, row["id"]) is None
        assert app_repo.list_apps(s, org_b) == []


def test_chat_thread_app_id_links_and_lists(two_orgs):
    from db import app_repo, chat_repo
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Membership, UserAccount

    from tests.conftest import _delete_test_users

    org_a, _ = two_orgs
    uid = None
    try:
        with owner_session() as s:
            u = UserAccount(id=uuid7(),
                            email=f"appchat-{uuid.uuid4().hex[:6]}@example.test")
            s.add(u)
            s.flush()
            uid = u.id
        with tenant_session(org_a) as s:
            s.add(Membership(org_id=org_a, user_id=uid, role="owner"))
            app = app_repo.upsert_app(s, org_a, slug="withchat", name="With Chat")
            thread = chat_repo.create_thread(s, org_a, uid, context_key="withchat",
                                             scope="app:withchat", title="hi")
            app_repo.set_thread_app_id(s, org_a, thread["id"], app["id"])

            rows = app_repo.list_chats_for_app(s, org_a, app["id"])
            assert len(rows) == 1
            assert rows[0]["title"] == "hi"
            assert app_repo.count_chats_for_app(s, org_a, app["id"]) == 1
    finally:
        _delete_test_users([uid])


def test_dev_only_column_exists_and_filters(two_orgs):
    """Exercises the real dev_only column — would raise UndefinedColumn if the
    migration was never applied."""
    from db import app_repo
    from db.engine import tenant_session

    org_a, _ = two_orgs
    with tenant_session(org_a) as s:
        normal = app_repo.upsert_app(s, org_a, slug="normal-app", name="Normal")
        dev = app_repo.upsert_app(s, org_a, slug="dev-app", name="Dev",
                                  dev_only=True)

        assert normal["dev_only"] is False
        assert dev["dev_only"] is True

        # dev_only=False must exclude dev-only rows
        prod_rows = app_repo.list_apps(s, org_a, dev_only=False)
        slugs = [r["slug"] for r in prod_rows]
        assert "normal-app" in slugs
        assert "dev-app" not in slugs

        # dev_only=None must return everything
        all_rows = app_repo.list_apps(s, org_a, dev_only=None)
        all_slugs = [r["slug"] for r in all_rows]
        assert "dev-app" in all_slugs
