"""The Workshop relational schema (ADR 0019).

Design notes that are not obvious from the columns:

**Every tenant-scoped table carries `org_id` and is protected by RLS.** The
policies and the FORCE flag are applied in the migration, not here, because they
are DDL that SQLAlchemy does not model. `db.engine.verify_rls()` asserts at
runtime that no table with an `org_id` escaped that treatment.

**States are rows, not an enum.** `workflow_state` is what lets a tenant name
its own workflow while integrations and the scheduler still reason over a fixed
`category`. This is the Linear/Jira model and it is the reason "any type of
workflow" is configuration rather than a fork. The eight statuses the JSON store
hard-coded become seeded rows.

**Hierarchy is a column, other relations are rows.** `ticket.parent_id` is a
self-referencing FK; blocked-by/relates/duplicates live in `ticket_link` with a
`kind`. Plane models it exactly this way, and it means adding a relation type is
a new value rather than a migration.

**History is field-level.** `ticket_event` records (verb, field, old_value,
new_value), so an activity feed renders straight from a query instead of parsing
a JSON payload per row. It replaces five fields on the old JSON record that were
all "an actor did a thing at a time": source, scoped_by, provenance,
discovered_from and evidence.

**Comments are not events.** Comments are mutable, editable, threadable user
content; events are immutable history. Collapsing them would force either
mutable events or immutable comments.

**Soft delete with partial uniqueness.** `deleted_at` plus
`UNIQUE (...) WHERE deleted_at IS NULL`, so a name is reusable after deletion
without destroying the history that referenced it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Enum as SAEnum, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from db.ids import uuid7
# IMPORTED, not restated. `latitude` already has exactly one definition of its
# vocabulary and adding a second here is how the four definitions of the
# autonomy enum happened. `services.latitude` reaches only the stdlib and
# `wsplatform.paths` at module scope, so this does not drag the service layer
# into the schema.
from services.latitude import LATITUDES

# The JSON store owns the state vocabulary because it is the store that has to
# heal a hand-edited TODO.md against it; the schema follows. Same test as
# LATITUDES above: `services.tasks` reaches only the stdlib and
# `services.latitude` at module scope, so importing it here does not drag the
# service layer into the schema. This was two identical tuples until 2026-08-17;
# `services/tasks.py` asked for exactly this pointing-one-at-the-other.
from services.chat_store import CHAT_MODES
from services.tasks import EFFORTS, STATE_TYPES as STATE_CATEGORIES

# --- controlled vocabularies -------------------------------------------------
# CHECK constraints rather than native Postgres ENUM types, with ONE deliberate
# exception (priority, below, which needs enum ordering). A native enum cannot
# have a value removed, and altering one takes a lock; a CHECK is a one-line
# migration. The tuples here are the single source of truth for both sides.

# Relation kinds taken from Plane's IssueRelation rather than invented.
# `blocked_by` is the scheduling edge: it is the one that makes a ticket
# ineligible for selection. The others are informational.
# `discovered_from` names the ticket whose work turned this one up. It was a
# scalar field on the JSON record (2 of 112) and it is an edge like any other.
LINK_KINDS = ("blocked_by", "relates_to", "duplicates", "discovered_from")

# Priority. THE ONE PLACE A NATIVE POSTGRES ENUM IS USED, and it is deliberate.
#
# Everywhere else this schema prefers a CHECK constraint, because a native enum
# cannot have a value removed and altering one takes a lock. Priority is the
# exception because a native enum type SORTS BY DECLARATION ORDER: `ORDER BY
# priority` puts urgent first and none last with no CASE expression, no rank
# column, and no chance of a caller sorting alphabetically by accident and
# getting high, low, medium, none, urgent.
#
# It replaced a signed integer. The integer was not carrying meaning: 112 live
# tickets held 38 distinct values between 0 and 140, including 87, 92 and 94,
# distinctions nobody chose and nobody could act on. Five named levels are what
# Plane, Jira and Linear all settle on.
#
# ONE CAVEAT, and it is easy to hit. `ORDER BY` resolves an unqualified name
# against the SELECT's OUTPUT columns before the table's, so
#
#     SELECT priority::text AS priority FROM ticket ORDER BY priority
#
# sorts the TEXT alphabetically (high, low, medium, none, urgent) and silently
# restores the bug this type exists to prevent. Either do not reuse the column
# name for the cast, or qualify the ORDER BY (`ORDER BY t.priority`). Pinned in
# tests/test_db_schema.py.
PRIORITIES = ("urgent", "high", "medium", "low", "none")

ACTOR_TYPES = ("user", "agent", "system")
MEMBERSHIP_ROLES = ("owner", "admin", "member")

# Where a ticket came from. The JSON store's `source.kind`, which took exactly
# these three values across all 112 tickets. `chat` carries a thread id in
# `source_ref` and is the chat-to-ticket link.
SOURCE_KINDS = ("chat", "operator", "agent")

_ALIVE = text("deleted_at IS NULL")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """`col IN ('a','b')`. Built rather than interpolated from a tuple repr,
    because a one-element tuple reprs as `('a',)` and that is not valid SQL."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


def _org_target(table: str) -> UniqueConstraint:
    """`UNIQUE (id, org_id)`, so children can reference the pair.

    Postgres runs referential-integrity checks with row security OFF, so a
    policy's `WITH CHECK (org_id = current_org)` constrains the label on a row
    and says nothing about what that row POINTS AT. Composite FKs against this
    pair make org agreement a constraint rather than a convention. Introduced by
    migration 402e96cbbb79 after both findings were reproduced live.
    """
    return UniqueConstraint("id", "org_id", name=f"uq_{table}_id_org")


def _org_ref(child: str, column: str, target: str, *,
             ondelete: str) -> ForeignKeyConstraint:
    """`FOREIGN KEY (<column>, org_id) REFERENCES <target> (id, org_id)`.

    The other half of `_org_target`, and the reason this is a function rather
    than 15 hand-spelled `ForeignKeyConstraint`s: a single-column FK here is
    indistinguishable at a glance from a composite one, and the composite form
    is the only thing that stops a row in org A pointing at a row in org B.
    Between 2026-08-05 and 2026-08-17 the live database carried all fifteen and
    this file declared one, so `alembic revision --autogenerate` emitted a
    migration that dropped every one of them. Add a tenant-scoped reference
    through here and that cannot happen again.

    ON DELETE SET NULL NAMES ITS COLUMN. A composite key's bare `SET NULL` nulls
    every column in the key, `org_id` included, and `org_id` is NOT NULL -- so
    the delete raises instead of clearing the pointer. Postgres 15+ takes a
    column list; see the same note on `chat_branch`, where it was measured.
    """
    return ForeignKeyConstraint(
        [column, "org_id"], [f"{target}.id", f"{target}.org_id"],
        name=f"{child}_{column}_org_fkey", ondelete=ondelete)


def _actor_consistent(type_column: str, user_column: str,
                      name: str) -> CheckConstraint:
    """A `user` actor names a user row; an `agent` or `system` actor does not.

    Both halves matter. Without the first, a comment attributed to a person
    carries no person and the activity feed silently renders "user" with no
    name; without the second, a machine-written row can point at whichever
    account happened to be in scope, which is worse -- it is a false
    attribution that reads as a true one. Nullable-plus-CHECK rather than two
    tables, because the pair is one fact about one row.
    """
    return CheckConstraint(
        f"({type_column} = 'user' AND {user_column} IS NOT NULL) OR "
        f"({type_column} <> 'user' AND {user_column} IS NULL)", name=name)


def _one_default(table: str) -> Index:
    """At most one row per project may be the default. A partial unique index,
    because "default" is a property of the live set and a soft-deleted row must
    not hold the slot."""
    return Index(f"uq_{table}_one_default", "project_id", unique=True,
                 postgresql_where=text("is_default AND deleted_at IS NULL"))


