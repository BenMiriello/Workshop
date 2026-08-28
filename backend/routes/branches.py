"""The worktree record and its timeline: `/branches/...`.

Step B2 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.

    GET  /branches                      ?project=&status=&ticket_id=&live=&limit=
    GET  /branches/{id}
    GET  /branches/{id}/timeline
    POST /branches/{id}/events                append one structured event (sec 9)

WHY `/branches` AND NOT `/worktrees`. Section 3 fixes the vocabulary: the disk
concept is the worktree, the USER-FACING concept is the branch, and section
13.2's list of addressable entities names `/branches` alongside `/git`,
`/leases`, `/presence` and `/changes`. It is also where the lease hangs off in
B3 (`POST /branches/{id}/lease`), so the id-addressed resource has to be this
one. The existing `/worktrees` router is a different surface with a different
key: it is addressed by `(project_slug, branch_slug)` and it operates on the
DISK -- create, diff, merge, publish, discard. This one is the RECORD, and the
`live=true` listing is the RECORD JOINED ONTO THE DISK: see that route.

A FIRST-CLASS RESOURCE. It declares a prefix and a tag -- a bare `APIRouter()`
is a bug by section 13.2's own rule -- and every route declares a real
`response_model`, so the record is drivable from the generated client, a script
or an agent without a bespoke path.

THERE IS NO POST /branches AND NO DELETE, deliberately. Creating a worktree
means creating a directory and a git worktree, which is `services/worktrees.py`'s
job and is already exposed at `POST /worktrees/{project_slug}`; a second create
verb here would be a second way to allocate, and the two would drift. Destroying
one means `DELETE /worktrees/{project}/{branch}`, which moves the record to
`discarded` as a consequence. A route that deleted the ROW while leaving the
tree on disk would manufacture exactly the record-versus-disk disagreement this
table exists to close.

NO LEASE ROUTE EITHER: that is B3, and a route that exists and does nothing is
indistinguishable to a caller from one that works.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import worktree_records as records, worktrees

router = APIRouter(prefix="/branches", tags=["branches"])


# --- schemas ------------------------------------------------------------------

class Worktree(BaseModel):
    """One isolated line of work: the branch, the tree it lives in, and what
    became of it.

    `base_ref`/`base_sha` are WHAT THIS TREE WAS CUT FROM, as a name and as the
    commit that name resolved to at creation. They are not the merge target: a
    bound project cuts from `origin/<default>` and merges into `<default>`, and
    the merge target is computed when it is needed because it moves.
    """
    id: str
    app_id: str | None = None
    project_slug: str
    # Internal addressing (`project:<slug>`). Carried because it is what the
    # allocator was called with; it is never shown in the UI.
    scope: str
    branch_slug: str
    branch: str
    path: str
    base_ref: str
    base_sha: str | None = None
    # NULL is first-class (design sec 10.4): a branch with no ticket is common
    # and a ticket can adopt it later.
    ticket_id: str | None = None
    allocated_by: str | None = None
    # NULL when nothing recorded this tree. "No record" and "a record that says
    # allocated" are different facts, and conflating them is what let `/branches`
    # report eleven abandoned trees as no trees at all; see
    # `worktrees.live_branches`.
    status: str | None = None
    merged_sha: str | None = None
    # When the directory left the disk. Set together with a terminal status, and
    # the database enforces that pairing.
    removed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # --- what the DISK says, filled only by the live view -------------------
    #
    # These four are the join `worktrees.live_branches` performs, and they are
    # nullable because "the disk was not consulted" is a real state: the history
    # listing (`live=false`) and `GET /branches/{id}` read the record table and
    # nothing else, so they answer null here rather than a default that would
    # read as an observation nobody made.

    # False for a tree that git knows about and the table does not, which is the
    # structurally common case: most trees were never allocated through the
    # record path.
    recorded: bool = True
    # `tree` on disk and known to git, `orphan` a directory git lost, `gone` a
    # record whose directory is not there. Null when the disk was not read.
    presence: str | None = None
    head_sha: str | None = None
    # When this branch's tip was last written. The age a monitoring view reads,
    # and the only one an unrecorded tree has, since it has no `created_at`.
    tip_at: str | None = None
    # git refuses to remove or prune a locked worktree, so a caller deciding
    # whether a tree can be reaped has to know.
    locked: bool | None = None


class WorktreeCollection(BaseModel):
    """`records_available` is False when the relational store could not be read.

    The live view still answers the trees, because they came from git and are
    still real; they simply carry no ticket, no allocator and no status. That is
    a different answer from "no trees", and it is said rather than implied.
    """
    worktrees: list[Worktree]
    records_available: bool = True


class WorktreeEvent(BaseModel):
    """One entry on the timeline (design sec 9): "structured events, not prose
    in a transcript".

    TYPED, not a free dict, and there is no open payload field on purpose: a
    blob is how "structured" decays back into prose. `sha` carries the
    `snapshot <sha>` and the merge commit, `actor` carries "preempted by whom",
    `paths` carries the directories or files an event is about, and `detail` is
    the event's own text (the steer request, the discard reason).
    """
    id: str
    worktree_id: str
    kind: str
    actor: str | None = None
    sha: str | None = None
    detail: str | None = None
    paths: list[str] = Field(default_factory=list)
    at: str | None = None


class WorktreeTimeline(BaseModel):
    """Oldest first: a handover read backwards is a handover read wrong."""
    worktree_id: str
    events: list[WorktreeEvent]


class WorktreeEventIn(BaseModel):
    kind: str = Field(min_length=1)
    actor: str | None = None
    sha: str | None = None
    detail: str | None = None
    paths: list[str] = Field(default_factory=list)


# --- helpers ------------------------------------------------------------------

def _guard(fn, *args, **kwargs):
    """Run a service call, mapping its refusals onto status codes.

    503 for an unreachable store rather than an empty list: an empty
    `/branches` because the database is down must not read the same as an empty
    `/branches` because nothing is allocated.
    """
    try:
        return fn(*args, **kwargs)
    except records.WorktreesUnavailable as e:
        raise HTTPException(503, str(e))
    except records.WorktreeNotFound as e:
        raise HTTPException(404, f"no such worktree: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- routes -------------------------------------------------------------------

@router.get("", response_model=WorktreeCollection)
def list_branches(project: str | None = Query(None),
                  status: str | None = Query(None),
                  ticket_id: str | None = Query(None),
                  app_id: str | None = Query(None),
                  live: bool = Query(False),
                  limit: int = Query(200, ge=1, le=1000)) -> WorktreeCollection:
    """Worktree records, newest activity first.

    `live=true` is THE TREES THAT EXIST, read from the disk and joined with the
    records that describe them (`worktrees.live_branches`), which is the question
    a "who is working where" view asks. Without it the answer is the record
    history, which is the question "what happened to this branch" asks. Both are
    real, so neither is the default hidden inside the other.

    THE LIVE VIEW USED TO ASK THE TABLE, and the table only ever has a row for a
    tree allocated through the record path. It therefore reported an
    installation with eleven abandoned worktrees on disk as having one branch,
    at a path that did not exist. A view whose subject is "what is stuck" cannot
    take its list of things from a register that most things were never entered
    in.
    """
    if live:
        # THE LIVE VIEW STARTS FROM THE DISK. Answering it from the record table
        # meant a tree that was never allocated through the record path did not
        # exist as far as this route was concerned, and on 2026-08-27 that was
        # every abandoned tree on this installation. `status` is a record filter
        # and has no meaning over trees with no record, so it is not offered
        # here; ask the history listing for it.
        if status:
            raise HTTPException(
                400, "status filters the record history; drop live=true to use it")
        out = worktrees.live_branches(project_slug=project, ticket_id=ticket_id,
                                      app_id=app_id, limit=limit)
        return WorktreeCollection(
            worktrees=[Worktree(**r) for r in out["worktrees"]],
            records_available=out["records_available"])

    rows = _guard(records.list_records, project_slug=project, status=status,
                  ticket_id=ticket_id, app_id=app_id, live_only=False,
                  limit=limit)
    return WorktreeCollection(worktrees=[Worktree(**r) for r in rows])


@router.get("/{worktree_id}", response_model=Worktree)
def get_branch(worktree_id: str) -> Worktree:
    return Worktree(**_guard(records.get_record, worktree_id))


@router.get("/{worktree_id}/timeline", response_model=WorktreeTimeline)
def branch_timeline(worktree_id: str,
                    limit: int = Query(500, ge=1, le=2000)) -> WorktreeTimeline:
    """Everything that happened to this tree, oldest first."""
    events = _guard(records.timeline, worktree_id, limit=limit)
    return WorktreeTimeline(worktree_id=worktree_id,
                            events=[WorktreeEvent(**e) for e in events])


@router.post("/{worktree_id}/events", response_model=WorktreeEvent)
def append_branch_event(worktree_id: str, body: WorktreeEventIn) -> WorktreeEvent:
    """Append one structured event.

    A sub-resource rather than a PATCH on the worktree because the timeline is
    append-only: an event is a new fact, never an edit to the record it is
    about. An unknown `kind` is a 400 -- the vocabulary is closed
    (`db.models.WORKTREE_EVENT_KINDS`), and a timeline whose kinds are free text
    is prose with punctuation.
    """
    row = _guard(records.add_event, worktree_id, kind=body.kind,
                 actor=body.actor, sha=body.sha, detail=body.detail,
                 paths=body.paths)
    return WorktreeEvent(**row)
