"""a killed turn is allowed to say so

Revision ID: 84da206d8bdf
Revises: a2c9f4b70e18
Create Date: 2026-08-18 14:16:14.614181

`_close_turn` (services/chat_branches.py) has always been able to write
status "error" (ev_meta["error"] set) or "stopped" (ev_meta["stopped"] set)
onto a turn it is closing, but `ck_chat_turn_status` only ever allowed
`('running', 'finished', 'lost')`. Both writers existed since the tree
transcript migration (c1a7b8e3d204); the constraint was just never widened to
match. Any turn that ended in error or was stopped hit an IntegrityError on
close instead of recording that outcome, which is strictly worse than the
outcome it was trying to record: the caller sees a raw 500 instead of a
closed, requeue-eligible turn.

This mattered in practice on 2026-08-18: a backend restart armed mid-turn on
one thread kills every OTHER thread's in-flight turn (restart_backend.sh only
waits indefinitely for the turn that armed it; everyone else gets a bounded
180s). Those killed turns tried to close as "error" and crashed instead,
turning an already-lossy restart into a hard failure for the user.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84da206d8bdf'
down_revision: Union[str, Sequence[str], None] = 'a2c9f4b70e18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_turn_status", "chat_turn", type_="check")
    op.create_check_constraint(
        "ck_chat_turn_status",
        "chat_turn",
        "status IN ('running', 'finished', 'lost', 'error', 'stopped')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_turn_status", "chat_turn", type_="check")
    op.create_check_constraint(
        "ck_chat_turn_status",
        "chat_turn",
        "status IN ('running', 'finished', 'lost')",
    )
