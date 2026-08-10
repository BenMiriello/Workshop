"""Keeping a linked ticket and its remote issue in step.

THREE OPERATIONS, and the design turns on which of them is even possible.

  publish   an APPROVED local ticket becomes a remote issue. Approval is the
            publish gate: a ticket becomes real work and reaches the tracker at
            the same moment, for the same reason, which is why no "should this go
            to the tracker?" setting exists. Drafts never publish.
  push      a local status change transitions the remote. Status is the one field
            workshop owns, because workshop runs the state machine and the
            tracker only learns the result.
  pull      remote changes come back, merged three ways against the shadow.

WHY THE SHADOW. Without a record of what both sides last agreed on, every
difference is a guess about who changed what, and the only implementable rule is
last-writer-wins — which silently destroys work. With it, "changed on one side" is
distinguishable from "changed on both", and only the second case needs a human.

WHY WRITE-THROUGH. A local edit to a tracker-owned field goes to the remote and
the local copy is updated from the response. There is never a local state the
remote has not seen, so divergence can only come from changes made remotely
between polls (normal) or a period when the integration was off (§6 of the
lifecycle doc). This is the operator's own instinct — "don't update ourselves till
we update that system" — made precise.

Nothing here raises into a caller that was doing something else: a failed sync
must not fail an approval or a run. Errors land on the binding's `last_error` and
on the ticket as evidence. The ONE exception is an explicit write-through
(`push_text(..., strict=True)`), where the caller IS the edit: §4 of the lifecycle
doc says a failed remote write rejects the edit, so that path raises
`RemoteWriteError` and the caller declines to save. Everything else swallows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from services import tasks, tracker_bindings, trackers

log = logging.getLogger(__name__)


class RemoteWriteError(RuntimeError):
    """The remote refused a write-through, so the local edit must not be applied.

    Only `push_text(strict=True)` raises it. Lifecycle doc §4: "A local edit to a
    tracker-owned field goes to the remote first and the local copy is updated
    from the response. If the remote write fails, the edit is rejected with the
    error." Rejecting is what guarantees there is never a local state the remote
    has not seen.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _binding(scope: str) -> dict | None:
    """This scope's binding, or None for ANY reason it cannot be read.

    Deliberately swallowing: sync is a courtesy path called from the middle of an
    approval and from the middle of a run, and its contract is that it never
    raises into them. Reading the connection needs a pinned data owner, so a
    background caller that has not pinned one gets None rather than an exception
    surfacing as a failed approval. The first version of this did raise, and it
    broke approving a ticket on a box with no tracker configured at all.
    """
    try:
        return tracker_bindings.binding_for(scope)
    except Exception as e:
        log.debug("no tracker binding readable for %s: %s", scope, e)
        return None


def _writes_allowed(scope: str) -> bool:
    b = _binding(scope)
    return bool(b and b.get("mode") == "sync")


def _shadow_of(issue: trackers.RemoteIssue) -> dict:
    return {"title": issue.title, "body": issue.body,
            "labels": list(issue.labels), "state": issue.state}


def _with_filter_label(labels: list[str]) -> list[str]:
    """Our own selection label kept on whatever we send.

    Without it, a write that replaces the label set drops the issue out of our own
    filter and the next poll reads it as a stranger's — the same trap the create
    path guards against, and a label edit is the other way to fall into it.
    """
    out = [str(x) for x in labels]
    filt = tracker_bindings.filter_for()
    if filt.kind == "label" and filt.label and filt.label not in out:
        out.append(filt.label)
    return out


def _sync_record(issue: trackers.RemoteIssue, provider: str, *,
                 state: str = "linked", conflict: dict | None = None) -> dict:
    return {"provider": provider, "remote_id": issue.remote_id,
            "remote_key": issue.key, "url": issue.url,
            "remote_updated": issue.updated, "last_synced": _now(),
            "state": state, "shadow": _shadow_of(issue),
            "conflict": conflict}


# ---- publish ---------------------------------------------------------------

def publish(scope: str, root: Path, task_id: str) -> dict | None:
    """Create the remote issue for an approved ticket. Idempotent.

    Returns the updated task, or None when this scope is not in `sync` mode, the
    ticket is already linked, or the ticket is a draft. All three are ordinary
    and none of them is an error.
    """
    if not _writes_allowed(scope):
        return None
    task = tasks.get_task(root, task_id)
    if task is None or task.get("status") == "draft":
        return None
    if (task.get("sync") or {}).get("remote_id"):
        return None

    binding = _binding(scope)
    if binding is None:
        return None
    try:
        adapter = trackers.for_binding(binding)
        # The selection label goes on, so the issue we just created will be found
        # by our own filter on the next poll instead of looking like a stranger's.
        labels = _with_filter_label(list(task.get("labels") or []))
        issue = adapter.create(title=task["title"], body=task.get("body") or "",
                               labels=labels, idem=task["id"])
    except Exception as e:
        log.warning("publish failed for %s: %s", task_id, e)
        tracker_bindings.note_poll(scope, None, error=f"publish: {e}"[:300])
        tasks.update(root, task_id,
                     evidence={"kind": "sync-error", "ref": f"publish failed: {e}"[:400]})
        return None

    return tasks.update(root, task_id,
                        sync=_sync_record(issue, binding["provider"]),
                        evidence={"kind": "sync",
                                  "ref": f"published as {issue.key} {issue.url}"})


