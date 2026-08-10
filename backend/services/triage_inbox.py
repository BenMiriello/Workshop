"""Imported issues land here, not in the run queue.

A poll never creates runnable work. It creates CANDIDATES, and a person decides.
Three properties this buys, and each one is a failure mode avoided:

  - Nothing external ever starts a worker. An issue someone else wrote, in a
    tracker we do not control, is exactly the thing that should not silently
    spend money.
  - A badly written remote issue becomes a visibly under-specified draft rather
    than a run that failed at 3am for reasons nobody can reconstruct.
  - Ignoring is DURABLE. A tombstone by remote id means re-polling cannot
    resurrect a rejected issue, which is the single behaviour that makes people
    switch an integration off.

Accepting creates a `draft` ticket carrying the link, so an imported issue still
passes through approval like any other proposal.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import ownership, tasks, tracker_bindings, trackers

log = logging.getLogger(__name__)

STATES = ("pending", "accepted", "ignored", "snoozed")


def _path() -> Path:
    d = ownership.user_runtime_dir() / "trackers"
    d.mkdir(parents=True, exist_ok=True)
    return d / "triage.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("candidates") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)]


def _save(items: list[dict]) -> None:
    p = _path()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"candidates": items}, indent=2) + "\n")
    os.replace(tmp, p)


def _locked(fn):
    def wrapper(*a, **kw):
        lock = _path().with_suffix(".lock")
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            return fn(*a, **kw)
    return wrapper


def _key(scope: str, remote_id: str) -> str:
    return f"{scope}:{remote_id}"


def _linked_remote_ids(root: Path) -> set[str]:
    """Remote ids this scope already has a ticket for. Read from the tickets
    themselves rather than from a side table, so a link made anywhere is honoured
    and a candidate is never proposed for work already imported."""
    out: set[str] = set()
    try:
        for t in tasks.list_tasks(root):
            sync = t.get("sync") or {}
            if sync.get("remote_id"):
                out.add(str(sync["remote_id"]))
            for ref in (t.get("external_refs") or []):
                if ref.get("remote_id"):
                    out.add(str(ref["remote_id"]))
    except Exception as e:
        log.warning("triage: cannot read tickets for %s: %s", root, e)
    return out


@_locked
def sync(scope: str, root: Path) -> dict:
    """Poll the tracker for this scope and upsert candidates.

    Idempotent, and cheap when nothing moved: the cursor is GitHub's own `since`
    filter, so an unchanged tracker costs one request.
    """
    binding = tracker_bindings.binding_for(scope)
    if binding is None:
        return {"raised": 0, "skipped": 0, "reason": "not integrated"}

    adapter = trackers.for_binding(binding)
    filt = tracker_bindings.filter_for()
    try:
        issues, cursor = adapter.list_since(binding.get("cursor"), filt)
    except Exception as e:
        tracker_bindings.note_poll(scope, None, error=str(e)[:300])
        raise

    items = _load()
    by_key = {_key(i["scope"], i["remote_id"]): i for i in items}
    already = _linked_remote_ids(root)
    raised = skipped = 0
    now = _iso(_now())

    for issue in issues:
        k = _key(scope, issue.remote_id)
        existing = by_key.get(k)
        # An issue we wrote (it carries our marker) or already imported is not a
        # candidate; it is the other side of a ticket we have.
        if issue.workshop_id or issue.remote_id in already:
            skipped += 1
            continue
        # A tombstone is permanent. Re-polling must not resurrect a rejection.
        if existing and existing.get("state") == "ignored":
            skipped += 1
            continue
        if existing and existing.get("state") == "accepted":
            skipped += 1
            continue
        row = {
            "id": existing["id"] if existing else f"{scope}:{issue.remote_id}",
            "scope": scope,
            "provider": binding["provider"],
            "remote_id": issue.remote_id,
            "key": issue.key,
            "url": issue.url,
            "title": issue.title,
            "body": issue.body,
            "labels": list(issue.labels),
            "remote_state": issue.state,
            "updated": issue.updated,
            "state": existing.get("state") if existing else "pending",
            "snooze_until": existing.get("snooze_until") if existing else None,
            "task_id": existing.get("task_id") if existing else None,
            "first_seen": existing.get("first_seen") if existing else now,
            "last_seen": now,
        }
        # A snooze that has expired becomes pending again; one that has not stays
        # put, so snoozing survives a poll.
        if row["state"] == "snoozed" and row.get("snooze_until"):
            if row["snooze_until"] <= now:
                row["state"] = "pending"
                row["snooze_until"] = None
        by_key[k] = row
        if not existing:
            raised += 1

    _save(list(by_key.values()))
    tracker_bindings.note_poll(scope, cursor)
    return {"raised": raised, "skipped": skipped, "cursor": cursor}


def list_candidates(scope: str | None = None, state: str = "pending") -> list[dict]:
    rows = [c for c in _load() if state in ("all", c.get("state"))]
    if scope:
        rows = [c for c in rows if c.get("scope") == scope]
    rows.sort(key=lambda c: c.get("last_seen") or "", reverse=True)
    return rows


def get(candidate_id: str) -> dict | None:
    for c in _load():
        if c.get("id") == candidate_id:
            return c
    return None


@_locked
def accept(candidate_id: str, root: Path) -> dict:
    """Create a linked DRAFT ticket from a candidate.

    A draft, not a todo: someone else's issue is a proposal as far as this system
    is concerned, and it goes through the same approval as any other.
    """
    items = _load()
    row = next((c for c in items if c.get("id") == candidate_id), None)
    if row is None:
        raise KeyError(candidate_id)
    if row.get("state") == "accepted" and row.get("task_id"):
        existing = tasks.get_task(root, row["task_id"])
        if existing:
            return {"candidate": row, "task": existing, "created": False}

    task = tasks.add(
        root,
        title=row["title"] or f"{row['key']} from {row['provider']}",
        body=row.get("body") or "",
        status="draft",
        labels=list(row.get("labels") or []),
        source={"kind": "import", "ref": f"{row['provider']}:{row['key']}"},
        # The spec's author is the tracker. A run that fails on a vague remote
        # description should point there, not at the operator.
        scoped_by=f"import:{row['provider']}",
        sync={
            "provider": row["provider"],
            "remote_id": row["remote_id"],
            "remote_key": row["key"],
            "url": row["url"],
            "remote_updated": row.get("updated"),
            "last_synced": _iso(_now()),
            "state": "linked",
            # The shadow is what makes a later three-way merge possible: without
            # a record of what both sides last agreed on, every divergence is a
            # guess about who changed what.
            "shadow": {"title": row["title"], "body": row.get("body") or "",
                       "labels": list(row.get("labels") or []),
                       "state": row.get("remote_state")},
        },
    )
    row["state"] = "accepted"
    row["task_id"] = task["id"]
    _save(items)
    return {"candidate": row, "task": task, "created": True}


@_locked
def ignore(candidate_id: str) -> dict:
    """Tombstone a candidate. Permanent by design: a rejection that a later poll
    undoes is why people turn integrations off."""
    items = _load()
    row = next((c for c in items if c.get("id") == candidate_id), None)
    if row is None:
        raise KeyError(candidate_id)
    row["state"] = "ignored"
    row["snooze_until"] = None
    _save(items)
    return row


@_locked
def snooze(candidate_id: str, days: int = 7) -> dict:
    items = _load()
    row = next((c for c in items if c.get("id") == candidate_id), None)
    if row is None:
        raise KeyError(candidate_id)
    row["state"] = "snoozed"
    row["snooze_until"] = _iso(_now() + timedelta(days=max(1, int(days))))
    _save(items)
    return row


@_locked
def unignore(candidate_id: str) -> dict:
    """Undo a tombstone. Rare, but a permanent decision with no way back is a trap
    rather than a safeguard."""
    items = _load()
    row = next((c for c in items if c.get("id") == candidate_id), None)
    if row is None:
        raise KeyError(candidate_id)
    row["state"] = "pending"
    _save(items)
    return row


def counts(scope: str | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in _load():
        if scope and c.get("scope") != scope:
            continue
        out[c.get("state", "pending")] = out.get(c.get("state", "pending"), 0) + 1
    return out
