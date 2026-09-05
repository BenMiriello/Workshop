"""the latitude column goes

`ticket.latitude` and its check constraint `ck_ticket_latitude` arrived in
e9c4a71f0d38, which restored five execution fields the JSON->Postgres mirror had
been silently dropping. That was the right fix for four of them. The fifth was
a field nothing ever set.

WHY IT GOES, measured rather than argued. The column is a per-ticket override of
a three-level cascade whose whole job was to tell a worker how much to decide on
its own where the ticket is silent. On 2026-09-05, across every store:

  * 0 of 207 live tickets carried a value, in Postgres or in any `.tasks.json`.
  * 1 row in `ticket` was non-null -- external_id ab9a0ff1fb21, titled
    "S0 round-trip probe", soft-deleted on 2026-08-17. It is a test fixture from
    the migration that added the column, not work anyone did.
  * Every project in the registry stored null, and no agent definition file in
    the tree carried the frontmatter key.

So the cascade resolved to its default on every sweep that has ever run, and no
run has ever been governed by this column. The operator's order is
docs/platform-redesign/work-status-contract-2026-08-24.md:159 ("remove it"), on
the reasoning that decision rights are already carried in words a person can
read, by the Boundaries line `services/ticket_format.py` requires on every
ticket. That line stays; this column does not.

The precedent is `services/agents_registry.py`, which deleted `depth` and
`fanout` on the same grounds and left the reason behind: "a control that lies.
Do not add them back without a reader."

NO DATA MIGRATES, and the one non-null row is not preserved as an event either.
`db/backfill.py` preserves a dropped JSON field as a `ticket_event` when it had
real occupancy; this had none, and writing a history row for a deleted probe's
test value would be inventing provenance rather than keeping it.

The downgrade restores the column and the constraint but cannot restore values,
which is exactly correct here: there were none to restore.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1e4f7c02b93"
down_revision: Union[str, Sequence[str], None] = "d3f81a5c9027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The constraint first: dropping the column would take it along, but naming
    # it explicitly keeps the downgrade symmetric and the intent readable.
    op.drop_constraint("ck_ticket_latitude", "ticket", type_="check")
    op.drop_column("ticket", "latitude")


def downgrade() -> None:
    """Restores the shape, not the data. Occupancy was zero, so there is no
    difference between the two here -- but a reader of this file should not have
    to infer that from the upgrade."""
    op.add_column("ticket", sa.Column("latitude", sa.String(16), nullable=True))
    op.create_check_constraint(
        "ck_ticket_latitude", "ticket",
        "latitude IS NULL OR latitude IN ('strict', 'standard', 'open')")