def _external_target(table: str) -> Index:
    """`UNIQUE (org_id, external_source, external_id)` over live rows. How the
    mirror finds a row it already wrote, and what makes a re-run idempotent."""
    return Index(f"uq_{table}_external", "org_id", "external_source",
                 "external_id", unique=True,
                 postgresql_where=text(
                     "external_id IS NOT NULL AND deleted_at IS NULL"))


def _thread_target(table: str) -> UniqueConstraint:
    """`UNIQUE (id, thread_id)`, the same trick one level narrower.

    Inside a conversation, org agreement is not the property that matters:
    SAME THREAD is. A message whose parent lives in another thread, or a branch
    head pointing outside its own conversation, corrupts the path walk in a way
    no tenant check would catch, and it implies a cross-org reference anyway
    since a thread pins its own org. So intra-chat references are composite on
    the thread, and that single constraint covers both.
    """
    return UniqueConstraint("id", "thread_id", name=f"uq_{table}_id_thread")


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
    )


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- tenancy and identity ----------------------------------------------------

class Organization(Base, Timestamps, SoftDelete):
    """The tenant. Deliberately has no `org_id`: it IS the org, and so it is not
    RLS-protected. Access is by membership, checked in the application, because
    a user has to be able to see which organizations they belong to before any
    tenant context exists to filter by."""
    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        Index("uq_organization_slug", "slug", unique=True, postgresql_where=_ALIVE),
    )


class UserAccount(Base, Timestamps, SoftDelete):
    """A person. Global rather than per-organization, because one person can
    belong to several.

    Credentials are NOT here. `.runtime/auth.db` stays the credential store
    until P4; this is identity, and `legacy_user_id` is the bridge to it during
    the migration."""
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    legacy_user_id: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        # Functional index: email is compared case-insensitively, matching the
        # `COLLATE NOCASE` the SQLite users table already used.
        Index("uq_user_account_email", text("lower(email)"), unique=True,
              postgresql_where=_ALIVE),
        Index("ix_user_account_legacy", "legacy_user_id"),
    )


class Membership(Base, Timestamps):
    __tablename__ = "membership"

    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"),
        primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="CASCADE"),
        primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False,
                                      server_default="member")

    __table_args__ = (
        CheckConstraint(_one_of("role", MEMBERSHIP_ROLES), name="ck_membership_role"),
        Index("ix_membership_user", "user_id"),
    )


# --- apps ----------------------------------------------------------------

class App(Base, Timestamps, SoftDelete):
    """The one container the user names
    (docs/platform-redesign/app-is-the-entity-2026-08-20.md sec 3.1, 4.1).
    Every app can be used and every app can be worked on; "project" is retired
    as a second entity and folds into this one as its build view.

    STAGE A OF THE DESIGN, NOT THE WHOLE OF IT. This table and the nullable
    `app_id` on `chat_thread`/`ticket` below are additive: `scope`/
    `context_key`, a knowledge `scope_key`, and every `.runtime/*.json` file
    registry keep meaning exactly what they mean today. `project_slug` here is
    a BRIDGE column, not a foreign key to `project` (the Postgres `project`
    table exists only for the two RELATIONAL_SCOPES roots, `development` and
    `workshop` -- see services.task_scope -- and names no app). The real
    project registry is still `.runtime/projects.json`, a file, so the link is
    a slug column resolved by `services/app_links.py`, the one place that
    computes it, always from the registries and never from a hardcoded roster
    (the same rule scripts/check_no_app_names.py enforces on platform code).

    `kind` distinguishes an app record from an agent-as-container record
    (design doc sec 3.3, explicitly deferred there). Every row the stage-A
    backfill writes is `'app'`; `'agent'` is reserved for that later stage.
    """
    __tablename__ = "app"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    # MUTABLE label ("a slug is a label, never an identity", design doc sec
    # 4.1). Renaming an app is a PATCH of this column, never a migration --
    # which is the whole reason `id` is the surrogate every caller keys on.
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # A design-system icon token (frontend lib/appIcon.tsx) or an uploaded
    # asset ref -- never a platform import (design doc sec 8.3). This column
    # is what makes that true at the data layer rather than by convention.
    icon: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="app")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # See the class docstring. Populated by the stage-A backfill wherever a
    # slug names both an installed app and a row in the project registry --
    # "the same thing, two records" the design doc calls out by name (sec
    # 2.1: budget, birds, location, commute, notes-app). NULL means this app
    # has no working tree of its own (an installed-only app with no repo).
    project_slug: Mapped[str | None] = mapped_column(String(64))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_one_of("kind", ("app", "agent")), name="ck_app_kind"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id",
                        name="ck_app_not_own_parent"),
        _org_target("app"),
        _org_ref("app", "parent_id", "app", ondelete="SET NULL (parent_id)"),
        Index("uq_app_slug", "org_id", "slug", unique=True, postgresql_where=_ALIVE),
        Index("ix_app_project_slug", "org_id", "project_slug",
              postgresql_where=text(
                  "project_slug IS NOT NULL AND deleted_at IS NULL")),
    )


# --- work --------------------------------------------------------------------

class Project(Base, Timestamps, SoftDelete):
    __tablename__ = "project"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The human-facing ticket prefix: `WS` gives `WS-142`.
    key: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Ticket numbering. A per-project counter, because a Postgres sequence is
    # global and cannot give per-project numbering.
    #
    # NOT YET CONCURRENCY-SAFE. The intended mechanism is
    # `SELECT ... FOR UPDATE` on this row in the same transaction as the insert;
    # the only writer today is the backfill, which is single-threaded and reads
    # it with a plain attribute access. `uq_ticket_number` means a race loses a
    # row to a constraint violation rather than duplicating a number, so it
    # fails safe, but it does fail. The lock lands with the first concurrent
    # writer in P3. Described as intent, not as fact, because documenting a
    # mechanism that does not exist is how the JSON store's docs drifted.
    next_ticket_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1")

    __table_args__ = (
        _org_target("project"),
        Index("uq_project_slug", "org_id", "slug", unique=True, postgresql_where=_ALIVE),
        Index("uq_project_key", "org_id", "key", unique=True, postgresql_where=_ALIVE),
    )


class WorkflowState(Base, Timestamps, SoftDelete):
    """A tenant-defined status. `category` is the fixed axis everything
    mechanical reasons over; `name` is what a human reads."""
    __tablename__ = "workflow_state"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[float] = mapped_column(Float, nullable=False, server_default="65535")
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    color: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(_one_of("category", STATE_CATEGORIES),
                        name="ck_workflow_state_category"),
        _org_target("workflow_state"),
        _org_ref("workflow_state", "project_id", "project", ondelete="CASCADE"),
        Index("uq_workflow_state_name", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
        _one_default("workflow_state"),
        Index("ix_workflow_state_project", "org_id", "project_id", "position"),
    )


