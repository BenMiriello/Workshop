"""a null updated_at means no opinion, not null

Follow-up to e5c2a9074b18, which taught `set_updated_at()` to honour a value the
statement supplied itself. It honoured NULL too, and that was wrong.

`updated_at` is NOT NULL on every table that has the trigger, so NULL can never
be a legitimate target: a statement that passes it is saying "I am not setting
this", not "set it to nothing". The previous, unconditional trigger silently
rescued those writes by overwriting with now(). Removing the rescue turned them
into NotNullViolations.

Caught by two chat backfill tests in another session's work, which UPDATE a
thread with `updated_at = NULL` among the other columns:

    UPDATE chat_thread SET ..., updated_at = %(updated_at)s WHERE id = %(tid)s

Both conditions are now handled: an untouched column and an explicit NULL both
get now(); a real timestamp is kept, which is what the migration was for.
"""
from alembic import op

revision = "f1a7c3d904e2"
down_revision = "e5c2a9074b18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            IF NEW.updated_at IS NULL
               OR NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
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
            IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
                NEW.updated_at = now();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
