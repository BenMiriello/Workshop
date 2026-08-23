"""a change can be pre-approved, and says who merged it

Step C7 of docs/platform-redesign/changes-and-review-2026-08-22.md, section 6.

Three columns and one check:

  auto_merge    the per-change pre-approval. FALSE for every existing row and
                false by default, so this migration turns nothing on: a change
                nobody marked never merges itself.
  merged_by     which of db.models.MERGE_ACTORS took it. NULLABLE, and null on
                the rows that merged before this existed -- an honest "not
                recorded" rather than a backfilled guess that would make a
                human acceptance indistinguishable from a machine one, which is
                the exact confusion the column exists to prevent.
  merge_basis   the conditions that were true when it merged, in prose, for the
                person who later asks why it landed without them.

Revision ID: d3f5a90c71b2
Revises: f2a6d0c31b78
Create Date: 2026-08-23

`down_revision` names f2a6d0c31b78 explicitly (the head at the time of writing,
found by walking down_revision from base) rather than "-1": a relative parent is
resolved against whatever the graph looks like at run time, which is how a
migration silently reattaches itself to the wrong ancestor.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f5a90c71b2'
down_revision: Union[str, Sequence[str], None] = 'f2a6d0c31b78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTORS = ("operator", "change_auto_merge", "scope_merge_policy")


def upgrade() -> None:
    op.add_column("change", sa.Column(
        "auto_merge", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("change", sa.Column("merged_by", sa.String(32), nullable=True))
    op.add_column("change", sa.Column("merge_basis", sa.Text(), nullable=True))
    joined = ", ".join(f"'{v}'" for v in _ACTORS)
    op.create_check_constraint(
        "ck_change_merged_by", "change",
        f"merged_by IS NULL OR merged_by IN ({joined})")


def downgrade() -> None:
    op.drop_constraint("ck_change_merged_by", "change", type_="check")
    op.drop_column("change", "merge_basis")
    op.drop_column("change", "merged_by")
    op.drop_column("change", "auto_merge")
