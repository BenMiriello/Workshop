"""The control channel: `/control/...`. Queue, steer and preempt.

Steps B6 and B7 of
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(sections 8 and 9).

    GET    /control                                    every channel with work waiting
    GET    /control/{session_id}                       what is waiting for this session
    POST   /control/{session_id}/requests              queue or steer a request
    DELETE /control/{session_id}/requests/{request_id} cancel a queued request
    POST   /control/{session_id}/preempt               stop it and take the lease

ADDRESSED BY THE SESSION BEING SPOKEN TO, which is why preempt is a sub-resource
of a session rather than of a lease. Section 14's Take-over button already
"names what will be stopped", so the caller has the holder in hand; and a
session can hold more than one subject, so the subject travels in the body while
the thing being stopped is the path. `/presence/directory` is where a client
finds both.

THERE IS NO SECOND QUEUE BEHIND THIS ROUTER (section 8). A `chat` session's
requests are `services/chat_queue.py` entries -- the same store the composer
writes, restart-surviving and visible from every device -- and a `run`'s are the
control file `scripts/project_ralph.sh` reads at the top of each iteration. One
shape on the wire, because cancelling is the same act either way.

NO PRIORITY FIELD, anywhere in this file. Section 8: "The verb is the urgency."
`ControlRequestIn.verb` is the whole of it, requests are FIFO, and any scalar
added here would be the thing that design explicitly refused.

THE STEER COPY IS SERVED, NOT INVENTED BY THE CLIENT. `ControlAccepted.detail`
carries the string from `services/control.py`, and none of those strings says
"now": measured behaviour is that the model addresses a steered message at the
end of its current plan. A client that wrote its own label would be free to
promise something this system cannot deliver, which is exactly what section 8
forbids.

`PreemptIn.human_action` IS REQUIRED AND HAS NO DEFAULT. That is section 8's
authority rule expressed on the wire: authority is bound to a live human action,
not to a session kind, because every chat acts on the operator's behalf. An
agent may never preempt another agent -- it queues or asks -- and the shape of
the request is what makes that the easy path rather than an allowlist of session
kinds or a permission matrix (this is a single-operator install).

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from routes.presence import LeaseRef
from services import control as control_svc
from services import leases as leases_svc
from services import run_control

router = APIRouter(prefix="/control", tags=["control"])


# --- schemas ------------------------------------------------------------------

class ControlRequest(BaseModel):
    """One request waiting for a session.

    `id` is what cancels it, and it is deliberately the same field for both
    backends: a chat queue entry's `client_id` and a control file request's
    `id`. A caller should not have to know which store answered in order to take
    its own message back out.

    NOTE WHAT IS ABSENT: there is no priority, rank, tier or position field.
    Order is the list's order, oldest first.
    """
    id: str
    verb: str
    text: str
    actor: str | None = None
    at: str | None = None


class ControlChannel(BaseModel):
    """What is waiting for one session, and whether it has been asked to stop."""
    session_id: str
    holder_kind: str
    requests: list[ControlRequest] = Field(default_factory=list)
    # Present only when a preempt has asked this session to snapshot, release
    # and exit cleanly (section 9) and it has not done so yet.
    yield_requested_at: str | None = None
    yield_requested_by: str | None = None
    yield_reason: str | None = None


class ControlChannelList(BaseModel):
    channels: list[ControlChannel]
    count: int


class ControlRequestIn(BaseModel):
    """A request for a busy session.

    `verb` is `queue` (the default) or `steer`. `yield` is not accepted here: it
    is not something a caller asks of a holder directly, it is what preempt asks
    first, and a route that let anything write it would be a stop with none of
    section 9's quiesce behind it.
    """
    text: str = Field(min_length=1)
    verb: str = "queue"
    actor: str | None = None
    # Idempotency, matching `chat_queue.enqueue`: a retried POST must not become
    # two requests. Ignored for `steer` into a live turn, which is a delivery
    # rather than a record.
    request_id: str | None = None
    # The tree this request is about, when there is one. Only used to write the
    # `steered` event onto its timeline (section 9); a request with no worktree
    # is still delivered.
    worktree_id: str | None = None


class ControlAccepted(BaseModel):
    """What actually happened to the request.

    `delivered` means THE BYTES REACHED THE RUNNING TURN, which is the only
    thing this system can observe. It does not mean the model has read them:
    measured behaviour (`chat-message-ordering-audit-2026-08-05.md` section 2)
    is that it addresses a steered message at the end of its current plan,
    skipping the step boundaries in between. `detail` is the string to show, and
    it is written to say that rather than to imply "now".
    """
    holder_id: str
    holder_kind: str
    verb: str
    request_id: str
    delivered: bool
    queued: bool
    detail: str
    pending: list[ControlRequest] = Field(default_factory=list)


class ControlCancelled(BaseModel):
    cancelled: bool
    session_id: str
    request_id: str


class PreemptIn(BaseModel):
    """Take the lease away from whoever holds it.

    `human_action` NAMES THE LIVE HUMAN INTERACTION THAT AUTHORIZED THIS and has
    no default, which is section 8's rule in the only place it can be enforced.
    A value like `"take-over confirmed in the lease card"` is what the UI's
    `ConfirmDialog` sends. An agent has queue and steer beside this route and no
    honest value to put here.
    """
    subject_id: str = Field(min_length=1)
    new_holder_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    human_action: str = Field(min_length=1)
    subject_kind: str = "worktree"
    new_holder_kind: str = "chat"
    reason: str = ""
    # Section 5's preempt grace, in seconds. Overridable downward for a caller
    # that has already waited (and by tests); it is never a caller's clock, only
    # a duration.
    grace_sec: float = Field(default=control_svc.PREEMPT_GRACE_SEC, ge=0, le=600)
    worktree_id: str | None = None
    # An explicit tree, for a subject whose record this installation does not
    # hold. Normally left null and resolved from `worktree_id`.
    repo_path: str | None = None


class QuiesceResult(BaseModel):
    """What the handover did to the tree (section 9).

    `head_before` and `head_after` are both here so "the branch did not move"
    is CHECKABLE rather than asserted: a preempted holder's work goes to
    `snapshot_ref`, a dangling `refs/wip/<session>`, and the branch tip and HEAD
    are untouched. `snapshot_sha` is null when the tree was already clean, which
    is `tree_was_clean`, not a failure.
    """
    repo: str
    ok: bool
    cleared_index_lock: bool
    snapshot_sha: str | None = None
    snapshot_ref: str | None = None
    tree_was_clean: bool
    restored: bool
    removed: list[str] = Field(default_factory=list)
    clean: bool
    head_before: str | None = None
    head_after: str | None = None
    detail: str = ""


class PreemptResult(BaseModel):
    subject_kind: str
    subject_id: str
    preempted: bool
    # True when the grace lapsed and the holder had to be stopped; False when it
    # let go on its own, which is the path that needed no force at all.
    forced: bool
    yielded: bool
    holder_id: str | None = None
    holder_kind: str | None = None
    grace_sec: float
    stop_detail: str = ""
    quiesce: QuiesceResult | None = None
    lease: LeaseRef | None = None
    detail: str = ""


# --- helpers ------------------------------------------------------------------

def _channel(session_id: str) -> ControlChannel:
    kind = control_svc.holder_kind_of(session_id)
    y = run_control.yield_requested(session_id) or {}
    return ControlChannel(
        session_id=session_id, holder_kind=kind,
        requests=[ControlRequest(**r)
                  for r in control_svc.pending(session_id, holder_kind=kind)],
        yield_requested_at=y.get("requested_at"),
        yield_requested_by=y.get("actor"),
        yield_reason=y.get("reason") or None)


# --- routes -------------------------------------------------------------------

@router.get("", response_model=ControlChannelList)
def list_channels() -> ControlChannelList:
    """Every run with a control channel on disk.

    RUNS ONLY, and the asymmetry is honest rather than an omission: a run's
    channel is a file, so the set of them is enumerable, while a chat's requests
    live in its own thread's queue and "every chat with something waiting" is a
    question `GET /chat/threads` already answers. Listing both here would mean
    walking every thread on the box to build a view nothing asks for.
    """
    channels = [_channel(rid) for rid in run_control.channels()]
    return ControlChannelList(channels=channels, count=len(channels))


@router.get("/{session_id}", response_model=ControlChannel)
def get_channel(session_id: str) -> ControlChannel:
    """What is waiting for this session, oldest first.

    A session with nothing waiting is an empty channel and a 200, not a 404: the
    question "is anything queued for you" has an answer for every session,
    including ones that have never been written to.
    """
    return _channel(session_id)


@router.post("/{session_id}/requests", response_model=ControlAccepted)
def submit_request(session_id: str, body: ControlRequestIn) -> ControlAccepted:
    """Queue or steer a request into a session that is already working.

    `queue` is the default because it is section 8's default: the holder takes
    it when it finishes, and nothing is interrupted. `steer` tries to reach the
    work in flight and falls back to the queue when it cannot -- a command turn
    never reads its input, and a message that could not be delivered is still
    owed to whoever wrote it.
    """
    verb = (body.verb or "queue").strip()
    try:
        if verb == "queue":
            out = control_svc.queue(session_id, body.text, actor=body.actor,
                                    request_id=body.request_id)
        elif verb == "steer":
            out = control_svc.steer(session_id, body.text, actor=body.actor,
                                    worktree_id=body.worktree_id)
        else:
            raise HTTPException(
                400, f"unknown verb {verb!r}: queue or steer. Preempt is "
                     f"POST /control/{{session_id}}/preempt, because it stops "
                     f"work and needs the human action that authorized it.")
    except control_svc.ControlRefused as e:
        raise HTTPException(400, str(e))
    except run_control.ControlError as e:
        raise HTTPException(400, str(e))
    return ControlAccepted(**out)


@router.delete("/{session_id}/requests/{request_id}",
               response_model=ControlCancelled)
def cancel_request(session_id: str, request_id: str) -> ControlCancelled:
    """Take a queued request back out before it is delivered.

    Section 8: queued requests are FIFO and CANCELLABLE. Cancelling something
    that is not there is `false` and a 200, not a 404 -- the caller's intent
    (it should not go) is satisfied either way, and a request that drained a
    millisecond earlier is not an error.
    """
    ok = control_svc.cancel(session_id, request_id)
    return ControlCancelled(cancelled=ok, session_id=session_id,
                            request_id=request_id)


@router.post("/{session_id}/preempt", response_model=PreemptResult)
async def preempt_session(session_id: str, body: PreemptIn) -> PreemptResult:
    """Stop this session, quiesce its tree, and grant the lease to another.

    A 403 means the request carried no human action. That is not a permission
    check against the caller's identity -- there is one operator here and no
    identity to check -- it is the shape of the request refusing to let an agent
    take a tree away from another agent. Queue and steer are the answer to
    urgency; section 8 kept them for exactly this.

    The response is the whole story: whether the holder yielded inside the grace
    or had to be stopped, what the quiesce did, where the snapshot went, and
    that the branch tip did not move.
    """
    try:
        out = await control_svc.preempt(
            subject_id=body.subject_id, new_holder_id=body.new_holder_id,
            actor=body.actor, human_action=body.human_action,
            subject_kind=body.subject_kind,
            new_holder_kind=body.new_holder_kind, reason=body.reason,
            grace_sec=body.grace_sec, repo_path=body.repo_path,
            worktree_id=body.worktree_id)
    except control_svc.HumanActionRequired as e:
        raise HTTPException(403, str(e))
    except control_svc.ControlRefused as e:
        raise HTTPException(400, str(e))
    except leases_svc.LeasesUnavailable as e:
        # 503 rather than a refusal, for the reason `services/changes.py` gives:
        # "nobody holds this" because the database is down must not read the
        # same as "nobody holds this" because nobody does.
        raise HTTPException(503, str(e))
    if session_id != str(out.get("holder_id") or session_id):
        # The path names who the caller believed was working here. Saying so is
        # cheaper than a stale UI silently stopping the wrong session.
        out["detail"] = (f"{out.get('detail') or ''} "
                         f"(the holder was {out.get('holder_id')}, "
                         f"not {session_id})").strip()
    return PreemptResult(**out)
