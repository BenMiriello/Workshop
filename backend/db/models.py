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
    CheckConstraint, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer,
    String, Text, func, text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
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
LINK_KINDS = ("blocked_by", "relates_to", "duplicates")

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

_ALIVE = text("deleted_at IS NULL")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """`col IN ('a','b')`. Built rather than interpolated from a tuple repr,
    because a one-element tuple reprs as `('a',)` and that is not valid SQL."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


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

    # Ticket numbering. Settled here rather than left open: a counter column
    # taken with SELECT ... FOR UPDATE in the same transaction as the insert.
    # A Postgres sequence is global and cannot give per-project numbering, and
    # an advisory lock would serialise on a hash rather than on the real row.
    # Contention is per project, which is the correct granularity.
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

    __table_args__ = (
        CheckConstraint(f"effort IS NULL OR {_one_of('effort', EFFORTS)}",
                        name="ck_ticket_effort"),
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

    This is where the nine claim and lease columns that used to sit on the
    ticket now live, because they describe an execution and not a durable work
    item. `ticket.attempts` becomes `count(run)`, which removes a
    hand-maintained counter that had already produced a real bug.
    """
    __tablename__ = "run"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent.id", ondelete="SET NULL"))

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

    legacy_job_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(_one_of("status", RUN_STATUSES), name="ck_run_status"),
        CheckConstraint(_one_of("trigger", RUN_TRIGGERS), name="ck_run_trigger"),
        Index("ix_run_ticket", "ticket_id", "started_at"),
        Index("ix_run_org_started", "org_id", "started_at"),
        Index("ix_run_legacy", "legacy_job_id"),
    )


# Tables that carry `org_id` and therefore must be RLS-protected. The migration
# reads this list, so a table added to this module is protected by construction
# rather than by someone remembering to write the policy.
TENANT_TABLES = tuple(
    t.name for t in Base.metadata.sorted_tables
    if "org_id" in t.columns
)
