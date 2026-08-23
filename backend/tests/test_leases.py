"""The lease: one mechanism, two subjects, and the rules that make it safe.

Step B3 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.

WHAT IS ACTUALLY UNDER TEST is the claim the design turns on: that exclusion is
a property of ONE ATOMIC STATEMENT in Postgres rather than of a lock two
processes may not share. So the contention tests run two real concurrent
acquires against a real database, from two threads with two sessions, instead of
asserting that a mocked repo was called twice. A fixture that reported "acquired"
without a row behind it would pass while proving the opposite of the point.

THE LAST TWO TESTS KEEP THE REST HONEST: with no relational store, a lease
cannot be taken and `tasks.claim()` still works. The ticket store is dual-backed
and every project tree is file-backed by design, so a lease that made claiming
impossible without Postgres would break the participant that most needs to
claim.

Needs the OWNER credential, not just the application one: the fixture mints a
throwaway organization, and creating a tenant is deliberately outside what the
application role may do (`db/engine.py`). Skipped without it, like the rest of
the db suite -- except the no-database tests, which need no database by
definition and run everywhere.
"""
from __future__ import annotations

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sqlalchemy")

_HAS_DB = bool(os.environ.get("WORKSHOP_DATABASE_ADMIN_URL"))
needs_db = pytest.mark.skipif(
    not _HAS_DB,
    reason="no WORKSHOP_DATABASE_ADMIN_URL; the fixture mints a throwaway org, "
           "which the application role deliberately cannot do",
)


@pytest.fixture()
def tenant(monkeypatch):
    """A throwaway org/user, with the lease service pinned to it."""
    from db.engine import owner_session
    from db.ids import uuid7
    from db.models import Membership, Organization, UserAccount
    from services import tenancy

    from tests.conftest import (DB_ORG_PREFIX, _delete_test_orgs,
                                _delete_test_users)

    tag = uuid.uuid4().hex[:8]
    org_ids: list = []
    user_ids: list = []
    try:
        with owner_session() as s:
            user = UserAccount(id=uuid7(), email=f"lease-{tag}@example.test",
                               name="Lease test")
            s.add(user)
            s.flush()
            user_ids.append(user.id)
            org = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}lease-{tag}",
                               name="Lease test")
            s.add(org)
            s.flush()
            s.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
            org_ids.append(org.id)

        org_id = org_ids[0]
        monkeypatch.setattr(tenancy, "configured", lambda: True)
        monkeypatch.setattr(tenancy, "available", lambda: True)
        monkeypatch.setattr(tenancy, "org_and_user", lambda: (org_id, user.id))
        yield {"org_id": org_id, "user_id": user.id}
    finally:
        _delete_test_orgs(org_ids)
        _delete_test_users(user_ids)


@pytest.fixture()
def empty_presence(tmp_path, monkeypatch):
    """A presence channel with nothing in it, pointed away from the real one.

    Every test in this file that involves a `chat` holder needs it, because
    section 6's rule gates an interactive holder's lease on its presence and the
    installation's own channel is full of live rows.
    """
    from services import presence

    channel = tmp_path / "presence"
    channel.mkdir()
    monkeypatch.setattr(presence, "PRESENCE_DIR", channel)
    return channel


def _subject() -> str:
    """A subject id no other test or installation could collide with.

    `UNIQUE (subject_kind, subject_id)` is deliberately NOT org-scoped (the
    subject is one physical thing), so a fixed literal here would make two
    concurrent test runs contend with each other for real.
    """
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# exclusion
# ---------------------------------------------------------------------------

@needs_db
def test_two_concurrent_acquires_of_one_subject_leave_exactly_one_winner(tenant):
    """The central claim, exercised concurrently rather than asserted.

    Two threads, two sessions, one statement each. Whichever reaches the unique
    index second finds the row no longer expired, its `DO UPDATE` matches
    nothing, and `RETURNING` hands it no row -- which is how it learns it lost.
    """
    from services import leases

    subject = _subject()

    def take(holder: str):
        return leases.acquire("worktree", subject, holder_kind="run",
                              holder_id=holder)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(take, ["run-a", "run-b"]))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, (
        f"expected exactly one winner, got {results}. Two holders of one tree "
        f"is the collision this table exists to prevent.")
    assert winners[0]["state"] == "held"
    assert winners[0]["generation"] == 1

    # And the loser is not merely unlucky: the subject really is held, by the
    # winner, and a third acquire is refused too.
    assert leases.acquire("worktree", subject, holder_kind="run",
                          holder_id="run-c") is None
    held = leases.get("worktree", subject)
    assert held["holder_id"] == winners[0]["holder_id"]


