"""The three verbs: queue, steer, preempt.

Steps B6 and B7 of
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(sections 8 and 9). They are one module because they are one control channel:
the same question ("someone else is working here and I want something") with
three answers, and the UI shows them together on one card (section 14).

    queue    (default)  record it; the holder takes it when it finishes
    steer               deliver it into the holder's running turn
    preempt             stop the holder and take the lease

WHAT THIS MODULE DOES NOT CONTAIN
---------------------------------
A queue. Section 8: the queue is "ALREADY BUILT as `chat_queue` + `chat_drain`;
do not write a second one", so `queue()` below is a dispatch and a handful of
lines, not a store. For a chat holder it enqueues through `chat_queue` and wakes
`chat_drain` exactly as a message typed during a turn does; for a sweep it
writes the control file, which is the FIFO a bash loop can actually read.

A lease. `services/leases.py` is the acquire/release primitive and it carries
the `generation` and `PREEMPT_GRACE_SEC` this module uses. Nothing here
re-implements exclusion.

Priority. Section 8: "No priority numbers. The verb is the urgency." There is no
scalar, no tier and no reordering anywhere below, and queued requests are FIFO
and cancellable. The precedent it names is GitHub Actions concurrency groups
(queue versus cancel-in-progress), not Kubernetes PriorityClasses.

A permission system. This is a single-operator install (CLAUDE.md), so there is
no allowlist here, no role table and no per-user matrix. What preempt requires
is one specific thing and it is described where it is enforced, below.

STEER IS BEST-EFFORT AND THE COPY SAYS SO
-----------------------------------------
Section 8, and this repo measured it: `build_streaming.inject` writes into the
running turn's stdin, and the model "addresses the message at the END of its
current plan, skipping the step boundaries in between"
(`chat-message-ordering-audit-2026-08-05.md` section 2). Revision 1 of the
design sold steer as "delivered at the next safe boundary... in seconds", which
is the behaviour this repo tested and found false.

So every user-facing string this module produces is in `STEER_COPY_*` below,
spelled once, and none of them says "now", "immediately" or "at the next tool
call". A UI that wants to describe a steer renders what the verb returned.

PREEMPT IS THE ONLY ONE THAT DESTROYS ANYTHING, SO IT QUIESCES
--------------------------------------------------------------
Section 9, and this is the correction that matters. A preempted holder must NOT
commit to the branch: that puts a half-applied, possibly non-compiling commit
permanently into the merge set and leaves the next holder on a HEAD it did not
choose. It snapshots to a DANGLING ref instead, which still yields the sha the
design wants -- diffable, revertable, resumable, linkable from the timeline --
while the branch tip and HEAD stay exactly where they were.

And the lapsed-grace path is not "nothing is destroyed": the next participant
would find someone else's half-applied edits indistinguishable from its own,
which is the exact scar this whole effort exists to close. So a force stop is
followed by a quiesce -- snapshot, restore, clear a killed git's `index.lock` --
and ONLY THEN is the lease granted. Handing over a dirty tree is never
acceptable, and the snapshot is what makes cleaning it safe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from typing import Any

from services import (build_streaming, chat_drain, chat_log, chat_queue,
                      chat_store, git_service, leases, port_registry, presence,
                      run_control, worktree_records)

log = logging.getLogger(__name__)

__all__ = [
    "HOLDER_KINDS", "PREEMPT_GRACE_SEC",
    "STEER_COPY_CHAT", "STEER_COPY_CHAT_QUEUED", "STEER_COPY_RUN",
    "QUEUE_COPY", "PREEMPT_COPY",
    "ControlRefused", "HumanActionRequired",
    "holder_kind_of", "queue", "steer", "pending", "cancel",
    "quiesce", "preempt",
]

# The same closed set as `presence.SESSION_KINDS` and `LEASE_HOLDER_KINDS`: a
# session is a chat or an agent run, and a session is never an owner (section 7).
HOLDER_KINDS = ("chat", "run")

# NOT REDEFINED HERE. Section 5 gives the four windows and `services/leases.py`
# already holds them; a second copy of 30 is how two components start disagreeing
# about how long a holder gets.
PREEMPT_GRACE_SEC = leases.PREEMPT_GRACE_SEC

# --- the copy ----------------------------------------------------------------
#
# Section 8: "The UI must not label this `now`." These are the strings the verbs
# return, spelled once so a client renders the measured behaviour rather than a
# hopeful paraphrase of it. Read them as a promise about what will NOT be
# claimed: no "now", no "immediately", no "at the next step".

STEER_COPY_CHAT = (
    "Sent to the agent. It will see this while it is working, but when it acts "
    "on it is not guaranteed: in practice it finishes the plan it is on "
    "first.")
STEER_COPY_CHAT_QUEUED = (
    "The turn in flight cannot be spoken to, so this is waiting for it to "
    "finish. It goes out as the next turn.")
STEER_COPY_RUN = (
    "Folded into this run's next iteration. It is picked up when the current "
    "task finishes, not during it.")
QUEUE_COPY = "Waiting for the current work to finish. You can take it back out."
PREEMPT_COPY = (
    "Stopped, and its work was snapshotted to a ref you can go back to. The "
    "branch was not moved.")


class ControlRefused(ValueError):
    """The verb cannot mean what was asked: an unknown holder kind, an empty
    request, a subject nobody holds."""


class HumanActionRequired(PermissionError):
    """A preempt arrived without the live human action that authorizes it.

    SECTION 8'S AUTHORITY RULE, AND REVISION 1 GOT IT WRONG. It said the
    operator "and any chat acting on his behalf" may preempt, which swallows the
    rule it was standing next to, because EVERY chat acts on his behalf. So the
    authority is not a property of the caller at all:

    > Preempt requires an explicit human interaction in the request that carries
    > it. An agent may never preempt another agent; it queues or asks.

    Which is why this is expressed as an argument the call must CARRY
    (`human_action`) and not as an allowlist of session kinds. A session kind
    cannot distinguish "the operator pressed Take over in a ConfirmDialog" from
    "a subagent decided it was in a hurry", because both are chats. The presence
    of a named human interaction can, and it is the UI that supplies it.

    This is honest about what it is: a REQUIREMENT, not a proof. Nothing here
    can verify that a finger touched a screen. What it does is make the
    unattended path impossible to take by accident -- an agent that wants the
    tree finds no argument it can truthfully fill in, and the two verbs that
    need no such argument (queue, steer) are sitting right beside it.
    """


# --- who is being spoken to ---------------------------------------------------

def holder_kind_of(holder_id: str) -> str:
    """`chat` or `run` for this session id, best-effort.

    Presence is the authority when it has anything to say: every participant
    that can be steered writes it (a sweep by shelling `scripts/presence.py`, a
    chat from the backend). Falling back to the chat log rather than to a guess
    matters because the two branches below are entirely different mechanisms,
    and calling a chat a run would file a message somewhere nothing reads.
    """
    try:
        record = presence.get(str(holder_id))
    except Exception:
        record = None
    kind = (record or {}).get("session_kind")
    if kind in HOLDER_KINDS:
        return kind
    try:
        if chat_log.exists(str(holder_id)):
            return "chat"
    except Exception:
        pass
    return "run"


def _resolved_kind(holder_id: str, holder_kind: str | None) -> str:
    if holder_kind is None:
        return holder_kind_of(holder_id)
    if holder_kind not in HOLDER_KINDS:
        raise ControlRefused(
            f"unknown holder kind {holder_kind!r}; one of {list(HOLDER_KINDS)}")
    return holder_kind


def _request(entry: dict, *, verb: str) -> dict:
    """One waiting request, in the shape the wire uses for both backends.

    A chat queue entry is keyed by `client_id` and a control-file request by
    `id`; the caller of `/control/{session}/requests` should not have to know
    which store answered, because cancelling is the same act either way.
    """
    return {
        "id": str(entry.get("id") or entry.get("client_id") or ""),
        "verb": entry.get("verb") or verb,
        "text": entry.get("text") or "",
        "actor": entry.get("actor"),
        "at": entry.get("at") or entry.get("ts") or entry.get("received_at"),
    }


def pending(holder_id: str, *, holder_kind: str | None = None) -> list[dict]:
    """What is waiting for this session, oldest first. FIFO, no priority."""
    kind = _resolved_kind(holder_id, holder_kind)
    if kind == "chat":
        return [_request(e, verb="queue") for e in _chat_waiting(holder_id)]
    return [_request(r, verb="queue") for r in run_control.pending(holder_id)]


def _chat_waiting(holder_id: str) -> list[dict]:
    """What is queued for a chat, or nothing if that is not a chat id.

    `chat_queue.read` validates the id through `chat_log._check_id` and RAISES
    on anything that is not a thread key, which reached the route as a 500 for
    the ordinary case of asking about a session that does not exist. Asking who
    is waiting for an unknown session has an answer, and the answer is nobody.
    `scripts/check_routes_answer.py` gates 5xx here, so this is also the gate.
    """
    try:
        return list(chat_queue.read(holder_id))
    except ValueError:
        return []


def cancel(holder_id: str, request_id: str, *,
           holder_kind: str | None = None) -> bool:
    """Take a waiting request back out. Section 8: queued requests are
    cancellable, and this is the only way one leaves undelivered."""
    kind = _resolved_kind(holder_id, holder_kind)
    if kind == "chat":
        before = {e.get("client_id") for e in _chat_waiting(holder_id)}
        if request_id not in before:
            return False
        chat_queue.drop(holder_id, [request_id])
        return True
    return run_control.cancel(holder_id, request_id)


# --- queue -------------------------------------------------------------------

def queue(holder_id: str, text: str, *, actor: str | None = None,
          holder_kind: str | None = None,
          request_id: str | None = None) -> dict:
    """Record a request; the holder takes it when it finishes. The default verb.

    NO SECOND QUEUE (section 8). For a chat this is `chat_queue.enqueue` plus
    `chat_drain.drain_soon`, which is byte for byte the path a message typed
    during a turn already takes: restart-surviving, visible from every device,
    re-entering through the same door a live message does. For a sweep it is the
    control file, because a bash loop has no queue to enqueue onto.
    """
    text = (text or "").strip()
    if not text:
        raise ControlRefused("a control request needs text")
    kind = _resolved_kind(holder_id, holder_kind)
    rid = request_id or uuid.uuid4().hex
    if kind == "chat":
        chat_queue.enqueue(holder_id, text=text, client_id=rid)
        # Fire-and-forget: the queue may already be drainable (the turn ended
        # between the caller deciding to queue and this write). The drain
        # refuses while a turn is live, so asking costs nothing.
        chat_drain.drain_soon(holder_id, reason="queued through the control channel")
    else:
        run_control.submit(holder_id, verb="queue", text=text, actor=actor,
                           request_id=rid)
    return {"holder_id": holder_id, "holder_kind": kind, "verb": "queue",
            "request_id": rid, "delivered": False, "queued": True,
            "detail": QUEUE_COPY,
            "pending": pending(holder_id, holder_kind=kind)}


# --- steer -------------------------------------------------------------------

def steer(holder_id: str, text: str, *, actor: str | None = None,
          holder_kind: str | None = None,
          worktree_id: str | None = None) -> dict:
    """Deliver a request into the holder's running work. Best-effort.

    THE LATENCY IS UNBOUNDED AND THE RETURN VALUE SAYS SO. `delivered=True`
    means the bytes reached the running turn, which is the only thing this
    system can actually observe; it does not mean the model has read them, and
    the measurement in section 2 of the message-ordering audit says it will
    address them at the end of its current plan. `detail` carries the copy a
    client should render and none of the strings promise immediacy.

    A REFUSED DELIVERY FALLS BACK TO THE QUEUE rather than failing. A command
    turn (`/compact`) never reads its input, and a write to it silently
    succeeds; `build_streaming.inject` already refuses in that case, and the
    message is owed either way because someone asked for it.

    For a sweep there is no turn to inject into and section 8 calls the
    alternative better: the request is folded into the NEXT ITERATION'S prompt,
    a boundary at which the sweep has nothing half-applied.
    """
    text = (text or "").strip()
    if not text:
        raise ControlRefused("a control request needs text")
    kind = _resolved_kind(holder_id, holder_kind)
    rid = uuid.uuid4().hex

    if kind == "run":
        run_control.submit(holder_id, verb="steer", text=text, actor=actor,
                           request_id=rid)
        out = {"holder_id": holder_id, "holder_kind": kind, "verb": "steer",
               "request_id": rid, "delivered": False, "queued": True,
               "detail": STEER_COPY_RUN,
               "pending": pending(holder_id, holder_kind=kind)}
        _timeline(worktree_id, kind="steered", actor=actor, detail=text)
        return out

    delivered = False
    try:
        turn_id = chat_store.active_turn_id(holder_id)
        if turn_id and build_streaming.turn_is_live(turn_id):
            delivered = bool(build_streaming.inject(turn_id, text, client_id=rid))
    except Exception as exc:  # a turn we cannot reach is a queue, not a 500
        log.warning("steer into %s could not reach a turn: %s", holder_id, exc)
        delivered = False
    if not delivered:
        out = queue(holder_id, text, actor=actor, holder_kind=kind,
                    request_id=rid)
        out["verb"] = "steer"
        out["detail"] = STEER_COPY_CHAT_QUEUED
        _timeline(worktree_id, kind="steered", actor=actor, detail=text)
        return out
    _timeline(worktree_id, kind="steered", actor=actor, detail=text)
    return {"holder_id": holder_id, "holder_kind": kind, "verb": "steer",
            "request_id": rid, "delivered": True, "queued": False,
            "detail": STEER_COPY_CHAT,
            "pending": pending(holder_id, holder_kind=kind)}


# --- the timeline -------------------------------------------------------------

def _timeline(worktree_id: str | None, *, kind: str, actor: str | None = None,
              sha: str | None = None, detail: str | None = None) -> None:
    """Append one structured event, or do nothing.

    Section 9 puts the record on the WORKTREE TIMELINE as structured events --
    "acquired, steered, preempted by whom, snapshot `<sha>`, merged" -- and
    never as prose in a transcript. The kinds already exist
    (`db.models.WORKTREE_EVENT_KINDS`); this writes them.

    SWALLOWED ON PURPOSE. A steer whose holder has no worktree record, and a
    preempt on an installation with no relational store, must still work: the
    record is how the event is REMEMBERED, not how it happens. Failing the verb
    because the timeline is unavailable would make the bookkeeping load-bearing
    for the behaviour, which `services/worktree_records.py` is explicit that it
    must not be.
    """
    if not worktree_id:
        return
    try:
        worktree_records.add_event(worktree_id, kind=kind, actor=actor,
                                   sha=sha, detail=detail)
    except Exception as exc:
        log.info("worktree %s got no %s event: %s", worktree_id, kind, exc)


# --- quiesce ------------------------------------------------------------------

def quiesce(repo_path: str, *, session_id: str, message: str = "") -> dict:
    """Make a stopped holder's tree safe to hand over (section 9).

    > force-stop is followed by a quiesce: snapshot to the dangling ref, restore
    > the tree to a known state, clear a stale `.git/index.lock` left by a killed
    > git, and only then grant the lease.

    THE ORDER IS LOCK, SNAPSHOT, RESTORE, and the lock moves to the front for a
    mechanical reason the sentence above does not have to care about: while a
    killed git's `index.lock` is present EVERY subsequent write to the tree
    fails, including the snapshot. All three still happen before the lease is
    granted, which is the part the design makes load-bearing.

    THE SNAPSHOT INCLUDES UNTRACKED FILES, and it has to. The restore deletes
    them (`git clean`), so a tracked-only snapshot would mean cleaning destroyed
    work that nothing had recorded -- the opposite of "the snapshot is what makes
    cleaning it safe". See `git_service.snapshot`'s `include_untracked`.

    THE BRANCH TIP IS NOT TOUCHED and neither is HEAD. `head_before` and
    `head_after` are both returned so a caller (and a test) can assert that
    rather than trust it.

    PRECONDITION: the holder is already stopped. Clearing the index lock of a
    live git corrupts the index, so this is called after the force stop and
    never on a read path.
    """
    out: dict[str, Any] = {
        "repo": repo_path, "ok": False, "cleared_index_lock": False,
        "snapshot_sha": None, "snapshot_ref": None, "tree_was_clean": False,
        "restored": False, "removed": [], "clean": False,
        "head_before": None, "head_after": None, "detail": "",
    }
    if not repo_path or not git_service.is_repo(repo_path):
        out["detail"] = f"not a git repository: {repo_path}"
        return out
    out["head_before"] = git_service.rev_parse(repo_path, "HEAD")
    out["cleared_index_lock"] = git_service.clear_index_lock(repo_path)

    ref = f"refs/wip/{session_id}"
    snap = git_service.snapshot(
        repo_path, ref=ref,
        message=message or f"workshop quiesce snapshot: {session_id}",
        include_untracked=True)
    if not snap.get("ok"):
        # NOTHING IS RESTORED WHEN THE SNAPSHOT FAILED. Cleaning a tree whose
        # contents were not recorded is the destruction this step exists to
        # prevent, so the tree is left dirty and the caller is told why. A dirty
        # tree the next holder can see beats work nobody can get back.
        out["detail"] = f"snapshot failed, tree left untouched: {snap.get('detail')}"
        out["head_after"] = out["head_before"]
        return out
    out["snapshot_sha"] = snap.get("sha")
    out["snapshot_ref"] = snap.get("ref")
    out["tree_was_clean"] = bool(snap.get("clean"))

    reset = git_service.reset_hard(repo_path)
    out["restored"] = bool(reset.get("ok"))
    if not reset.get("ok"):
        out["detail"] = reset.get("detail") or "reset failed"
    # Ignored files stay: `node_modules` and friends are the app's heavy inputs
    # (`worktrees.workspace_inputs`), not the holder's half-applied edits, and
    # deleting them turns a handover into a re-provision.
    out["removed"] = git_service.clean(repo_path, directories=True, ignored=False)
    status = git_service.status(repo_path)
    out["clean"] = bool(status.get("clean"))
    out["head_after"] = git_service.rev_parse(repo_path, "HEAD")
    out["ok"] = out["restored"] and out["clean"]
    if out["ok"] and not out["detail"]:
        out["detail"] = ""
    elif not out["ok"] and not out["detail"]:
        out["detail"] = "the working tree is still dirty after the restore"
    return out


# --- preempt ------------------------------------------------------------------

async def _force_stop(holder_id: str, holder_kind: str) -> dict:
    """Stop the holder now, the grace having lapsed. Returns what was done.

    AWAITED, NOT FIRED AND FORGOTTEN, and that is the whole reason it is async.
    The next thing `preempt` does is quiesce the tree -- snapshot it, reset it,
    clean it -- and doing that while the holder is still writing would produce a
    snapshot of a moving target and then delete files out from under a live
    process. `build_streaming.stop_turn` is already bounded (it waits
    `STOP_WAIT_S` and reports rather than hanging), so waiting here costs a
    known amount and buys the guarantee the quiesce needs.

    A RUN is a separate OS process and its pid is on its presence record (the
    sweeps write `--pid $$`). Its process GROUP is signalled, because the sweep
    spawns `ralph.sh` which spawns `claude`, and SIGTERMing the parent alone
    leaves the expensive child running against the tree we are about to hand
    over. Liveness is `port_registry.process_alive`, which section 6 names
    specifically: it reads `/proc/<pid>/stat` and treats a zombie as dead, which
    `os.kill(pid, 0)` does not.

    A CHAT's work is a turn inside this process, and `build_streaming.stop_turn`
    already owns the SIGTERM-then-SIGKILL discipline for it.
    """
    done: dict[str, Any] = {"stopped": False, "how": "", "pid": None}
    if holder_kind == "chat":
        try:
            turn_id = chat_store.active_turn_id(holder_id)
            if turn_id and build_streaming.turn_is_live(turn_id):
                stopped = await build_streaming.stop_turn(turn_id)
                done.update(stopped=bool(stopped.get("stopped")),
                            how=stopped.get("reason") or "turn stopped")
            else:
                done.update(stopped=True, how="no turn was running")
        except Exception as exc:
            done["how"] = f"could not stop the turn: {exc}"
        return done

    record = presence.get(holder_id) or {}
    pid = record.get("pid")
    done["pid"] = pid
    if not pid or not port_registry.process_alive(int(pid)):
        done.update(stopped=True, how="the process was already gone")
        return done
    pid = int(pid)
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            done["how"] = f"could not signal pid {pid}: {exc}"
            return done
    done.update(stopped=True, how=f"SIGTERM to pid {pid}")
    return done


async def _wait_for_gone(pid: int | None, seconds: float) -> bool:
    if not pid:
        return True
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if not port_registry.process_alive(pid):
            return True
        await asyncio.sleep(0.2)
    return not port_registry.process_alive(pid)


def _lease_gone(subject_kind: str, subject_id: str, holder_id: str,
                generation: int) -> bool:
    """Whether the holder we are preempting has let go.

    Three ways it can be true and all of them mean the same thing to us: the
    lease expired, someone else holds it, or the generation moved past the one
    we saw. Read through `services/leases.py` so the presence rule (section 6)
    is applied on the way, and treated as "not yet" when the store cannot answer
    -- a database blip must not be read as a holder that stopped.
    """
    try:
        current = leases.get(subject_kind, subject_id)
    except Exception:
        return False
    if current is None:
        return True
    return (current.get("state") != "held"
            or str(current.get("holder_id")) != str(holder_id)
            or int(current.get("generation") or 0) != int(generation))


async def preempt(*, subject_id: str, new_holder_id: str,
                  actor: str, human_action: str,
                  subject_kind: str = "worktree",
                  new_holder_kind: str = "chat",
                  reason: str = "",
                  grace_sec: float = PREEMPT_GRACE_SEC,
                  repo_path: str | None = None,
                  worktree_id: str | None = None) -> dict:
    """Stop the holder, quiesce its tree, and take the lease. Section 9.

    `human_action` NAMES THE LIVE HUMAN INTERACTION THAT AUTHORIZED THIS, and
    an empty one is refused. See `HumanActionRequired` for why the rule is an
    argument rather than an allowlist of session kinds: every chat acts on the
    operator's behalf, so the session kind cannot carry the distinction, and an
    agent that wants this tree has queue and steer beside it.

    THE SEQUENCE, and each step is section 9's:

      1. Ask the holder to yield, which starts the grace. For a sweep that is
         the control file; it reads it at its next iteration and exits through
         the normal finalize path.
      2. Wait up to `grace_sec` (30s by default, section 5) for it to let go on
         its own. A holder that yields voluntarily has snapshotted itself.
      3. If the grace lapses, force stop.
      4. QUIESCE, always -- snapshot to `refs/wip/<holder>`, restore, clear a
         killed git's lock. A voluntary yield leaves a clean tree, so this is
         nearly free there; a forced one is exactly the case the quiesce exists
         for.
      5. Fence the lease (the generation moves, so the old holder's next write
         is refused) and acquire it for the new one.
      6. Write the timeline: `preempted` by whom, `snapshot <sha>`, `acquired`.

    Returns everything a caller needs to render what happened, including the
    snapshot sha and the head before and after, so "the branch did not move" is
    checkable rather than asserted.
    """
    if not (human_action or "").strip():
        raise HumanActionRequired(
            "preempt needs the human action that authorized it. Taking a tree "
            "away from whoever is working in it is a person's decision, not an "
            "agent's: an agent queues the request or asks for it.")
    if new_holder_kind not in HOLDER_KINDS:
        raise ControlRefused(
            f"unknown holder kind {new_holder_kind!r}; one of {list(HOLDER_KINDS)}")

    out: dict[str, Any] = {
        "subject_kind": subject_kind, "subject_id": str(subject_id),
        "preempted": False, "forced": False, "yielded": False,
        "holder_id": None, "holder_kind": None,
        "grace_sec": float(grace_sec), "stop_detail": "",
        "quiesce": None, "lease": None, "detail": "",
    }

    # `LeasesUnavailable` IS DELIBERATELY NOT CAUGHT, here or below. The route
    # answers it 503, for the reason `services/changes.py` gives: "nobody holds
    # this" because the database is down must not read the same as "nobody holds
    # this" because nobody does. Swallowing it into a refusal would make an
    # outage look like a decision.
    current = leases.get(subject_kind, str(subject_id))

    if current is None or current.get("state") != "held":
        # NOBODY IS HERE, so there is nothing to stop. This is a success and not
        # a refusal: the caller wanted the lease, and an unheld subject is the
        # cheapest possible way to give it to them. It is also the honest answer
        # to the race where the holder finished while the dialog was open.
        lease = leases.acquire(subject_kind, str(subject_id),
                               holder_kind=new_holder_kind,
                               holder_id=new_holder_id)
        out["lease"] = lease
        out["detail"] = "nobody was holding it"
        _timeline(worktree_id, kind="acquired", actor=new_holder_id,
                  detail=human_action)
        return out

    holder_id = str(current["holder_id"])
    holder_kind = str(current["holder_kind"])
    generation = int(current["generation"])
    out.update(holder_id=holder_id, holder_kind=holder_kind)

    if holder_id == str(new_holder_id):
        out["lease"] = current
        out["detail"] = "you already hold it"
        return out

    # 1. Ask. The control channel is per-session and works for either kind; for
    #    a sweep it is the only mechanism there is, and for a chat it is the
    #    durable record that it was asked before it was stopped.
    try:
        run_control.request_yield(holder_id, actor=actor,
                                  reason=reason or human_action)
    except run_control.ControlError as exc:
        log.info("preempt could not write a yield for %s: %s", holder_id, exc)

    # 2. The grace.
    deadline = asyncio.get_running_loop().time() + max(0.0, float(grace_sec))
    while asyncio.get_running_loop().time() < deadline:
        if _lease_gone(subject_kind, str(subject_id), holder_id, generation):
            out["yielded"] = True
            break
        await asyncio.sleep(0.25)
    else:
        out["yielded"] = _lease_gone(subject_kind, str(subject_id), holder_id,
                                     generation)

    # 3. The grace lapsed: stop it.
    if not out["yielded"]:
        stop = await _force_stop(holder_id, holder_kind)
        out["forced"] = bool(stop.get("stopped"))
        out["stop_detail"] = stop.get("how") or ""
        if stop.get("pid"):
            await _wait_for_gone(int(stop["pid"]), 3.0)
            if port_registry.process_alive(int(stop["pid"])):
                try:
                    os.killpg(os.getpgid(int(stop["pid"])), signal.SIGKILL)
                    out["stop_detail"] += f"; SIGKILL to pid {stop['pid']}"
                except OSError:
                    pass

    # 4. Quiesce. ALWAYS, and before the lease moves.
    path = repo_path or _repo_for(worktree_id)
    if path:
        out["quiesce"] = quiesce(path, session_id=holder_id)

    # 5. The lease. Fence first so the displaced holder's generation is dead
    #    even if the acquire below loses a race with a third party -- that is
    #    what `leases.fence` is for, and section 5 is explicit that it is the
    #    storage primitive underneath this verb rather than the verb itself.
    leases.fence(subject_kind, str(subject_id))
    out["lease"] = leases.acquire(subject_kind, str(subject_id),
                                  holder_kind=new_holder_kind,
                                  holder_id=new_holder_id)
    out["preempted"] = True
    out["detail"] = PREEMPT_COPY

    # 6. The record, structured, on the timeline (section 9).
    _timeline(worktree_id, kind="preempted", actor=actor,
              detail=reason or human_action)
    snap_sha = (out["quiesce"] or {}).get("snapshot_sha")
    if snap_sha:
        _timeline(worktree_id, kind="snapshot", actor=holder_id, sha=snap_sha,
                  detail=(out["quiesce"] or {}).get("snapshot_ref"))
    _timeline(worktree_id, kind="acquired", actor=new_holder_id,
              detail=human_action)
    # The presence record is the displaced holder's own claim to be here, and it
    # is no longer true. Withdrawing it is what keeps the directory honest
    # between the stop and the record's own expiry.
    try:
        presence.release(holder_id)
    except Exception:
        pass
    return out


def _repo_for(worktree_id: str | None) -> str | None:
    """The directory behind a worktree record, or None.

    Kept separate so `preempt` reads as its six steps: an installation with no
    relational store has no record to resolve, which is a preempt that moves no
    lease and quiesces nothing rather than an error.
    """
    if not worktree_id:
        return None
    try:
        return worktree_records.get_record(worktree_id).get("path")
    except Exception:
        return None
