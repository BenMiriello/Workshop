"""The lease: the durable, exclusive right to write to one subject.

Step B3 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 5). One mechanism, one table, a typed subject, covering both the ticket
lease (who took this work) and the worktree lease (who may write to this tree).

THE LEASE IS BOOKKEEPING. THE MOUNT IS ENFORCEMENT.
Section 4 is the correction that reorganizes the whole design, and this module
is written to it. A fencing token works because a storage layer VALIDATES it.
For the ticket subject that layer exists and really does check
(`tasks.update()`'s `expect_claim_token`). For a working tree the storage layer
is the filesystem and git, and nothing there validates a generation: a holder
whose lease expired mid-test, whose replacement has already started, will
`git add -A && git commit` into the same directory and no check anywhere stops
it. What excludes a second writer from a tree is getting it mounted READ-ONLY,
which is step B1. Nothing in this module protects a tree; it records who holds
one.

TWO SUBJECTS, BOTH NEEDED (section 5). Revision 1 folded the ticket lease into
the tree lease, arguing that holding the tree admits you to the work. That is
wrong at the only moment that matters: a `todo` ticket has no tree yet, so two
sweeps both see `todo`, both create a worktree, both hold a lease on DIFFERENT
trees, and both work the same ticket. The ticket lease is kept.

--------------------------------------------------------------------------
WHY THE UPSERT AND NOT `SELECT ... FOR UPDATE SKIP LOCKED`
--------------------------------------------------------------------------
Section 5 names the boring alternative and it deserves a real answer rather than
silence: `SELECT ... FOR UPDATE SKIP LOCKED` is the standard Postgres
queue-claim primitive, and for the ticket subject "using it would make this a
genuine simplification rather than a re-implementation". It was evaluated
against the upsert on the ticket subject specifically, and the upsert wins on
three counts that are not stylistic:

1. **SKIP LOCKED's exclusion lasts exactly one transaction.** The row lock is
   released at COMMIT. A ticket claim has to hold for as long as the work does
   -- `tasks.LEASE_TTL_SEC` is an hour, sized as a missed-heartbeat window
   around a single `claude -p` turn that can run for twenty minutes -- and the
   claiming process is a bash sweep that exits and comes back. Holding a
   Postgres transaction open across that is exactly the anti-pattern SKIP
   LOCKED exists inside of, not a use of it. The moment the transaction ends the
   exclusion has to be re-expressed as durable state, which is a lease, which is
   this table. SKIP LOCKED would be the primitive plus the table, not instead
   of it.

2. **It gives no fencing generation.** A lock is not a token. `tasks.update()`
   already refuses a write whose `claim_token` does not match, which is the half
   of the Kubernetes lease pattern that makes a resurrected zombie harmless, and
   a row lock cannot express it. We would end up bumping a counter under the
   lock -- at which point the counter, not the lock, is the mechanism.

3. **It answers a different question.** SKIP LOCKED is for "hand me any row
   nobody else is working", a set-selection primitive. Both callers here name
   the subject they want: `tasks.claim()` is given a ticket id, and a worktree
   lease is taken on a specific directory. Selecting WHICH ticket to work is
   `next_ready`'s job and is deliberately not changed by this step.

Where SKIP LOCKED would genuinely be the better tool is a future batch dispatcher
that pulls N ready tickets in one query. That is not this, and if it is ever
built it should sit in front of this table rather than replace it.

--------------------------------------------------------------------------
WHAT THIS MODULE DOES *NOT* DO
--------------------------------------------------------------------------
No preempt, no steer, no yield: those are verbs with a grace window, a snapshot
and a timeline entry, and they are step B7. `fence()` is the storage primitive
underneath preempt, not preempt.

No read-only mount (B1), no change to how work is SELECTED (`next_ready`), no
change to the supervised or budget ceilings.

--------------------------------------------------------------------------
DEGRADING WITHOUT A DATABASE IS PART OF THE CONTRACT
--------------------------------------------------------------------------
The same rule `services/worktree_records.py` follows, for the same reason. The
ticket store is dual-backed: `development` and `workshop` read Postgres, and
every project tree under `projects/` is file-backed BY DESIGN and has no
database anywhere near it. So every writer here raises `LeasesUnavailable` when
there is no store, and `services/tasks.py` treats that as "this scope keeps its
fence in its own record", never as a failed claim. A project sweep on a laptop
with no Postgres must still be able to claim a ticket.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from db import lease_repo
from db.engine import tenant_session
from db.models import LEASE_HOLDER_KINDS, LEASE_SUBJECT_KINDS
from services import ownership, presence, tenancy

__all__ = [
    "LEASE_SUBJECT_KINDS", "LEASE_HOLDER_KINDS",
    "LEASE_TTL_SEC", "HEARTBEAT_SEC", "IDLE_AFTER_SEC", "PREEMPT_GRACE_SEC",
    "LeasesUnavailable", "available",
    "acquire", "renew", "release", "release_for_holder", "fence",
    "get", "list_leases", "lease_source", "main",
]

# --- the windows (section 5, "Windows, with values") ------------------------
#
# A TREE LEASE MUST NOT INHERIT THE TICKET LEASE'S ONE-HOUR TTL, and section 5
# says why in one line: "an hour of write-exclusivity for a crashed chat is
# unusable". A chat that crashes, or a tab that is closed, would lock the
# directory for an hour against every other participant, which is the orphaned
# tree this whole design exists to remove. So the DEFAULT here is the short one
# and the long one is passed in by the caller that has earned it:
# `services/tasks.py` hands its own `LEASE_TTL_SEC` (3600) to the ticket
# subject, where an hour is correct because the lease is sized as a
# missed-heartbeat window around a single `claude -p` turn -- see that module's
# own note. There is no second copy of 3600 in this file.
LEASE_TTL_SEC = 90

# Renew at a third of the TTL, so two consecutive heartbeats can be lost to a
# slow disk or a scheduling stall without the lease lapsing under a holder that
# is perfectly alive.
HEARTBEAT_SEC = 30

# "Idle after 10 minutes with no work event." A TIMESTAMP, NOT A STATE
# (section 6: two lease states, held or expired). Measured against
# `last_work_at` rather than `last_renewed_at`, because a holder that heartbeats
# every 30 seconds while making no progress is exactly what this window is for.
IDLE_AFTER_SEC = 600

# How long a holder gets to quiesce when it is preempted. Recorded here because
# it is one of section 5's four values and they belong together; the verb that
# uses it is step B7 and is not built.
PREEMPT_GRACE_SEC = 30


class LeasesUnavailable(RuntimeError):
    """The relational store is not configured or not reachable.

    Callers inside `services/tasks.py` swallow this: a file-backed scope keeps
    its fence in its own record and predates this table. Callers at the HTTP
    layer should report it as a 503, for the reason `services/changes.py` gives
    -- "nobody holds this" because the database is down must not read the same
    as "nobody holds this" because nobody does.
    """


def _require_db() -> tuple[UUID, UUID]:
    if not tenancy.configured() or not tenancy.available():
        raise LeasesUnavailable("no relational store configured/reachable")
    return tenancy.org_and_user()


def available() -> bool:
    """Whether a lease can be taken at all. Cheap, and never raises."""
    try:
        _require_db()
        return True
    except Exception:
        return False


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(v)


# --- the presence join rule (section 6) -------------------------------------
#
# > An interactive holder's lease is renewed by its PRESENCE. A non-interactive
# > holder's is renewed by its WORK EVENTS. Presence expiry for an interactive
# > holder releases its lease immediately.
#
# Without that rule, closing a chat tab orphans a tree for a full TTL, which is
# exactly the cost section 7 promises is zero.
#
# It has two halves and they are implemented in two different places, because
# they are two different events:
#
#   * The EXPLICIT half -- a chat says it is leaving -- is a write, and it hangs
#     off `presence.release()`, which calls `release_for_holder` here. That is
#     the tab-close path and it costs one UPDATE.
#
#   * The PASSIVE half -- a chat stops beating without saying anything -- cannot
#     be a write, because there is no process left to make it. It is a
#     comparison, applied on read and immediately before an acquire, in exactly
#     the shape `presence.effective_expiry` already uses for its own accelerator:
#     a `min()` that can only ever SHORTEN a lifetime, never extend one. A
#     presence store that is empty, unreadable or unwritten therefore cannot
#     lengthen a lease by so much as a second.
#
# Only `chat` holders are gated on presence. A `run` holder is a sweep whose
# lease is renewed by its work events, and a sweep that is mid-`claude -p` turn
# for twenty minutes is not absent -- gating it on presence would reclaim live
# work, which is the Gitpod failure section 16.3 records with the roles swapped.

_INTERACTIVE = "chat"


def _presence_gone(holder_id: str) -> bool:
    """True when an interactive holder's presence is definitely not live.

    FAILS OPEN, on purpose. An unreadable channel, a missing directory or a
    record that will not parse all answer False, so a broken presence store
    cannot mass-release live leases. The lease's own `expires_at` still bounds
    the damage in the other direction.
    """
    try:
        record = presence.get(str(holder_id))
    except Exception:
        return False
    if record is None:
        # Never wrote a presence at all, or the record was withdrawn. Both mean
        # nothing is here saying otherwise. A chat that holds a lease writes
        # presence (that is what renews it), so absence is meaningful for THIS
        # holder kind -- and for `run` holders this function is never called.
        return True
    try:
        return not presence.is_live(record)
    except Exception:
        return False


def _held(row: dict, *, now: datetime | None = None) -> bool:
    """Whether this row is held right now, presence rule included.

    The authority is `expires_at`, which Postgres computed and Postgres
    compares. This adds one SHORTENING overlay and nothing else.
    """
    expires = row.get("expires_at")
    if not isinstance(expires, datetime):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= (now or datetime.now(timezone.utc)):
        return False
    if row.get("holder_kind") == _INTERACTIVE and _presence_gone(row.get("holder_id")):
        return False
    return True


def _shape(row: dict | None, *, now: datetime | None = None) -> dict | None:
    """The wire shape. Ids as strings, timestamps as ISO, state derived.

    `state` IS DERIVED AND THERE ARE TWO OF THEM (section 6): held, or expired.
    `idle_since` is a TIMESTAMP the UI renders as a `warn`, not a third state
    with its own verb -- it is null on a lease whose work moved inside the idle
    window, and the moment of the last work event on one whose has not.

    `expires_at` IS THE STORED INSTANT, not the shortened one, and `state` is
    the answer that accounts for the presence rule. They can disagree: an
    interactive holder whose presence has gone reads `expired` with an
    `expires_at` still in the future, and that is the honest pair -- the column
    says what Postgres recorded, the state says whether anyone holds it. A
    client renders the state; the timestamp is there to explain it.
    """
    if row is None:
        return None
    at = now or datetime.now(timezone.utc)
    held = _held(row, now=at)
    last_work = row.get("last_work_at")
    idle_since = None
    if held and isinstance(last_work, datetime):
        stamp = last_work if last_work.tzinfo else last_work.replace(
            tzinfo=timezone.utc)
        if (at - stamp).total_seconds() > IDLE_AFTER_SEC:
            idle_since = stamp
    return {
        "id": str(row["id"]),
        "subject_kind": row["subject_kind"],
        "subject_id": row["subject_id"],
        "holder_kind": row["holder_kind"],
        "holder_id": row["holder_id"],
        "principal_user_id": (None if row.get("principal_user_id") is None
                              else str(row["principal_user_id"])),
        "generation": int(row["generation"]),
        "state": "held" if held else "expired",
        "idle_since": _iso(idle_since),
        "expires_at": _iso(row.get("expires_at")),
        "acquired_at": _iso(row.get("acquired_at")),
        "last_renewed_at": _iso(row.get("last_renewed_at")),
        "last_work_at": _iso(last_work),
    }


# --- the verbs ---------------------------------------------------------------

def acquire(subject_kind: str, subject_id: str, *, holder_kind: str,
            holder_id: str, ttl_sec: int = LEASE_TTL_SEC,
            min_generation: int = 1) -> dict | None:
    """Take the lease on this subject, or return None because someone holds it.

    ONE ATOMIC STATEMENT does the taking (`db/lease_repo.acquire`). What happens
    here first is the passive half of the presence rule: if the current holder
    is a chat whose presence has gone, its row is expired before the upsert
    runs, so the upsert's own `WHERE lease.expires_at < now()` sees an
    acquirable subject. That pre-step can only ever shorten a lease, it happens
    in the same transaction as the acquire, and it is skipped entirely unless
    the incumbent is interactive -- so the ordinary contended path is still the
    single statement section 5 asks for.
    """
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        current = lease_repo.get_lease(s, org_id, subject_kind=subject_kind,
                                       subject_id=subject_id)
        if (current is not None
                and current.get("holder_kind") == _INTERACTIVE
                and str(current.get("holder_id")) != str(holder_id)
                and _presence_gone(current.get("holder_id"))):
            lease_repo.release_for_holder(
                s, org_id, holder_id=str(current["holder_id"]),
                holder_kind=_INTERACTIVE)
        row = lease_repo.acquire(
            s, org_id, subject_kind=subject_kind, subject_id=str(subject_id),
            holder_kind=holder_kind, holder_id=str(holder_id),
            ttl_sec=ttl_sec, principal_user_id=user_id,
            min_generation=min_generation)
    return _shape(row)


def renew(subject_kind: str, subject_id: str, generation: int, *,
          ttl_sec: int = LEASE_TTL_SEC, work: bool = False) -> dict | None:
    """Extend this lease. None means the generation is stale: you were fenced."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = lease_repo.renew(s, org_id, subject_kind=subject_kind,
                               subject_id=str(subject_id),
                               generation=int(generation), ttl_sec=ttl_sec,
                               work=work)
    return _shape(row)