@needs_db
def test_ten_concurrent_acquires_still_leave_exactly_one(tenant):
    """The same claim under more pressure, because "exactly one" is the whole
    property and two threads can pass by luck."""
    from services import leases

    subject = _subject()
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(
            lambda h: leases.acquire("worktree", subject, holder_kind="run",
                                     holder_id=h),
            [f"run-{i}" for i in range(10)]))
    assert sum(1 for r in results if r is not None) == 1


@needs_db
def test_an_expired_lease_is_acquirable_and_the_generation_moves(tenant):
    """Expiry is a comparison, not an event: nothing sweeps, nothing reconciles.

    The generation bump is the half that matters. A lease that changed hands
    without moving it would leave the previous holder's fencing token valid, so
    a resurrected worker would write alongside its replacement -- the exact
    failure the Kubernetes lease pattern pairs a fencing token with.
    """
    from services import leases

    subject = _subject()
    first = leases.acquire("ticket", subject, holder_kind="run",
                           holder_id="run-a", ttl_sec=1)
    assert first["generation"] == 1

    # Expire it the way the world does: by moving the clock forward. The column
    # is server-computed, so the test moves the ROW rather than a Python clock,
    # which is also a check that nothing here reads a caller's clock.
    _expire_now(tenant["org_id"], "ticket", subject)

    second = leases.acquire("ticket", subject, holder_kind="run",
                            holder_id="run-b")
    assert second is not None, "an expired lease must be acquirable"
    assert second["generation"] == 2, "the fence must move when the holder does"
    assert second["holder_id"] == "run-b"


@needs_db
def test_a_renew_with_a_stale_generation_is_refused(tenant):
    """`WHERE generation = :g` is the fence. A displaced holder finds out by
    renewing nothing."""
    from services import leases

    subject = _subject()
    first = leases.acquire("worktree", subject, holder_kind="run",
                           holder_id="run-a", ttl_sec=1)
    _expire_now(tenant["org_id"], "worktree", subject)
    second = leases.acquire("worktree", subject, holder_kind="run",
                            holder_id="run-b")

    assert leases.renew("worktree", subject, first["generation"]) is None
    assert leases.renew("worktree", subject, second["generation"]) is not None
    # And the refusal changed nothing: the live holder is untouched.
    assert leases.get("worktree", subject)["holder_id"] == "run-b"


@needs_db
def test_a_release_with_a_stale_generation_is_refused(tenant):
    """A zombie that wakes up and tidily releases must not release its
    replacement's lease. That is the fence pointing the other way."""
    from services import leases

    subject = _subject()
    first = leases.acquire("worktree", subject, holder_kind="run",
                           holder_id="run-a", ttl_sec=1)
    _expire_now(tenant["org_id"], "worktree", subject)
    second = leases.acquire("worktree", subject, holder_kind="run",
                            holder_id="run-b")

    assert leases.release("worktree", subject, first["generation"]) is False
    assert leases.get("worktree", subject)["state"] == "held"
    assert leases.release("worktree", subject, second["generation"]) is True
    assert leases.get("worktree", subject)["state"] == "expired"


@needs_db
def test_a_released_lease_keeps_its_generation_counter(tenant):
    """Release EXPIRES the row; it never deletes it. Deleting would restart the
    count at 1 and hand a resurrected holder's stale token a live match."""
    from services import leases

    subject = _subject()
    first = leases.acquire("ticket", subject, holder_kind="run", holder_id="a")
    leases.release("ticket", subject, first["generation"])
    again = leases.acquire("ticket", subject, holder_kind="run", holder_id="b")
    assert again["generation"] == first["generation"] + 1


@needs_db
def test_a_caller_that_already_holds_a_higher_fence_carries_it_in(tenant):
    """`min_generation` exists so the ticket lease can take over `claim_token`
    on a ticket that has already been claimed five times. A fence that goes
    backwards is a fence a zombie can replay."""
    from services import leases

    subject = _subject()
    row = leases.acquire("ticket", subject, holder_kind="run", holder_id="a",
                         min_generation=6)
    assert row["generation"] == 6
    _expire_now(tenant["org_id"], "ticket", subject)
    assert leases.acquire("ticket", subject, holder_kind="run", holder_id="b",
                          min_generation=2)["generation"] == 7


