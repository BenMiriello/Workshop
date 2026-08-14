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

# --- controlled vocabularies -------------------------------------------------
# CHECK constraints rather than native Postgres ENUM types, with ONE deliberate
# exception (priority, below, which needs enum ordering). A native enum cannot
# have a value removed, and altering one takes a lock; a CHECK is a one-line
# migration. The tuples here are the single source of truth for both sides.

# Linear's five, verified against its documentation. Plane adds a sixth
# (`triage`); it is skipped because `draft` already covers that lane here, and
# adding a category to serve one state would be inventing a distinction rather
# than adopting one.
STATE_CATEGORIES = ("backlog", "unstarted", "started", "completed", "canceled")

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
AGENT_ROLES = ("maker", "reviewer", "triager", "keeper", "broker")
AGENT_AUTONOMY = ("reactive", "supervised", "autonomous")
RUN_STATUSES = ("queued", "running", "blocked", "done", "failed", "cancelled")
RUN_TRIGGERS = ("manual", "cron", "event", "loop")
EFFORTS = ("quick", "standard", "deep")

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
        PgUUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[float] = mapped_column(Float, nullable=False, server_default="65535")
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    color: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(_one_of("category", STATE_CATEGORIES),
                        name="ck_workflow_state_category"),
        Index("uq_workflow_state_name", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
        Index("ix_workflow_state_project", "org_id", "project_id", "position"),
    )


class TicketType(Base, Timestamps, SoftDelete):
    """What kind of work this is. Replaces the bespoke `execution` enum
    (code / judgement / external) with the concept every tracker already has,
    tenant-configurable like workflow state."""
    __tablename__ = "ticket_type"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # Whether an unattended worker may select tickets of this type. This is the
    # one behaviour the old `execution` field actually drove, kept as a property
    # of the type rather than as a separate column on every ticket.
    agent_executable: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"))

    __table_args__ = (
        Index("uq_ticket_type_name", "org_id", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
    )


class Ticket(Base, Timestamps, SoftDelete):
    __tablename__ = "ticket"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    state_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflow_state.id", ondelete="RESTRICT"),
        nullable=False)
    type_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket_type.id", ondelete="SET NULL"))

    priority: Mapped[str] = mapped_column(
        SAEnum(*PRIORITIES, name="ticket_priority"),
        nullable=False, server_default="none")
    effort: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[float] = mapped_column(Float, nullable=False,
                                              server_default="65535")

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="SET NULL"))

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

    # `{kind, spec, criteria: [{desc, cmd, expect}]}`. Genuinely nested, 5 of
    # 112, and nothing filters or sorts on it, so a column per key would be
    # modelling for its own sake.
    acceptance: Mapped[dict | None] = mapped_column(JSONB)

    # The claim fence's ALLOCATOR. Bumped on every (re)claim; a run stamps the
    # value it got and every subsequent write is checked against it, so a
    # resurrected worker holding generation 3 cannot write over generation 4.
    claim_token: Mapped[int] = mapped_column(Integer, nullable=False,
                                             server_default="0")

    __table_args__ = (
        CheckConstraint(f"effort IS NULL OR {_one_of('effort', EFFORTS)}",
                        name="ck_ticket_effort"),
        CheckConstraint(
            f"source_kind IS NULL OR {_one_of('source_kind', SOURCE_KINDS)}",
            name="ck_ticket_source_kind"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id",
                        name="ck_ticket_not_own_parent"),
        Index("uq_ticket_number", "project_id", "number", unique=True),
        # The list view: newest-first within a project.
        Index("ix_ticket_updated", "org_id", "project_id", "updated_at"),
        Index("ix_ticket_state", "org_id", "project_id", "state_id"),
        # The scheduler. Partial because the queue only ever looks at open work,
        # and most tickets in a mature store are closed.
        Index("ix_ticket_queue", "org_id", "project_id", "priority", "created_at",
              postgresql_where=text("closed_at IS NULL AND deleted_at IS NULL")),
        Index("ix_ticket_parent", "parent_id"),
    )


class TicketLink(Base, Timestamps, SoftDelete):
    """A typed edge between two tickets. Hierarchy is NOT here: that is
    `ticket.parent_id`."""
    __tablename__ = "ticket_link"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    from_ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"),
        nullable=False)
    to_ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"),
        nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint(_one_of("kind", LINK_KINDS), name="ck_ticket_link_kind"),
        CheckConstraint("from_ticket_id <> to_ticket_id", name="ck_ticket_link_not_self"),
        Index("uq_ticket_link", "from_ticket_id", "to_ticket_id", "kind",
              unique=True, postgresql_where=_ALIVE),
        # The readiness predicate reads edges by target, so index both directions.
        Index("ix_ticket_link_to", "to_ticket_id", "kind"),
    )


class Label(Base, Timestamps, SoftDelete):
    __tablename__ = "label"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("uq_label_name", "org_id", "project_id", "name", unique=True,
              postgresql_where=_ALIVE),
    )


class TicketLabel(Base):
    __tablename__ = "ticket_label"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"),
        primary_key=True)
    label_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("label.id", ondelete="CASCADE"),
        primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_fk()

    __table_args__ = (Index("ix_ticket_label_label", "label_id"),)