def release(subject_kind: str, subject_id: str, generation: int) -> bool:
    """Give the lease up now. False means the generation is stale."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = lease_repo.release(s, org_id, subject_kind=subject_kind,
                                 subject_id=str(subject_id),
                                 generation=int(generation))
    return row is not None


def release_for_holder(holder_id: str, *, holder_kind: str | None = None) -> int:
    """Expire every lease this session holds. Returns how many were released.

    The write half of section 6's join rule. `presence.release()` calls it, so
    closing a chat frees the tree immediately rather than in TTL seconds.
    """
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        rows = lease_repo.release_for_holder(s, org_id, holder_id=str(holder_id),
                                             holder_kind=holder_kind)
    return len(rows)


def fence(subject_kind: str, subject_id: str) -> int | None:
    """Invalidate whoever holds this subject; returns the new generation.

    The storage primitive under an operator edit that displaces a worker, which
    `tasks.update()` has always performed on its own record. Not preempt (B7).
    """
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = lease_repo.fence(s, org_id, subject_kind=subject_kind,
                               subject_id=str(subject_id))
    return None if row is None else int(row["generation"])


def get(subject_kind: str, subject_id: str) -> dict | None:
    """This subject's lease, held or expired, or None if it never had one."""
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        row = lease_repo.get_lease(s, org_id, subject_kind=subject_kind,
                                   subject_id=str(subject_id))
    return _shape(row)


