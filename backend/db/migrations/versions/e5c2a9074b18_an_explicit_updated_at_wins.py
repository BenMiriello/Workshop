"""an explicit updated_at wins over the trigger

`set_updated_at()` set `NEW.updated_at = now()` on every UPDATE unconditionally,
so a caller could never supply one. That is right for an ordinary write and
wrong for a migration, which is carrying a timestamp that already happened.

Measured cost before the fix: reading all 109 tickets out of Postgres instead of
the file changed `updated` on 109 of 109 and `created` on 11, because both were
the row's insert time rather than the ticket's own. `updated` is user-visible
and every list sorts by it, so the flip would have relabelled the whole backlog
as modified at the moment of the migration.

The fix is the standard one: only stamp `now()` when the statement did not set
the column itself. An ordinary UPDATE is unaffected (it does not mention
`updated_at`, so NEW and OLD match and the trigger fires); an explicit
`SET updated_at = X` is honoured.

`IS NOT DISTINCT FROM` rather than `=` so a NULL on either side compares the way
a person means it.
"""
from alembic import op

revision = "e5c2a9074b18"
down_revision = "d7b3e0c5f981"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
                NEW.updated_at = now();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
