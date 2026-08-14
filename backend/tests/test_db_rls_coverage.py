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


def _tables_in_migration() -> set[str]:
    src = INITIAL.read_text()
    block = re.search(r"_TENANT_TABLES\s*=\s*\((.*?)\)", src, re.S)
    assert block, "could not find _TENANT_TABLES in the initial migration"
    return set(re.findall(r'"([a-z_]+)"', block.group(1)))


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
