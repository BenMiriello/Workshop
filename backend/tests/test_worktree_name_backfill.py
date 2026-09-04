"""The back-fill names an unnamed worktree record, and never invents one.

Migration `d3f81a5c9027`. It runs once and it ran green on an installation whose
live records happened to have no title available, which means the SQL itself was
never exercised by the thing that proves it. So this drives the migration's own
statements -- imported from the migration module, not retyped -- against a
throwaway tenant that has each of the three shapes in it.

The negative case is the point of the file as much as the positive ones: a
record whose title is nowhere to be found keeps its NULL. A de-kebabbed slug
would look like a name and would be a fabrication, and the operator asked for
the opposite ("leave them alone rather than inventing a name").

Needs the OWNER credential: the fixture mints a throwaway organization, which
the application role deliberately cannot do (`db/engine.py`).
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"),
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)

_MIGRATION = (Path(__file__).resolve().parents[1] / "db" / "migrations"
              / "versions"
              / "d3f81a5c9027_name_the_worktrees_that_were_made_unnamed.py")


def _backfill_statements() -> list[str]:
    """The migration's own SQL, loaded from the migration file.

    IMPORTED, NOT COPIED. A test that retypes the statements tests the copy, and
    the copy is the one thing that cannot drift from itself.
    """
    spec = importlib.util.spec_from_file_location("_wt_backfill", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [module._FROM_TICKET_ID, module._FROM_CHAT, module._FROM_LEGACY_SLUG]


@pytest.fixture()
def seeded():
    """One org holding a worktree record of each shape the back-fill sees."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import (Membership, Organization, Project, Ticket,
                           UserAccount, WorkflowState)
    from tests.conftest import (DB_ORG_PREFIX, _delete_test_orgs,
                                _delete_test_users)

    tag = uuid.uuid4().hex[:8]
    org_ids: list = []
    user_ids: list = []
    try:
        with owner_session() as s:
            user = UserAccount(id=uuid7(), email=f"wtfill-{tag}@example.test",
                               name="Backfill test")
            s.add(user)
            s.flush()
            user_ids.append(user.id)
            org = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}wtfill-{tag}",
                               name="Backfill test")
            s.add(org)
            s.flush()
            s.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
            org_ids.append(org.id)
        org_id = org_ids[0]

        # Shaped like the real ones (32 hex, the legacy one digit-leading so
        # `_kebab_branch_slug` would have prefixed it `t-`), unique per run
        # because `chat_thread.external_id` is unique across the whole table.
        ticket_external = "a" + uuid.uuid4().hex[1:]
        legacy_external = "3" + uuid.uuid4().hex[1:]
        chat_external = uuid.uuid4().hex

        with tenant_session(org_id, user.id) as s:
            project = Project(id=uuid7(), org_id=org_id, slug=f"p-{tag}",
                              name="P", key=f"W{tag[:3]}")
            s.add(project)
            s.flush()
            state = WorkflowState(id=uuid7(), org_id=org_id,
                                  project_id=project.id, name="Todo",
                                  category="unstarted", is_default=True)
            s.add(state)
            s.flush()
            s.add(Ticket(id=uuid7(), org_id=org_id, project_id=project.id,
                         number=1, title="Name a worktree after the work",
                         state_id=state.id, external_id=ticket_external))
            s.add(Ticket(id=uuid7(), org_id=org_id, project_id=project.id,
                         number=2, title="Chat toolbar: pill, rename, new chat",
                         state_id=state.id, external_id=legacy_external))
            s.flush()

        # The chat and the worktrees go in as SQL: the point under test is the
        # migration's statements, and building them through the ORM's own
        # defaults would be a different write than the one production made.
        from sqlalchemy import text
        with owner_session() as s:
            s.execute(text(
                "INSERT INTO chat_thread (id, org_id, owner_user_id, "
                "context_key, scope, external_id, title) VALUES "
                "(:id, :org, :user, 'project:demo', 'project:demo', :ext, "
                ":title)"),
                {"id": str(uuid7()), "org": str(org_id), "user": str(user.id),
                 "ext": chat_external, "title": "Chat toolbar fixes"})
            for slug, ticket_id, allocated_by in (
                ("by-ticket-id", ticket_external, None),
                ("t-26c69d40", None, f"chat:{chat_external}"),
                (f"t-{legacy_external}", None, None),
                ("nothing-knows-me", None, "sweep"),
            ):
                s.execute(text(
                    "INSERT INTO worktree (id, org_id, project_slug, scope, "
                    "branch_slug, branch, path, base_ref, ticket_id, "
                    "allocated_by, status) VALUES (:id, :org, 'demo', "
                    "'project:demo', :slug, :branch, :path, 'main', :ticket, "
                    ":by, 'allocated')"),
                    {"id": str(uuid7()), "org": str(org_id), "slug": slug,
                     "branch": f"wt/demo/{slug}",
                     "path": f"/tmp/backfill-test/{slug}",
                     "ticket": ticket_id, "by": allocated_by})

        yield {"org_id": org_id, "user_id": user.id}
    finally:
        _delete_test_orgs(org_ids)
        _delete_test_users(user_ids)