def list_leases(*, subject_kind: str | None = None,
                holder_kind: str | None = None, holder_id: str | None = None,
                held_only: bool = True, limit: int = 200) -> list[dict]:
    """Leases for this org, most recently renewed first.

    `held_only` is filtered AGAIN in Python after the query, because the SQL
    predicate knows about `expires_at` and not about a chat whose presence has
    gone. Both halves of the rule, in the order that keeps the cheap one first.
    """
    org_id, user_id = _require_db()
    with tenant_session(org_id, user_id) as s:
        rows = lease_repo.list_leases(s, org_id, subject_kind=subject_kind,
                                      holder_kind=holder_kind,
                                      holder_id=holder_id,
                                      held_only=held_only, limit=limit)
    shaped = [_shape(r) for r in rows]
    if held_only:
        return [r for r in shaped if r and r["state"] == "held"]
    return [r for r in shaped if r]


# --- the directory seam (section 6) -----------------------------------------

def lease_source(subject_kind: str, subject_id: str) -> dict | None:
    """`(subject_kind, subject_id) -> lease | None`, for `presence.directory`.

    THE SEAM B4 LEFT AND THIS STEP FILLS. `presence.directory` is a read-time
    join over presence, leases, tickets and worktrees; the lease was the one
    field it could not answer, and it carried a declared always-null `lease` key
    and this callable's signature rather than a plausible-looking fake. Passing
    this function is the whole wiring -- the join itself is unchanged.

    NEVER RAISES. The directory's contract is that it answers "who is here" even
    when it cannot decorate a row, so an installation with no database, an
    unreachable one, or a subject kind the presence channel allows and the lease
    table does not (`none`) all return None rather than taking the directory
    down.
    """
    if subject_kind not in LEASE_SUBJECT_KINDS or not subject_id:
        return None
    try:
        return get(subject_kind, subject_id)
    except Exception:
        return None