# ---------------------------------------------------------------------------
# two subjects, one mechanism
# ---------------------------------------------------------------------------

@needs_db
def test_a_ticket_lease_and_a_worktree_lease_on_the_same_work_do_not_interfere(
        tenant, empty_presence):
    """Section 5's two subjects, held at once, by different sessions.

    This is the arrangement the design is FOR: a sweep holds the ticket (it took
    the work) while a tree it created carries its own lease (who may write
    there). They are one table and one mechanism, and the typed subject is what
    keeps them from colliding -- including when the two ids happen to be equal,
    which is the sharpest version of the question.
    """
    from services import leases

    from services import presence

    shared = _subject()  # same id under both kinds, on purpose
    # The chat holder writes presence first: section 6 renews an INTERACTIVE
    # holder's lease by its presence, so a chat that never says it is here does
    # not hold anything. That is the rule, not a fixture detail.
    presence.beat("chat-1", session_kind="chat", subject_kind="worktree",
                  subject_id=shared)
    ticket = leases.acquire("ticket", shared, holder_kind="run",
                            holder_id="sweep-1")
    tree = leases.acquire("worktree", shared, holder_kind="chat",
                          holder_id="chat-1")
    assert ticket is not None and tree is not None
    assert ticket["generation"] == tree["generation"] == 1

    # Releasing one leaves the other exactly where it was.
    assert leases.release("ticket", shared, ticket["generation"]) is True
    assert leases.get("worktree", shared)["state"] == "held"
    assert leases.get("worktree", shared)["holder_id"] == "chat-1"

    # And renewing the tree lease does not touch the ticket's row.
    leases.renew("worktree", shared, tree["generation"], work=True)
    assert leases.get("ticket", shared)["state"] == "expired"


@needs_db
def test_the_tree_lease_does_not_inherit_the_tickets_one_hour_ttl(tenant):
    """Section 5: "an hour of write-exclusivity for a crashed chat is unusable".

    So the module's DEFAULT is 90 seconds, and the hour is something the ticket
    caller passes in explicitly. Asserted on the row rather than on the
    constant, because a default that is right in the source and wrong on the
    row is the failure that matters.
    """
    from services import leases

    subject = _subject()
    row = leases.acquire("worktree", subject, holder_kind="chat",
                         holder_id="chat-x")
    life = (_parse(row["expires_at"]) - _parse(row["acquired_at"])).total_seconds()
    assert 85 <= life <= 95, f"tree lease should live ~90s, lived {life}s"
    assert leases.LEASE_TTL_SEC == 90
    assert leases.HEARTBEAT_SEC == 30
    assert leases.IDLE_AFTER_SEC == 600
    assert leases.PREEMPT_GRACE_SEC == 30


@needs_db
def test_idle_is_a_timestamp_and_not_a_third_state(tenant):
    """Section 6: two lease states, held or expired. Idle is a WARNING.

    And it is measured against the last WORK event rather than the last
    renewal, so a holder that heartbeats every 30 seconds while making no
    progress still shows as idle -- which is the entire point of the window.
    """
    from services import leases

    subject = _subject()
    row = leases.acquire("worktree", subject, holder_kind="run",
                         holder_id="run-a")
    assert row["state"] == "held" and row["idle_since"] is None

    _age_work(tenant["org_id"], "worktree", subject, seconds=leases.IDLE_AFTER_SEC + 60)
    # A plain renewal keeps it alive and does NOT clear the idle clock.
    idle = leases.renew("worktree", subject, row["generation"])
    assert idle["state"] == "held", "idle is not a fourth state; it is still held"
    assert idle["idle_since"] is not None

    # A WORK event does clear it.
    busy = leases.renew("worktree", subject, row["generation"], work=True)
    assert busy["idle_since"] is None


# ---------------------------------------------------------------------------
# the presence join rule (section 6)
# ---------------------------------------------------------------------------

