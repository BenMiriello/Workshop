"""a worktree is a record with a timeline

Step B2 of docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md.

A worktree stops being only a directory that exists on disk. Until this table,
`services/worktrees.py` created branches and directories and nothing durable
remembered them: `list_branches` re-derived the whole set by shelling out to
`git worktree list`, so "who allocated this, when, for which ticket, and what
happened to it" had no answer -- and once a merge reaped the tree, the question
stopped being askable at all.

TWO TABLES, and the split is concurrency rather than taste. `worktree` is the
resource; `worktree_event` is its timeline. Section 9 requires the record of a
handover to be "structured events, not prose in a transcript", and an
append-only timeline cannot live in a JSON column on the resource row: appending
there is a read-modify-write of the whole array, so two participants appending
at once lose one of the two entries. Losing who took a tree from whom, in the
design whose entire subject is two participants wanting the same tree, is not an
acceptable failure. An INSERT cannot lose a row. `ticket_event` is the existing
precedent in this schema for history-as-rows.

WHAT IS DELIBERATELY NOT HERE. No holder, no generation, no expiry, no lease of
any kind: the lease is step B3 and section 5 gives it its own table. No presence
(B4). This migration adds a record and its history, and nothing that grants a
right to write.

IT ALSO PAYS OFF a7c3e10d9b64. That migration left `change.worktree_id` as a
bare nullable column with no foreign key and said in its own docstring that the
composite `(worktree_id, org_id) REFERENCES worktree (id, org_id)` belongs to
the migration that creates this table. This is that migration, so the reference
is added here.

`status` and `kind` are CHECK constraints, not native Postgres enums, matching
every other controlled vocabulary in this schema except `priority` (which needs
declaration-order sorting and says so in db/models.py).

Revision ID: e7d4c1b90a63
Revises: c8299a81457a
Create Date: 2026-08-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7d4c1b90a63"
down_revision: str | Sequence[str] | None = "c8299a81457a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as every other policy in this schema (966ddcaa35ae). The
# two-argument current_setting is load-bearing: with one argument an unset
# variable raises rather than filtering.
_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
_ALIVE = sa.text("deleted_at IS NULL")

# Kept in step with db.models.TENANT_TABLES; enforced by
# tests/test_db_rls_coverage.py, which needs no database.
_TENANT_TABLES = ("worktree", "worktree_event")


def upgrade() -> None:
    op.create_table(
        "worktree",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("app_id", sa.UUID(), nullable=True),
        sa.Column("project_slug", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("branch_slug", sa.String(length=64), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.String(length=255), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=True),
        sa.Column("ticket_id", sa.String(length=255), nullable=True),
        sa.Column("allocated_by", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="allocated",
                  nullable=False),
        sa.Column("merged_sha", sa.String(length=40), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('allocated', 'provisioned', 'merged', 'discarded')",
            name="ck_worktree_status"),
        # A terminal worktree has left the disk, and the row has to say when.
        # Without this a record could claim `merged` while the tree it names is
        # still on disk, which is exactly the record-versus-disk disagreement
        # this table exists to close.
        sa.CheckConstraint(
            "(status IN ('merged', 'discarded')) = (removed_at IS NOT NULL)",
            name="ck_worktree_terminal_is_removed"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_worktree_id_org"),
    )
    # Composite, org-scoped reference to the app, matching the `_org_ref`
    # pattern every tenant-scoped reference in this schema uses: a bare FK
    # would let a worktree in org A name an app in org B, because Postgres runs
    # referential-integrity checks with row security OFF (402e96cbbb79).
    op.create_foreign_key(
        "worktree_app_id_org_fkey", "worktree", "app",
        ["app_id", "org_id"], ["id", "org_id"], ondelete="SET NULL (app_id)")

    # ONE LIVE RECORD PER BRANCH, over the unfinished set only: a merged or
    # discarded branch is reaped and its slug becomes reusable. Same shape and
    # same reason as `uq_change_open_branch`.
    op.create_index("uq_worktree_live_branch", "worktree",
                    ["org_id", "project_slug", "branch_slug"], unique=True,
                    postgresql_where=sa.text(
                        "deleted_at IS NULL AND "
                        "status NOT IN ('merged', 'discarded')"))
    op.create_index("ix_worktree_status", "worktree",
                    ["org_id", "status", "updated_at"], postgresql_where=_ALIVE)
    op.create_index("ix_worktree_ticket", "worktree", ["org_id", "ticket_id"],
                    postgresql_where=sa.text(
                        "ticket_id IS NOT NULL AND deleted_at IS NULL"))
    op.create_index("ix_worktree_app", "worktree", ["org_id", "app_id"],
                    postgresql_where=sa.text(
                        "app_id IS NOT NULL AND deleted_at IS NULL"))

    op.create_table(
        "worktree_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("worktree_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("sha", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("paths", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # No `deleted_at`: a timeline you can quietly delete from is not a
        # record. The rows go when the worktree is hard-deleted, and only then.
        sa.CheckConstraint(
            "kind IN ('allocated', 'provisioned', 'acquired', 'steered', "
            "'preempted', 'snapshot', 'merged', 'discarded', 'reaped')",
            name="ck_worktree_event_kind"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_worktree_event_id_org"),
    )
    op.create_foreign_key(
        "worktree_event_worktree_id_org_fkey", "worktree_event", "worktree",
        ["worktree_id", "org_id"], ["id", "org_id"], ondelete="CASCADE")
    op.create_index("ix_worktree_event_timeline", "worktree_event",
                    ["org_id", "worktree_id", "created_at"])

    # a7c3e10d9b64 deferred this to here by name.
    op.create_foreign_key(
        "change_worktree_id_org_fkey", "change", "worktree",
        ["worktree_id", "org_id"], ["id", "org_id"],
        ondelete="SET NULL (worktree_id)")

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
    # The change's reference goes first: dropping `worktree` under a live FK
    # would fail, and leaving the constraint behind would leave `change`
    # pointing at a table that is no longer there.
    op.drop_constraint("change_worktree_id_org_fkey", "change",
                       type_="foreignkey")
    for table in _TENANT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("worktree_event")
    op.drop_table("worktree")
