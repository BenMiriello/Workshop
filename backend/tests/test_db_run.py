"""Round-trip test for the `run` table.

Inserts a run carrying every field the live .runtime/jobs.jsonl actually uses
(measured 2026-08-22, 3,527 collapsed records), reads it back, and asserts
nothing was silently dropped or coerced.

Skipped when there is no database, like all db-layer tests.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)

pytest.importorskip("sqlalchemy")


@pytest.fixture
def org_with_ticket():
    """A throwaway org, project, state, and one ticket to link a run to."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Organization, Project, Ticket, WorkflowState
    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}run-{tag}",
                             name="Run test")
            s.add(o)
            s.flush()
            oid = o.id

        with tenant_session(oid) as s:
            p = Project(id=uuid7(), org_id=oid, slug=f"p-{tag}", name="P",
                        key=f"R{tag[:3]}")
            s.add(p)
            s.flush()
            st = WorkflowState(id=uuid7(), org_id=oid, project_id=p.id,
                               name="Todo", category="unstarted", is_default=True)
            s.add(st)
            s.flush()
            t = Ticket(id=uuid7(), org_id=oid, project_id=p.id, number=1,
                       title="ticket for run test", state_id=st.id)
            s.add(t)
            s.flush()
            ctx = {"org": oid, "ticket": t.id}

        yield ctx
    finally:
        _delete_test_orgs([oid])


def _now():
    return datetime.now(timezone.utc)


def test_run_insert_and_read_all_fields(org_with_ticket):
    """A run carrying every field the live log uses survives a round-trip."""
    from db.engine import tenant_session
    from db.models import Run

    oid = org_with_ticket["org"]
    tid = org_with_ticket["ticket"]

    job_id = f"job_1792100000000_abcd"
    parent_job_id = f"job_1792099990000_ef01"
    started = _now()
    ended = _now()

    # Insert the parent first so the self-FK is satisfiable.
    with tenant_session(oid) as s:
        parent = Run(
            id=parent_job_id,
            org_id=oid,
            trigger="cron",
            status="done",
            started_at=started,
            ended_at=ended,
        )
        s.add(parent)
        s.flush()

        run = Run(
            id=job_id,
            org_id=oid,
            agent_id="agent-abc123",
            project_slug="my-project",
            chat_id="discord:1234567890",
            trigger="manual",
            status="done",
            started_at=started,
            ended_at=ended,
            exit_code=0,
            tasks_done=3,
            tasks_failed=0,
            pid=12345,
            kind="sweep",
            task_id=tid,
            parent_id=parent_job_id,
            evidence={
                "kind": "receipt",
                "summary": "3 tasks done, 0 failed",
                "files": ["backend/db/models.py"],
                "commits": ["abc123def456"],
                "iterations": 2,
                "tokens": 8000,
            },
            budget={"max_turns": 10, "max_cost_usd": 0.50},
            binding={"deploy_id": "dep_xyz"},
        )
        s.add(run)
        s.flush()
        run_id = run.id

    # Read back and assert all fields survived.
    with tenant_session(oid) as s:
        got = s.get(Run, run_id)
        assert got is not None, "run not found after insert"
        assert got.id == job_id
        assert got.org_id == oid
        assert got.agent_id == "agent-abc123"
        assert got.project_slug == "my-project"
        assert got.chat_id == "discord:1234567890"
        assert got.trigger == "manual"
        assert got.status == "done"
        assert got.started_at is not None
        assert got.ended_at is not None
        assert got.exit_code == 0
        assert got.tasks_done == 3
        assert got.tasks_failed == 0
        assert got.pid == 12345
        assert got.kind == "sweep"
        assert got.task_id == tid
        assert got.parent_id == parent_job_id
        assert isinstance(got.evidence, dict)
        assert got.evidence["summary"] == "3 tasks done, 0 failed"
        assert got.evidence["files"] == ["backend/db/models.py"]
        assert got.evidence["commits"] == ["abc123def456"]
        assert got.evidence["iterations"] == 2
        assert got.evidence["tokens"] == 8000
        assert isinstance(got.budget, dict)
        assert got.budget["max_turns"] == 10
        assert isinstance(got.binding, dict)
        assert got.binding["deploy_id"] == "dep_xyz"


def test_run_minimal_fields(org_with_ticket):
    """A run with only required fields (trigger/status/started_at) is valid."""
    from db.engine import tenant_session
    from db.models import Run

    oid = org_with_ticket["org"]
    job_id = "job_1792100001000_1234"

    with tenant_session(oid) as s:
        run = Run(
            id=job_id,
            org_id=oid,
            trigger="cron",
            status="running",
            started_at=_now(),
        )
        s.add(run)
        s.flush()

    with tenant_session(oid) as s:
        got = s.get(Run, job_id)
        assert got is not None
        assert got.agent_id is None
        assert got.task_id is None
        assert got.parent_id is None
        assert got.evidence is None
        assert got.ended_at is None


def test_run_ticket_link_is_a_real_fk(org_with_ticket):
    """task_id is a FK to ticket; a nonexistent ticket is rejected."""
    from sqlalchemy.exc import IntegrityError
    from db.engine import tenant_session
    from db.models import Run

    oid = org_with_ticket["org"]
    from db.ids import uuid7

    with pytest.raises(IntegrityError):
        with tenant_session(oid) as s:
            run = Run(
                id="job_1792100002000_dead",
                org_id=oid,
                trigger="manual",
                status="done",
                started_at=_now(),
                task_id=uuid7(),  # nonexistent ticket id
            )
            s.add(run)
            s.flush()


def test_run_invalid_trigger_rejected(org_with_ticket):
    """A trigger value not in the controlled vocabulary violates the CHECK."""
    from sqlalchemy.exc import IntegrityError
    from db.engine import tenant_session
    from db.models import Run

    oid = org_with_ticket["org"]

    with pytest.raises(IntegrityError):
        with tenant_session(oid) as s:
            run = Run(
                id="job_1792100003000_bad1",
                org_id=oid,
                trigger="unknown_trigger",
                status="done",
                started_at=_now(),
            )
            s.add(run)
            s.flush()


def test_run_invalid_status_rejected(org_with_ticket):
    """A status value not in the controlled vocabulary violates the CHECK."""
    from sqlalchemy.exc import IntegrityError
    from db.engine import tenant_session
    from db.models import Run

    oid = org_with_ticket["org"]

    with pytest.raises(IntegrityError):
        with tenant_session(oid) as s:
            run = Run(
                id="job_1792100004000_bad2",
                org_id=oid,
                trigger="manual",
                status="crashed",  # not in JOB_STATUSES
                started_at=_now(),
            )
            s.add(run)
            s.flush()
