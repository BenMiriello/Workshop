"""a run gets a relational home

Destination table for the move-runs-off-file-store ticket. NOT a revert of T4
(def2cbf58df8, which dropped the old `run` for cause: zero readers, written
only by a one-shot backfill, already diverged by 2026-08-10). This is a NEW
shape, derived from the LIVE record measured off .runtime/jobs.jsonl (3,527
collapsed rows, 2026-08-22).

What is different from the old `run`:
- `id` is TEXT (`job_<epoch>_<hex>`), not UUID -- preserves the file store's
  natural keys so a writer can carry them unchanged.
- `attempt_no` and the write-never lease columns (`lease_expires_at`,
  `heartbeat_at`, `worker_pid`) are absent. Their job now belongs to columns
  already on `ticket` (job_ids, lease_until, last_heartbeat, claim_pid).
- `evidence` is JSONB (typed as JobEvidence), not freeform `summary TEXT`.
- `budget` and `binding` are new JSONB columns present in the live record.
- `task_id` is a real UUID FK → ticket (the ticket-to-run link as a column,
  not a linear scan over ticket.job_ids).
- `parent_id` is a self-referencing FK so coordinator→child sweep graphs are
  first-class, not derived.

This migration only creates the table and wires RLS. The file-store write path
(services/jobs.py) is NOT changed here; the dependent ticket does the writer
switch. Both stores are safe to run concurrently until that switch lands.

Reversible: downgrade drops the table; proven on a throwaway database via
upgrade → downgrade → upgrade over the full chain.

Revision ID: f3b8d2a17e94
Revises: 39046bfbe03c
Create Date: 2026-08-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID

revision: str = "f3b8d2a17e94"
down_revision: str | Sequence[str] | None = "39046bfbe03c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
_ALIVE = sa.text("deleted_at IS NULL")

# Kept in step with db.models.TENANT_TABLES; enforced by
# tests/test_db_rls_coverage.py, which needs no database.
_TENANT_TABLES = ("run",)

# Taken directly from services.jobs.STATUSES and services.jobs.TRIGGERS --
# the same source the model's CHECK constraints import from. Frozen here so
# the migration stays correct for the schema as it was when written.
_STATUSES = ("queued", "running", "awaiting_input", "done", "failed", "cancelled")
_TRIGGERS = ("manual", "cron", "event", "loop")


def upgrade() -> None:
    op.create_table(
        "run",
        # TEXT pk: preserves `job_<epoch>_<hex>` natural keys from the file store.
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("project_slug", sa.String(length=64), nullable=True),
        sa.Column("chat_id", sa.String(length=255), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("tasks_done", sa.Integer(), nullable=True),
        sa.Column("tasks_failed", sa.Integer(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        # The ticket link: UUID so the FK can be enforced and the index compact.
        sa.Column("task_id", PgUUID(as_uuid=True), nullable=True),
        # Coordinator job id (same `job_<epoch>_<hex>` format as `id`).
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("budget", JSONB, nullable=True),
        sa.Column("binding", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # Constraints
        sa.CheckConstraint(
            "trigger IN ({})".format(", ".join(f"'{v}'" for v in _TRIGGERS)),
            name="ck_run_trigger"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{v}'" for v in _STATUSES)),
            name="ck_run_status"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_run_id_org"),
    )

    # Ticket link: composite FK so a run cannot name a ticket in a different org.
    # Referential checks run with RLS OFF (402e96cbbb79), so a single-column FK
    # here would be no guard at all.
    op.create_foreign_key(
        "run_task_id_org_fkey", "run", "ticket",
        ["task_id", "org_id"], ["id", "org_id"],
        ondelete="SET NULL (task_id)")

    # Parent coordinator: same composite pattern, self-referencing.
    op.create_foreign_key(
        "run_parent_id_org_fkey", "run", "run",
        ["parent_id", "org_id"], ["id", "org_id"],
        ondelete="SET NULL (parent_id)")

    op.create_index(
        "ix_run_task", "run", ["org_id", "task_id", "started_at"],
        postgresql_where=sa.text("task_id IS NOT NULL"))
    op.create_index(
        "ix_run_org_started", "run", ["org_id", "started_at"])
    op.create_index(
        "ix_run_parent", "run", ["parent_id"],
        postgresql_where=sa.text("parent_id IS NOT NULL"))

    # Row-level security: one org sees only its own runs.
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON "
            f"{table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")

    op.drop_index("ix_run_parent", table_name="run")
    op.drop_index("ix_run_org_started", table_name="run")
    op.drop_index("ix_run_task", table_name="run")
    op.drop_constraint("run_parent_id_org_fkey", "run", type_="foreignkey")
    op.drop_constraint("run_task_id_org_fkey", "run", type_="foreignkey")
    op.drop_table("run")
