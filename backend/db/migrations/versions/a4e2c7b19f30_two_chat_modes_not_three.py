"""two chat modes, not three: orchestrated becomes auto

`chat_thread.mode` offered three values, and two of them were one behaviour.
`orchestrated` meant "work leaves as a Ticket, then wait to be let out";
`auto` meant "work leaves as a Ticket, don't wait". They differed ONLY in the
prose `services/chat_context.chat_mode_block` emits -- no branch, no guard, no
tool difference anywhere in the tree. The operator states no intended
distinction between them (2026-08-27), so there is one mode for "work leaves
this conversation" and it is `auto`: the behaviour actually in use, and the
value on 85 of 201 threads against orchestrated's 44.

ORDER MATTERS AND IT IS THE DATA FIRST. Narrowing `ck_chat_thread_mode` before
moving the rows fails outright against the live database, which holds 44 rows
that the narrowed constraint forbids. Update, then drop, then recreate.

The rows move to `auto`, NOT to the default. `direct` would flip 44 live
conversations from "work leaves here" to "work stays here", which is a
behaviour change on a chat nobody asked to change.

SAFE TO RUN WITH THE APP UP. The UPDATE touches 44 rows in one short
transaction and the constraint swap is a catalog change on a small table; the
only writer to this column is the mode route, which by then can no longer send
the retired value. A chat mid-turn does not write `mode`.

The column's server default moves with the constraint. It was `orchestrated`,
which the narrowed constraint no longer admits, so any INSERT omitting `mode`
would have failed. It becomes `direct`, matching `chat_store.DEFAULT_CHAT_MODE`
and the value the file fold gives a thread whose meta carries no mode at all.

THE FILE LOG IS NOT TOUCHED. `.runtime/users/<owner>/chat/<id>/meta.json` is
the record; Postgres is the mirror. Legacy `orchestrated` values there are
mapped at READ time by `chat_store._clamp_mode`, so no thread log is rewritten
and the mirror re-syncs to `auto` on the next write of each thread.

Revision ID: a4e2c7b19f30
Revises: a1b2c7e40d59
Create Date: 2026-08-27

`down_revision` names a1b2c7e40d59 explicitly (the single head at write time,
confirmed via `alembic heads`) rather than a relative parent.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4e2c7b19f30"
down_revision: Union[str, Sequence[str], None] = "a1b2c7e40d59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DATA FIRST. 44 rows on the live database hold the value the next two
    # statements make illegal.
    op.execute("UPDATE chat_thread SET mode = 'auto' WHERE mode = 'orchestrated'")
    op.alter_column("chat_thread", "mode", server_default="direct")
    op.drop_constraint("ck_chat_thread_mode", "chat_thread", type_="check")
    op.create_check_constraint(
        "ck_chat_thread_mode", "chat_thread", "mode IN ('direct', 'auto')")


def downgrade() -> None:
    # Widen first, for the same reason upgrade narrows last: nothing may hold a
    # value the live constraint forbids, at any point.
    op.drop_constraint("ck_chat_thread_mode", "chat_thread", type_="check")
    op.create_check_constraint(
        "ck_chat_thread_mode", "chat_thread",
        "mode IN ('orchestrated', 'direct', 'auto')")
    op.alter_column("chat_thread", "mode", server_default="orchestrated")
    # THE SPLIT IS NOT RECOVERABLE and this does not pretend otherwise. Which
    # `auto` rows were `orchestrated` before the collapse is not recorded
    # anywhere, so putting them all back would move the 85 threads that chose
    # `auto` on their own onto a mode that waits for approval. Down leaves every
    # row on `auto` and only restores the wider vocabulary, which is what makes
    # this reversible without being lossy in the other direction.