# ---- push ------------------------------------------------------------------

def push_status(scope: str, root: Path, task_id: str) -> dict | None:
    """Transition the remote to match the local status. Workshop owns status."""
    if not _writes_allowed(scope):
        return None
    task = tasks.get_task(root, task_id)
    if task is None:
        return None
    sync = task.get("sync") or {}
    if not sync.get("remote_id"):
        return None
    want, _reason = trackers.remote_state(task["status"])
    if (sync.get("shadow") or {}).get("state") == want:
        return None  # already there; a no-op write is a wasted request

    binding = _binding(scope)
    if binding is None:
        return None
    try:
        adapter = trackers.for_binding(binding)
        issue = adapter.transition(sync["remote_id"], task["status"])
    except Exception as e:
        log.warning("status push failed for %s: %s", task_id, e)
        tasks.update(root, task_id,
                     evidence={"kind": "sync-error", "ref": f"status push failed: {e}"[:400]})
        return None
    return tasks.update(root, task_id, sync=_sync_record(issue, binding["provider"]))


def push_text(scope: str, root: Path, task_id: str, *,
              pending: dict | None = None, strict: bool = False) -> dict | None:
    """Write a local title/body/labels edit through to the remote.

    The tracker OWNS these fields, so this exists for the case where the operator
    edits a linked ticket here anyway. Write-through means the remote sees it
    immediately and the shadow is refreshed from the response, so the next poll
    does not read our own edit as a remote change.

    `pending` is the edit BEFORE it has been saved locally, which is the order §4
    requires: the remote goes first and the local copy follows. Any of "title",
    "body", "labels" it omits falls back to what the ticket already says, so a
    title-only edit does not have to resend the body. Called with no `pending`
    (the conflict-resolution path) it pushes what is already stored.

    `strict` says the caller IS the edit and will reject it if this fails, so the
    error is raised as `RemoteWriteError` instead of being swallowed. It is still
    recorded on the ticket either way.

    Returns None — not an error — when the ticket is local, the scope is not in
    `sync` mode, or there is no connection. Those are the ordinary cases where an
    edit is nobody else's business.
    """
    if not _writes_allowed(scope):
        return None
    task = tasks.get_task(root, task_id)
    if task is None:
        return None
    sync = task.get("sync") or {}
    if not sync.get("remote_id"):
        return None
    binding = _binding(scope)
    if binding is None:
        return None
    pending = pending or {}
    patch = {"title": pending.get("title", task["title"]),
             "body": pending.get("body", task.get("body") or ""),
             "task_id": task["id"]}
    if "labels" in pending:
        patch["labels"] = _with_filter_label(list(pending["labels"] or []))
    try:
        adapter = trackers.for_binding(binding)
        issue = adapter.update(sync["remote_id"], patch)
    except Exception as e:
        log.warning("text push failed for %s: %s", task_id, e)
        tasks.update(root, task_id,
                     evidence={"kind": "sync-error", "ref": f"text push failed: {e}"[:400]})
        if strict:
            raise RemoteWriteError(str(e)) from e
        return None
    return tasks.update(root, task_id, sync=_sync_record(issue, binding["provider"]))


def comment(scope: str, root: Path, task_id: str, markdown: str) -> bool:
    """One comment on the remote. The only write `import` mode ever performs,
    because a run summary is the thing the person who filed the issue wants."""
    task = tasks.get_task(root, task_id)
    if task is None:
        return False
    sync = task.get("sync") or {}
    binding = _binding(scope)
    if binding is None or not sync.get("remote_id"):
        return False
    try:
        trackers.for_binding(binding).comment(sync["remote_id"], markdown)
        return True
    except Exception as e:
        log.warning("comment failed for %s: %s", task_id, e)
        return False


# ---- pull ------------------------------------------------------------------

# Fields the tracker owns, so a remote change to one of them simply wins unless
# the local side also changed it. From services/trackers.OWNERSHIP, not a second
# opinion about the same question.
_REMOTE_OWNED = tuple(f for f, who in trackers.OWNERSHIP.items() if who == "remote")