class TicketType(Base, Timestamps, SoftDelete):
    """What kind of work this is. Replaces the bespoke `execution` enum
    (code / judgement / external) with the concept every tracker already has,
    tenant-configurable like workflow state."""
    __tablename__ = "ticket_type"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # Whether an unattended worker may select tickets of this type. This is the
    # one behaviour the old `execution` field actually drove, kept as a property
    # of the type rather than as a separate column on every ticket.
    agent_executable: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"))

    __table_args__ = (
        _org_target("ticket_type"),
        _org_ref("ticket_type", "project_id", "project", ondelete="CASCADE"),
        Index("uq_ticket_type_name", "org_id", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
        _one_default("ticket_type"),
    )


class Ticket(Base, Timestamps, SoftDelete):
    __tablename__ = "ticket"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    state_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    type_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    priority: Mapped[str] = mapped_column(
        SAEnum(*PRIORITIES, name="ticket_priority"),
        nullable=False, server_default="none")
    effort: Mapped[str | None] = mapped_column(String(16))

    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # External tracker binding: two columns, the way Plane does it, rather than
    # the three-way-merge shadow object the old schema reserved and never used.
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    # WHO FILED THIS AND FROM WHERE. `{kind: "chat", ref: <thread id>}` is the
    # chat-to-ticket link, and carrying it as an unindexed JSON blob is the
    # reason "show me the tickets this conversation filed" is not a query today.
    # Populated on all 112 tickets, so it is current state, not history.
    source_kind: Mapped[str | None] = mapped_column(String(16))
    source_ref: Mapped[str | None] = mapped_column(String(255))

    # The hash over the spec fields ONLY (not `updated`), which is what lets a
    # queue triager decide it has already seen this ticket and terminate.
    spec_hash: Mapped[str | None] = mapped_column(String(64))

    # The paths a worker may change. A coordination guard, not a security
    # boundary (SEC-A2): it is checked post-hoc against the committed diff.
    touches: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Why this ticket is stuck. A blocked ticket that has lost this is the sharp
    # failure the backfill review already caught once.
    blocked_reason: Mapped[str | None] = mapped_column(Text)

    # STAGE A (app-is-the-entity-2026-08-20.md sec 4.2). Nullable and
    # additive: `project_id` stays how a ticket is filed today. Currently
    # backfilled to 0 rows -- only `development`/`workshop`-scoped tickets are
    # mirrored into Postgres at all (services.task_scope.RELATIONAL_SCOPES),
    # and neither is an app -- so this column exists now so ticket mirroring
    # can widen to an app-linked project scope later without a second
    # migration. An app's REAL tickets live in the JSON store today and are
    # served to `GET /apps/{id}/tickets` from there (routes/apps.py), not from
    # this column; see that route's docstring for why.
    app_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # `{kind, spec, criteria: [{desc, cmd, expect}]}`. Genuinely nested, 5 of
    # 112, and nothing filters or sorts on it, so a column per key would be
    # modelling for its own sake.
    acceptance: Mapped[dict | None] = mapped_column(JSONB)

    # The claim fence's ALLOCATOR. Bumped on every (re)claim; a run stamps the
    # value it got and every subsequent write is checked against it, so a
    # resurrected worker holding generation 3 cannot write over generation 4.
    claim_token: Mapped[int] = mapped_column(Integer, nullable=False,
                                             server_default="0")

    # --- the execution fields (2026-08-16) ----------------------------------
    # These five were left out when the schema was first cut, on a July
    # measurement of "0% occupancy or a mirror of another field". Postgres then
    # became authoritative for `development` and `workshop` on 2026-08-15
    # (b2c0db5) and the omission stopped being a modelling opinion and became
    # data loss: all five are reachable from `PATCH /tasks/{id}` and all five
    # were being written into a store that had nowhere to put them.
    #
    # Occupancy at rest is the WRONG test for three of them. `awaiting` is
    # raised at a block and cleared at the unblock, so zero at rest is what a
    # healthy queue looks like; `latitude` and `max_attempts` are overrides that
    # exist to be set on the exceptional ticket. The right test is whether a
    # live path writes them, and for all five it does.

    # NULL means INHERIT from the scope, never "standard" — coercing an unset
    # value to the default silently undoes a project's strict setting
    # (services/latitude.py). Hence nullable with no server_default.
    latitude: Mapped[str | None] = mapped_column(String(16))

    # The per-task cap on worker RUNS, distinct from ralph's inner iteration
    # cap. NULL means "use the sweep's default" (project_ralph.sh:64).
    max_attempts: Mapped[int | None] = mapped_column(Integer)

    # Worker runs spent, and the counter the cap is enforced against
    # (services/tasks.py `reclaim`, project_ralph.sh:856). It was briefly
    # derived from `count(run)` instead, against a Postgres `run` table that
    # nothing wrote after the one-shot backfill, so the derived value could
    # only ever go stale and the cap became unenforceable. It is a stored
    # counter again; `run` itself is gone (T4, coherence-overhaul-2026-08-16).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False,
                                          server_default="0")

    # THE BLOCK RECORD: the general escalation primitive (services/tasks).
    # Whatever stopped the work writes the same shape, so one review surface
    # serves all of them. Genuinely nested (`options`, `proposal`, `context`)
    # and read whole by the inbox reconciler, so JSONB rather than a column per
    # key. NULL means the ticket is not asking anything.
    awaiting: Mapped[dict | None] = mapped_column(JSONB)

    # THE DELEGATE, as the work model spells it: `operator`, `agent:<slug>`, or
    # NULL for unassigned (which `tasks._match_assignee` treats as claimable by
    # ANY scope, a distinction a default would destroy). Deliberately beside
    # `assignee_id` and not merged into it: that column is a FK to
    # `user_account` for when assignment means a person, and most delegates
    # today are agents that have no user row at all.
    assignee: Mapped[str | None] = mapped_column(String(64))

    # --- the claim fields (2026-08-17) --------------------------------------
    # `job_ids`, `lease_until`, `last_heartbeat` and `claim_pid` sat only in the
    # compatibility read shape, DERIVED from the Postgres `run` table (the
    # newest row's lease/heartbeat/pid, and the array of every run's
    # `legacy_job_id`). `run` had zero readers and one writer, the one-shot
    # backfill, so the derivation could only freeze -- exactly the class of bug
    # e9c4a71f0d38 fixed for latitude/max_attempts/attempts/awaiting/assignee.
    # `tasks.claim()` and `tasks.heartbeat()` write all four of these on every
    # claim of a ticket in a relational scope, through the same `PATCH
    # /tasks/{id}` -> `_persist` -> `ticket_sync.mirror` path as everything
    # else on this record, and `ticket_sync` was not tracking any of them --
    # so a claim's own lease/heartbeat/pid/job-id landed nowhere. Real columns
    # now, matching the JSON store's own field names 1:1 so there is one
    # definition of what a ticket's claim looks like, not two.
    job_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_pid: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(f"effort IS NULL OR {_one_of('effort', EFFORTS)}",
                        name="ck_ticket_effort"),
        CheckConstraint(f"latitude IS NULL OR {_one_of('latitude', LATITUDES)}",
                        name="ck_ticket_latitude"),
        # A cap of zero would park a ticket before its first attempt, and a
        # negative attempt count is not a thing that happened.
        CheckConstraint("max_attempts IS NULL OR max_attempts > 0",
                        name="ck_ticket_max_attempts"),
        CheckConstraint("attempts >= 0", name="ck_ticket_attempts"),
        CheckConstraint(
            f"source_kind IS NULL OR {_one_of('source_kind', SOURCE_KINDS)}",
            name="ck_ticket_source_kind"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id",
                        name="ck_ticket_not_own_parent"),
        _org_target("ticket"),
        _org_ref("ticket", "project_id", "project", ondelete="CASCADE"),
        _org_ref("ticket", "state_id", "workflow_state", ondelete="RESTRICT"),
        _org_ref("ticket", "type_id", "ticket_type",
                 ondelete="SET NULL (type_id)"),
        _org_ref("ticket", "parent_id", "ticket",
                 ondelete="SET NULL (parent_id)"),
        _org_ref("ticket", "app_id", "app", ondelete="SET NULL (app_id)"),
        Index("uq_ticket_number", "project_id", "number", unique=True),
        # The list view: newest-first within a project.
        Index("ix_ticket_updated", "org_id", "project_id", "updated_at"),
        Index("ix_ticket_state", "org_id", "project_id", "state_id"),
        Index("ix_ticket_app", "org_id", "app_id",
              postgresql_where=text("app_id IS NOT NULL AND deleted_at IS NULL")),
        # The scheduler. Partial because the queue only ever looks at open work,
        # and most tickets in a mature store are closed.
        Index("ix_ticket_queue", "org_id", "project_id", "priority", "created_at",
              postgresql_where=text("closed_at IS NULL AND deleted_at IS NULL")),
        Index("ix_ticket_parent", "parent_id"),
        # "which tickets did this conversation file", the query `source_kind` /
        # `source_ref` exist to make possible. Partial on the one kind that
        # carries a thread id.
        Index("ix_ticket_source_chat", "org_id", "source_ref",
              postgresql_where=text(
                  "source_kind = 'chat' AND source_ref IS NOT NULL "
                  "AND deleted_at IS NULL")),
    )


