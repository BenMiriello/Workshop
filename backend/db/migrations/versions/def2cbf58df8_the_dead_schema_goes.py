"""the dead schema goes

T4 of `docs/platform-redesign/coherence-overhaul-2026-08-16.md`, sequenced
after S0 (e9c4a71f0d38) so the field-loss fix landed before this migration,
per the doc's own correction to the review's plan.

MEASURED TODAY, before dropping anything:

  run                    2,753 rows. Zero readers since e9c4a71f0d38 stopped
                          deriving `ticket.attempts` from `count(run)`; its
                          only writer is the one-shot `db/backfill.py`
                          (newest row 2026-08-05, while the queue kept moving
                          past it). `agent_id` is NULL on all 2,753.
  agent                   8 rows (from `build_agents.json`). Zero reads, zero
                          writes, anywhere in the application.
  comment.author_agent_id 0 of however-many non-NULL.
  ticket_event.actor_agent_id  0 non-NULL.
  ticket.sort_order        135 rows, 1 distinct value (the column default).
                          Nothing reads or writes it outside this column's
                          own definition.
  chat_thread.title_source / .mcp_servers_enabled / .pre_plan_permission_mode
                          88 threads. 14/0/0 non-NULL respectively.
                          `chat_repo.sync_thread_fields`, the one live mirror
                          from the file store into this table, carries none
                          of the three; `title_source` was written once, by
                          the one-shot `db/chat_backfill.py`, and the other
                          two were never written here at all.

WHAT REPLACES `run` FOR THE FIELDS THAT HAD A LIVE WRITER. The compatibility
read shape (`db/repo.py`) derived four things from `run`: `job_ids` (every
run's `legacy_job_id`) and the claim liveness triple `lease_until` /
`last_heartbeat` / `claim_pid` (the newest run's). `tasks.claim()` and
`tasks.heartbeat()` write all four on a relational-scope ticket through the
same `PATCH /tasks/{id}` -> `_persist` -> `ticket_sync.mirror` path as
`attempts`/`awaiting`/`assignee` -- and `ticket_sync` was not tracking any of
the four, so a claim's own lease/heartbeat/pid/job-id landed nowhere. That is
the exact defect e9c4a71f0d38 fixed for the other five fields, so the same
fix applies: real columns on `ticket`, matching the JSON store's own field
names, TRACKED (not merely mirrored) in `ticket_sync._TRACKED` so a
heartbeat-only write still issues an UPDATE instead of freezing at the first
one.

Checked before deciding this rather than assuming it: every run's
`lease_expires_at`/`heartbeat_at`/`worker_pid` is NULL (nothing has ever
heartbeated a relational-scope claim), so there is nothing to backfill into
the three new columns. `job_ids` DOES have live data -- 57 run rows carry
both `ticket_id` and `legacy_job_id` -- and is seeded from it below.

WHY `agent` IS SAFE TO DROP OUTRIGHT, with no replacement column anywhere.
Unlike `run`, nothing ever wrote through `comment.author_agent_id` or
`ticket_event.actor_agent_id` (0 of both, live). There is no in-flight write
this migration could orphan.

Reversible: the downgrade recreates `agent` and `run` in the shape they were
in immediately before this migration (not their original 966ddcaa35ae shape;
`run.claim_token` and `run.parent_id` were added by later migrations and are
included), re-adds the three dropped columns each to `comment`,
`ticket_event`, `ticket.sort_order` and the three `chat_thread` columns, and
drops the four new `ticket` claim columns. Proved by upgrade -> downgrade ->
upgrade on a throwaway database built from the full chain, the same method
e9c4a71f0d38 used.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID

revision = "def2cbf58df8"
down_revision = "e9c4a71f0d38"
branch_labels = None
depends_on = None

_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def upgrade() -> None:
    # --- 1. give the claim fields a real home on `ticket`, before `run` goes ---
    op.add_column("ticket", sa.Column("job_ids", ARRAY(sa.Text())))
    op.add_column("ticket", sa.Column(
        "lease_until", sa.DateTime(timezone=True)))
    op.add_column("ticket", sa.Column(
        "last_heartbeat", sa.DateTime(timezone=True)))
    op.add_column("ticket", sa.Column("claim_pid", sa.Integer()))

    # Seed `job_ids` from the same rows the compatibility shape was deriving
    # it from. `lease_until`/`last_heartbeat`/`claim_pid` are NOT seeded: every
    # one of them is NULL on every row of `run` today (verified above), so
    # there is nothing to carry forward and seeding would only be inventing a
    # claim that was never live.
    op.execute("""
        UPDATE ticket t SET job_ids = sub.ids
          FROM (SELECT ticket_id, array_agg(legacy_job_id ORDER BY started_at) AS ids
                  FROM run
                 WHERE ticket_id IS NOT NULL AND legacy_job_id IS NOT NULL
                 GROUP BY ticket_id) sub
         WHERE t.id = sub.ticket_id
    """)

    # --- 2. the agent-identity columns nothing ever wrote ----------------------
    op.execute("ALTER TABLE comment DROP COLUMN author_agent_id")
    op.execute("ALTER TABLE ticket_event DROP COLUMN actor_agent_id")
    # Dropping the column drops `comment_author_agent_id_org_fkey` and
    # `ticket_event_actor_agent_id_org_fkey` with it (they exist only because
    # of the column), and also drops `ck_comment_actor_is_consistent` /
    # `ck_ticket_event_actor_is_consistent`, which named it. Recreated below
    # without the `agent` branch: `author_type`/`actor_type` still allow the
    # literal 'agent' (nothing currently writes it; see models.py), but there
    # is no id column left for it to be consistent WITH, so the invariant left
    # is simply "user names a user, anything else names nobody".
    op.execute("""
        ALTER TABLE comment ADD CONSTRAINT ck_comment_actor_is_consistent CHECK (
            (author_type = 'user' AND author_user_id IS NOT NULL)
         OR (author_type != 'user' AND author_user_id IS NULL))
    """)
    op.execute("""
        ALTER TABLE ticket_event ADD CONSTRAINT ck_ticket_event_actor_is_consistent CHECK (
            (actor_type = 'user' AND actor_user_id IS NOT NULL)
         OR (actor_type != 'user' AND actor_user_id IS NULL))
    """)

    # --- 3. the tables themselves -----------------------------------------------
    # `run` first: it holds the last FK pointing at `agent`
    # (`run_agent_id_org_fkey`), so dropping it removes that dependency before
    # `agent` is dropped. DROP TABLE removes the table's own indexes, checks,
    # the RLS policy and (for `run`) the FK to `agent` and to `ticket`
    # automatically; nothing outside either table references them any more.
    op.drop_table("run")
    op.drop_table("agent")

    # --- 4. the confirmed-dead columns ------------------------------------------
    op.drop_column("ticket", "sort_order")
    op.drop_column("chat_thread", "title_source")
    op.drop_column("chat_thread", "mcp_servers_enabled")
    op.drop_column("chat_thread", "pre_plan_permission_mode")


def downgrade() -> None:
    # --- 4. the confirmed-dead columns, restored empty --------------------------
    op.add_column("chat_thread", sa.Column(
        "pre_plan_permission_mode", sa.String(length=32)))
    op.add_column("chat_thread", sa.Column(
        "mcp_servers_enabled", ARRAY(sa.String(length=64))))
    op.add_column("chat_thread", sa.Column("title_source", sa.String(length=16)))
    op.add_column("ticket", sa.Column(
        "sort_order", sa.Float(), server_default="65535", nullable=False))

    # --- 3. the tables themselves, in the shape they were in just before this
    # migration (post-402e96cbbb79 composite FKs, post-a3f1c72b9e40 SET NULL
    # column lists, post-b8d2e5a14c73 run.parent_id, post-d7b3e0c5f981
    # run.claim_token) -----------------------------------------------------------
    op.create_table(
        "agent",
        sa.Column("id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("org_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("autonomy", sa.String(length=32), server_default="reactive",
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "autonomy IN ('reactive', 'supervised', 'autonomous')",
            name="ck_agent_autonomy"),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('maker', 'reviewer', 'triager', "
            "'keeper', 'broker')", name="ck_agent_role"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_agent_id_org"),
    )
    op.create_index("uq_agent_slug", "agent", ["org_id", "slug"], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "run",
        sa.Column("id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("org_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("ticket_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("parent_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("attempt_no", sa.Integer(), server_default="1",
                  nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_pid", sa.Integer(), nullable=True),
        sa.Column("claim_token", sa.Integer(), nullable=True),
        sa.Column("legacy_job_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'blocked', 'done', 'failed', "
            "'cancelled')", name="ck_run_status"),
        sa.CheckConstraint(
            "trigger IN ('manual', 'cron', 'event', 'loop')",
            name="ck_run_trigger"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id",
                           name="ck_run_not_own_parent"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", name="uq_run_id_org"),
    )
    op.execute(
        "ALTER TABLE run ADD CONSTRAINT run_ticket_id_org_fkey "
        "FOREIGN KEY (ticket_id, org_id) REFERENCES ticket (id, org_id) "
        "ON DELETE CASCADE")
    op.execute(
        "ALTER TABLE run ADD CONSTRAINT run_agent_id_org_fkey "
        "FOREIGN KEY (agent_id, org_id) REFERENCES agent (id, org_id) "
        "ON DELETE SET NULL (agent_id)")
    op.execute(
        "ALTER TABLE run ADD CONSTRAINT run_parent_id_org_fkey "
        "FOREIGN KEY (parent_id, org_id) REFERENCES run (id, org_id) "
        "ON DELETE SET NULL (parent_id)")
    op.create_index("ix_run_legacy", "run", ["legacy_job_id"])
    op.create_index("ix_run_org_started", "run", ["org_id", "started_at"])
    op.create_index("ix_run_ticket", "run", ["ticket_id", "started_at"])
    op.create_index("ix_run_parent", "run", ["parent_id", "started_at"])

    for table in ("agent", "run"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_ORG}) WITH CHECK (org_id = {_ORG})")
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()")

    # --- 2. the agent-identity columns, restored empty --------------------------
    op.execute("ALTER TABLE comment DROP CONSTRAINT ck_comment_actor_is_consistent")
    op.execute(
        "ALTER TABLE ticket_event DROP CONSTRAINT ck_ticket_event_actor_is_consistent")
    op.add_column("comment", sa.Column("author_agent_id", PgUUID(as_uuid=True)))
    op.add_column("ticket_event", sa.Column("actor_agent_id", PgUUID(as_uuid=True)))
    op.execute(
        "ALTER TABLE comment ADD CONSTRAINT comment_author_agent_id_org_fkey "
        "FOREIGN KEY (author_agent_id, org_id) REFERENCES agent (id, org_id) "
        "ON DELETE SET NULL (author_agent_id)")
    op.execute(
        "ALTER TABLE ticket_event ADD CONSTRAINT ticket_event_actor_agent_id_org_fkey "
        "FOREIGN KEY (actor_agent_id, org_id) REFERENCES agent (id, org_id) "
        "ON DELETE SET NULL (actor_agent_id)")
    op.execute("""
        ALTER TABLE comment ADD CONSTRAINT ck_comment_actor_is_consistent CHECK (
            (author_type = 'user' AND author_user_id IS NOT NULL AND author_agent_id IS NULL)
         OR (author_type = 'agent' AND author_agent_id IS NOT NULL AND author_user_id IS NULL)
         OR (author_type = 'system' AND author_user_id IS NULL AND author_agent_id IS NULL))
    """)
    op.execute("""
        ALTER TABLE ticket_event ADD CONSTRAINT ck_ticket_event_actor_is_consistent CHECK (
            (actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_agent_id IS NULL)
         OR (actor_type = 'agent' AND actor_agent_id IS NOT NULL AND actor_user_id IS NULL)
         OR (actor_type = 'system' AND actor_user_id IS NULL AND actor_agent_id IS NULL))
    """)

    # --- 1. the claim fields, no longer needed once `run` is back --------------
    op.drop_column("ticket", "claim_pid")
    op.drop_column("ticket", "last_heartbeat")
    op.drop_column("ticket", "lease_until")
    op.drop_column("ticket", "job_ids")