@needs_db
def test_presence_release_frees_an_interactive_holders_lease_immediately(
        tenant, empty_presence):
    """Closing a chat tab must cost nothing (section 7).

    Without this rule the tree stays locked for a full TTL after the only
    session that wanted it has gone, which is structurally the Gitpod failure
    section 16.3 records with the roles swapped.
    """
    from services import leases, presence

    subject = _subject()
    chat = "chat-" + uuid.uuid4().hex[:8]
    presence.beat(chat, session_kind="chat", subject_kind="worktree",
                  subject_id=subject, activity="editing")
    held = leases.acquire("worktree", subject, holder_kind="chat",
                          holder_id=chat)
    assert held["state"] == "held"

    assert presence.release(chat) is True

    assert leases.get("worktree", subject)["state"] == "expired"
    # Immediately, not in 90 seconds: someone else can take it right now.
    taken = leases.acquire("worktree", subject, holder_kind="run",
                           holder_id="run-next")
    assert taken is not None and taken["generation"] == 2


@needs_db
def test_an_interactive_holder_whose_presence_expired_is_not_holding(
        tenant, empty_presence):
    """The PASSIVE half of the same rule: a chat that stops beating without
    saying anything.

    There is no process left to make a write, so this cannot be one. It is a
    comparison, in the same shape `presence.effective_expiry` uses: it can only
    ever SHORTEN a lease. The row's own `expires_at` is still an hour away here,
    and the lease is still not held.
    """
    from services import leases, presence

    subject = _subject()
    chat = "chat-" + uuid.uuid4().hex[:8]
    presence.beat(chat, session_kind="chat", subject_kind="worktree",
                  subject_id=subject, ttl_sec=1)
    row = leases.acquire("worktree", subject, holder_kind="chat",
                         holder_id=chat, ttl_sec=3600)
    assert row["state"] == "held"

    # Age the presence past its TTL without touching the lease row at all.
    _age_presence(presence, chat, seconds=120)

    assert leases.get("worktree", subject)["state"] == "expired"
    taken = leases.acquire("worktree", subject, holder_kind="run",
                           holder_id="run-next")
    assert taken is not None, ("an interactive holder with no presence is not "
                              "holding, whatever its expires_at says")
    assert taken["generation"] == 2


@needs_db
def test_a_non_interactive_holder_is_not_gated_on_presence(tenant, empty_presence):
    """The other half of section 6's rule, and the one that would do damage if
    it were got wrong. A sweep's lease is renewed by its WORK EVENTS, not by
    presence: a run that is twenty minutes into a `claude -p` turn writes no
    presence and is not absent. Gating it would reclaim live work."""
    from services import leases

    subject = _subject()
    row = leases.acquire("worktree", subject, holder_kind="run",
                         holder_id="sweep-with-no-presence", ttl_sec=3600)
    assert row["state"] == "held"
    assert leases.get("worktree", subject)["state"] == "held"
    assert leases.acquire("worktree", subject, holder_kind="run",
                          holder_id="other") is None


@needs_db
def test_the_directory_finally_answers_who_holds_what(tenant, empty_presence):
    """`presence.directory(lease_source=...)`, the seam B4 left and B3 fills.

    The join itself is unchanged -- this passes a callable and reads the field.
    """
    from services import leases, presence

    subject = _subject()
    chat = "chat-" + uuid.uuid4().hex[:8]
    presence.beat(chat, session_kind="chat", subject_kind="worktree",
                  subject_id=subject, activity="editing")
    leases.acquire("worktree", subject, holder_kind="chat", holder_id=chat)

    entries = presence.directory(lease_source=leases.lease_source)
    row = next(e for e in entries if e["session_id"] == chat)
    assert row["lease"] is not None
    assert row["lease"]["holder_id"] == chat
    assert row["lease"]["state"] == "held"
    assert row["lease"]["generation"] == 1

    # And with no source it is honestly absent rather than a plausible fake.
    plain = next(e for e in presence.directory() if e["session_id"] == chat)
    assert plain["lease"] is None


# ---------------------------------------------------------------------------
# the ticket lease, through `services/tasks.py`
# ---------------------------------------------------------------------------