class TicketLink(Base, Timestamps, SoftDelete):
    """A typed edge between two tickets. Hierarchy is NOT here: that is
    `ticket.parent_id`."""
    __tablename__ = "ticket_link"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    from_ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    to_ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint(_one_of("kind", LINK_KINDS), name="ck_ticket_link_kind"),
        CheckConstraint("from_ticket_id <> to_ticket_id", name="ck_ticket_link_not_self"),
        # No `_org_target` here: nothing references a link, so there is no pair
        # to expose. Both ENDS are composite, which is the property that matters
        # -- a link cannot join a ticket in one org to a ticket in another.
        _org_ref("ticket_link", "from_ticket_id", "ticket", ondelete="CASCADE"),
        _org_ref("ticket_link", "to_ticket_id", "ticket", ondelete="CASCADE"),
        Index("uq_ticket_link", "from_ticket_id", "to_ticket_id", "kind",
              unique=True, postgresql_where=_ALIVE),
        # The readiness predicate reads edges by target, so index both directions.
        Index("ix_ticket_link_to", "to_ticket_id", "kind"),
    )


class Label(Base, Timestamps, SoftDelete):
    __tablename__ = "label"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        _org_target("label"),
        _org_ref("label", "project_id", "project", ondelete="CASCADE"),
        Index("uq_label_name", "org_id", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
    )


class TicketLabel(Base):
    __tablename__ = "ticket_label"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True)
    label_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_fk()

    __table_args__ = (
        _org_ref("ticket_label", "ticket_id", "ticket", ondelete="CASCADE"),
        _org_ref("ticket_label", "label_id", "label", ondelete="CASCADE"),
        Index("ix_ticket_label_label", "label_id"),
    )


class Comment(Base, Timestamps, SoftDelete):
    """Mutable user content. Distinct from `ticket_event`, which is immutable
    history. `author_type` can still name `agent`; there is no `agent` table
    to point an id at (T4, coherence-overhaul-2026-08-16 -- it had zero reads
    and zero writes), so an agent-authored comment is `system`, the same as
    any other machine-written one, until something actually identifies agents
    to this schema."""
    __tablename__ = "comment"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nullable because a `system` (or `agent`, see above) author has no row to
    # point at.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # How the mirror finds this comment again, and what `delete_comment` matches
    # on. Missing until c4a9f18d6b25; the read-shadow caught it.
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint(_one_of("author_type", ACTOR_TYPES), name="ck_comment_author_type"),
        _actor_consistent("author_type", "author_user_id",
                          "ck_comment_actor_is_consistent"),
        _org_target("comment"),
        _org_ref("comment", "ticket_id", "ticket", ondelete="CASCADE"),
        _org_ref("comment", "parent_id", "comment", ondelete="CASCADE"),
        Index("ix_comment_ticket", "ticket_id", "created_at"),
        _external_target("comment"),
    )


class TicketEvent(Base):
    """Immutable, field-level history.

    Shape taken from Plane's IssueActivity: (verb, field, old_value, new_value).
    An activity feed renders straight from this without a per-event parser, and
    "who changed the priority, and from what" is a query rather than a dig
    through a JSON payload.

    No `updated_at` and no `deleted_at`: an audit row that can be edited or
    removed is not an audit row.
    """
    __tablename__ = "ticket_event"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nullable because a `system` (or `agent`; see `Comment.author_user_id`
    # for why there is no `actor_agent_id`) actor has no row to point at.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))
    verb: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(_one_of("actor_type", ACTOR_TYPES), name="ck_ticket_event_actor_type"),
        _actor_consistent("actor_type", "actor_user_id",
                          "ck_ticket_event_actor_is_consistent"),
        # No `_org_target`: an audit row is never referenced.
        _org_ref("ticket_event", "ticket_id", "ticket", ondelete="CASCADE"),
        _org_ref("ticket_event", "comment_id", "comment",
                 ondelete="SET NULL (comment_id)"),
        Index("ix_ticket_event_ticket", "ticket_id", "created_at"),
    )


# --- execution ---------------------------------------------------------------

# The OLD `Agent` and OLD `Run` tables lived here and are gone (T4,
# coherence-overhaul-2026-08-16, migration def2cbf58df8). The old `agent` had
# zero reads and zero writes ever. The old `run` had 2,753 rows and zero readers
# once e9c4a71f0d38 stopped deriving `ticket.attempts` from `count(run)`; its
# only writer was a one-shot backfill and it had diverged. What those tables'
# live writers actually needed a home for -- `ticket.job_ids`/`lease_until`/
# `last_heartbeat`/`claim_pid` -- moved onto `Ticket` itself.
#
# The NEW `Run` below is NOT a revert of T4. It is shaped from the LIVE record
# measured off .runtime/jobs.jsonl (3,527 collapsed rows, 2026-08-22), with the
# columns T4 dropped for cause left out, and evidence modelled as JSONB (the
# typed JobEvidence), not freeform text. The writer switch is a dependent ticket.


class Run(Base, Timestamps):
    """One bounded execution: a sweep tick, a Ralph run, a manual run.

    Shaped from services/jobs.py's record (3,527 collapsed rows measured
    2026-08-22 off .runtime/jobs.jsonl). `id` preserves the `job_<epoch>_<hex>`
    string the file store mints; a future writer can carry it unchanged.

    NOT the old `run` table (T4, def2cbf58df8 dropped it for cause).
    Excluded from the old shape: `attempt_no` (write-never), lease columns
    (moved to ticket), `summary` freeform text (replaced by JSONB evidence).

    No soft delete: like `ticket_event` and `chat_turn`, a run is a record
    that something happened. It is never edited; liveness reconcile appends
    a new terminal line to the JSONL, which a writer will land as an UPDATE.
    """
    __tablename__ = "run"

    # TEXT, not UUID: preserves the `job_<epoch>_<hex>` format the file store
    # mints so a writer from that store can carry IDs unchanged.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_fk()

    # Who ran. Letta id (`agent-...`) or logical name. Present on 3085/3527.
    agent_id: Mapped[str | None] = mapped_column(String(255))

    # Context the run belonged to. Both nullable: a cron tick belongs to neither.
    project_slug: Mapped[str | None] = mapped_column(String(64))
    chat_id: Mapped[str | None] = mapped_column(String(255))

    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    exit_code: Mapped[int | None] = mapped_column(Integer)
    tasks_done: Mapped[int | None] = mapped_column(Integer)
    tasks_failed: Mapped[int | None] = mapped_column(Integer)
    pid: Mapped[int | None] = mapped_column(Integer)

    # Open-set (KINDS in jobs.py is recognized, not exhaustive). Not constrained.
    kind: Mapped[str | None] = mapped_column(String(32))

    # THE TICKET LINK -- a real FK column, not a scan over ticket.job_ids.
    # The file store's `task_id` is a UUID-hex string; this stores the same
    # identity as a proper UUID so the FK is enforceable and the index compact.
    task_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # Coordinator sweep that spawned this child run (same string format as id).
    parent_id: Mapped[str | None] = mapped_column(String(64))

    # What the run produced, typed as JobEvidence (routes/api_models.py).
    # extra="allow" on the Pydantic model means this column is a floor, not a
    # ceiling: a writer or future producer may add keys and nothing here drops them.
    evidence: Mapped[dict | None] = mapped_column(JSONB)

    # Loop caps that bounded the run (max_turns, max_cost, etc.).
    budget: Mapped[dict | None] = mapped_column(JSONB)

    # Arbitrary binding for deploy/event runs (3/3527 live).
    binding: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(  # keep in step with services/jobs.py TRIGGERS
            _one_of("trigger", ("manual", "cron", "event", "loop")),
            name="ck_run_trigger"),
        CheckConstraint(  # keep in step with services/jobs.py STATUSES
            _one_of("status", ("queued", "running", "awaiting_input", "done", "failed", "cancelled")),
            name="ck_run_status"),
        # UNIQUE (id, org_id) -- expose the composite so children can FK against
        # it, matching the _org_target pattern used by every other table here.
        # TEXT pk is still unique by itself; this adds the org_id dimension for
        # the referential-integrity guard described in `_org_ref`'s docstring.
        UniqueConstraint("id", "org_id", name="uq_run_id_org"),
        # Ticket link: composite so a run cannot cross an org boundary.
        _org_ref("run", "task_id", "ticket", ondelete="SET NULL (task_id)"),
        # Parent coordinator: self-composite for the same reason.
        ForeignKeyConstraint(
            ["parent_id", "org_id"], ["run.id", "run.org_id"],
            name="run_parent_id_org_fkey", ondelete="SET NULL (parent_id)"),
        Index("ix_run_task", "org_id", "task_id", "started_at",
              postgresql_where=text("task_id IS NOT NULL")),
        Index("ix_run_org_started", "org_id", "started_at"),
        Index("ix_run_parent", "parent_id",
              postgresql_where=text("parent_id IS NOT NULL")),
    )


