"""the serving process can resolve its own tenant

THE BUG THIS FIXES, because it is a design error and not a typo.

`services/tenancy.py` has to answer one question on every request: given the app
user id the backend already authenticated (a small integer from
`.runtime/auth.db`), which `organization.id` and `user_account.id` is that? It
asked it through `owner_session()`, and the backend DOES NOT HOLD the owner
credential -- deliberately, since 402e96cbbb79 moved it into its own file that
the service unit does not load, precisely so a request handler can never run as
a role that row security does not apply to.

So every tenant resolution raised in the running process, and the chat branch
control never rendered. It passed under pytest because the test shell had both
DSNs exported. That gap between the test environment and the serving one is the
whole lesson.

WHY NOT JUST LET THE APP ROLE SELECT IT. `user_account`, `organization` and
`membership` are RLS-protected with SELECT policies keyed on `app.current_user`
and `app.current_org` (7bf9ae20fe0d). This lookup is what PRODUCES those values,
so it cannot be filtered by them: with neither set, all three read as empty.
That is the classic bootstrap cycle, and the standard answer is a narrow
SECURITY DEFINER function rather than either a wider policy or a privileged
connection.

WHAT IT CAN AND CANNOT DO. It takes one integer and returns at most one
(org_id, user_id) pair. It selects no other column, exposes no other table, and
cannot be aimed at a different question. `SET search_path` is not optional on a
SECURITY DEFINER function: without it a caller who can create objects can shadow
a name inside the body and have it execute as the owner. EXECUTE is revoked from
PUBLIC and granted only to the application role.

Revision ID: d4e17c9b3a55
Revises: c1a7b8e3d204
"""
from collections.abc import Sequence

from alembic import op

revision: str = "d4e17c9b3a55"
down_revision: str | Sequence[str] | None = "c1a7b8e3d204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "workshop_app"


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION workshop_resolve_tenant(p_legacy integer)
        RETURNS TABLE (org_id uuid, user_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT m.org_id, u.id
              FROM user_account u
              JOIN membership m    ON m.user_id = u.id
              JOIN organization o  ON o.id = m.org_id
             WHERE u.legacy_user_id = p_legacy
               AND u.deleted_at IS NULL
               AND o.deleted_at IS NULL
             ORDER BY m.created_at ASC, m.org_id ASC
             LIMIT 1
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION workshop_resolve_tenant(integer) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION workshop_resolve_tenant(integer) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS workshop_resolve_tenant(integer)")