class Comment(Base, Timestamps, SoftDelete):
    """Mutable user content. Distinct from `ticket_event`, which is immutable
    history. An agent can author a comment; that is what `author_type` is for."""
    __tablename__ = "comment"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"),
        nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("comment.id", ondelete="CASCADE"))
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nullable because a `system` author has no row to point at.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # How the mirror finds this comment again, and what `delete_comment` matches
    # on. Missing until c4a9f18d6b25; the read-shadow caught it.
    external_source: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint(_one_of("author_type", ACTOR_TYPES), name="ck_comment_author_type"),
        Index("ix_comment_ticket", "ticket_id", "created_at"),
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
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"),
        nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id", ondelete="SET NULL"))
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent.id", ondelete="SET NULL"))
    verb: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("comment.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(_one_of("actor_type", ACTOR_TYPES), name="ck_ticket_event_actor_type"),
        Index("ix_ticket_event_ticket", "ticket_id", "created_at"),
    )


# --- execution ---------------------------------------------------------------

class Agent(Base, Timestamps, SoftDelete):
    __tablename__ = "agent"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32))
    autonomy: Mapped[str] = mapped_column(String(32), nullable=False,
                                          server_default="reactive")

    __table_args__ = (
        CheckConstraint(f"role IS NULL OR {_one_of('role', AGENT_ROLES)}",
                        name="ck_agent_role"),
        CheckConstraint(_one_of("autonomy", AGENT_AUTONOMY), name="ck_agent_autonomy"),
        Index("uq_agent_slug", "org_id", "slug", unique=True, postgresql_where=_ALIVE),
    )


class Run(Base, Timestamps):
    """One execution attempt.

    The claim and lease columns that used to sit on the ticket live here,
    because they describe an execution and not a durable work item.
    `ticket.attempts` becomes `count(run)`, which removes a hand-maintained
    counter that had already produced a real bug.

    All of them have now moved. `claim_token` is the monotonic fence the JSON
    store hand-rolled to reject a resurrected zombie worker's late write:
    `lease_expires_at` + `heartbeat_at` + `worker_pid` cover liveness, but not
    compare-and-swap, and a held-but-unchecked token does not stop a late write
    from a worker everyone else has given up on. It is a per-ticket generation
    counter, so it lives on `ticket` as the allocator and is STAMPED here, on
    the execution that holds it.
    """
    __tablename__ = "run"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent.id", ondelete="SET NULL"))

    # THE SWEEP THAT SPAWNED THIS RUN, and it is what carries ticket identity.
    #
    # A queue sweep works N tickets. The sweep run itself has no ticket -- it
    # records counts -- and each ticket's actual work is a CHILD run that does
    # carry `ticket_id`. Measured on the real log: every child run created since
    # the mechanism landed on 2026-07-15 carries its ticket, 74 of 74, so this
    # relation is how "which tickets did that sweep work" is answerable at all.
    # The first cut of this schema dropped the edge, which would have quietly
    # flattened every sweep back into a pair of integers.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)

    # The claim. A lease renewed by heartbeat, not a fixed wall-clock guess, so
    # a healthy-but-slow worker is never reclaimed out from under itself.
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_pid: Mapped[int | None] = mapped_column(Integer)

    # The generation this run claimed, compared against `ticket.claim_token` on
    # every write. A run holding a stale one has been superseded and its writes
    # are refused, which is what makes the fence a fence rather than a label.
    claim_token: Mapped[int | None] = mapped_column(Integer)

    legacy_job_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(_one_of("status", RUN_STATUSES), name="ck_run_status"),
        CheckConstraint(_one_of("trigger", RUN_TRIGGERS), name="ck_run_trigger"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id",
                        name="ck_run_not_own_parent"),
        Index("ix_run_ticket", "ticket_id", "started_at"),
        Index("ix_run_parent", "parent_id", "started_at"),
        Index("ix_run_org_started", "org_id", "started_at"),
        Index("ix_run_legacy", "legacy_job_id"),
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
# `during_run` and `injected` all cease to exist. See ADR 0018 for the
# render-time version of the same principle.
#
# **What deliberately did NOT move.** The per-run scratch: `live.json` (the
# in-flight draft, rewritten every ~0.4s and deliberately not fsynced),
# `queue.json`, `drafts.json`, `prefs.json`. None of it is transcript, all of it
# is rewritten constantly, and none of it survives a restart in any meaningful
# sense. The line is: the record of what was said is in the database; the
# scratchpad of a turn in flight is a file.

CHAT_MODES = ("orchestrated", "direct")
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
TURN_STATUSES = ("running", "finished", "lost")

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

    title: Mapped[str | None] = mapped_column(String(255))
    title_source: Mapped[str | None] = mapped_column(String(16))
    # The first user message, truncated. Stands in until naming succeeds, and
    # naming failing silently behind it is a known open defect, so it is a
    # separate column rather than a title that lies about being one.
    seed_title: Mapped[str | None] = mapped_column(String(255))

    model: Mapped[str | None] = mapped_column(String(64))
    effort: Mapped[str | None] = mapped_column(String(16))
    permission_mode: Mapped[str | None] = mapped_column(String(32))
    pre_plan_permission_mode: Mapped[str | None] = mapped_column(String(32))
    # NULL means ALL servers, which is not the same as none of them. A nullable
    # array carries that distinction; a join table would need a sentinel row.
    mcp_servers_enabled: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
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
            name="chat_thread_current_branch_fkey", ondelete="SET NULL",
            use_alter=True),
        Index("uq_chat_thread_external", "external_id", unique=True,
              postgresql_where=text("external_id IS NOT NULL AND deleted_at IS NULL")),
        # The thread list: one owner's chats in one scope, most recent first.
        Index("ix_chat_thread_owner", "org_id", "owner_user_id", "updated_at",
              postgresql_where=_ALIVE),
        Index("ix_chat_thread_scope", "org_id", "scope", "updated_at",
              postgresql_where=_ALIVE),
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
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)

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
        Index("uq_chat_turn_run", "run_id", unique=True),
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
            name="chat_message_turn_fkey", ondelete="SET NULL"),
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