# --- worktrees ---------------------------------------------------------------
# docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md, step B2.
#
# THE POINT OF THE TABLE. `services/worktrees.py` created directories and git
# worktrees and nothing durable remembered them: `list_branches` re-derived the
# whole set by shelling out to `git worktree list`, so "who allocated this, when,
# for which ticket, and what happened to it" had no answer at all. Once a merge
# reaped the tree, even the question stopped being askable. This row is the
# durable half, and §7 of the design is why it is the one that gets a lease
# later: a worktree is a durable resource, a session is disposable and never an
# owner.
#
# VOCABULARY (§3, binding). The disk concept is "worktree" and the user-facing
# concept is "branch"; the word "workspace" is NOT available for either, because
# `/workspace` is the bwrap mountpoint every agent sees and `workspace_inputs`
# already means an app's heavy dependency directories. So: table `worktree`,
# HTTP resource `/branches`.
#
# NO LEASE FIELDS HERE. No holder, no generation, no expiry. The lease is B3 and
# it is its own table for the reason §5 gives -- it is a different lifetime from
# the thing it is taken on, and folding it in would make releasing a lease a
# write to the resource record.

WORKTREE_STATUSES = ("allocated", "provisioned", "merged", "discarded")

# The timeline vocabulary. The five §9 names ("acquired, steered, preempted by
# whom, snapshot <sha>, merged") plus the four lifecycle transitions the writers
# in `services/worktrees.py` actually produce today. Closed, because a timeline
# whose kinds are free text is prose with punctuation, which is exactly what §9
# refuses.
WORKTREE_EVENT_KINDS = (
    "allocated", "provisioned", "acquired", "steered", "preempted",
    "snapshot", "merged", "discarded", "reaped",
)


class Worktree(Base, Timestamps, SoftDelete):
    """One isolated line of work: a git worktree, its branch, and its history.

    THE COLUMNS ARE READ OFF THE CODE, not guessed. `allocate_task_worktree`
    resolves a project slug to a directory, names a branch `wt/<project>/<slug>`,
    picks a start point (`origin/<default>` for a bound project, the local
    canonical branch otherwise) and adds a locked worktree at
    `<branches root>/<project>/<slug>`. Every one of those is a column here, and
    `base_sha` pins what the start point RESOLVED TO at creation -- a ref name
    moves, and the commit a tree was cut from is not recoverable afterwards.

    THE TICKET IS NULLABLE AND THAT IS DELIBERATE (§10.4): "writing with no
    ticket is common and not second-class: it gets a branch a ticket can adopt
    later". `create_branch` is exactly that path. The id is TEXT, not a uuid FK,
    for the same reason `change.ticket_ids` is: those are the ids the rest of
    the system passes around, and Postgres holds `ticket` rows only for the two
    relational scopes, so a FK would be a promise the data cannot keep.

    STATUS IS A CHECK, not a native enum, matching every controlled vocabulary
    in this file except `priority`. `merged` and `discarded` are terminal and
    both mean the directory is gone -- which is what `removed_at` records, and
    what `ck_worktree_terminal_is_removed` makes a constraint rather than a
    convention. Without it a row could claim `merged` while the tree it names
    is still on disk, which is precisely the record-versus-disk disagreement
    this table exists to close.
    """
    __tablename__ = "worktree"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()

    # The app this branch is work on, when the project slug has an `app` row.
    # Nullable for the same reason `change.app_id` is: the two registries are
    # still unlinked (app-is-the-entity-2026-08-20.md §9).
    app_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # HOW THE PLATFORM ADDRESSES THIS TREE. `project_slug` is what every
    # published entry point in `services/worktrees.py` takes; `scope` is the
    # `project:<slug>` string `provision_worktree` is called with, kept because
    # it is the caller's own addressing and re-deriving it would bake in the
    # assumption that a scope is always a project (§13.2: scope is internal
    # addressing, and it is not shown in the UI).
    project_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)

    # The branch, both halves: the slug the platform addresses it by and the
    # full git ref it actually is.
    branch_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)

    # Where it lives on disk. Text, not String(n): a path has no useful bound.
    path: Mapped[str] = mapped_column(Text, nullable=False)

    # What it was cut from, as a name and as the commit that name resolved to.
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String(40))

    # The ticket it was allocated for. NULL is a supported, first-class state.
    ticket_id: Mapped[str | None] = mapped_column(String(255))

    # WHO ALLOCATED IT. Free text, not a FK: sessions are disposable and are not
    # modelled until B4, and the callers today are a sweep, a chat and a route.
    # A pointer into a table that does not exist yet would be worse than a name.
    allocated_by: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        server_default="allocated")

    # The commit the merge produced, and when the directory left the disk.
    merged_sha: Mapped[str | None] = mapped_column(String(40))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_one_of("status", WORKTREE_STATUSES),
                        name="ck_worktree_status"),
        CheckConstraint(
            "(status IN ('merged', 'discarded')) = (removed_at IS NOT NULL)",
            name="ck_worktree_terminal_is_removed"),
        _org_target("worktree"),
        _org_ref("worktree", "app_id", "app", ondelete="SET NULL (app_id)"),
        # ONE LIVE RECORD PER BRANCH, and only while it is live: a merged or
        # discarded branch is reaped and its slug becomes reusable, so the
        # uniqueness is over the unfinished set. Same shape, and the same
        # reason, as `uq_change_open_branch`.
        Index("uq_worktree_live_branch", "org_id", "project_slug", "branch_slug",
              unique=True,
              postgresql_where=text(
                  "deleted_at IS NULL AND status NOT IN ('merged', 'discarded')")),
        Index("ix_worktree_status", "org_id", "status", "updated_at",
              postgresql_where=_ALIVE),
        Index("ix_worktree_ticket", "org_id", "ticket_id",
              postgresql_where=text(
                  "ticket_id IS NOT NULL AND deleted_at IS NULL")),
        Index("ix_worktree_app", "org_id", "app_id",
              postgresql_where=text("app_id IS NOT NULL AND deleted_at IS NULL")),
    )


