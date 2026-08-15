"""a chat can be told to file and run on its own

`chat_thread.mode` has carried two values since the table was created:
`direct`, where work stays in the conversation, and `orchestrated`, where it
leaves as a Ticket that waits for the operator to approve it. The Work settings
surface offers three choices, and without a third value the control is a two-way
switch drawn as a three-way one.

`auto` is orchestrated minus the approval step: file and run, and come back only
for a question that cannot be answered. It is an explicit per-chat operator
choice, not a learned preference (ADR 0006 stays Proposed and unbuilt), so
nothing infers it and nothing writes it except the mode route.

The check constraint is the only schema fact that has to move. The column is
already String(16) with an `orchestrated` server default, and every existing row
keeps the value it has.
"""
from alembic import op

revision = "b2f6c0a9d713"
down_revision = "f1a7c3d904e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_thread_mode", "chat_thread", type_="check")
    op.create_check_constraint(
        "ck_chat_thread_mode", "chat_thread",
        "mode IN ('orchestrated', 'direct', 'auto')")


def downgrade() -> None:
    # A row already set to `auto` would fail the narrower constraint, so put it
    # back on the mode that keeps the ticket flow and only loses the approval
    # skip. Dropping to `direct` would instead stop the chat filing at all.
    op.execute("UPDATE chat_thread SET mode = 'orchestrated' WHERE mode = 'auto'")
    op.drop_constraint("ck_chat_thread_mode", "chat_thread", type_="check")
    op.create_check_constraint(
        "ck_chat_thread_mode", "chat_thread",
        "mode IN ('orchestrated', 'direct')")
