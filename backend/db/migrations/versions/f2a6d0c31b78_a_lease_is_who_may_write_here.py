"""a lease is who may write here

Step B3 of docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md.

ONE TABLE FOR BOTH LEASES (section 5). The ticket lease -- who took this work --
and the worktree lease -- who may write to this tree -- are one mechanism with a
typed subject, not two implementations. Both are needed and section 5 is
explicit about why the ticket half is KEPT rather than folded away: a `todo`
ticket has no tree yet, so two sweeps would both see `todo`, both create a
worktree, and both work the same ticket.

THE TABLE IS BOOKKEEPING; THE MOUNT IS ENFORCEMENT (section 4). A fencing token
works because a storage layer validates it, and for a working tree the storage
layer is the filesystem, which validates nothing. Read-only mounts for sessions
without a lease are step B1. This migration adds the record of who holds what,
and nothing that physically stops a second writer.

THE ROW IS NEVER DELETED, and that is the one way this table differs from its
neighbours. `generation` is a fencing token and is worth something only while it
never goes backwards for a subject; deleting on release would restart it at 1
and a resurrected holder carrying generation 4 would match the fourth holder
after the reset. So release is an UPDATE that expires the row (section 5:
"Renew and release are `UPDATE ... WHERE generation = :g`") and there is no
`deleted_at`.

UNIQUE (subject_kind, subject_id) WITHOUT `org_id`, unlike every other unique
constraint in this schema. It is not a naming rule, it is the exclusion itself:
the subject is one physical thing, its id is already globally unique, and "one
holder per subject" has to mean one rather than one per tenant. RLS still
governs who may see or take a lease. It is a named constraint because the
atomic acquire names it as its ON CONFLICT target.

`expires_at` is NOT NULL and every writer computes it as `now() + interval`
inside Postgres. No caller's clock ever reaches this column, which is also the
answer to clock skew between the sweeps, the backend and a chat.

`status`-style CHECK constraints rather than native enums, matching every other
controlled vocabulary in this schema except `priority`.

Revision ID: f2a6d0c31b78
Revises: e7d4c1b90a63
Create Date: 2026-08-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6d0c31b78"
down_revision: str | Sequence[str] | None = "e7d4c1b90a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as every other policy in this schema (966ddcaa35ae). The
# two-argument current_setting is load-bearing: with one argument an unset
# variable raises rather than filtering.
_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

# Kept in step with db.models.TENANT_TABLES; enforced by
# tests/test_db_rls_coverage.py, which needs no database.
_TENANT_TABLES = ("lease",)


def upgrade() -> None:
    op.create_table(
        "lease",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        # TEXT, not a uuid FK, for the same reason `worktree.ticket_id` is:
        # Postgres holds `ticket` rows only for the two relational scopes, so a
        # foreign key would be a promise the data cannot keep.
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("holder_kind", sa.String(length=16), nullable=False),
        sa.Column("holder_id", sa.String(length=128), nullable=False),
        sa.Column("principal_user_id", sa.UUID(), nullable=True),
        sa.Column("generation", sa.Integer(), server_default="1",
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # Separate from `last_renewed_at` because section 6 makes them different
        # facts: a holder renews every 30s to keep a 90s lease alive, which says
        # the process is up rather than that the work is moving. Idle is "10
        # minutes with no WORK event", so it needs its own timestamp.
        sa.Column("last_work_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("subject_kind IN ('worktree', 'ticket')",
                           name="ck_lease_subject_kind"),
        sa.CheckConstraint("holder_kind IN ('chat', 'run')",
                           name="ck_lease_holder_kind"),
        sa.CheckConstraint("generation > 0",
                           name="ck_lease_generation_positive"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        # `user_account` is global rather than per-organization, so this is a
        # plain single-column reference; the composite `_org_ref` form does not
        # apply to a table with no `org_id`.
        sa.ForeignKeyConstraint(["principal_user_id"], ["user_account.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_lease_id_org"),
        # THE EXCLUSION. The acquire statement names this constraint as its
        # ON CONFLICT target, so it is named rather than inferred.
        sa.UniqueConstraint("subject_kind", "subject_id",
                            name="uq_lease_subject"),
    )
    # "What does this session hold", which is what section 6's join rule needs
    # to release an interactive holder's leases the moment its presence goes.
    op.create_index("ix_lease_holder", "lease",
                    ["org_id", "holder_kind", "holder_id"])
    op.create_index("ix_lease_live", "lease", ["org_id", "expires_at"])

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})"
        )
        # The ORM's `onupdate` is an ORM-level hook and does not fire for raw
        # SQL, and every write in `db/lease_repo.py` is raw SQL (402e96cbbb79
        # found this the hard way for the earlier tables).
        op.execute(f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON "
                   f"{table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("lease")
