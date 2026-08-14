"""harden the schema after adversarial review

Two independent reviews of 966ddcaa35ae, 2026-08-13. Every finding below was
REPRODUCED against the live database by the reviewer, not inferred.

1. CROSS-TENANT FOREIGN KEYS. Postgres runs referential-integrity checks with
   row security OFF, so `WITH CHECK (org_id = current_org)` constrains the label
   on a row and says nothing about what that row POINTS AT. All of these
   succeeded from inside one tenant:

       ticket with another org's project_id      -> allowed
       ticket with another org's state_id        -> allowed
       ticket with another org's parent_id       -> allowed
       ticket_link from my ticket to theirs      -> allowed
       comment attached to another org's ticket  -> allowed

   No cross-tenant READ falls out (a join back through `ticket` is re-filtered),
   but three real consequences do: an existence oracle (a real UUID raises no
   FK violation, a fabricated one does), cross-tenant writes that cascade-delete
   with the attacker's org, and a reproduced cross-tenant DENIAL OF SERVICE:
   after tenant B deleted all its own tickets it still could not delete its own
   workflow state, because tenant A held a hidden reference. B cannot see the
   blocker and gets an unactionable RESTRICT error.

   The fix is structural: `UNIQUE (id, org_id)` on every parent, and composite
   `(child_col, org_id) REFERENCES parent (id, org_id)` on every child. Org
   agreement stops being a convention and becomes a constraint.

2. SELF-CONTRADICTORY ACTOR ROWS, 117 of them live. `author_type`/`actor_type`
   were CHECKed against the vocabulary, but nothing tied the value to which id
   column was populated, so every comment claimed a human author with a NULL
   author id. Repaired here, then constrained.

3. `updated_at` NEVER FIRED FOR RAW SQL. `onupdate=func.now()` is an ORM-level
   hook. `ix_ticket_updated` and the default list order both depend on that
   column being honest, and any `UPDATE` not issued through the ORM left it
   stale. Now a BEFORE UPDATE trigger, with the ORM hook kept as belt and
   braces.

4. NO CYCLE PROTECTION. `A parent B` + `B parent A` was accepted, as was
   `A blocked_by B` + `B blocked_by A`, which makes both tickets permanently
   ineligible for the scheduler with no diagnostic. ADR 0001 ratified a
   hierarchy depth cap of 2 and nothing enforced it.

5. NO SINGLE DEFAULT. A project could have zero or five `is_default` states, so
   ticket creation had no defined fallback.

Revision ID: 402e96cbbb79
Revises: 7bf9ae20fe0d
"""
from collections.abc import Sequence

from alembic import op

revision: str = "402e96cbbb79"
down_revision: str | Sequence[str] | None = "7bf9ae20fe0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (child table, child column, parent table, old constraint name, ondelete)
_COMPOSITE = [
    ("workflow_state", "project_id", "project", "workflow_state_project_id_fkey", "CASCADE"),
    ("ticket_type", "project_id", "project", "ticket_type_project_id_fkey", "CASCADE"),
    ("label", "project_id", "project", "label_project_id_fkey", "CASCADE"),
    ("ticket", "project_id", "project", "ticket_project_id_fkey", "CASCADE"),
    ("ticket", "state_id", "workflow_state", "ticket_state_id_fkey", "RESTRICT"),
    ("ticket", "type_id", "ticket_type", "ticket_type_id_fkey", "SET NULL"),
    ("ticket", "parent_id", "ticket", "ticket_parent_id_fkey", "SET NULL"),
    ("ticket_link", "from_ticket_id", "ticket", "ticket_link_from_ticket_id_fkey", "CASCADE"),
    ("ticket_link", "to_ticket_id", "ticket", "ticket_link_to_ticket_id_fkey", "CASCADE"),
    ("ticket_label", "ticket_id", "ticket", "ticket_label_ticket_id_fkey", "CASCADE"),
    ("ticket_label", "label_id", "label", "ticket_label_label_id_fkey", "CASCADE"),
    ("comment", "ticket_id", "ticket", "comment_ticket_id_fkey", "CASCADE"),
    ("comment", "parent_id", "comment", "comment_parent_id_fkey", "CASCADE"),
    ("comment", "author_agent_id", "agent", "comment_author_agent_id_fkey", "SET NULL"),
    ("ticket_event", "ticket_id", "ticket", "ticket_event_ticket_id_fkey", "CASCADE"),
    ("ticket_event", "comment_id", "comment", "ticket_event_comment_id_fkey", "SET NULL"),
    ("ticket_event", "actor_agent_id", "agent", "ticket_event_actor_agent_id_fkey", "SET NULL"),
    ("run", "ticket_id", "ticket", "run_ticket_id_fkey", "CASCADE"),
    ("run", "agent_id", "agent", "run_agent_id_fkey", "SET NULL"),
]

# Parents that must expose (id, org_id) for the composite references above.
_PARENTS = ("project", "workflow_state", "ticket_type", "ticket", "label",
            "comment", "agent")


