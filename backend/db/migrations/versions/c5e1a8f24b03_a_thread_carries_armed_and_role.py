"""a thread carries armed and role

The keystone of `docs/platform-redesign/threads-branches-and-pages-2026-08-29.md`
section 1: there is one entity, and it is a Thread. Two independent fields
replace the second entity that used to sit beside it (four markdown files under
`agents/`, joined to the 214 real threads by nothing but a `context_key` string
used on two of them).

  armed  what advances this thread without a person:
         off | on-message | schedule | on-ready-ticket. NOT NULL, default
         `off`, because "nothing advances it" is a real answer and every
         existing thread's answer. The check constraint mirrors
         `services.chat_store.ARMED_MODES`; that tuple is the one list, and
         changing it means changing this constraint too.

  role   nullable, names a role definition by its free-text NAME (Planner,
         Builder, Researcher). Deliberately NOT the closed
         maker/reviewer/triager/keeper/broker enum: that one is a
         write-authority set (`services.agent_authority._ROLE_WRITES`), it
         stays closed, and it answers a different question.

NO BACKFILL, and that is the correct migration rather than an omission. `off`
and NULL are what every one of the existing rows means today: nothing on this
installation has ever advanced a thread unasked through a thread field, and the
four retired file definitions named no thread, so there is nothing to carry
forward. A backfill here could only invent an intent.

Ticket: d5a051c8047648e194ffd10db8620d4a

Revision ID: c5e1a8f24b03
Revises: b9f3e2d07a16
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e1a8f24b03"
down_revision: str | Sequence[str] | None = "b9f3e2d07a16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ARMED = ("off", "on-message", "schedule", "on-ready-ticket")


def upgrade() -> None:
    op.add_column(
        "chat_thread",
        sa.Column("armed", sa.String(length=24), nullable=False,
                  server_default=sa.text("'off'")),
    )
    op.add_column("chat_thread", sa.Column("role", sa.String(length=64), nullable=True))
    values = ", ".join(f"'{v}'" for v in _ARMED)
    op.create_check_constraint("ck_chat_thread_armed", "chat_thread",
                               f"armed IN ({values})")


def downgrade() -> None:
    op.drop_constraint("ck_chat_thread_armed", "chat_thread", type_="check")
    op.drop_column("chat_thread", "role")
    op.drop_column("chat_thread", "armed")