def _merge_one(task: dict, issue: trackers.RemoteIssue) -> tuple[dict, dict | None]:
    """Three-way compare for one ticket. Returns (patch, conflict).

    local == shadow  and remote != shadow  -> take remote
    local != shadow  and remote == shadow  -> keep local (a push will carry it)
    both changed                            -> the owner wins, the loser is
                                               recorded as a conflict rather than
                                               being thrown away
    """
    shadow = (task.get("sync") or {}).get("shadow") or {}
    remote_now = {"title": issue.title, "body": issue.body, "labels": list(issue.labels)}
    local_now = {"title": task.get("title") or "", "body": task.get("body") or "",
                 "labels": list(task.get("labels") or [])}

    patch: dict = {}
    clashed: list[str] = []
    for field in ("title", "body", "labels"):
        if field not in _REMOTE_OWNED:
            continue
        base = shadow.get(field)
        r_changed = remote_now[field] != base
        l_changed = local_now[field] != base
        if r_changed and not l_changed:
            patch[field] = remote_now[field]
        elif r_changed and l_changed and remote_now[field] != local_now[field]:
            # The tracker owns the words, so it wins the write; the operator's
            # version is preserved on the ticket so nothing is silently lost.
            patch[field] = remote_now[field]
            clashed.append(field)

    conflict = None
    if clashed:
        conflict = {"fields": clashed, "since": _now(),
                    "mine": {f: local_now[f] for f in clashed},
                    "theirs": {f: remote_now[f] for f in clashed}}
    return patch, conflict


def pull(scope: str, root: Path) -> dict:
    """Bring remote changes back for every linked ticket in this scope.

    A closed remote issue moves the local ticket to done or cancelled: `state` is
    the one thing a tracker can tell us that workshop's own state machine did not
    already know, because somebody closed it over there.
    """
    binding = _binding(scope)
    if binding is None:
        return {"pulled": 0, "conflicts": 0, "reason": "not integrated"}

    try:
        adapter = trackers.for_binding(binding)
    except ValueError as e:
        return {"pulled": 0, "conflicts": 0, "reason": str(e)}

    pulled = conflicts = 0
    for task in tasks.list_tasks(root):
        sync = task.get("sync") or {}
        if not sync.get("remote_id") or sync.get("state") == "unlinked":
            continue
        try:
            issue = adapter.get(sync["remote_id"])
        except Exception as e:
            log.warning("pull failed for %s: %s", task.get("id"), e)
            tasks.update(root, task["id"],
                         sync={**sync, "state": "stale", "last_synced": sync.get("last_synced")})
            continue

        patch, conflict = _merge_one(task, issue)
        shadow_state = (sync.get("shadow") or {}).get("state")
        if issue.state != shadow_state and issue.state == "closed":
            # Somebody closed it over there. Honour that, with the reason.
            patch["status"] = trackers.local_status(
                issue.state, closed_reason=issue.closed_reason)

        if patch or conflict:
            pulled += 1
            if conflict:
                conflicts += 1
            tasks.update(
                root, task["id"],
                title=patch.get("title"),
                body=patch.get("body"),
                labels=patch.get("labels"),
                status=patch.get("status"),
                sync=_sync_record(issue, binding["provider"],
                                  state="conflict" if conflict else "linked",
                                  conflict=conflict),
                evidence=({"kind": "sync-conflict",
                           "ref": f"{', '.join(conflict['fields'])} changed on both sides"}
                          if conflict else None),
            )
        else:
            tasks.update(root, task["id"],
                         sync=_sync_record(issue, binding["provider"]))
    return {"pulled": pulled, "conflicts": conflicts}


def resolve_conflict(scope: str, root: Path, task_id: str, *, keep: str) -> dict | None:
    """Settle a conflict. `keep` is "mine" or "theirs".

    "theirs" is already applied, so it only clears the flag. "mine" re-asserts the
    operator's value and pushes it, which is the only way the local side can win a
    field the tracker owns.
    """
    task = tasks.get_task(root, task_id)
    if task is None:
        return None
    sync = task.get("sync") or {}
    conflict = sync.get("conflict") or {}
    if not conflict:
        return task
    if keep == "mine":
        mine = conflict.get("mine") or {}
        tasks.update(root, task_id, title=mine.get("title"), body=mine.get("body"),
                     labels=mine.get("labels"))
        push_text(scope, root, task_id)
        task = tasks.get_task(root, task_id) or task
        sync = task.get("sync") or {}
    return tasks.update(root, task_id,
                        sync={**sync, "state": "linked", "conflict": None},
                        evidence={"kind": "sync", "ref": f"conflict resolved, kept {keep}"})
