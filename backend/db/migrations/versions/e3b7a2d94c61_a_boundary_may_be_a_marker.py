"""a boundary may be a marker: reset and auto join clear and compact

`ck_chat_message_boundary_mode` has admitted `('clear', 'compact')` since the
tree transcript migration (c1a7b8e3d204). The file log carries two more modes,
and the live mirror (`chat_branches._sync_record`) and the backfill both copy a
boundary's `mode` into `boundary_mode` verbatim:

  * `auto`, written by `chat_store.note_auto_compact` every time the CLI
    compacts its own window mid-turn.
  * `reset`, written by `chat_store.note_session_reset`.

Neither moves a cut. They are markers the transcript draws a divider at, and
`MessageList`'s `ContextCut` renders each one distinctly from a `compact`, so
mapping them onto the two allowed values in `chat_repo` would draw the wrong
divider (or, as `compact`, a cut that never happened). The constraint was
simply never widened to match its writers, the same gap 84da206d8bdf closed for
`chat_turn.status`.

Found 2026-10-02: every auto-compaction raised CheckViolation inside the
best-effort mirror, which logged and dropped it, so the divider never got a
place in the tree (21 such failures in the backend journal since 2026-09-25,
all `auto`). The file logs hold 30 `auto` boundaries and no `reset` one.

Widening only, so it is safe with the app up: a catalog change on the
constraint, no row is touched.

Revision ID: e3b7a2d94c61
Revises: a1e4f7c02b93
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e3b7a2d94c61"
down_revision: Union[str, Sequence[str], None] = "a1e4f7c02b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_message_boundary_mode", "chat_message",
                       type_="check")
    op.create_check_constraint(
        "ck_chat_message_boundary_mode", "chat_message",
        "boundary_mode IS NULL OR "
        "boundary_mode IN ('clear', 'compact', 'reset', 'auto')")


def downgrade() -> None:
    # DATA FIRST: nothing may hold a value the narrowed constraint forbids. The
    # rows are kept, because deleting one cascades to its whole subtree; a NULL
    # mode keeps the node's place and the client skips a boundary it cannot
    # name, so the divider disappears and the transcript is otherwise intact.
    op.execute("UPDATE chat_message SET boundary_mode = NULL "
               "WHERE boundary_mode IN ('reset', 'auto')")
    op.drop_constraint("ck_chat_message_boundary_mode", "chat_message",
                       type_="check")
    op.create_check_constraint(
        "ck_chat_message_boundary_mode", "chat_message",
        "boundary_mode IS NULL OR boundary_mode IN ('clear', 'compact')")