class WorktreeEvent(Base, Timestamps):
    """One structured entry on a worktree's timeline (§9).

    A TABLE, NOT A JSON LIST ON `worktree`, and the reason is concurrency rather
    than taste. `change.gates` is a validated JSON list and that is right for it:
    a bounded set, rewritten wholesale by one writer. A timeline is neither. It
    is append-only and unbounded (a `steered` per request, a `snapshot` per
    preempt), and appending to a JSON column is a read-modify-write of the whole
    array -- so two participants appending at once silently lose one of the two
    entries. Losing the record of who took a tree from whom, in the design whose
    entire subject is two participants wanting the same tree, is not an
    acceptable failure mode. An INSERT cannot lose a row. `ticket_event` is the
    existing precedent in this schema for history-as-rows.

    NO `updated_at` SEMANTICS ARE INTENDED. The row is immutable once written;
    it inherits `Timestamps` so `created_at` is the event time and so the
    tenant-table trigger convention is unbroken. There is no `deleted_at`: a
    timeline you can quietly delete from is not a record.

    THE FIELDS ARE TYPED, NOT A BLOB. §9 asks for structured events, so the
    payload is columns: who (`actor`), which commit (`sha`, the `snapshot <sha>`
    and the merge commit), which files or directories (`paths`), and the event's
    own text (`detail` -- the steer request, the discard reason). There is
    deliberately no free-form `data` JSONB: a blob is how "structured" decays
    back into prose, and it generates an untyped field in the client.
    """
    __tablename__ = "worktree_event"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    worktree_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128))
    sha: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    paths: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    __table_args__ = (
        CheckConstraint(_one_of("kind", WORKTREE_EVENT_KINDS),
                        name="ck_worktree_event_kind"),
        _org_target("worktree_event"),
        # CASCADE, not SET NULL: an event with no worktree is not a timeline
        # entry, it is a fragment. The worktree row itself is soft-deleted, so
        # this only fires on a real hard delete (an org going away).
        _org_ref("worktree_event", "worktree_id", "worktree", ondelete="CASCADE"),
        Index("ix_worktree_event_timeline", "org_id", "worktree_id", "created_at"),
    )


# --- changes -----------------------------------------------------------------
# docs/platform-redesign/changes-and-review-2026-08-22.md, step C1.
#
# THE POINT OF THE TABLE. A ticket says what should be true; a change says here
# is the code that makes it true. Until this row existed, `in_review` meant a
# worker had SAID it was finished and the operator was asked to accept an
# assertion with no artifact behind it. A change is the artifact: a branch that
# is ahead of its base, the tickets it claims to satisfy, its head sha, and the
# machine-check results that answer the questions a person should not have to.

CHANGE_STATUSES = ("draft", "ready", "changes_requested", "approved",
                   "merged", "closed")

# A gate's outcome. `running` exists because a gate is recorded when it starts
# (C3 writes these); `skipped` is a gate that did not apply to this diff, which
# is a different fact from one that passed.
GATE_STATUSES = ("pass", "fail", "running", "skipped")


class Change(Base, Timestamps, SoftDelete):
    """One branch's worth of work, reviewable on its own.

    STATUS IS A CHECK, NOT A NATIVE ENUM, matching every other controlled
    vocabulary in this file (`priority` is the single deliberate exception and
    it earns it by needing declaration-order sorting; a change status is never
    sorted by rank). The design doc writes `enum(draft, ready, ...)` to mean
    the value set, and this is that value set.

    HOW A CHANGE NAMES ITS BRANCH. `worktree_id` names B2's worktree record,
    which now exists, through the composite `_org_ref` below. It stays NULLABLE:
    a change can be opened for a branch that predates the record, and a branch
    allocated with no database configured has no record to point at (the record
    is additive -- `services/worktrees.py` allocates either way). What addresses
    a branch unconditionally is still the
    `(project_slug, branch_slug)` pair that every published entry point in
    `services/worktrees.py` takes, so the record carries it: without it a
    change could not re-derive its own diff, and a record that cannot produce
    its payload is exactly the assertion-without-artifact this table replaces.

    TICKET IDS ARE TEXT, NOT A UUID ARRAY OR A JOIN TABLE. They are the ids the
    rest of this system passes around (`services/tasks`, `TODO.md`, every shell
    worker), and Postgres holds `ticket` rows only for the two relational
    scopes (`services.task_scope.RELATIONAL_SCOPES`) -- an app's tickets are
    still file-backed. A FK array would therefore be a promise the data cannot
    keep for most changes. A change carrying SEVERAL tickets is the normal
    case, not an edge one: it merges as one unit, which is why
    `workspace-ownership-and-leases-2026-08-22.md` sec 10 defaults a session to
    a new branch rather than joining an existing one.
    """
    __tablename__ = "change"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()

    # The app this change is work on. Nullable: a project slug does not
    # necessarily have an `app` row (the two registries are still unlinked --
    # see app-is-the-entity-2026-08-20.md sec 9).
    app_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # See the class docstring. Composite-FK'd to `worktree` below.
    worktree_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # The branch, addressed the way `services/worktrees.py` addresses one.
    project_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_slug: Mapped[str] = mapped_column(String(64), nullable=False)

    # What it merges into, and where it is now. `head_sha` is a full 40-char
    # object name, never an abbreviation: an abbreviation is ambiguous the day
    # the repository grows.
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    # The commit the base stood at when this change merged. Null until then.
    # A merged change's diff is base_sha..merged_sha, because by that point
    # base_ref CONTAINS the change and diffing against the name is empty.
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # THE AUTHOR'S CLAIM, and the review surface labels it as one (design doc
    # sec 5.1). The diff is the primary object; this is secondary by design.
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    ticket_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))

    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        server_default="draft")

    # `[{name, status, detail, at}]`, typed as ChangeGate (routes/changes.py).
    # A list rather than a dict keyed by name because a gate can legitimately
    # be recorded more than once for one change (a re-run after a fix), and the
    # order is the order they were produced in.
    gates: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    # `{kind, url, number}` when the change is mirrored to a remote pull
    # request, NULL when it is purely local. Typed as ChangeRemote. Local-first
    # is the requirement: the same record either way, never two code paths.
    remote: Mapped[dict | None] = mapped_column(JSONB)

    merged_sha: Mapped[str | None] = mapped_column(String(40))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_one_of("status", CHANGE_STATUSES), name="ck_change_status"),
        # A merged change names what it merged as, and nothing else does. Both
        # halves: without the first a change can claim `merged` with no commit
        # behind it, which is the same "status with no artifact" defect this
        # whole table exists to remove.
        CheckConstraint(
            "(status = 'merged') = (merged_sha IS NOT NULL AND merged_at IS NOT NULL)",
            name="ck_change_merged_is_recorded"),
        _org_target("change"),
        _org_ref("change", "app_id", "app", ondelete="SET NULL (app_id)"),
        # THE FORWARD SLOT IS WIRED UP NOW. `a7c3e10d9b64`'s own docstring said
        # the composite reference belongs to the migration that creates the
        # `worktree` table, because a FK to a table that did not exist could
        # only have failed. That table is above, so this is that reference.
        _org_ref("change", "worktree_id", "worktree",
                 ondelete="SET NULL (worktree_id)"),
        # ONE BRANCH PRODUCES ONE CHANGE (design doc sec 3), but only while it
        # is open: a merged or closed branch is reaped and its slug becomes
        # reusable, so the uniqueness is over the live, unfinished set.
        Index("uq_change_open_branch", "org_id", "project_slug", "branch_slug",
              unique=True,
              postgresql_where=text(
                  "deleted_at IS NULL AND status NOT IN ('merged', 'closed')")),
        Index("ix_change_status", "org_id", "status", "updated_at",
              postgresql_where=_ALIVE),
        Index("ix_change_app", "org_id", "app_id",
              postgresql_where=text("app_id IS NOT NULL AND deleted_at IS NULL")),
        # "which change carries this ticket" is the query C2's status
        # derivation runs on every ticket, so the array is indexed for
        # containment rather than scanned.
        Index("ix_change_tickets", "ticket_ids", postgresql_using="gin"),
    )


# --- chat --------------------------------------------------------------------
# ADR 0020. The load-bearing idea is that a transcript is a TREE, not a list,
# and that the tree is RECORDED rather than reconstructed. Both halves matter:
#
# **Why a tree.** Going back to an earlier point in a conversation and carrying
# on from there is the operation every one of "undo", "rewind", "edit and
# retry" and "branch" actually is. Given a parent pointer and a ref, they are
# one operation, and nothing is ever destroyed to perform it. A list cannot
# express any of them; the JSONL log this replaces says so explicitly in its own
# header ("editing a stored message is not expressible").
#
# **Why the tree also fixes ordering.** A message sent while a reply was being
# written belongs INSIDE that reply, after the work that had been done and
# before the work that had not. The file log recorded that as a token pair
# (`meta.cut_id` on the message, `seg.cut_after` on the segment it arrived
# after) and re-derived the split on every render, in three separate readers.
# Here the reply is simply stored as several messages, one per stretch, threaded
# through the message that interrupted it. The split is a fact, not a
# computation, and `cut_id`, `cut_after`, `cut_head`, `at_unit`, `at_segment`,
# `during_turn` and `injected` all cease to exist. See ADR 0018 for the
# render-time version of the same principle.
#
# **What deliberately did NOT move.** The per-run scratch: `live.json` (the
# in-flight draft, rewritten every ~0.4s and deliberately not fsynced),
# `queue.json`, `drafts.json`, `prefs.json`. None of it is transcript, all of it
# is rewritten constantly, and none of it survives a restart in any meaningful
# sense. The line is: the record of what was said is in the database; the
# scratchpad of a turn in flight is a file.

