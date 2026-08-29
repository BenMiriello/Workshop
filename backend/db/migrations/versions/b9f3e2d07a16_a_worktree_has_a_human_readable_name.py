"""a worktree has a human-readable name

The branch slug is kebab-derived from the task id (a 32-char hex uuid),
which is machine-legible and nothing else: the pill says "t-30777f91..."
while the ticket title is "Name a worktree after the work, not after a
database id".

`display_name` stores the original text the slug was derived from -- the
ticket title for task worktrees, a caller-supplied label for the no-ticket
path. Nullable so existing rows carry NULL rather than a fabricated name;
not back-filled.

The slug derivation changes at the same time: `allocate_task_worktree` now
derives the slug from the title string rather than the hex task id, so new
worktrees get a branch like `wt/development/chat-toolbar-pill-rename-new-chat`.
The record stores both the slug (the git ref component) and the original title
(what the UI shows). A collision with an existing slug appends a 5-char suffix
from the task id, making the pair deterministic for a given (title, task_id).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9f3e2d07a16"
down_revision: Union[str, Sequence[str], None] = "a8b7c6d5e4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("worktree", sa.Column("display_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("worktree", "display_name")
