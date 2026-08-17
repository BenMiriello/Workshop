"""the execution fields get a home too

`d7b3e0c5f981` gave every ticket field a home except five, and said so
explicitly: `latitude`, `max_attempts`, `attempts`, `awaiting` and `assignee`
were left out on a July measurement of "0% occupancy or a mirror of another
field". That was a defensible modelling call while `.tasks.json` was still
authoritative and Postgres was a shadow.

It stopped being defensible on 2026-08-15 (b2c0db5), when `tasks._persist`
started writing Postgres and NOT the file for the `development` and `workshop`
scopes. From that commit, every one of these five fields written through
`PATCH /tasks/{id}` — which accepts all five — went into a store with nowhere to
put it. This is the fix, and it is additive: no existing column is touched.

RE-MEASURED 2026-08-16 across all 27 ticket stores (217 records), not assumed:

  attempts      44 records non-zero (31 of them in `development` alone). The
                July justification had already expired for this one.
  max_attempts  3 records set.
  assignee      the literal "operator" on 128/128 records in the two relational
                scopes; "agent:x" on 5 records elsewhere.
  latitude      0 records.
  awaiting      0 records.

The last two are kept anyway, and the reason is that occupancy at rest is the
wrong test for them. `awaiting` is the general escalation primitive
(`services/run_inbox.py`): it is written when a run blocks and cleared when the
block lifts, so zero at rest is exactly what a queue with nothing stuck looks
like — and dropping it means a run that blocks on the two authoritative scopes
cannot record what it is blocked on, so nothing can surface the question at all.
`latitude` is a per-ticket override of a scope-level cascade
(`services/latitude.py`); zero occupancy means nobody has needed the exception
yet, and the column is what makes setting one possible rather than silently
discarded. Both have live writers today. A field with no column and a live
writer is not an unused field, it is a leak.

TWO BACKFILLS, both restoring what the data already said:

  attempts   seeded from `count(run WHERE started_at IS NOT NULL)`, which is
             what the compatibility read shape has been deriving. Nothing has
             written `run` since the one-shot backfill (newest row 2026-08-14),
             so that derivation could only go stale and the retry cap
             (`tasks.reclaim`, `project_ralph.sh:856`) was structurally
             unenforceable. Seeding keeps the correction the derivation made —
             it repaired 11 tickets whose hand-maintained counter was BELOW
             their real run count — and hands the counter back to the writer.

  assignee   set to 'operator' on every existing row, because that is the value
             all 128 records carried in the last authoritative file and the
             value the read shape has been synthesising for every relational
             ticket since. Leaving NULL would not be neutral: `_match_assignee`
             treats an unassigned ticket as claimable by ANY scope, so a null
             would quietly widen who may pick these up.

The `run` and `agent` tables are NOT touched here. Removing them is a separate
thread (T4 of `coherence-overhaul-2026-08-16.md`) and this migration is a
prerequisite for it, not part of it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e9c4a71f0d38"
down_revision = "b2f6c0a9d713"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("latitude", sa.String(16)))
    op.add_column("ticket", sa.Column("max_attempts", sa.Integer()))
    op.add_column("ticket", sa.Column(
        "attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ticket", sa.Column("awaiting", JSONB()))
    op.add_column("ticket", sa.Column("assignee", sa.String(64)))

    op.create_check_constraint(
        "ck_ticket_latitude", "ticket",
        "latitude IS NULL OR latitude IN ('strict', 'standard', 'open')")
    # A cap of zero would park a ticket before its first attempt ever ran.
    op.create_check_constraint(
        "ck_ticket_max_attempts", "ticket",
        "max_attempts IS NULL OR max_attempts > 0")
    op.create_check_constraint("ck_ticket_attempts", "ticket", "attempts >= 0")

    # Seed the counter from the runs that started, which is what the read shape
    # has been returning. Soft-deleted tickets included: they are restorable and
    # a restored ticket with a reset counter would get its attempts back.
    op.execute("""
        UPDATE ticket t SET attempts = r.n
          FROM (SELECT ticket_id, count(*) AS n FROM run
                 WHERE ticket_id IS NOT NULL AND started_at IS NOT NULL
                 GROUP BY ticket_id) r
         WHERE t.id = r.ticket_id
    """)
    op.execute("UPDATE ticket SET assignee = 'operator' WHERE assignee IS NULL")


def downgrade() -> None:
    op.drop_constraint("ck_ticket_attempts", "ticket", type_="check")
    op.drop_constraint("ck_ticket_max_attempts", "ticket", type_="check")
    op.drop_constraint("ck_ticket_latitude", "ticket", type_="check")
    for column in ("assignee", "awaiting", "attempts", "max_attempts", "latitude"):
        op.drop_column("ticket", column)
