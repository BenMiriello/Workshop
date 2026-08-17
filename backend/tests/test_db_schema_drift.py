"""The models must describe the live schema, in the removal direction.

WHY THIS EXISTS, precisely. `alembic revision --autogenerate` diffs
`db.models.Base.metadata` against the database and writes whatever it takes to
make the database look like the models. So anything the live schema HAS and the
models do not DECLARE is not "undocumented": it is queued for deletion, and it
gets deleted the next time anyone autogenerates, silently, inside a migration
that looks like it is about something else.

That had already happened. Between the migration that introduced them and
2026-08-17, the live database carried 15 composite tenant-carrying foreign keys
(`REFERENCES project(id, org_id)`), 6 of the `UNIQUE (id, org_id)` pairs they
point at, 2 actor-consistency CHECKs and 4 indexes that `models.py` never
mentioned. `compare_metadata()` returned 46 entries, 29 of them removals, and 17
matching `add_fk` that would have put back the SINGLE-COLUMN form. Composite
tenant FKs are the one thing standing between this schema and a row in org A
referencing a row in org B, because Postgres runs referential-integrity checks
with row security OFF -- so that migration would have removed the schema's most
valuable safety property and replaced it with a weaker constraint of the same
name.

THE ASSERTION IS ONE-DIRECTIONAL, deliberately. Additive diffs are the normal
state of work in progress: you add a column to a model, autogenerate, upgrade.
A REMOVAL is never that. It means the database knows something the models have
forgotten, and the only way to "resolve" it is to destroy it.

Skipped when there is no database, like the rest of the db suite.
"""
from __future__ import annotations

import os

import pytest

# Reflection reads pg_catalog, which the application role can also read, so
# either DSN answers the question. The admin one is preferred only because it is
# the credential alembic itself uses (db/migrations/env.py), and this test is
# about what alembic would do.
_URL = (os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()
        or os.environ.get("WORKSHOP_DATABASE_URL", "").strip())

pytestmark = pytest.mark.skipif(
    not _URL,
    reason="no WORKSHOP_DATABASE_ADMIN_URL or WORKSHOP_DATABASE_URL; "
           "see infra/postgres/docker-compose.yml",
)

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

# Every autogenerate directive that DROPS something. `modify_nullable` and
# friends are not here: they alter, they do not remove, and a nullability
# difference is a legitimate work-in-progress state.
REMOVALS = (
    "remove_table", "remove_column", "remove_index",
    "remove_constraint", "remove_fk",
)


def _diffs():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from db.models import Base

    engine = create_engine(_URL, future=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def _flatten(diffs):
    """compare_metadata nests the `modify_*` directives one list deep."""
    for d in diffs:
        if isinstance(d, list):
            yield from d
        else:
            yield d


def _describe(entry) -> str:
    obj = entry[1] if len(entry) > 1 else None
    table = getattr(obj, "table", None)
    name = getattr(obj, "name", None) or getattr(obj, "key", None)
    where = getattr(table, "name", None) or (obj if isinstance(obj, str) else "?")
    return f"{entry[0]}: {where}.{name}"


def test_models_declare_everything_the_live_schema_has():
    """Zero remove_fk, zero remove_constraint, zero remove_index, zero
    remove_table, zero remove_column."""
    dropped = [e for e in _flatten(_diffs()) if e[0] in REMOVALS]
    assert not dropped, (
        "db/models.py has drifted BEHIND the live schema. "
        "`alembic revision --autogenerate` would emit a migration that DROPS "
        "the following, which exist in the database and are not declared in "
        "the models:\n  "
        + "\n  ".join(sorted(_describe(e) for e in dropped))
        + "\n\nThe fix is to declare them in db/models.py -- use `_org_ref` / "
          "`_org_target` for tenant-carrying foreign keys -- NOT to generate "
          "the migration."
    )


def test_every_tenant_reference_is_composite():
    """A tenant-scoped FK names (col, org_id), never col alone.

    The complement of the test above: that one catches the models falling
    behind the database, this one catches a new single-column tenant reference
    being added to BOTH at once. `organization` and `user_account` are exempt --
    neither carries an `org_id`, so there is no pair to reference.
    """
    from db.models import Base

    exempt = {"organization", "user_account"}
    single = []
    for table in Base.metadata.sorted_tables:
        if "org_id" not in table.columns:
            continue
        for fk in table.foreign_key_constraints:
            targets = {e.column.table.name for e in fk.elements}
            if targets & exempt or len(fk.columns) > 1:
                continue
            single.append(f"{table.name}.{list(fk.columns)[0].name} -> "
                          f"{sorted(targets)}")
    assert not single, (
        "single-column foreign key into a tenant-scoped table:\n  "
        + "\n  ".join(sorted(single))
        + "\n\nPostgres checks referential integrity with row security OFF, so "
          "this permits a row in one org to reference a row in another. Use "
          "`_org_ref(...)` against the target's `_org_target(...)` pair."
    )