def _names(org_id) -> dict[str, str | None]:
    from db.engine import owner_session
    from sqlalchemy import text
    with owner_session() as s:
        rows = s.execute(text(
            "SELECT branch_slug, display_name FROM worktree WHERE org_id = :o"),
            {"o": str(org_id)}).all()
    return {r[0]: r[1] for r in rows}


def _run_backfill():
    from db.engine import owner_session
    from sqlalchemy import text
    with owner_session() as s:
        for statement in _backfill_statements():
            s.execute(text(statement))


def test_the_backfill_names_what_it_can_and_nothing_else(seeded):
    """All three sources, plus the record that has none, in one pass."""
    before = _names(seeded["org_id"])
    assert set(before.values()) == {None}, \
        "the fixture seeded a name; the back-fill would prove nothing"

    _run_backfill()
    after = _names(seeded["org_id"])

    # 1. `ticket_id` names a ticket.
    assert after["by-ticket-id"] == "Name a worktree after the work"
    # 2. `allocated_by` is `chat:<external id>`. This is the shape every record
    #    the offer path made is in.
    assert after["t-26c69d40"] == "Chat toolbar fixes"
    # 3. The slug IS a ticket id, with the `t-` prefix a digit-leading id gets.
    legacy_slug = next(k for k in after if k.startswith("t-3"))
    assert after[legacy_slug] == "Chat toolbar: pill, rename, new chat"
    # 4. And the one nothing can speak for keeps its NULL.
    assert after["nothing-knows-me"] is None


def test_the_backfill_renames_nothing(seeded):
    """Identity is the slug, the branch and the path, and it must not move.

    A rename would break the directory on disk, the git ref and every chat
    pinned to the old name -- the trees that already exist are addressed by
    record, and the record's address is what this asserts stays put.
    """
    from db.engine import owner_session
    from sqlalchemy import text

    def _identity():
        with owner_session() as s:
            return sorted(tuple(r) for r in s.execute(text(
                "SELECT branch_slug, branch, path FROM worktree "
                "WHERE org_id = :o"), {"o": str(seeded["org_id"])}).all())

    before = _identity()
    _run_backfill()
    assert _identity() == before


def test_running_it_twice_changes_nothing_the_second_time(seeded):
    """It is a one-shot migration, but a re-run must not double-write.

    `display_name IS NULL` is the guard, so a name a caller supplied afterwards
    survives a second pass. Asserted by naming one by hand between the runs.
    """
    from db.engine import owner_session
    from sqlalchemy import text

    _run_backfill()
    first = _names(seeded["org_id"])

    with owner_session() as s:
        s.execute(text(
            "UPDATE worktree SET display_name = 'A name somebody chose' "
            "WHERE org_id = :o AND branch_slug = 'nothing-knows-me'"),
            {"o": str(seeded["org_id"])})

    _run_backfill()
    second = _names(seeded["org_id"])

    assert second["nothing-knows-me"] == "A name somebody chose"
    del first["nothing-knows-me"], second["nothing-knows-me"]
    assert first == second
