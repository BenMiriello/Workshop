"""the chat turn stops calling itself a run

ONE VALUE, ONE NAME. `chat_turn.run_id` is the identifier the streaming layer
mints for a single streaming turn on a thread. It reached the client as
`meta.turn_id`, because `db/chat_repo._row` popped `run_id` and re-emitted it
under the other name -- a translation layer standing in for a rename nobody
finished. The dataclass in `services/build_streaming.py` was already `Turn`,
the table was already `chat_turn`, and the frontend already said `turn_id`
twice as often as `run_id`. This migration moves the last holdout, so the
translation could be deleted rather than maintained.

It also frees the word. `run` is needed for the Job execution records the UI
already labels "Runs" (the `run` table dropped in def2cbf58df8 and its
successor), and it cannot be spent on two concepts at once.

WHAT MOVES WITH THE COLUMN. Two things, and missing either would break a
write rather than a read:

  uq_chat_turn_run   the unique index the upsert's `ON CONFLICT (run_id)`
                     names. A conflict target must match an existing unique
                     index, so renaming the column without the index would
                     leave `chat_repo.upsert_turn` raising on every second
                     write of a turn. Renamed to `uq_chat_turn_turn_id`.
  ON CONFLICT        lives in application SQL (`db/chat_repo.upsert_turn`,
                     `db/chat_backfill`), changed in the same commit.

Postgres carries an index's definition through `ALTER TABLE ... RENAME
COLUMN` on its own, so the index does not need dropping and rebuilding; only
its NAME is stale, and that is a second, separate rename.

Reversible exactly: both directions are pure renames, no data is read or
rewritten, and the row count is invariant.

Revision ID: a2c9f4b70e18
Revises: def2cbf58df8
"""
from collections.abc import Sequence

from alembic import op

revision: str = "a2c9f4b70e18"
down_revision: str | Sequence[str] | None = "def2cbf58df8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("chat_turn", "run_id", new_column_name="turn_id")
    op.execute("ALTER INDEX uq_chat_turn_run RENAME TO uq_chat_turn_turn_id")


def downgrade() -> None:
    op.execute("ALTER INDEX uq_chat_turn_turn_id RENAME TO uq_chat_turn_run")
    op.alter_column("chat_turn", "turn_id", new_column_name="run_id")