@needs_db
def test_tasks_claim_takes_the_ticket_lease_and_a_second_claim_is_refused(
        tenant, tmp_path):
    """`tasks` calls the lease module rather than keeping a second claim
    implementation. The generation on the record IS the lease's.

    A file-backed store under `tmp_path`, deliberately: this proves the ticket
    lease is taken for ANY scope that can reach a database, not only for the two
    scopes whose tickets live in Postgres.
    """
    from services import leases, tasks

    root = tmp_path / "scope"
    root.mkdir()
    task = tasks.add(root, text="work on the thing")
    claimed = tasks.claim(root, task["id"], "job-1")
    assert claimed is not None

    lease = leases.get("ticket", task["id"])
    assert lease is not None and lease["state"] == "held"
    assert lease["holder_id"] == "job-1"
    assert claimed["claim_token"] == lease["generation"], (
        "the record's fence must be the lease's generation, not a second count")

    # Releasing the ticket back to `todo` gives the lease back with it: a ticket
    # that is not being worked must not hold one.
    tasks.update(root, task["id"], status="todo",
                 expect_claim_token=claimed["claim_token"])
    assert leases.get("ticket", task["id"])["state"] == "expired"

    # AND THE CASE SECTION 5 IS ABOUT. Two sweeps both see `todo`. The first
    # takes the ticket lease; the second is refused BEFORE it can create a
    # worktree, even though the record still says `todo` and its own
    # compare-and-set would have passed.
    other = leases.acquire("ticket", task["id"], holder_kind="run",
                           holder_id="the-other-sweep")
    assert other is not None
    assert tasks.get_task(root, task["id"])["status"] == "todo"
    assert tasks.claim(root, task["id"], "job-2") is None


@needs_db
def test_finishing_a_ticket_gives_its_lease_back(tenant, tmp_path):
    """A ticket that is finished, blocked, parked or released is not being
    worked, so it must not hold a lease -- the same rule the record has always
    applied to `lease_until` and `claim_pid`."""
    from services import leases, tasks

    root = tmp_path / "scope"
    root.mkdir()
    task = tasks.add(root, text="finish me")
    claimed = tasks.claim(root, task["id"], "job-1")
    tasks.update(root, task["id"], status="done",
                 expect_claim_token=claimed["claim_token"])
    assert leases.get("ticket", task["id"])["state"] == "expired"


@needs_db
def test_an_operator_edit_fences_the_worker_through_the_lease(tenant, tmp_path):
    """`update()`'s Fix 1, now with one authority for the number.

    An untokened material edit to a ticket a worker holds bumps the generation,
    so the worker's next tokened write is refused. Before B3 the bump was a
    counter on the record and the lease did not exist; now the lease issues it
    and the two cannot disagree.
    """
    from services import leases, tasks

    root = tmp_path / "scope"
    root.mkdir()
    task = tasks.add(root, text="held by a worker")
    claimed = tasks.claim(root, task["id"], "job-1")

    edited = tasks.update(root, task["id"], title="the operator renamed it")
    assert edited["claim_token"] > claimed["claim_token"]
    assert leases.get("ticket", task["id"])["generation"] == edited["claim_token"]

    # The displaced worker's fenced write is refused, by the record's own check.
    assert tasks.update(root, task["id"], status="done",
                        expect_claim_token=claimed["claim_token"]) is None
    # And its heartbeat is refused too.
    assert tasks.heartbeat(root, task["id"], claimed["claim_token"]) is None


@needs_db
def test_a_heartbeat_renews_the_ticket_lease_as_a_work_event(tenant, tmp_path):
    """`ralph.sh` renews by shelling into the task CLI, which renews the lease.

    As WORK, not as a bare beat: the call is only made from inside a running
    worker's loop, so it is a work event by construction, and the 10-minute idle
    window has to measure progress rather than that the process is up.
    """
    from services import leases, tasks

    root = tmp_path / "scope"
    root.mkdir()
    task = tasks.add(root, text="long running")
    claimed = tasks.claim(root, task["id"], "job-1")
    _age_work(tenant["org_id"], "ticket", task["id"],
              seconds=leases.IDLE_AFTER_SEC + 60)
    assert leases.get("ticket", task["id"])["idle_since"] is not None

    assert tasks.heartbeat(root, task["id"], claimed["claim_token"]) is not None
    assert leases.get("ticket", task["id"])["idle_since"] is None


@needs_db
def test_reclaiming_a_dead_worker_frees_the_ticket_lease(tenant, tmp_path):
    """Otherwise the reclaim does not actually free the ticket: the next
    `claim()` would find the dead worker's lease still held and refuse."""
    from services import leases, tasks

    root = tmp_path / "scope"
    root.mkdir()
    task = tasks.add(root, text="worker will die")
    tasks.claim(root, task["id"], "job-1", ttl=3600)

    later = datetime.now(timezone.utc) + timedelta(seconds=7200)
    actions = tasks.reclaim(root, now=later)
    assert [a["action"] for a in actions] == ["released"]
    assert leases.get("ticket", task["id"])["state"] == "expired"
    assert tasks.claim(root, task["id"], "job-2") is not None


