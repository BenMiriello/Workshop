"""A table that gains `org_id` must gain a policy, and nothing enforces that.

`db/models.TENANT_TABLES` is derived from the SQLAlchemy metadata; the migration
carries its own hardcoded tuple. Two lists that must agree, maintained by hand.
`models.py` used to claim "the migration reads this list, so a table added to
this module is protected by construction". It does not read it, and that comment
was false.

This test is the mechanism the comment described. It needs NO DATABASE, so
unlike the other db tests it runs in every CI, which matters: the drift it
catches is a table created with `org_id`, no policy, and full DML granted to the
application role by `ALTER DEFAULT PRIVILEGES`. That table would be readable
across every tenant, and the only other detector (`verify_rls`) needs a live
database to run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
INITIAL = MIGRATIONS / "966ddcaa35ae_initial_work_schema.py"


def _upgrade_section(source: str) -> str:
    """The text of `def upgrade(): ...`, up to `def downgrade(` or EOF.

    A migration's `downgrade()` recreates tables `upgrade()` drops (T4,
    coherence-overhaul-2026-08-16, migration def2cbf58df8, dropped `agent`
    and `run`): it has its own `op.create_table(...)` calls naming them right
    back. Scanning the whole file for `op.drop_table` would see `upgrade()`'s
    real drop AND `downgrade()`'s mirror-image drop of everything ELSE (every
    `downgrade()` in this tree tears its own `upgrade()` back down), and the
    second kind would wrongly subtract every currently-live table from
    `_tables_in_migration()`'s result. Only `upgrade()`'s drops are real.
    """
    m = re.search(r"^def upgrade\b.*?(?=^def downgrade\b|\Z)", source,
                  re.S | re.M)
    return m.group(0) if m else ""


def _tables_in_migration() -> set[str]:
    """The set of tenant-scoped tables with RLS policies, derived by replaying
    every migration in alphabetical order (revision IDs are hex; alphabetical
    order matches chronological when all migrations share the same filename
    prefix convention this repo uses).

    EVERY migration, not just the initial one. This read the initial schema
    alone, which meant the check could not see a table added by a later
    migration at all: the chat tables (c1a7b8e3d204) arrived correctly policied
    and this still reported them unprotected, because it was looking in one
    file. A guard that goes blind the moment the schema grows is worse than no
    guard, because it keeps passing.

    The convention it enforces, and the reason a migration adding a
    tenant-scoped table must follow it: name the tuple `_TENANT_TABLES` and put
    the table names in it as string literals. A migration that DROPS a
    tenant-scoped table (`op.drop_table` in `upgrade()`) must not name the
    table in `_TENANT_TABLES`, so the drop removes it from the live set.
    A migration that RE-ADDS a previously dropped table must include it in
    `_TENANT_TABLES` again so the replay puts it back; the alphabetical
    ordering guarantees the re-add is seen after the drop.
    """
    current: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.py")):
        source = path.read_text()
        for block in re.finditer(r"_TENANT_TABLES\s*=\s*\((.*?)\)", source, re.S):
            current.update(re.findall(r'"([a-z_]+)"', block.group(1)))
        current -= set(re.findall(
            r'op\.drop_table\(\s*["\']([a-z_]+)["\']', _upgrade_section(source)))
    assert current, "no migration declares _TENANT_TABLES"
    return current


def test_every_org_scoped_table_has_a_policy_in_the_migration():
    from db.models import TENANT_TABLES

    declared = set(TENANT_TABLES)
    protected = _tables_in_migration()

    unprotected = declared - protected
    assert not unprotected, (
        f"tables carry org_id but get no RLS policy: {sorted(unprotected)}. "
        f"Add them to _TENANT_TABLES in a migration, or they are readable by "
        f"every tenant."
    )

    stale = protected - declared
    assert not stale, (
        f"the migration protects tables that no longer carry org_id: "
        f"{sorted(stale)}"
    )


def test_tenant_tables_is_derived_not_hand_written():
    """If someone replaces the metadata-derived tuple with a literal, this test
    stops meaning anything, so assert the derivation still happens."""
    from db.models import Base, TENANT_TABLES

    derived = {t.name for t in Base.metadata.sorted_tables if "org_id" in t.columns}
    assert set(TENANT_TABLES) == derived


def test_the_identity_tables_are_deliberately_excluded():
    """`organization` and `user_account` carry no `org_id` and so are absent
    from TENANT_TABLES by construction. They are NOT unprotected: migration
    7bf9ae20fe0d gives them their own membership-based policies. This test
    exists so that fact is stated somewhere a reader will find it."""
    from db.models import TENANT_TABLES

    assert "organization" not in TENANT_TABLES
    assert "user_account" not in TENANT_TABLES

    later = (MIGRATIONS / "7bf9ae20fe0d_close_the_membership_escalation_and_.py").read_text()
    for table in ("organization", "user_account"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in later
        assert f"CREATE POLICY {table}_visible" in later
