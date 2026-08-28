"""Who holds what, right now: `/leases`.

Step B3 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
gave the lease a table, a service and a directory join, and §13.2 names
`/leases` alongside `/git`, `/branches`, `/presence` and `/changes` as an
addressable entity. It was the one on that list with no router, so
`services/leases.list_leases` -- which has existed since B3 -- could be reached
from the CLI and from Python and from nowhere else.

WHY `/presence/directory` IS NOT THIS ROUTE, which is the whole reason this file
had to be added rather than reusing what was there. The directory is a join
STARTING FROM PRESENCE: it lists live sessions and decorates each with the lease
it holds. That is the right shape for "who is here", and it is structurally
incapable of answering the question a stuck-work view actually asks, because the
rows it cannot produce are exactly the interesting ones:

  * A lease whose holder stopped writing presence has no directory row at all.
    That is an ABANDONED tree -- the failure mode the whole ownership design
    exists to remove -- and the directory renders it as silence.
  * An `expired` lease is likewise invisible, for the same reason: the session
    that took it is gone, so there is nothing to join from.

Starting from the LEASE TABLE inverts that. Every subject anyone has taken is a
row, held or not, and a holder with no presence behind it becomes a visible,
nameable state instead of an absence. The directory answers "who is working";
this answers "what is claimed, and is anyone still there".

READ ONLY, AND THAT IS NOT AN OMISSION. There is no POST, PATCH or DELETE here.
Taking a lease is something a participant does as part of starting work, and
giving one up is `presence.release()` or the holder's own finalize path; a route
that let a browser mint a lease for a holder that is not running would create
exactly the orphaned tree this design removes. Taking one AWAY is a real verb
and it already has a home that carries the authority rule with it:
`POST /control/{session_id}/preempt`.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from db.models import LEASE_HOLDER_KINDS, LEASE_SUBJECT_KINDS
from routes.presence import LeaseRef
from services import leases as leases_svc
from pydantic import BaseModel

router = APIRouter(prefix="/leases", tags=["leases"])


class LeaseRow(LeaseRef):
    """A lease, with the derived staleness a client would otherwise recompute.

    It EXTENDS `LeaseRef` rather than redeclaring it, so the shape a lease has
    when the directory decorates a session is the shape it has when listed here.
    Two hand-kept copies of these eleven fields is the drift ADR 0023 clause 6
    exists to stop.

    `held_for_sec` and `since_heartbeat_sec` are DERIVED AND SERVED rather than
    left to the caller, because the windows they are compared against
    (`IDLE_AFTER_SEC`, `LEASE_TTL_SEC`) live on this side and a client that did
    its own arithmetic would be comparing against a copy of them. They are
    seconds, not a label: `state` is the label, and it is `LeaseRef`'s.
    """
    held_for_sec: float | None = None
    since_heartbeat_sec: float | None = None
    since_work_sec: float | None = None


class LeaseWindows(BaseModel):
    """The four windows from §5, served so no client hardcodes them.

    A UI that decides "this holder is idle" needs the same threshold the server
    derives `idle_since` from. Shipping the numbers means the two can never
    disagree, and it means the copy on screen ("no work for 10 minutes") is
    generated from the constant rather than typed next to it.
    """
    lease_ttl_sec: int
    heartbeat_sec: int
    idle_after_sec: int
    preempt_grace_sec: int


class LeaseList(BaseModel):
    """`available` DISTINGUISHES AN EMPTY ANSWER FROM AN ABSENT ONE.

    An installation with no relational store holds no leases and never will, and
    that must not render as "nothing is claimed right now" -- the two look
    identical in `leases: []` and mean opposite things. The list route answers
    200 with `available: false` rather than 503, because "this installation does
    not keep leases" is a fact about the installation that a view should render
    calmly, not an error it should retry.
    """
    leases: list[LeaseRow]
    count: int
    available: bool
    windows: LeaseWindows
    subject_kinds: list[str]
    holder_kinds: list[str]


def _windows() -> LeaseWindows:
    from services import control as control_svc
    return LeaseWindows(
        lease_ttl_sec=leases_svc.LEASE_TTL_SEC,
        heartbeat_sec=leases_svc.HEARTBEAT_SEC,
        idle_after_sec=leases_svc.IDLE_AFTER_SEC,
        preempt_grace_sec=int(control_svc.PREEMPT_GRACE_SEC),
    )


@router.get("", response_model=LeaseList)
def list_leases(subject_kind: str | None = Query(None),
                holder_kind: str | None = Query(None),
                holder_id: str | None = Query(None),
                held_only: bool = Query(False),
                limit: int = Query(200, ge=1, le=1000)) -> LeaseList:
    """Every lease this installation records, most recently renewed first.

    `held_only` DEFAULTS TO FALSE HERE and to True in the service, and the flip
    is the point of the route. The service's callers ask "may I write" and want
    only live claims; a view asking "what is stuck" wants the dead ones most of
    all, because an expired lease over a tree that still exists on disk is
    abandoned work and nothing else reports it.
    """
    if subject_kind and subject_kind not in LEASE_SUBJECT_KINDS:
        raise HTTPException(
            400, f"unknown subject kind {subject_kind!r}; "
                 f"one of {list(LEASE_SUBJECT_KINDS)}")
    if holder_kind and holder_kind not in LEASE_HOLDER_KINDS:
        raise HTTPException(
            400, f"unknown holder kind {holder_kind!r}; "
                 f"one of {list(LEASE_HOLDER_KINDS)}")
    try:
        rows = leases_svc.list_leases(subject_kind=subject_kind,
                                      holder_kind=holder_kind,
                                      holder_id=holder_id,
                                      held_only=held_only, limit=limit)
    except leases_svc.LeasesUnavailable:
        return LeaseList(leases=[], count=0, available=False,
                         windows=_windows(),
                         subject_kinds=list(LEASE_SUBJECT_KINDS),
                         holder_kinds=list(LEASE_HOLDER_KINDS))
    shaped = [LeaseRow(**_derive(r)) for r in rows]
    return LeaseList(leases=shaped, count=len(shaped), available=True,
                     windows=_windows(),
                     subject_kinds=list(LEASE_SUBJECT_KINDS),
                     holder_kinds=list(LEASE_HOLDER_KINDS))


def _derive(row: dict) -> dict:
    """Add the three elapsed-second fields to a shaped lease.

    Computed from the SERVER's clock against the server's own timestamps. A
    browser subtracting its own `Date.now()` from these ISO strings would fold
    in clock skew, and "this holder last beat 40 seconds ago" is a number the
    operator is about to act on.
    """
    from datetime import datetime, timezone

    def _ago(iso: str | None) -> float | None:
        if not iso:
            return None
        try:
            stamp = datetime.fromisoformat(iso)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - stamp).total_seconds(), 1)

    out = dict(row)
    out.pop("id", None)  # LeaseRef has no `id`; the subject is the identity
    out["held_for_sec"] = _ago(row.get("acquired_at"))
    out["since_heartbeat_sec"] = _ago(row.get("last_renewed_at"))
    out["since_work_sec"] = _ago(row.get("last_work_at"))
    return out
