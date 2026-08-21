"""an app is a row now

Stage A of docs/platform-redesign/app-is-the-entity-2026-08-20.md: one `app`
table, and a nullable, additive `app_id` on `chat_thread` and `ticket`.

Those two are the ONLY collections from the design's sec 4.2 list
("chat_thread, ticket, run, deploy, note, agent each carry app_id") that are
actually Postgres tables today. `run`, `deploy`, `note` (knowledge) and
`agent` (the roster) are still file-backed -- see
docs/platform-redesign/tickets-postgres-cutover-2026-08-16.md's own "what else
violates the same boundary" list -- so there is no table to add a column to
without inventing one that would misrepresent file data as relational. Those
four are served to `GET /apps/{id}/...` by resolving the app's slug (and its
`project_slug` bridge) against the existing file stores; see
`services/app_links.py` and `routes/apps.py`.

`app.project_slug` is a plain string, not a foreign key: `.runtime/projects.json`
is a file, not a table, and the Postgres `project` table that already exists
names only the two RELATIONAL_SCOPES roots (`development`, `workshop`), never
an app. See `App`'s docstring in db/models.py for the full reasoning.

This migration only creates the columns. The backfill that populates `app`
rows and sets `app_id` on existing chat threads is `db/app_backfill.py`, run
separately, exactly as `db/backfill.py` seeds the work schema and
`db/chat_backfill.py` seeds the chat one.

Revision ID: 39046bfbe03c
Revises: 84da206d8bdf
Create Date: 2026-08-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "39046bfbe03c"
down_revision: str | Sequence[str] | None = "84da206d8bdf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as every other policy in this schema (966ddcaa35ae). The
# two-argument current_setting is load-bearing: with one argument an unset
# variable raises rather than filtering.
_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
_ALIVE = sa.text("deleted_at IS NULL")

# Kept in step with db.models.TENANT_TABLES; enforced by
# tests/test_db_rls_coverage.py, which needs no database.
_TENANT_TABLES = ("app",)


def upgrade() -> None:
    # --- 1. the app table -----------------------------------------------
    op.create_table(
        "app",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=16), server_default="app",
                  nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("project_slug", sa.String(length=64), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('app', 'agent')", name="ck_app_kind"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id",
                           name="ck_app_not_own_parent"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_app_id_org"),
    )
    # Composite, org-scoped self-reference (containment), matching the
    # `_org_ref` pattern every other table in this schema uses -- a bare FK
    # here would let a row in org A name a parent in org B, and referential
    # checks run with row security OFF (402e96cbbb79).
    op.create_foreign_key(
        "app_parent_id_org_fkey", "app", "app",
        ["parent_id", "org_id"], ["id", "org_id"],
        ondelete="SET NULL (parent_id)")
    op.create_index("uq_app_slug", "app", ["org_id", "slug"], unique=True,
                    postgresql_where=_ALIVE)
    op.create_index("ix_app_project_slug", "app", ["org_id", "project_slug"],
                    postgresql_where=sa.text(
                        "project_slug IS NOT NULL AND deleted_at IS NULL"))

    # --- 2. app_id on the two collections that are real tables today -----
    op.add_column("chat_thread", sa.Column("app_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "chat_thread_app_id_org_fkey", "chat_thread", "app",
        ["app_id", "org_id"], ["id", "org_id"], ondelete="SET NULL (app_id)")
    op.create_index("ix_chat_thread_app", "chat_thread",
                    ["org_id", "app_id", "updated_at"],
                    postgresql_where=sa.text(
                        "app_id IS NOT NULL AND deleted_at IS NULL"))

    op.add_column("ticket", sa.Column("app_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "ticket_app_id_org_fkey", "ticket", "app",
        ["app_id", "org_id"], ["id", "org_id"], ondelete="SET NULL (app_id)")
    op.create_index("ix_ticket_app", "ticket", ["org_id", "app_id"],
                    postgresql_where=sa.text(
                        "app_id IS NOT NULL AND deleted_at IS NULL"))

    # --- 3. row-level security ---------------------------------------------
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

    op.drop_index("ix_ticket_app", table_name="ticket")
    op.drop_constraint("ticket_app_id_org_fkey", "ticket", type_="foreignkey")
    op.drop_column("ticket", "app_id")

    op.drop_index("ix_chat_thread_app", table_name="chat_thread")
    op.drop_constraint("chat_thread_app_id_org_fkey", "chat_thread",
                       type_="foreignkey")
    op.drop_column("chat_thread", "app_id")

    op.drop_table("app")