def upgrade() -> None:
    # --- 1. composite foreign keys -----------------------------------------
    for parent in _PARENTS:
        op.execute(f"ALTER TABLE {parent} ADD CONSTRAINT uq_{parent}_id_org "
                   f"UNIQUE (id, org_id)")

    for child, col, parent, old_name, ondelete in _COMPOSITE:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT {old_name}")
        # MATCH SIMPLE (the default) is what we want: when the child column is
        # NULL the constraint does not fire, which is correct for the optional
        # references (type_id, parent_id, agent ids).
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {child}_{col}_org_fkey "
            f"FOREIGN KEY ({col}, org_id) REFERENCES {parent} (id, org_id) "
            f"ON DELETE {ondelete}"
        )

    # --- 2. repair, then constrain, the actor columns -----------------------
    # 117 live rows claimed a human or agent actor with no actor id. Where the
    # organization has exactly one member, attribute to them; otherwise the
    # honest answer is `system`, because the actor genuinely is not recorded.
    for table, kind_col, user_col, agent_col in (
        ("comment", "author_type", "author_user_id", "author_agent_id"),
        ("ticket_event", "actor_type", "actor_user_id", "actor_agent_id"),
    ):
        op.execute(f"""
            UPDATE {table} t SET {user_col} = sole.user_id
            FROM (SELECT org_id, (array_agg(user_id))[1] AS user_id
                    FROM membership GROUP BY org_id HAVING count(*) = 1) sole  -- min() has no uuid overload
            WHERE t.org_id = sole.org_id
              AND t.{kind_col} = 'user' AND t.{user_col} IS NULL
        """)
        op.execute(f"""
            UPDATE {table} SET {kind_col} = 'system'
            WHERE ({kind_col} = 'user'  AND {user_col}  IS NULL)
               OR ({kind_col} = 'agent' AND {agent_col} IS NULL)
        """)
        op.execute(f"""
            ALTER TABLE {table} ADD CONSTRAINT ck_{table}_actor_is_consistent CHECK (
                ({kind_col} = 'user'   AND {user_col} IS NOT NULL AND {agent_col} IS NULL)
             OR ({kind_col} = 'agent'  AND {agent_col} IS NOT NULL AND {user_col} IS NULL)
             OR ({kind_col} = 'system' AND {user_col} IS NULL AND {agent_col} IS NULL))
        """)

    # --- 3. updated_at, in the database ------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("organization", "user_account", "membership", "project",
                  "workflow_state", "ticket_type", "ticket", "ticket_link",
                  "label", "comment", "agent", "run"):
        op.execute(f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()")

    # --- 4. cycles ----------------------------------------------------------
    # Hierarchy: ADR 0001 caps depth at 2, so a parent may not itself have a
    # parent. That single rule makes cycles impossible without a recursive walk.
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_parent_depth_guard() RETURNS trigger AS $$
        DECLARE grandparent uuid;
        BEGIN
            IF NEW.parent_id IS NULL THEN RETURN NEW; END IF;
            SELECT parent_id INTO grandparent FROM ticket WHERE id = NEW.parent_id;
            IF grandparent IS NOT NULL THEN
                RAISE EXCEPTION 'sub-ticket depth is capped at 2 (ADR 0001): % already has a parent',
                    NEW.parent_id USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM ticket WHERE id = NEW.parent_id AND parent_id = NEW.id) THEN
                RAISE EXCEPTION 'parent cycle' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER ticket_parent_depth BEFORE INSERT OR UPDATE ON ticket "
               "FOR EACH ROW EXECUTE FUNCTION ticket_parent_depth_guard()")

    # Dependencies: a full recursive walk, because blocked_by chains are not
    # depth-capped. A cycle here makes every ticket in it permanently ineligible
    # for the scheduler, with no error and no diagnostic.
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_link_cycle_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.kind <> 'blocked_by' OR NEW.deleted_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            IF EXISTS (
                WITH RECURSIVE reach(id) AS (
                    SELECT NEW.to_ticket_id
                    UNION
                    SELECT l.to_ticket_id FROM ticket_link l JOIN reach r ON l.from_ticket_id = r.id
                    WHERE l.kind = 'blocked_by' AND l.deleted_at IS NULL
                )
                SELECT 1 FROM reach WHERE id = NEW.from_ticket_id
            ) THEN
                RAISE EXCEPTION 'blocked_by cycle: % already blocks %',
                    NEW.to_ticket_id, NEW.from_ticket_id USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER ticket_link_cycle BEFORE INSERT OR UPDATE ON ticket_link "
               "FOR EACH ROW EXECUTE FUNCTION ticket_link_cycle_guard()")

    # --- 5. exactly one default per project --------------------------------
    for table in ("workflow_state", "ticket_type"):
        op.execute(f"CREATE UNIQUE INDEX uq_{table}_one_default ON {table} (project_id) "
                   f"WHERE is_default AND deleted_at IS NULL")


def downgrade() -> None:
    for table in ("workflow_state", "ticket_type"):
        op.execute(f"DROP INDEX IF EXISTS uq_{table}_one_default")

    op.execute("DROP TRIGGER IF EXISTS ticket_link_cycle ON ticket_link")
    op.execute("DROP FUNCTION IF EXISTS ticket_link_cycle_guard()")
    op.execute("DROP TRIGGER IF EXISTS ticket_parent_depth ON ticket")
    op.execute("DROP FUNCTION IF EXISTS ticket_parent_depth_guard()")

    for table in ("organization", "user_account", "membership", "project",
                  "workflow_state", "ticket_type", "ticket", "ticket_link",
                  "label", "comment", "agent", "run"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    for table in ("comment", "ticket_event"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_actor_is_consistent")

    for child, col, parent, old_name, ondelete in reversed(_COMPOSITE):
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT {child}_{col}_org_fkey")
        op.execute(f"ALTER TABLE {child} ADD CONSTRAINT {old_name} "
                   f"FOREIGN KEY ({col}) REFERENCES {parent} (id) ON DELETE {ondelete}")

    for parent in _PARENTS:
        op.execute(f"ALTER TABLE {parent} DROP CONSTRAINT uq_{parent}_id_org")
