"""The change resource: `/changes/...`.

Step C1 of `docs/platform-redesign/changes-and-review-2026-08-22.md`. A change
is the reviewable artifact a branch produces: the tickets it carries, the sha
it is at, its diff, and a slot for machine-gate results. A ticket says what
should be true; a change says here is the code that makes it true.

    GET    /changes           ?status=&app_id=&project=&ticket_id=&limit=
    POST   /changes                                      open one for a branch
    GET    /changes/{id}
    PATCH  /changes/{id}                                 title/summary/tickets/
                                                         status/gates/remote
    DELETE /changes/{id}                                 soft delete
    GET    /changes/{id}/diff                            the review payload
    POST   /changes/{id}/refresh                         re-read the branch

A FIRST-CLASS RESOURCE, not a side effect of asking about a branch
(workspace-ownership-and-leases-2026-08-22.md sec 13.2). It declares a prefix
and a tag -- a bare `APIRouter()` is a bug by that section's own rule -- and
every route declares a `response_model`, so every one of these is drivable from
the generated client, a script or an agent without a bespoke path.

THERE IS NO MERGE ROUTE HERE, deliberately. `POST /changes/{id}/merge` is the
right shape and the design names it, but merging is step C5. A route that
exists and refuses, or exists and does nothing, is indistinguishable to a
caller from one that works, and the generated client would publish it as a
capability this system does not have. It arrives with the behaviour.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import changes as changes_svc

router = APIRouter(prefix="/changes", tags=["changes"])


# --- schemas ------------------------------------------------------------------

class ChangeGate(BaseModel):
    """One machine check's outcome on this change (design doc sec 5.3).

    TYPED, not a free dict: the review surface's whole job is to let a person
    skip what a checker already answered, and it cannot do that with a payload
    whose shape the client has to guess. `detail` is the checker's own words
    (a failing line, a count); `at` is when it ran.
    """
    name: str
    status: str
    detail: str | None = None
    at: str | None = None


class ChangeRemote(BaseModel):
    """The mirrored pull request, when the app is bound to a remote. Null for
    the common local case -- same record either way, which is the point of the
    design's local-first requirement."""
    kind: str
    url: str | None = None
    number: int | None = None


class Change(BaseModel):
    id: str
    app_id: str | None = None
    # The worktree record (step B2) does not exist yet; this is its slot. See
    # db.models.Change.
    worktree_id: str | None = None
    project_slug: str
    branch_slug: str
    base_ref: str
    # The commit the base stood at when this merged; null until then. It is what
    # makes a merged change's own diff recoverable, since by then the base ref
    # contains the change.
    base_sha: str | None = None
    head_sha: str
    title: str
    # THE AUTHOR'S CLAIM. The review surface labels it as one and shows the
    # diff first (design doc sec 5.1); this field is not evidence of anything.
    summary: str = ""
    ticket_ids: list[str] = Field(default_factory=list)
    status: str
    gates: list[ChangeGate] = Field(default_factory=list)
    remote: ChangeRemote | None = None
    merged_sha: str | None = None
    merged_at: str | None = None
    closed_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ChangeCollection(BaseModel):
    changes: list[Change]


class ChangeOpenIn(BaseModel):
    project_slug: str = Field(min_length=1)
    branch_slug: str = Field(min_length=1)
    title: str = ""
    summary: str = ""
    ticket_ids: list[str] = Field(default_factory=list)
    status: str = "draft"
    worktree_id: str | None = None


class ChangePatchIn(BaseModel):
    """Every field optional; only what is sent is written.

    The merge outcome is absent on purpose: `merged_sha`, `merged_at` and
    `status='merged'` are written together by whatever performs the merge
    (step C5), never patched.
    """
    title: str | None = None
    summary: str | None = None
    ticket_ids: list[str] | None = None
    status: str | None = None
    gates: list[ChangeGate] | None = None
    remote: ChangeRemote | None = None
    closed_reason: str | None = None
    app_id: str | None = None
    worktree_id: str | None = None


class ChangeFileDiff(BaseModel):
    path: str
    status: str
    diff: str