# CHAT_MODES is imported from `services.chat_store` at the top of this file: it
# was a mirrored restatement here until 2026-08-17, and the two copies had even
# drifted into different orders. Adding a value there means a migration for
# `ck_chat_thread_mode` as well; the constraint is not derived at runtime.
MESSAGE_ROLES = ("user", "assistant")

# What a node in the transcript can be. All three occupy a POSITION, which is
# why they are one table: a context cut sits between two messages, and rewinding
# to a point before it has to un-apply it. Modelling boundaries separately would
# make "where is the cut relative to the branch head" a join against a second
# ordering, which is the exact class of bug this schema exists to remove.
# `run`/`title` from the file log's KINDS are NOT here: a run is `chat_turn`, and
# a title is a column on the thread.
MESSAGE_KINDS = ("message", "boundary", "scope")
BOUNDARY_MODES = ("clear", "compact")
TURN_STATUSES = ("running", "finished", "lost", "error", "stopped")

# Documented for the reader; NOT constrained, because segments are a JSONB
# render payload (see ChatMessage.segments).
SEGMENT_KINDS = ("text", "tool", "agent", "approval")


class ChatThread(Base, Timestamps, SoftDelete):
    """One conversation.

    `current_branch_id` is the only mutable pointer that decides what the
    transcript IS. Switching branches is one UPDATE of one column; nothing is
    copied and nothing is deleted, which is what makes the operation safe enough
    to offer without a warning about losing work.
    """
    __tablename__ = "chat_thread"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    # Whose chat it is. RLS isolates the organization; this is the second,
    # application-level predicate, and it is the direct successor to the
    # `.runtime/users/<owner_id>/` directory partition.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="CASCADE"),
        nullable=False)
    # The 32-hex id the URLs, the frontend and the file archive already use.
    # Kept for exactly the reason `ticket.external_id` is: every existing link,
    # bookmark and `claude_session` mapping names a thread by it.
    external_id: Mapped[str | None] = mapped_column(String(64))

    context_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_locked: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False,
                                      server_default="orchestrated")

    # STAGE A (app-is-the-entity-2026-08-20.md sec 4.2, 5.1). Nullable and
    # additive: `scope`/`context_key` remain how a thread is addressed and
    # routed today. This is the real, indexed, queryable owner the design doc
    # asks for -- backfilled from `scope` (an `app:<slug>` scope, or a
    # `project:<slug>` scope whose slug is a known app's linked project) via
    # `services/app_links.py`, and set going forward wherever a thread's scope
    # resolves to an app. `GET /apps/{id}/chats` (routes/apps.py) reads this
    # column directly rather than re-deriving the scope per row, which is what
    # makes the query real instead of a directory walk.
    app_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    title: Mapped[str | None] = mapped_column(String(255))
    # `title_source` and `pre_plan_permission_mode` and `mcp_servers_enabled`
    # lived here and are gone (T4, coherence-overhaul-2026-08-16, migration
    # def2cbf58df8). All three were dead in this table specifically:
    # `chat_repo.sync_thread_fields`, the one live mirror from the file store
    # into `chat_thread`, never included any of the three in its UPDATE, so
    # none of them was kept current past a thread's creation, and two of them
    # (`mcp_servers_enabled`, `pre_plan_permission_mode`) were never written
    # here even once -- `chat_repo.create_thread`'s own `allowed` field list
    # did not carry them either. `title_source` was written exactly once, by
    # the one-shot `db/chat_backfill.py`. This is a gap in the relational
    # mirror, not evidence the concepts are unwanted: `services/chat_store.py`
    # still tracks all three in the file store today. Re-add them to
    # `chat_repo` (T2/T3 territory, not T4) if and when they need to be read
    # back out of Postgres rather than dropped a second time.
    # The first user message, truncated. Stands in until naming succeeds, and
    # naming failing silently behind it is a known open defect, so it is a
    # separate column rather than a title that lies about being one.
    seed_title: Mapped[str | None] = mapped_column(String(255))

    model: Mapped[str | None] = mapped_column(String(64))
    effort: Mapped[str | None] = mapped_column(String(16))
    permission_mode: Mapped[str | None] = mapped_column(String(32))
    agent_slug: Mapped[str | None] = mapped_column(String(64))

    # chat_thread and chat_branch reference each other, so this FK is added
    # after both tables exist (`use_alter`). Composite on (id, org_id) like
    # every other FK here: see `_org_target`.
    current_branch_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # The compaction fold. Transcript-adjacent (it REPLACES the messages behind
    # the cut in the prompt), so it belongs with the transcript.
    summary: Mapped[str | None] = mapped_column(Text)
    context_tokens: Mapped[int | None] = mapped_column(Integer)
    claude_session: Mapped[str | None] = mapped_column(String(64))
    voice_session: Mapped[str | None] = mapped_column(String(64))

    # Opaque per-thread client state: `turn_marks`, `card_states`,
    # `ticket_drafts`, `agent_sessions`. Bags today and bags here. JSONB rather
    # than columns because nothing filters, sorts or joins on any of it; the
    # moment something does, it earns a column. Contrast `ticket_event`, which
    # got field-level columns precisely because it IS queried.
    state: Mapped[dict | None] = mapped_column(JSONB)

    # Insertion counter for `chat_message.seq`, allocated with SELECT ... FOR
    # UPDATE in the same transaction as the insert. Same mechanism and the same
    # reasoning as `project.next_ticket_number`: a Postgres sequence is global
    # and cannot be per-thread.
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_one_of("mode", CHAT_MODES), name="ck_chat_thread_mode"),
        _org_target("chat_thread"),
        # The current branch must be a branch OF THIS THREAD. `id` on the left
        # is the thread's own id, matched against chat_branch.thread_id.
        ForeignKeyConstraint(
            ["current_branch_id", "id"], ["chat_branch.id", "chat_branch.thread_id"],
            name="chat_thread_current_branch_fkey",
            # Names its column, for the reason spelled out on `chat_branch`
            # below: a bare SET NULL on a composite key also nulls `id`.
            ondelete="SET NULL (current_branch_id)",
            use_alter=True),
        Index("uq_chat_thread_external", "external_id", unique=True,
              postgresql_where=text("external_id IS NOT NULL AND deleted_at IS NULL")),
        # The thread list: one owner's chats in one scope, most recent first.
        Index("ix_chat_thread_owner", "org_id", "owner_user_id", "updated_at",
              postgresql_where=_ALIVE),
        Index("ix_chat_thread_scope", "org_id", "scope", "updated_at",
              postgresql_where=_ALIVE),
        _org_ref("chat_thread", "app_id", "app", ondelete="SET NULL (app_id)"),
        Index("ix_chat_thread_app", "org_id", "app_id", "updated_at",
              postgresql_where=text("app_id IS NOT NULL AND deleted_at IS NULL")),
    )


