"""chat transcripts become a branchable tree in postgres

ADR 0020. Chat was sequenced last in the persistence plan (P6) and is pulled
forward by an operator decision on 2026-08-13: going back to a point in a
conversation and carrying on from there is not expressible in the append-only
JSONL log, whose own header states that editing a stored message "is not
expressible and is not attempted". The feature and the migration are therefore
the same piece of work.

FOUR TABLES.

  chat_thread   the conversation. `current_branch_id` is the one pointer that
                decides what the transcript is.
  chat_branch   a named ref: a head message and the message it forked from.
                Stores no membership, so it cannot disagree with the tree.
  chat_message  a node. `parent_id` IS the render order; `seq` is an insertion
                counter and the sync cursor, and the two are deliberately
                different numbers. They were the same in the file log, which is
                exactly why a message sent mid-reply needed a second mechanism
                to be placed.
  chat_turn     one run. Produces several messages when it was interrupted.

CONSTRAINTS THAT ARE NOT DECORATION.

Composite `(id, thread_id)` uniqueness with composite FKs against it, so a
parent, a branch head or a turn reference cannot name a row in another
conversation. Postgres runs referential-integrity checks with row security OFF,
so an org-only policy constrains the label on a row and says nothing about what
it points at; migration 402e96cbbb79 reproduced that live for tickets. Inside a
chat the property that matters is narrower than org anyway: same THREAD, which
implies same org because the thread pins it.

`parent_id` is IMMUTABLE, enforced by trigger. Nothing in the design ever
re-parents a message: branching moves refs and creates rows, it never rewrites
history. Immutability is cheaper than cycle detection and strictly stronger,
because a cycle can only be built by an UPDATE.

Revision ID: c1a7b8e3d204
Revises: 402e96cbbb79
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1a7b8e3d204"
down_revision: str | Sequence[str] | None = "402e96cbbb79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as every other policy in this schema. The two-argument
# current_setting is load-bearing: with one argument an unset variable raises
# rather than filtering. See 966ddcaa35ae.
_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

_CHAT_TABLES = ("chat_thread", "chat_branch", "chat_turn", "chat_message")

_ALIVE = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    # --- 1. tables ----------------------------------------------------------
    # Order is dependency order. chat_thread and chat_branch reference each
    # other, so that one FK is added at the end.
    op.create_table(
        "chat_thread",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("context_key", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("scope_locked", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("mode", sa.String(length=16), server_default="orchestrated",
                  nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("title_source", sa.String(length=16), nullable=True),
        sa.Column("seed_title", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("effort", sa.String(length=16), nullable=True),
        sa.Column("permission_mode", sa.String(length=32), nullable=True),
        sa.Column("pre_plan_permission_mode", sa.String(length=32), nullable=True),
        sa.Column("mcp_servers_enabled", postgresql.ARRAY(sa.String(length=64)),
                  nullable=True),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column("current_branch_id", sa.UUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("context_tokens", sa.Integer(), nullable=True),
        sa.Column("claude_session", sa.String(length=64), nullable=True),
        sa.Column("voice_session", sa.String(length=64), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_seq", sa.Integer(), server_default="1", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('orchestrated', 'direct')",
                           name="ck_chat_thread_mode"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user_account.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_chat_thread_id_org"),
    )

    op.create_table(
        "chat_turn",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("timeout", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("stopped", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('running', 'finished', 'lost')",
                           name="ck_chat_turn_status"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id", "org_id"],
                                ["chat_thread.id", "chat_thread.org_id"],
                                name="chat_turn_thread_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "thread_id", name="uq_chat_turn_id_thread"),
    )

    op.create_table(
        "chat_message",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("turn_id", sa.UUID(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="message",
                  nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("boundary_mode", sa.String(length=16), nullable=True),
        sa.Column("scope_from", sa.String(length=128), nullable=True),
        sa.Column("scope_to", sa.String(length=128), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "boundary_mode IS NULL OR boundary_mode IN ('clear', 'compact')",
            name="ck_chat_message_boundary_mode"),
        sa.CheckConstraint("kind IN ('message', 'boundary', 'scope')",
                           name="ck_chat_message_kind"),
        sa.CheckConstraint("role IN ('user', 'assistant')",
                           name="ck_chat_message_role"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id",
                           name="ck_chat_message_not_own_parent"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id", "org_id"],
                                ["chat_thread.id", "chat_thread.org_id"],
                                name="chat_message_thread_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id", "thread_id"],
                                ["chat_message.id", "chat_message.thread_id"],
                                name="chat_message_parent_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id", "thread_id"],
                                ["chat_turn.id", "chat_turn.thread_id"],
                                name="chat_message_turn_fkey", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "thread_id", name="uq_chat_message_id_thread"),
    )

    op.create_table(
        "chat_branch",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("parent_branch_id", sa.UUID(), nullable=True),
        sa.Column("fork_message_id", sa.UUID(), nullable=True),
        sa.Column("head_message_id", sa.UUID(), nullable=True),
        sa.Column("ordinal", sa.Integer(), server_default="1", nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("parent_branch_id IS NULL OR parent_branch_id <> id",
                           name="ck_chat_branch_not_own_parent"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id", "org_id"],
                                ["chat_thread.id", "chat_thread.org_id"],
                                name="chat_branch_thread_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_branch_id", "thread_id"],
                                ["chat_branch.id", "chat_branch.thread_id"],
                                name="chat_branch_parent_fkey", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fork_message_id", "thread_id"],
                                ["chat_message.id", "chat_message.thread_id"],
                                name="chat_branch_fork_message_fkey",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["head_message_id", "thread_id"],
                                ["chat_message.id", "chat_message.thread_id"],
                                name="chat_branch_head_message_fkey",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "thread_id", name="uq_chat_branch_id_thread"),
    )

    # The cycle in the schema graph, closed now that both sides exist.
    op.create_foreign_key(
        "chat_thread_current_branch_fkey", "chat_thread", "chat_branch",
        ["current_branch_id", "id"], ["id", "thread_id"], ondelete="SET NULL")

    # --- 2. indexes ---------------------------------------------------------
    op.create_index("uq_chat_thread_external", "chat_thread", ["external_id"],
                    unique=True,
                    postgresql_where=sa.text(
                        "external_id IS NOT NULL AND deleted_at IS NULL"))
    op.create_index("ix_chat_thread_owner", "chat_thread",
                    ["org_id", "owner_user_id", "updated_at"],
                    postgresql_where=_ALIVE)
    op.create_index("ix_chat_thread_scope", "chat_thread",
                    ["org_id", "scope", "updated_at"], postgresql_where=_ALIVE)

    op.create_index("uq_chat_turn_run", "chat_turn", ["run_id"], unique=True)
    op.create_index("ix_chat_turn_thread", "chat_turn",
                    ["org_id", "thread_id", "started_at"])

    op.create_index("uq_chat_message_seq", "chat_message", ["thread_id", "seq"],
                    unique=True)
    op.create_index("uq_chat_message_client", "chat_message",
                    ["thread_id", "client_id"], unique=True,
                    postgresql_where=sa.text(
                        "client_id IS NOT NULL AND deleted_at IS NULL"))
    op.create_index("ix_chat_message_thread", "chat_message",
                    ["org_id", "thread_id", "seq"], postgresql_where=_ALIVE)
    op.create_index("ix_chat_message_parent", "chat_message", ["parent_id"],
                    postgresql_where=_ALIVE)
    op.create_index("ix_chat_message_turn", "chat_message", ["turn_id"])

    op.create_index("uq_chat_branch_ordinal", "chat_branch",
                    ["thread_id", "ordinal"], unique=True, postgresql_where=_ALIVE)
    op.create_index("ix_chat_branch_thread", "chat_branch",
                    ["org_id", "thread_id", "created_at"], postgresql_where=_ALIVE)

    # --- 3. row-level security ----------------------------------------------
    for table in _CHAT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )

    # --- 4. updated_at ------------------------------------------------------
    # The ORM's `onupdate` is an ORM-level hook and does not fire for raw SQL,
    # which the branch operations use. 402e96cbbb79 found that the hard way.
    for table in _CHAT_TABLES:
        op.execute(f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()")

    # --- 5. parent_id is write-once -----------------------------------------
    # The transcript is a tree that only ever grows. Re-parenting is not an
    # operation this design has, and forbidding it outright is what makes a
    # cycle unconstructible: a cycle requires an UPDATE, and there is none.
    # `thread_id` rides along because moving a message between conversations is
    # the same class of rewrite.
    op.execute("""
        CREATE OR REPLACE FUNCTION chat_message_lineage_is_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
                RAISE EXCEPTION
                    'chat_message.parent_id is immutable (message %)', OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.thread_id IS DISTINCT FROM OLD.thread_id THEN
                RAISE EXCEPTION
                    'chat_message.thread_id is immutable (message %)', OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER chat_message_lineage_guard BEFORE UPDATE ON "
               "chat_message FOR EACH ROW EXECUTE FUNCTION "
               "chat_message_lineage_is_immutable()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chat_message_lineage_guard ON chat_message")
    op.execute("DROP FUNCTION IF EXISTS chat_message_lineage_is_immutable()")
    for table in _CHAT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    # chat_thread -> chat_branch has to go before chat_branch does.
    op.drop_constraint("chat_thread_current_branch_fkey", "chat_thread",
                       type_="foreignkey")
    op.drop_table("chat_branch")
    op.drop_table("chat_message")
    op.drop_table("chat_turn")
    op.drop_table("chat_thread")
