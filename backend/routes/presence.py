"""The presence resource: `/presence/...`.

Step B4 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 6). Presence is the expiring fact that a session is here and what it is
doing, and the directory is the read-time join that answers "who is working
here".

    GET    /presence            ?session_kind=&subject_kind=&subject_id=&scope=
                                &include_expired=
    POST   /presence                       write or refresh a session's presence
    GET    /presence/directory             the join over presence + its subjects
    DELETE /presence/{session_id}          withdraw a presence now

THE STORE IS NOT BEHIND THIS ROUTER, and that is the design's central
constraint. The processes that hold trees -- `project_ralph.sh`, `ralph.sh`,
`queue_runner.sh` -- write presence by shelling `scripts/presence.py`, not by
calling this API, so this surface is one reader (and one convenience writer) of
a shared channel rather than its owner. A backend restart loses nothing.

`expires_at` IS NEVER ACCEPTED FROM A CALLER. `PresenceBeat` carries a TTL, a
duration, and the service computes the instant (section 5's rule for the lease,
which applies identically to presence). A body field for `expires_at` would let
a client with a skewed clock declare itself present indefinitely.

`DirectoryEntry.lease` ARRIVED WITH THE BEHAVIOUR, which is what this file said
it was waiting for. Section 6's join is over presence, leases, tickets and
worktrees; the lease is step B3 and is now built, so the directory is passed
`leases.lease_source` and the field carries a real row. It is still NULL for two
honest reasons and neither is an error: the session holds no subject the lease
table knows about, or this installation has no relational store. A directory
that could not decorate one row must still answer "who is here".

TWO LEASE STATES, NOT THREE (section 6). `LeaseRef.state` is `held` or
`expired`. `idle_since` is a TIMESTAMP the client renders as a `warn` -- it is
deliberately not a third state with its own verb, because a state a caller can
key on is a state the rest of the system then has to handle.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import leases as leases_svc
from services import presence as presence_svc

router = APIRouter(prefix="/presence", tags=["presence"])


# --- schemas ------------------------------------------------------------------

class PresenceRow(BaseModel):
    """One session's presence record, exactly as the channel stores it.

    `ts` is the last beat and `expires_at` is when that beat stops counting;
    both are server-computed. A row served here has NOT been filtered for
    liveness by its own presence in the list -- `GET /presence` does that
    filtering -- because `?include_expired=true` deliberately serves expired
    rows so an operator can see a session that has just gone.
    """
    session_id: str
    session_kind: str
    subject_kind: str
    subject_id: str | None = None
    scope: str | None = None
    principal: str | None = None
    activity: str = ""
    pid: int | None = None
    hostname: str | None = None
    liveness_profile: str = "heartbeat"
    ttl_sec: int = presence_svc.DEFAULT_TTL_SEC
    ts: str | None = None
    expires_at: str | None = None
    # The writer's own tail (`heartbeat.sh`'s `key=val` extras land here).
    # Typed as string-valued so it generates a usable index signature rather
    # than the `Record<string, never>` an untyped dict would.
    meta: dict[str, str] = Field(default_factory=dict)


class PresenceListOut(BaseModel):
    presences: list[PresenceRow]
    count: int


class PresenceBeat(BaseModel):
    """A beat. Note what is NOT here: `ts` and `expires_at`."""
    session_id: str = Field(min_length=1, max_length=128)
    session_kind: str = "chat"
    activity: str = ""
    subject_kind: str = "none"
    subject_id: str | None = None
    scope: str | None = None
    principal: str | None = None
    pid: int | None = None
    liveness_profile: str = "heartbeat"
    ttl_sec: int | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class LeaseRef(BaseModel):
    """The lease on a directory entry's subject, read at join time.

    TYPED, not a `dict[str, Any]`. An opaque field satisfies the route-level
    response_model check and still generates `Record<string, never>` in the
    client, so the frontend hand-writes the shape and drifts from it -- which is
    the defect ADR 0023 clause 6 exists to remove.

    `state` has exactly two values (section 6): `held` or `expired`. `idle_since`
    is the moment of the last work event on a lease whose work has not moved for
    ten minutes, and null otherwise; it is a warning a client renders, not a
    third state.
    """
    subject_kind: str
    subject_id: str
    holder_kind: str
    holder_id: str
    principal_user_id: str | None = None
    generation: int
    state: str
    idle_since: str | None = None
    expires_at: str | None = None
    acquired_at: str | None = None
    last_renewed_at: str | None = None
    last_work_at: str | None = None


class DirectoryEntry(BaseModel):
    """One live session, joined to what it is working on.

    `subject_label` and `subject_status` are resolved from the ticket store or
    the branch records AT READ TIME and stored nowhere; they are null when the
    subject cannot be resolved (an unknown scope, a deleted ticket), which is
    the honest answer and not an error.
    """
    session_id: str
    session_kind: str
    principal: str | None = None
    activity: str = ""
    scope: str | None = None
    subject_kind: str
    subject_id: str | None = None
    subject_label: str | None = None
    subject_status: str | None = None
    pid: int | None = None
    hostname: str | None = None
    last_beat_at: str | None = None
    expires_at: str | None = None
    lease: LeaseRef | None = None


class DirectoryOut(BaseModel):
    entries: list[DirectoryEntry]
    count: int


class PresenceReleaseOut(BaseModel):
    released: bool
    session_id: str


# --- routes -------------------------------------------------------------------

@router.get("", response_model=PresenceListOut)
async def list_presence(session_kind: str | None = Query(None),
                        subject_kind: str | None = Query(None),
                        subject_id: str | None = Query(None),
                        scope: str | None = Query(None),
                        include_expired: bool = Query(False)):
    """Who is here. Reading changes nothing: an expired row is filtered out, not
    deleted, not rewritten and not marked."""
    if include_expired:
        rows = presence_svc.list_all()
        rows = [r for r in rows
                if (not session_kind or r.get("session_kind") == session_kind)
                and (not subject_kind or r.get("subject_kind") == subject_kind)
                and (not subject_id or r.get("subject_id") == subject_id)
                and (not scope or r.get("scope") == scope)]
    else:
        rows = presence_svc.list_live(session_kind=session_kind,
                                      subject_kind=subject_kind,
                                      subject_id=subject_id, scope=scope)
    return {"presences": rows, "count": len(rows)}


@router.get("/directory", response_model=DirectoryOut)
async def presence_directory(session_kind: str | None = Query(None),
                             subject_kind: str | None = Query(None),
                             subject_id: str | None = Query(None),
                             scope: str | None = Query(None)):
    """The directory: live sessions joined to their subjects. Stores nothing.

    Declared ABOVE `/{session_id}` so the literal path never competes with a
    parameter, the same ordering rule `routes/agents.py` follows.
    """
    entries = presence_svc.directory(session_kind=session_kind,
                                     subject_kind=subject_kind,
                                     subject_id=subject_id, scope=scope,
                                     lease_source=leases_svc.lease_source)
    return {"entries": entries, "count": len(entries)}


@router.post("", response_model=PresenceRow)
async def write_presence(body: PresenceBeat):
    """Write or refresh a presence. For a session that lives INSIDE the backend
    (a chat turn); a separate OS process uses `scripts/presence.py` instead."""
    try:
        return presence_svc.beat(
            body.session_id, session_kind=body.session_kind,
            activity=body.activity, subject_kind=body.subject_kind,
            subject_id=body.subject_id, scope=body.scope,
            principal=body.principal, pid=body.pid,
            liveness_profile=body.liveness_profile, ttl_sec=body.ttl_sec,
            meta=body.meta)
    except presence_svc.PresenceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{session_id}", response_model=PresenceReleaseOut)
async def release_presence(session_id: str):
    """Withdraw a presence now rather than in TTL seconds -- a chat closing, a
    sweep finishing. Idempotent: releasing what is not there is `false`, not a
    404, because the caller's intent (be absent) is satisfied either way."""
    return {"released": presence_svc.release(session_id), "session_id": session_id}