class ChatBranch(Base, Timestamps, SoftDelete):
    """A named pointer to a message. A git ref, deliberately.

    A branch stores NO membership: which messages are on it is derived by
    walking `head_message_id` up its parent chain. That is what keeps the two
    tables from being able to disagree about what the conversation is.

    `fork_message_id` is therefore metadata, not structure. It exists for two
    reasons that walking cannot supply: it labels the branch in the picker
    ("branched from here"), and it is the ONLY record of a branch that is still
    empty because the operator went back to a point and has not typed yet. That
    state is the common one, not an edge case.
    """
    __tablename__ = "chat_branch"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    thread_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    parent_branch_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    fork_message_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    head_message_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # Ordinal, allocated per thread. Unique so the picker cannot show two rows
    # with the same label, which is the whole point of naming them.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        CheckConstraint("parent_branch_id IS NULL OR parent_branch_id <> id",
                        name="ck_chat_branch_not_own_parent"),
        _thread_target("chat_branch"),
        ForeignKeyConstraint(
            ["thread_id", "org_id"], ["chat_thread.id", "chat_thread.org_id"],
            name="chat_branch_thread_fkey", ondelete="CASCADE"),
        # THE COLUMN LIST ON EACH `SET NULL` IS LOAD-BEARING, and leaving it off
        # is a deadlock you only meet at delete time. A composite FK's plain
        # `ON DELETE SET NULL` nulls EVERY column in the key, `thread_id`
        # included, and `thread_id` is NOT NULL -- so deleting a message that
        # any branch points at raised NotNullViolation instead of clearing the
        # pointer. Measured: it made a branch-head message undeletable. Naming
        # the columns (Postgres 15+) nulls the pointer and leaves the tenant
        # column alone, which is what "the message went away" should mean.
        ForeignKeyConstraint(
            ["parent_branch_id", "thread_id"],
            ["chat_branch.id", "chat_branch.thread_id"],
            name="chat_branch_parent_fkey",
            ondelete="SET NULL (parent_branch_id)"),
        # Both message refs must name a message in THIS thread.
        ForeignKeyConstraint(
            ["fork_message_id", "thread_id"],
            ["chat_message.id", "chat_message.thread_id"],
            name="chat_branch_fork_message_fkey",
            ondelete="SET NULL (fork_message_id)",
            use_alter=True),
        ForeignKeyConstraint(
            ["head_message_id", "thread_id"],
            ["chat_message.id", "chat_message.thread_id"],
            name="chat_branch_head_message_fkey",
            ondelete="SET NULL (head_message_id)",
            use_alter=True),
        Index("uq_chat_branch_ordinal", "thread_id", "ordinal", unique=True,
              postgresql_where=_ALIVE),
        Index("ix_chat_branch_thread", "org_id", "thread_id", "created_at",
              postgresql_where=_ALIVE),
    )


class ChatTurn(Base, Timestamps):
    """One run of the model.

    A turn produces ONE assistant message normally and N when it was
    interrupted N-1 times, which is the whole reason this table exists: the
    stretches need something to say they were one reply, so the UI can group
    them and put the turn's outcome on the last one.

    No soft delete: like `ticket_event`, a turn is a record that something ran.
    """
    __tablename__ = "chat_turn"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    thread_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    # The uuid the streaming layer already mints and puts on the wire.
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    agent_slug: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    timeout: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # NULL, "operator" or "interrupted".
    stopped: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint(_one_of("status", TURN_STATUSES), name="ck_chat_turn_status"),
        _thread_target("chat_turn"),
        ForeignKeyConstraint(
            ["thread_id", "org_id"], ["chat_thread.id", "chat_thread.org_id"],
            name="chat_turn_thread_fkey", ondelete="CASCADE"),
        Index("uq_chat_turn_turn_id", "turn_id", unique=True),
        Index("ix_chat_turn_thread", "org_id", "thread_id", "started_at"),
    )


class ChatMessage(Base, Timestamps, SoftDelete):
    """A node in the transcript tree.

    `parent_id` IS the order. The rendered transcript is the path from the
    current branch's head up to the root, reversed; `seq` is an insertion
    counter and a sync cursor, never a render order. Those two were the same
    number in the file log and that is precisely why a message sent mid-reply
    could not be placed without a second mechanism.
    """
    __tablename__ = "chat_message"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    thread_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    # CASCADE, not SET NULL: severing a parent would orphan its whole subtree
    # into an unreachable, unlistable state. A branch is abandoned by moving a
    # ref, never by deleting rows, so this fires only on a real hard delete.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False,
                                      server_default="message")
    # The joined text of the top-level text segments, exactly as before.
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # The rendering payload: the ordered tool/text/agent/approval segments.
    #
    # JSONB rather than a `chat_segment` table, and the reasoning is not "it was
    # easier". Three things decide it: nothing filters, sorts or joins on a
    # segment field, so a table would buy no query; a live turn MUTATES this
    # list a few times a second and row-per-segment turns one in-memory append
    # into ~20 writes; and the one field that genuinely was relational,
    # `cut_after`, is deleted outright by threading the message into the tree.
    # The schema's general anti-JSONB stance came from `ticket_event`, whose
    # fields ARE queried ("who changed priority, from what"). That argument does
    # not carry here.
    segments: Mapped[list | None] = mapped_column(JSONB)
    # The remaining sparse render keys, in the shape the client already reads:
    # notes, receipts, attachments, voice, autonomous, ticket, block_kind.
    meta: Mapped[dict | None] = mapped_column(JSONB)

    # kind="boundary"
    boundary_mode: Mapped[str | None] = mapped_column(String(16))
    # kind="scope"
    scope_from: Mapped[str | None] = mapped_column(String(128))
    scope_to: Mapped[str | None] = mapped_column(String(128))

    # Per-thread insertion counter. The sync cursor (`events?after=N`), and the
    # tiebreak for listing siblings. NOT the render order.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # Browser-minted idempotency key. Was a linear scan of the log; now a
    # constraint, so a double-send cannot race past the check.
    client_id: Mapped[str | None] = mapped_column(String(64))
    # The ULID this event had in the file log.
    external_id: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(_one_of("role", MESSAGE_ROLES), name="ck_chat_message_role"),
        CheckConstraint(_one_of("kind", MESSAGE_KINDS), name="ck_chat_message_kind"),
        CheckConstraint(
            "boundary_mode IS NULL OR " + _one_of("boundary_mode", BOUNDARY_MODES),
            name="ck_chat_message_boundary_mode"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id",
                        name="ck_chat_message_not_own_parent"),
        _thread_target("chat_message"),
        ForeignKeyConstraint(
            ["thread_id", "org_id"], ["chat_thread.id", "chat_thread.org_id"],
            name="chat_message_thread_fkey", ondelete="CASCADE"),
        # A parent in another thread would silently corrupt every path walk.
        ForeignKeyConstraint(
            ["parent_id", "thread_id"], ["chat_message.id", "chat_message.thread_id"],
            name="chat_message_parent_fkey", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["turn_id", "thread_id"], ["chat_turn.id", "chat_turn.thread_id"],
            name="chat_message_turn_fkey", ondelete="SET NULL (turn_id)"),
        Index("uq_chat_message_seq", "thread_id", "seq", unique=True),
        Index("uq_chat_message_client", "thread_id", "client_id", unique=True,
              postgresql_where=text("client_id IS NOT NULL AND deleted_at IS NULL")),
        Index("ix_chat_message_thread", "org_id", "thread_id", "seq",
              postgresql_where=_ALIVE),
        # Children of a node: sibling detection and the branch picker.
        Index("ix_chat_message_parent", "parent_id", postgresql_where=_ALIVE),
        Index("ix_chat_message_turn", "turn_id"),
    )


# Tables that carry `org_id` and therefore must be RLS-protected.
#
# This is DERIVED from the metadata, but the migration does NOT read it: a
# migration must stay frozen against the schema as it was when written, so it
# carries its own literal tuple. Two lists that have to agree.
#
# The agreement is enforced by tests/test_db_rls_coverage.py, which needs no
# database and therefore runs everywhere. That matters: the drift it catches is
# a new table with `org_id`, no policy, and full DML granted to the application
# role by ALTER DEFAULT PRIVILEGES, which is readable by every tenant. The only
# other detector is verify_rls(), and that needs a live connection.
TENANT_TABLES = tuple(
    t.name for t in Base.metadata.sorted_tables
    if "org_id" in t.columns
)
