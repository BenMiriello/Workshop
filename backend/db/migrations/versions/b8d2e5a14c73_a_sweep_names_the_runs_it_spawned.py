"""a sweep names the runs it spawned

`run` had no `parent_id`, so the schema could not express the one relation that
actually carries ticket identity through a queue sweep.

WHAT THE FILE STORE ALREADY DOES, measured on `.runtime/jobs.jsonl`: a sweep
opens a parent run with no ticket and closes it with two integers
(`tasks_done`, `tasks_failed`); each ticket it works gets a CHILD run carrying
`task_id` and `parent_id`. That mechanism landed 2026-07-15 and is reliable:
74 of 74 child runs since carry their ticket, and every sweep that did work
after that day produced children. The 2.9% of all-time runs carrying a ticket is
an artifact of 2,317 runs recorded before the mechanism existed, not a live gap.

Without this column the backfill flattened all of that: children and parents
became indistinguishable rows and "which tickets did that sweep work" was
answerable only for the 79 runs that happened to carry a task_id directly.

`SET NULL (parent_id)` rather than CASCADE: a child run holds its own ticket,
exit code and receipt, so it stays meaningful after its sweep is gone. The
column list is required, per a3f1c72b9e40.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "b8d2e5a14c73"
down_revision = "a3f1c72b9e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run", sa.Column("parent_id", PgUUID(as_uuid=True),
                                   nullable=True))
    # Composite, like every other cross-row reference here: a run's parent must
    # belong to the same tenant, and RI checks run with row security off, so the
    # tenant column has to be part of the key rather than assumed.
    op.execute("ALTER TABLE run ADD CONSTRAINT uq_run_id_org UNIQUE (id, org_id)")
    op.execute(
        "ALTER TABLE run ADD CONSTRAINT run_parent_id_org_fkey "
        "FOREIGN KEY (parent_id, org_id) REFERENCES run (id, org_id) "
        "ON DELETE SET NULL (parent_id)"
    )
    op.execute("ALTER TABLE run ADD CONSTRAINT ck_run_not_own_parent "
               "CHECK (parent_id IS NULL OR parent_id <> id)")
    op.create_index("ix_run_parent", "run", ["parent_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_run_parent", table_name="run")
    op.execute("ALTER TABLE run DROP CONSTRAINT ck_run_not_own_parent")
    op.execute("ALTER TABLE run DROP CONSTRAINT run_parent_id_org_fkey")
    op.execute("ALTER TABLE run DROP CONSTRAINT uq_run_id_org")
    op.drop_column("run", "parent_id")
