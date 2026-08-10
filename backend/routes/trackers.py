"""The external tracker surface: one connection, per-scope targets, triage.

Design: `docs/platform-redesign/ticket-lifecycle-and-integration-2026-08-10.md`.

Connect asks one question and disconnect asks one question; everything else that
looked like an option collapsed into a decision already made. Remote issues are
never deleted by any path here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import (tasks, tracker_bindings, tracker_credentials, tracker_sync,
                      trackers, triage_inbox)
from routes.tasks_all import _root_for_scope, scope_roots

router = APIRouter(prefix="/trackers", tags=["trackers"])


@router.get("")
async def get_connection() -> dict:
    """The connection, its targets, and whether the credential is usable."""
    rec = tracker_bindings.load()
    provider = rec.get("provider")
    cred = (tracker_credentials.status(provider, rec.get("credential_source") or "gh")
            if provider else None)
    return {
        "connection": rec,
        "credential": cred,
        "providers_built": list(trackers.PROVIDERS),
        "modes": list(trackers.MODES),
        "triage": triage_inbox.counts(),
    }


class ConnectIn(BaseModel):
    provider: str = Field(min_length=1)
    # owner/repo for github. Chosen by the operator, never guessed: filing into a
    # repo nobody picked is how tickets end up in a stranger's tracker.
    target: str = Field(min_length=1)
    scope: str = "workshop"
    mode: str = "import"
    credential_source: str = "gh"
    label: str | None = None


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    """Open THE connection, or point another scope at a target within it.

    Verifies the credential and the target BEFORE storing anything, so a binding
    never exists in a state that cannot talk to the tracker.
    """
    if _root_for_scope(body.scope) is None:
        raise HTTPException(400, f"unknown scope: {body.scope}")
    probe = {"provider": body.provider, "target": body.target,
             "credential_source": body.credential_source}
    try:
        adapter = trackers.for_binding(probe)
        account = adapter.whoami()
        adapter.labels()          # proves the target exists and is readable
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"could not reach {body.target}: {e}")
    try:
        rec = tracker_bindings.connect(
            provider=body.provider, target=body.target, scope=body.scope,
            mode=body.mode, credential_source=body.credential_source,
            label=body.label)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"connection": rec, "account": account.__dict__}


class ModeIn(BaseModel):
    mode: str


@router.post("/mode")
async def set_mode(body: ModeIn) -> dict:
    try:
        return {"connection": tracker_bindings.set_mode(body.mode)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class FilterIn(BaseModel):
    kind: str = "label"
    label: str = "workshop"
    query: str = ""


@router.post("/filter")
async def set_filter(body: FilterIn) -> dict:
    try:
        return {"connection": tracker_bindings.set_filter(
            kind=body.kind, label=body.label, query=body.query)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class DisconnectIn(BaseModel):
    # THE one question at disconnect. Default keeps everything: never lose data as
    # a side effect of a settings change.
    keep_imported: bool = True


@router.post("/disconnect")
async def disconnect(body: DisconnectIn) -> dict:
    """Close the connection. Remote issues are NEVER touched.

    Every linked ticket's `sync` record collapses into a frozen `external_refs`
    entry, so it can always say what it used to be. `keep_imported=false`
    additionally cancels the tickets that came FROM the tracker — cancelled, not
    deleted, because no ticket is ever deleted by any path.
    """
    unlinked = cancelled = 0
    for skey, root in scope_roots():
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            if not (t.get("sync") or {}).get("remote_id"):
                continue
            imported = str(t.get("scoped_by") or "").startswith("import:")
            tasks.update(root, t["id"], unlink=True)
            unlinked += 1
            if imported and not body.keep_imported and t.get("status") not in (
                    "done", "cancelled"):
                tasks.update(root, t["id"], status="cancelled",
                             blocked_reason="the tracker it came from was disconnected")
                cancelled += 1
    rec = tracker_bindings.disconnect()
    return {"connection": rec, "unlinked": unlinked, "cancelled": cancelled}


# ---- triage ----------------------------------------------------------------


@router.post("/poll")
async def poll(scope: str = "workshop") -> dict:
    """Pull changed issues into triage. Never creates runnable work."""
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    try:
        return triage_inbox.sync(scope, root)
    except Exception as e:
        raise HTTPException(502, f"poll failed: {e}")


@router.get("/triage")
async def list_triage(scope: str | None = None, state: str = "pending") -> dict:
    rows = triage_inbox.list_candidates(scope, state)
    return {"candidates": rows, "count": len(rows), "counts": triage_inbox.counts(scope)}


@router.post("/triage/{candidate_id}/accept")
async def accept(candidate_id: str) -> dict:
    row = triage_inbox.get(candidate_id)
    if row is None:
        raise HTTPException(404, f"candidate not found: {candidate_id}")
    root = _root_for_scope(row["scope"])
    if root is None:
        raise HTTPException(400, f"unknown scope: {row['scope']}")
    out = triage_inbox.accept(candidate_id, root)
    return {**out, "task": dict(out["task"], scope=row["scope"])}


@router.post("/triage/{candidate_id}/ignore")
async def ignore(candidate_id: str) -> dict:
    try:
        return {"candidate": triage_inbox.ignore(candidate_id)}
    except KeyError:
        raise HTTPException(404, f"candidate not found: {candidate_id}")


class SnoozeIn(BaseModel):
    days: int = 7


@router.post("/triage/{candidate_id}/snooze")
async def snooze(candidate_id: str, body: SnoozeIn | None = None) -> dict:
    try:
        return {"candidate": triage_inbox.snooze(candidate_id,
                                                 (body.days if body else 7))}
    except KeyError:
        raise HTTPException(404, f"candidate not found: {candidate_id}")


@router.post("/triage/{candidate_id}/unignore")
async def unignore(candidate_id: str) -> dict:
    try:
        return {"candidate": triage_inbox.unignore(candidate_id)}
    except KeyError:
        raise HTTPException(404, f"candidate not found: {candidate_id}")


# ---- two-way sync ----------------------------------------------------------


@router.post("/pull")
async def pull(scope: str = "workshop") -> dict:
    """Bring remote changes back for every linked ticket in this scope.

    Three-way against the shadow, so "changed here" and "changed there" are told
    apart and only a genuine clash needs the operator. A clash does not throw the
    loser away: the tracker owns the words so it wins the write, and the
    operator's version is kept on the ticket to choose from."""
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    return tracker_sync.pull(scope, root)


class ResolveIn(BaseModel):
    keep: str = Field(pattern="^(mine|theirs)$")


@router.post("/conflicts/{scope}/{task_id}/resolve")
async def resolve(scope: str, task_id: str, body: ResolveIn) -> dict:
    """Settle a conflict. "mine" re-asserts the operator's value and pushes it,
    which is the only way the local side wins a field the tracker owns."""
    root = _root_for_scope(scope)
    if root is None:
        raise HTTPException(400, f"unknown scope: {scope}")
    out = tracker_sync.resolve_conflict(scope, root, task_id, keep=body.keep)
    if out is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return {"task": dict(out, scope=scope)}
