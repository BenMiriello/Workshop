"""a composite SET NULL must name its columns

THE BUG. A composite foreign key declared `ON DELETE SET NULL` nulls EVERY
column in the key, not just the pointer. Every composite FK in this schema pairs
the pointer with a NOT NULL discriminator (`org_id`, or `thread_id` on the chat
tables), so deleting a referenced row tried to null that column too and raised:

    null value in column "thread_id" of relation "chat_branch"
    violates not-null constraint

Found by deleting a message that a branch pointed at. It is not a test-only
path: it means an agent row could not be deleted while any run referenced it, a
comment could not be deleted while an event referenced it, and a branch-head
message could not be deleted at all. The failure is a raised constraint at
delete time, so nothing was silently corrupted, but the delete simply could not
happen and the error named a column the caller never touched.

`chat_thread_current_branch_fkey` was the worst of them: its second column is
`id`, the thread's own primary key, so deleting a branch attempted to null the
primary key of the thread that owned it.

THE FIX. Postgres 15+ accepts a column list on the action -- `ON DELETE SET
NULL (col)` -- which nulls that column and leaves the rest of the key alone.
Applied to all eleven composite SET NULL constraints. The single-column ones
(assignee_id, created_by_id, author_user_id, actor_user_id -> user_account) are
correct as they stand and are not touched.

`tests/test_db_schema.py` gains a guard so a new composite SET NULL cannot be
added without its column list.
"""
from alembic import op

revision = "a3f1c72b9e40"
down_revision = "d4e17c9b3a55"
branch_labels = None
depends_on = None


# (table, constraint, "child_cols", "parent(cols)", set-null column)
_FIX = [
    ("ticket", "ticket_type_id_org_fkey",
     "type_id, org_id", "ticket_type (id, org_id)", "type_id"),
    ("ticket", "ticket_parent_id_org_fkey",
     "parent_id, org_id", "ticket (id, org_id)", "parent_id"),
    ("comment", "comment_author_agent_id_org_fkey",
     "author_agent_id, org_id", "agent (id, org_id)", "author_agent_id"),
    ("ticket_event", "ticket_event_comment_id_org_fkey",
     "comment_id, org_id", "comment (id, org_id)", "comment_id"),
    ("ticket_event", "ticket_event_actor_agent_id_org_fkey",
     "actor_agent_id, org_id", "agent (id, org_id)", "actor_agent_id"),
    ("run", "run_agent_id_org_fkey",
     "agent_id, org_id", "agent (id, org_id)", "agent_id"),
    ("chat_message", "chat_message_turn_fkey",
     "turn_id, thread_id", "chat_turn (id, thread_id)", "turn_id"),
    ("chat_branch", "chat_branch_parent_fkey",
     "parent_branch_id, thread_id", "chat_branch (id, thread_id)",
     "parent_branch_id"),
    ("chat_branch", "chat_branch_fork_message_fkey",
     "fork_message_id, thread_id", "chat_message (id, thread_id)",
     "fork_message_id"),
    ("chat_branch", "chat_branch_head_message_fkey",
     "head_message_id, thread_id", "chat_message (id, thread_id)",
     "head_message_id"),
    ("chat_thread", "chat_thread_current_branch_fkey",
     "current_branch_id, id", "chat_branch (id, thread_id)",
     "current_branch_id"),
]


def upgrade() -> None:
    for table, name, child, parent, setcol in _FIX:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({child}) REFERENCES {parent} "
            f"ON DELETE SET NULL ({setcol})"
        )


def downgrade() -> None:
    # Restores the broken form deliberately: a downgrade must reproduce the
    # schema it names, not a better one.
    for table, name, child, parent, _ in _FIX:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({child}) REFERENCES {parent} ON DELETE SET NULL"
        )