class ChangeDiff(BaseModel):
    """`worktrees.diff_branch`'s output, unchanged: a shortstat summary plus
    per-file unified diffs. THE DIFF IS THE PRIMARY OBJECT of a review."""
    summary: str
    files: list[ChangeFileDiff]


class ChangeDeleted(BaseModel):
    ok: bool
    id: str


# --- helpers ------------------------------------------------------------------

def _guard(fn, *args, **kwargs):
    """Run a service call, mapping its refusals onto status codes.

    `NothingToReview` is a 409 rather than a 400: the request was well formed
    and the answer is about the state of the repository, which is the
    distinction a caller needs to decide whether to retry after committing.
    """
    try:
        return fn(*args, **kwargs)
    except changes_svc.ChangesUnavailable as e:
        raise HTTPException(503, str(e))
    except changes_svc.ChangeNotFound as e:
        raise HTTPException(404, f"no such change: {e}")
    except changes_svc.NothingToReview as e:
        raise HTTPException(409, str(e))
    except changes_svc.BranchUnknown as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- routes -------------------------------------------------------------------

@router.get("", response_model=ChangeCollection)
def list_changes(status: str | None = Query(None),
                 app_id: str | None = Query(None),
                 project: str | None = Query(None),
                 ticket_id: str | None = Query(None),
                 limit: int = Query(200, ge=1, le=1000)) -> ChangeCollection:
    """Open and historical changes, newest activity first.

    `ticket_id` answers "what change carries this ticket", which is the query
    the status derivation (step C2) runs; it is a filter here so that step does
    not have to scan.
    """
    rows = _guard(changes_svc.list_changes, status=status, app_id=app_id,
                  project_slug=project, ticket_id=ticket_id, limit=limit)
    return ChangeCollection(changes=[Change(**r) for r in rows])


@router.post("", response_model=Change)
def open_change(body: ChangeOpenIn) -> Change:
    """Open the change a branch has earned.

    409 when the branch has no commits ahead of its base: a change with an
    empty diff would assert work that does not exist. Idempotent -- a branch
    that already has an open change gets that change back, brought up to date
    with its current head.
    """
    row = _guard(changes_svc.open_change, body.project_slug, body.branch_slug,
                 title=body.title, summary=body.summary,
                 ticket_ids=body.ticket_ids, status=body.status,
                 worktree_id=body.worktree_id)
    return Change(**row)


@router.get("/{change_id}", response_model=Change)
def get_change(change_id: str) -> Change:
    return Change(**_guard(changes_svc.get_change, change_id))


@router.patch("/{change_id}", response_model=Change)
def patch_change(change_id: str, body: ChangePatchIn) -> Change:
    fields = body.model_dump(exclude_unset=True)
    if "gates" in fields and fields["gates"] is not None:
        fields["gates"] = [dict(g) for g in fields["gates"]]
    if "remote" in fields and fields["remote"] is not None:
        fields["remote"] = dict(fields["remote"])
    return Change(**_guard(changes_svc.update_change, change_id, **fields))


@router.delete("/{change_id}", response_model=ChangeDeleted)
def delete_change(change_id: str) -> ChangeDeleted:
    ok = _guard(changes_svc.delete_change, change_id)
    if not ok:
        raise HTTPException(404, f"no such change: {change_id}")
    return ChangeDeleted(ok=True, id=change_id)


@router.get("/{change_id}/diff", response_model=ChangeDiff)
def change_diff(change_id: str) -> ChangeDiff:
    """The review payload, computed from the branch on demand.

    Not stored on the row: the branch moves, and a stored diff is a stale diff
    the moment it does.
    """
    payload = _guard(changes_svc.diff_for, change_id)
    return ChangeDiff(summary=payload["summary"],
                      files=[ChangeFileDiff(**f) for f in payload["files"]])


@router.post("/{change_id}/refresh", response_model=Change)
def refresh_change(change_id: str) -> Change:
    """Bring `head_sha` and `base_ref` back in step with the branch.

    A sub-resource rather than a PATCH because the caller supplies no value:
    the new state comes from the repository, and a GET that silently mutated
    the row it returns would make "why did this change" unanswerable.
    """
    return Change(**_guard(changes_svc.refresh_change, change_id))