# ---------------------------------------------------------------------------
# and none of it may require a database
# ---------------------------------------------------------------------------

def test_without_a_relational_store_a_lease_cannot_be_taken_and_says_so(monkeypatch):
    """`LeasesUnavailable`, not a silent None. "Nobody holds this because the
    database is down" must never read the same as "nobody holds this"."""
    from services import leases, tenancy

    monkeypatch.setattr(tenancy, "configured", lambda: False)
    monkeypatch.setattr(tenancy, "available", lambda: False)

    assert leases.available() is False
    with pytest.raises(leases.LeasesUnavailable):
        leases.acquire("worktree", "anything", holder_kind="run", holder_id="r")
    with pytest.raises(leases.LeasesUnavailable):
        leases.renew("worktree", "anything", 1)
    # The directory seam is the one exception, and deliberately: it must answer
    # "who is here" even when it cannot decorate a row.
    assert leases.lease_source("worktree", "anything") is None


def test_a_project_tree_with_no_database_still_claims_its_tickets(monkeypatch,
                                                                  tmp_path):
    """THE TEST THAT KEEPS THE REST HONEST. Every tree under `projects/` is
    file-backed by design and has no database near it, so a claim path that
    required a lease row would break the participant that most needs to claim.
    The record's own counter is the fence there, exactly as before B3."""
    from services import tasks, tenancy

    monkeypatch.setattr(tenancy, "configured", lambda: False)
    monkeypatch.setattr(tenancy, "available", lambda: False)

    root = tmp_path / "project"
    root.mkdir()
    task = tasks.add(root, text="offline work")
    claimed = tasks.claim(root, task["id"], "job-1")
    assert claimed is not None
    assert claimed["claim_token"] == 1
    assert claimed["status"] == "doing"
    # The status compare-and-set still excludes the second claimer.
    assert tasks.claim(root, task["id"], "job-2") is None
    # And the fence still fences.
    assert tasks.heartbeat(root, task["id"], 99) is None
    assert tasks.heartbeat(root, task["id"], 1) is not None


# ---------------------------------------------------------------------------
# helpers that move the SERVER's idea of time, never the caller's
# ---------------------------------------------------------------------------

def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sql(org_id, statement: str, params: dict) -> None:
    """Run one owner-level statement. Used only to AGE a row.

    Ageing by moving the row rather than by patching a Python clock is the point:
    every comparison in this feature is `expires_at < now()` evaluated by
    Postgres, so a test that moved a Python clock would prove nothing.
    """
    from sqlalchemy import text

    from db.engine import owner_session

    with owner_session() as s:
        s.execute(text(statement), {"org": str(org_id), **params})


def _expire_now(org_id, subject_kind: str, subject_id: str) -> None:
    _sql(org_id,
         "UPDATE lease SET expires_at = now() - interval '1 second' "
         "WHERE org_id = :org AND subject_kind = :sk AND subject_id = :sid",
         {"sk": subject_kind, "sid": str(subject_id)})


def _age_work(org_id, subject_kind: str, subject_id: str, *, seconds: int) -> None:
    _sql(org_id,
         "UPDATE lease SET last_work_at = now() - make_interval(secs => :secs) "
         "WHERE org_id = :org AND subject_kind = :sk AND subject_id = :sid",
         {"sk": subject_kind, "sid": str(subject_id), "secs": seconds})


def _age_presence(presence_module, session_id: str, *, seconds: int) -> None:
    """Rewrite a presence record's timestamps into the past, in place.

    The channel is a file and `beat()` computes `expires_at` itself, so this is
    the honest way to say "that beat was two minutes ago" without asking the
    module to accept a caller's clock -- which is exactly what it must never do.
    """
    import json

    path = presence_module.PRESENCE_DIR / f"{session_id}.json"
    record = json.loads(path.read_text())
    then = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    record["ts"] = then.isoformat().replace("+00:00", "Z")
    record["expires_at"] = (
        then + timedelta(seconds=record.get("ttl_sec") or 90)
    ).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(record))