# --- CLI --------------------------------------------------------------------
#
# The reason this module has one at all: section 15 says of B3 that "its most
# important caller is bash". `project_ralph.sh`, `ralph.sh` and
# `queue_runner.sh` are separate OS processes and cannot import anything.
# `scripts/lease.py` is the shim they call; it does nothing but hand argv here.
#
# UNLIKE `scripts/presence.py`, THIS ONE NEEDS THE VENV, and the shim re-execs
# into it for exactly the reason `scripts/project_tasks.py` does. Presence is
# file-backed and has no wrong-store failure mode, so it deliberately runs under
# whatever python3 a sweep has. A lease is a Postgres row reached through
# sqlalchemy, which the system python3 does not have; without the re-exec every
# `lease.py acquire` from a sweep would raise `LeasesUnavailable` and the sweep
# would conclude, wrongly and silently, that this installation has no lease
# store. That is the same silent-wrong-store defect e7b758a fixed for tasks.


def _emit(value: Any) -> int:
    """Print JSON, and make "I did not get it" an EXIT CODE as well as a null.

    Bash reads exit codes far more reliably than it reads JSON, and every caller
    of this CLI is bash. 1 means the verb did not take (someone else holds it,
    or the generation is stale); 2 is reserved for argparse's usage errors; 3
    means there is no lease store at all, which a caller may legitimately want
    to treat as "carry on without one" rather than as a failure.
    """
    print(json.dumps(value))
    return 0 if value else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lease",
        description="Take, renew and release leases on a ticket or a worktree. "
                    "Every command prints JSON on stdout; exit 1 means the verb "
                    "did not take, exit 3 means there is no lease store.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", help="take the lease, or report who has it")
    acq.add_argument("subject_kind", choices=list(LEASE_SUBJECT_KINDS))
    acq.add_argument("subject_id")
    acq.add_argument("--holder-kind", default="run", choices=list(LEASE_HOLDER_KINDS))
    acq.add_argument("--holder-id", required=True)
    acq.add_argument("--ttl", type=int, default=LEASE_TTL_SEC)
    acq.add_argument("--min-generation", type=int, default=1)

    ren = sub.add_parser("renew", help="extend a lease you hold (fenced)")
    ren.add_argument("subject_kind", choices=list(LEASE_SUBJECT_KINDS))
    ren.add_argument("subject_id")
    ren.add_argument("generation", type=int)
    ren.add_argument("--ttl", type=int, default=LEASE_TTL_SEC)
    ren.add_argument("--work", action="store_true",
                     help="this renewal is a WORK event: resets the idle clock")

    rel = sub.add_parser("release", help="give a lease up now (fenced)")
    rel.add_argument("subject_kind", choices=list(LEASE_SUBJECT_KINDS))
    rel.add_argument("subject_id")
    rel.add_argument("generation", type=int)

    rfh = sub.add_parser("release-holder",
                         help="expire every lease one session holds")
    rfh.add_argument("holder_id")
    rfh.add_argument("--holder-kind", default=None, choices=list(LEASE_HOLDER_KINDS))

    g = sub.add_parser("get", help="one subject's lease, held or expired")
    g.add_argument("subject_kind", choices=list(LEASE_SUBJECT_KINDS))
    g.add_argument("subject_id")

    ls = sub.add_parser("list", help="leases for this org")
    ls.add_argument("--subject-kind", default=None, choices=list(LEASE_SUBJECT_KINDS))
    ls.add_argument("--holder-kind", default=None, choices=list(LEASE_HOLDER_KINDS))
    ls.add_argument("--holder-id", default=None)
    ls.add_argument("--all", action="store_true", help="include expired leases")

    a = ap.parse_args(argv)

    # Pinned here rather than in each verb: without an owner the two relational
    # scopes cannot resolve a tenant, and a CLI that silently answered "no lease
    # store" would be the wrong-store failure this shim exists to avoid.
    # Swallowed, not required: a checkout with no auth store still gets a usable
    # `lease.py get` against whatever tenant is already pinned.
    try:
        ownership.pin_operator()
    except Exception:
        pass

    try:
        if a.cmd == "acquire":
            return _emit(acquire(a.subject_kind, a.subject_id,
                                 holder_kind=a.holder_kind, holder_id=a.holder_id,
                                 ttl_sec=a.ttl, min_generation=a.min_generation))
        if a.cmd == "renew":
            return _emit(renew(a.subject_kind, a.subject_id, a.generation,
                               ttl_sec=a.ttl, work=a.work))
        if a.cmd == "release":
            ok = release(a.subject_kind, a.subject_id, a.generation)
            print(json.dumps({"released": ok}))
            return 0 if ok else 1
        if a.cmd == "release-holder":
            n = release_for_holder(a.holder_id, holder_kind=a.holder_kind)
            print(json.dumps({"released": n}))
            return 0 if n else 1
        if a.cmd == "get":
            return _emit(get(a.subject_kind, a.subject_id))
        if a.cmd == "list":
            rows = list_leases(subject_kind=a.subject_kind,
                               holder_kind=a.holder_kind, holder_id=a.holder_id,
                               held_only=not a.all)
            print(json.dumps(rows))
            return 0
    except LeasesUnavailable as exc:
        print(json.dumps({"error": "no lease store", "detail": str(exc)}))
        return 3
    return 2


if __name__ == "__main__":  # `python3 -m services.leases ...`
    sys.exit(main())
