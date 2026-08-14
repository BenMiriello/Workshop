"""priority becomes a named enum

Operator decision, 2026-08-13: "I don't get the integer thing for priority.
Prefer enum."

The integer was not carrying meaning. Across the 112 backfilled tickets it held
**38 distinct values between 0 and 140**, including 87, 92 and 94: distinctions
nobody deliberately chose and nobody could act on. Five named levels are what
Plane, Jira and Linear all settle on.

A NATIVE enum type, not a CHECK constraint, and this is the only one in the
schema. The reason is ordering: a Postgres enum sorts by DECLARATION order, so
`ORDER BY priority` yields urgent before none with no CASE expression and no
rank column. With a text column plus CHECK, the natural sort is alphabetical
(high, low, medium, none, urgent), which is silently wrong and exactly the kind
of bug that survives review.

The bucket mapping is chosen from the live distribution, not invented:

    >= 100  urgent   10 tickets
    80..99  high     37
    40..79  medium   28
     1..39  low      27
         0  none     10

Downgrade restores an integer using the midpoint of each band. It is lossy and
says so: the original 38 values cannot be recovered, because collapsing them is
the entire point of the change.

Revision ID: 769d64dbfa74
Revises: 966ddcaa35ae
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "769d64dbfa74"
down_revision: str | Sequence[str] | None = "966ddcaa35ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIORITIES = ("urgent", "high", "medium", "low", "none")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE ticket_priority AS ENUM ("
        + ", ".join(f"'{p}'" for p in PRIORITIES)
        + ")"
    )

    # The old column has a server_default of '0'. It has to go before the type
    # changes, or Postgres tries to cast the integer default to the enum and
    # fails the whole migration.
    op.execute("ALTER TABLE ticket ALTER COLUMN priority DROP DEFAULT")

    op.execute("""
        ALTER TABLE ticket
        ALTER COLUMN priority TYPE ticket_priority
        USING (CASE
                 WHEN priority >= 100 THEN 'urgent'
                 WHEN priority >=  80 THEN 'high'
                 WHEN priority >=  40 THEN 'medium'
                 WHEN priority >=   1 THEN 'low'
                 ELSE 'none'
               END)::ticket_priority
    """)

    op.execute("ALTER TABLE ticket ALTER COLUMN priority SET DEFAULT 'none'")


def downgrade() -> None:
    op.execute("ALTER TABLE ticket ALTER COLUMN priority DROP DEFAULT")
    op.execute("""
        ALTER TABLE ticket
        ALTER COLUMN priority TYPE integer
        USING (CASE priority
                 WHEN 'urgent' THEN 120
                 WHEN 'high'   THEN 90
                 WHEN 'medium' THEN 60
                 WHEN 'low'    THEN 20
                 ELSE 0
               END)
    """)
    op.execute("ALTER TABLE ticket ALTER COLUMN priority SET DEFAULT 0")
    op.execute("DROP TYPE ticket_priority")
