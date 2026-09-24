"""close the membership escalation and unprotected identity tables

Adversarial review of 966ddcaa35ae, 2026-08-13. Both findings were reproduced
against the live database, not inferred.

FINDING 1, CRITICAL: privilege escalation through `membership_self`.

That policy was created `FOR ALL` (the default) with a `WITH CHECK`. Postgres ORs
permissive policies, so `WITH CHECK (user_id = app.current_user)` applied to
INSERT and UPDATE as well as SELECT. Any authenticated caller could therefore
write a membership row naming themselves, in ANY organization:

    with user_session(me):            # a session cookie, no org at all
        INSERT INTO membership (org_id, user_id, role)
        VALUES (<someone else's org>, me, 'owner')      -> SUCCEEDED

The read-side justification is still correct: a user who has just logged in has
no org yet and must be able to see which organizations they may enter. That
requires SELECT and nothing more. Narrowing the policy to `FOR SELECT` leaves
membership writes to `membership_org_isolation`, which requires already being
inside the organization.

FINDING 2, CRITICAL: `organization` and `user_account` had no RLS at all, while
`workshop_app` held full DML on both.

From inside one tenant: every other organization's row was readable, every other
customer's email was readable, `UPDATE organization SET name=...` on another
tenant applied, and because every tenant table cascades from `organization`, a
`DELETE FROM organization` with a forgotten WHERE erases every tenant in the
database.

The original rationale was that a user must see their organizations before a
tenant context exists. That is true, and it is what the `membership`-based
policy below expresses; it never required leaving the tables unprotected.

Provisioning (creating an organization, creating a user) is an ADMIN operation
and now runs through the owner connection. The application role loses INSERT,
UPDATE and DELETE on both tables, so a bug in a request handler cannot create or
destroy tenants.

Also revokes write on `alembic_version`, which `GRANT ... ON ALL TABLES` had
swept up: the app cannot run DDL, but it could rewrite the migration stamp.

Revision ID: 7bf9ae20fe0d
Revises: 769d64dbfa74
"""
from collections.abc import Sequence

from alembic import op

revision: str = "7bf9ae20fe0d"
down_revision: str | Sequence[str] | None = "769d64dbfa74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
_USR = "nullif(current_setting('app.current_user', true), '')::uuid"

APP_ROLE = "workshop_app"


def upgrade() -> None:
    # --- 1. membership: read-only self-visibility --------------------------
    op.execute("DROP POLICY IF EXISTS membership_self ON membership")
    op.execute(
        "CREATE POLICY membership_self ON membership "
        f"FOR SELECT USING (user_id = {_USR})"
    )

    # --- 2. organization ----------------------------------------------------
    # Visible when it is the tenant currently in context, OR when the acting
    # user is a member of it. The second disjunct is the login bootstrap: it is
    # what makes "which organizations may I enter?" answerable with no org set.
    #
    # The subquery reads `membership`, which is itself RLS-protected. That is
    # fine and not circular: membership's own policies do not reference
    # organization, and `membership_self` exposes exactly the caller's rows.
    op.execute("ALTER TABLE organization ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization FORCE  ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY organization_visible ON organization
        FOR SELECT USING (
            id = {_ORG}
            OR EXISTS (SELECT 1 FROM membership m
                       WHERE m.org_id = organization.id AND m.user_id = {_USR})
        )
    """)

    # --- 3. user_account ----------------------------------------------------
    # Yourself, or someone you share an organization with. A tenant must be able
    # to render an assignee's name; it has no business enumerating the customer
    # list.
    op.execute("ALTER TABLE user_account ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_account FORCE  ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY user_account_visible ON user_account
        FOR SELECT USING (
            id = {_USR}
            OR EXISTS (SELECT 1 FROM membership mine
                       JOIN membership theirs ON theirs.org_id = mine.org_id
                       WHERE mine.user_id = {_USR} AND theirs.user_id = user_account.id)
        )
    """)

    # --- 4. take provisioning away from the application role ----------------
    # On a fresh database the role does not exist yet: `db.bootstrap` creates
    # it, and bootstrap cannot run before `ticket_event` exists, which is a
    # later revision. So a one-step `alembic upgrade head` failed here. Create
    # it bare (NOLOGIN, no privileges) so this revision and the later grants to
    # it apply; bootstrap then gives it a password, LOGIN and its DML grants.
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$
    """)
    for table in ("organization", "user_account", "alembic_version"):
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {table} FROM {APP_ROLE}")
    # And for tables created by future migrations, the default grant would hand
    # these back. Nothing to do for existing ones, but state the intent: the
    # default privileges deliberately stay broad, and any table that must be
    # admin-only is revoked explicitly here, as these three are.


def downgrade() -> None:
    for table in ("organization", "user_account", "alembic_version"):
        op.execute(f"GRANT INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS user_account_visible ON user_account")
    op.execute("ALTER TABLE user_account NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_account DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS organization_visible ON organization")
    op.execute("ALTER TABLE organization NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS membership_self ON membership")
    op.execute(
        "CREATE POLICY membership_self ON membership "
        f"USING (user_id = {_USR}) WITH CHECK (user_id = {_USR})"
    )
