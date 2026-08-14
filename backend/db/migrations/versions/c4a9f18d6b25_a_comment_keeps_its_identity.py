"""a comment keeps its identity

Found by the read-shadow, which is what a read-shadow is for.

`comment` had no `external_id`, so the backfill discarded the id the JSON store
gave each comment. `services/tasks.delete_comment(root, task_id, comment_id)`
addresses a comment by exactly that id, so after the cutover it would have had
nothing to match on: comments would render but could never be deleted, and the
failure would look like a UI bug rather than a lost column.

Every other mirrored entity already carries `external_source`/`external_id`
(that pair is how the mirror finds a row again). Comment was simply missed.

`author_type` is unchanged and still the fixed actor axis. The five live
comments are authored by "assistant", which is an AGENT, not a `system` event
and not a human user; the backfill previously flattened them to user-or-system
depending on how many members the org had, which recorded the wrong actor.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4a9f18d6b25"
down_revision = "b8d2e5a14c73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comment", sa.Column("external_source", sa.String(64)))
    op.add_column("comment", sa.Column("external_id", sa.String(255)))
    # Partial, like the other identity indexes: a comment created in this system
    # has no external id, and many NULLs must not collide.
    op.execute("CREATE UNIQUE INDEX uq_comment_external ON comment "
               "(org_id, external_source, external_id) "
               "WHERE external_id IS NOT NULL AND deleted_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX uq_comment_external")
    op.drop_column("comment", "external_id")
    op.drop_column("comment", "external_source")
