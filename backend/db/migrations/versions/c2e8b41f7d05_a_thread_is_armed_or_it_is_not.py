"""a thread is armed or it is not

A thread is the one entity and every thread is chattable
(`docs/platform-redesign/threads-branches-and-pages-2026-08-29.md` section 1).
"Agent" stops being a kind of object and becomes a description of a thread that
is armed, so the two settings that used to live on a markdown agent definition
become two columns on `chat_thread`:

`armed` -- what advances the thread without a person: `off`, `on-message`,
`schedule`, `on-ready-ticket`. NOT NULL with a server default of `off`, because
"unknown arming" is not a state anything may act on and the safe answer is the
one the column should give. Existing rows take the default, which is correct
and not a guess: nothing was armed before this column existed.

`role` -- optional, nullable, names a role definition. NULL is a perfectly good
value (an armed thread with no role continues with its own context), so it
carries no default and nothing back-fills it.

The two are INDEPENDENT and neither implies the other, which is why they are
two columns and not one. `ck_chat_thread_armed` is added alongside, on the same
terms as `ck_chat_thread_mode`: the enum is not derived at runtime, so adding
or removing a value means another migration here.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2e8b41f7d05"
down_revision: Union[str, Sequence[str], None] = "b9f3e2d07a16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ARMED = ("off", "on-message", "schedule", "on-ready-ticket")


def upgrade() -> None:
    op.add_column(
        "chat_thread",
        sa.Column("armed", sa.String(length=16), nullable=False,
                  server_default="off"),
    )
    op.add_column("chat_thread", sa.Column("role", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_chat_thread_armed",
        "chat_thread",
        sa.column("armed").in_(_ARMED),
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_thread_armed", "chat_thread", type_="check")
    op.drop_column("chat_thread", "role")
    op.drop_column("chat_thread", "armed")
