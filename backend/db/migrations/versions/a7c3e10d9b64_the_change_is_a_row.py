"""the change is a row

Step C1 of docs/platform-redesign/changes-and-review-2026-08-22.md.

A branch that is ahead of its base gets a first-class record: the tickets it
claims to satisfy, the sha it is at, and a slot for machine-gate results. Until
this table existed, "finished" was a status a worker set on itself and the
operator was asked to accept an assertion with no artifact behind it.

WHAT IS DELIBERATELY NOT HERE. `worktree_id` is a bare nullable column with no
foreign key, because the `worktree` table (step B2 of
HANDOFF-branch-isolation-2026-08-22.md) does not exist yet. The composite
`(worktree_id, org_id) REFERENCES worktree (id, org_id)` belongs to the
migration that creates that table; adding a FK to nothing here would either
fail or invent a table this step has no business inventing. What addresses a
branch today is `(project_slug, branch_slug)`, the pair every published entry
point in `services/worktrees.py` takes, and those two columns are NOT NULL.

`status` is a CHECK, not a native Postgres enum, matching every other
controlled vocabulary in this schema except `priority` (which needs
declaration-order sorting and says so in db/models.py).

Revision ID: a7c3e10d9b64
Revises: f3b8d2a17e94
Create Date: 2026-08-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e10d9b64"
down_revision: str | Sequence[str] | None = "f3b8d2a17e94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as every other policy in this schema (966ddcaa35ae). The
# two-argument current_setting is load-bearing: with one argument an unset
# variable raises rather than filtering.
_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
_ALIVE = sa.text("deleted_at IS NULL")

# Kept in step with db.models.TENANT_TABLES; enforced by
# tests/test_db_rls_coverage.py, which needs no database.
_TENANT_TABLES = ("change",)


def upgrade() -> None:
    op.create_table(
        "change",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("app_id", sa.UUID(), nullable=True),
        sa.Column("worktree_id", sa.UUID(), nullable=True),
        sa.Column("project_slug", sa.String(length=64), nullable=False),
        sa.Column("branch_slug", sa.String(length=64), nullable=False),
        sa.Column("base_ref", sa.String(length=255), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("ticket_ids", postgresql.ARRAY(sa.Text()),
                  server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft",
                  nullable=False),
        sa.Column("gates", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("remote", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
        sa.Column("merged_sha", sa.String(length=40), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'changes_requested', 'approved', "
            "'merged', 'closed')",
            name="ck_change_status"),
        sa.CheckConstraint(
            "(status = 'merged') = (merged_sha IS NOT NULL AND "
            "merged_at IS NOT NULL)",
            name="ck_change_merged_is_recorded"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_change_id_org"),
    )
    # Composite, org-scoped reference to the app, matching the `_org_ref`
    # pattern every other tenant-scoped reference in this schema uses: a bare
    # FK would let a change in org A name an app in org B, because Postgres
    # runs referential-integrity checks with row security OFF (402e96cbbb79).
    op.create_foreign_key(
        "change_app_id_org_fkey", "change", "app",
        ["app_id", "org_id"], ["id", "org_id"], ondelete="SET NULL (app_id)")

    op.create_index("uq_change_open_branch", "change",
                    ["org_id", "project_slug", "branch_slug"], unique=True,
                    postgresql_where=sa.text(
                        "deleted_at IS NULL AND "
                        "status NOT IN ('merged', 'closed')"))
    op.create_index("ix_change_status", "change",
                    ["org_id", "status", "updated_at"], postgresql_where=_ALIVE)
    op.create_index("ix_change_app", "change", ["org_id", "app_id"],
                    postgresql_where=sa.text(
                        "app_id IS NOT NULL AND deleted_at IS NULL"))
    op.create_index("ix_change_tickets", "change", ["ticket_ids"],
                    postgresql_using="gin")

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )
        # The ORM's `onupdate` is an ORM-level hook and does not fire for raw
        # SQL (402e96cbbb79 found this the hard way for the earlier tables).
        op.execute(f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON "
                   f"{table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("change")
