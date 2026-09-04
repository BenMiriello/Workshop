"""name the worktrees that were made unnamed

`display_name` arrived in b9f3e2d07a16 and was deliberately not back-filled:
every row already on disk carried NULL and the column's own docstring said so.
That was the right call for a column that had no readers yet. It is the wrong
state now, because the pill, the branch picker and the Branches view all read
`display_name` and fall back to the slug -- so an unnamed record still shows
`t-26c69d40`, which is the exact string §3 of
`worktree-as-the-place-2026-08-28.md` calls "why the menu reads as noise".

THE NAME IS LOOKED UP, NEVER INVENTED. Three sources, in order of how directly
they say what the work was, and a record that matches none of them is left
alone with its NULL rather than given a de-kebabbed version of its own slug:

  1. `ticket_id` names a ticket -> the ticket's title.
  2. `allocated_by` is `chat:<external id>` -> that chat's title. This is the
     one that matters here: the offer path in `chat_worktree.apply_choice` cut
     branches from the work id alone, and every record it made is in this shape.
  3. The slug IS a ticket's external id, with or without the `t-` prefix
     `_kebab_branch_slug` adds to an id that starts with a digit. That is the
     pre-b9f3e2d07a16 naming rule, read backwards.

NOTHING IS RENAMED. The slug, the branch and the path are the identity of a
tree and moving them would break every lookup keyed on the old name -- the
directory on disk, the git ref, the chat pinned to it. Only the display name is
written, which is why this is safe to run against live worktrees.

Irreversible by nature: the downgrade cannot tell a back-filled name from one a
caller supplied, so it does nothing rather than erase both.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f81a5c9027"
down_revision: Union[str, Sequence[str], None] = "c2e8b41f7d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FROM_TICKET_ID = """
UPDATE worktree w
   SET display_name = t.title
  FROM ticket t
 WHERE w.display_name IS NULL
   AND w.deleted_at IS NULL
   AND w.ticket_id IS NOT NULL
   AND t.org_id = w.org_id
   AND t.deleted_at IS NULL
   AND t.external_id = w.ticket_id
   AND btrim(t.title) <> ''
"""

_FROM_CHAT = """
UPDATE worktree w
   SET display_name = c.title
  FROM chat_thread c
 WHERE w.display_name IS NULL
   AND w.deleted_at IS NULL
   AND w.allocated_by LIKE 'chat:%'
   AND c.org_id = w.org_id
   AND c.deleted_at IS NULL
   AND c.external_id = substring(w.allocated_by from 6)
   AND c.title IS NOT NULL
   AND btrim(c.title) <> ''
"""

_FROM_LEGACY_SLUG = """
UPDATE worktree w
   SET display_name = t.title
  FROM ticket t
 WHERE w.display_name IS NULL
   AND w.deleted_at IS NULL
   AND t.org_id = w.org_id
   AND t.deleted_at IS NULL
   AND t.external_id IS NOT NULL
   AND (w.branch_slug = t.external_id
        OR w.branch_slug = 't-' || t.external_id)
   AND btrim(t.title) <> ''
"""


def upgrade() -> None:
    for statement in (_FROM_TICKET_ID, _FROM_CHAT, _FROM_LEGACY_SLUG):
        op.execute(statement)


def downgrade() -> None:
    """Deliberately empty. See the module docstring: a back-filled name and a
    caller-supplied one are indistinguishable afterwards, so clearing the column
    would destroy names this migration never wrote."""
