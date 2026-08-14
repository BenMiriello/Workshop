"""every ticket field gets a home

Stage 1 of `docs/platform-redesign/tickets-one-store-2026-08-14.md`. Postgres
becomes the ticket store and `.tasks.json` is retired, so every field the JSON
record carried needs somewhere real to live. Occupancy measured across all 112
live tickets in both scopes; nothing here was decided from the shape of the code.

COLUMNS, for fields that are current state and get read on the hot path:

  source_kind/source_ref  112/112. `{kind: "chat", ref: <thread id>}` is the
                          chat-to-ticket link. Carried as an unindexed blob
                          until now, which is why "which tickets did this
                          conversation file" has never been a query. Indexed
                          here, so it becomes one.
  spec_hash               56/112. The hash over the spec fields only (not
                          `updated`), which is what lets a queue triager decide
                          it has seen a ticket already and stop.
  touches                 35/112. The paths a worker may change.
  blocked_reason          23/112. Why it is stuck. A blocked ticket that has
                          lost this is a failure the backfill review already
                          caught once.
  acceptance              5/112, jsonb. Genuinely nested
                          (`{kind, spec, criteria[]}`) and nothing filters on
                          it, so a column per key would be modelling for its
                          own sake.

THE CLAIM FENCE, which is the last thing blocking P4:

  ticket.claim_token      the allocator, bumped on every (re)claim.
  run.claim_token         the generation a run holds.

  `lease_expires_at` + `heartbeat_at` + `worker_pid` already covered liveness.
  They do not cover compare-and-swap, and a held-but-unchecked token does not
  stop a resurrected worker's late write from landing over newer work. Split
  across the two tables because the ticket allocates and the execution holds.

AN EDGE, not a scalar:

  discovered_from         2/112. It names another ticket, so it is a
                          `ticket_link` kind like every other ticket-to-ticket
                          reference.

Deliberately NOT added, and this is the measured part: `assignee` and `owner`
are both the literal string "operator" on all 112 tickets in both scopes. Two
fields, one concept, no information, and `ticket.assignee_id` already exists for
when assignment becomes real. `attempts` becomes `count(run)`. `scoped_by` and
`evidence` become `ticket_event` rows, because each is an actor doing a thing at
a time and that table already has the shape.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "d7b3e0c5f981"
down_revision = "c4a9f18d6b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("source_kind", sa.String(16)))
    op.add_column("ticket", sa.Column("source_ref", sa.String(255)))
    op.add_column("ticket", sa.Column("spec_hash", sa.String(64)))
    op.add_column("ticket", sa.Column("touches", ARRAY(sa.Text())))
    op.add_column("ticket", sa.Column("blocked_reason", sa.Text()))
    op.add_column("ticket", sa.Column("acceptance", JSONB()))
    op.add_column("ticket", sa.Column(
        "claim_token", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("run", sa.Column("claim_token", sa.Integer()))

    op.execute(
        "ALTER TABLE ticket ADD CONSTRAINT ck_ticket_source_kind "
        "CHECK (source_kind IS NULL OR "
        "       source_kind IN ('chat','operator','agent'))")

    # The query this exists for: every ticket a given chat filed. Partial on
    # `chat` because that is the only kind with a ref worth looking up, and it
    # keeps the index off the 93 tickets whose source is an operator or an agent
    # with a null ref.
    op.execute(
        "CREATE INDEX ix_ticket_source_chat ON ticket (org_id, source_ref) "
        "WHERE source_kind = 'chat' AND source_ref IS NOT NULL "
        "  AND deleted_at IS NULL")

    op.execute("ALTER TABLE ticket_link DROP CONSTRAINT ck_ticket_link_kind")
    op.execute(
        "ALTER TABLE ticket_link ADD CONSTRAINT ck_ticket_link_kind "
        "CHECK (kind IN ('blocked_by','relates_to','duplicates',"
        "'discovered_from'))")


def downgrade() -> None:
    op.execute("ALTER TABLE ticket_link DROP CONSTRAINT ck_ticket_link_kind")
    op.execute(
        "ALTER TABLE ticket_link ADD CONSTRAINT ck_ticket_link_kind "
        "CHECK (kind IN ('blocked_by','relates_to','duplicates'))")
    op.execute("DROP INDEX ix_ticket_source_chat")
    op.execute("ALTER TABLE ticket DROP CONSTRAINT ck_ticket_source_kind")
    op.drop_column("run", "claim_token")
    for col in ("claim_token", "acceptance", "blocked_reason", "touches",
                "spec_hash", "source_ref", "source_kind"):
        op.drop_column("ticket", col)
