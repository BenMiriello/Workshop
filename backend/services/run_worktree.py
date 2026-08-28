"""Which tree a NON-INTERACTIVE run works in, and the lease it holds on it.

The other half of `services/chat_worktree.py`. That module connected an
interactive chat to the lease layer built by
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`; this one
connects the participant section 6 calls non-interactive -- a sweep
(`scripts/project_ralph.sh`), the worker it drives (`scripts/ralph.sh`), and
anything else that is handed a worktree by `worktrees.provision_worktree`.

WHY IT HAD TO EXIST BEFORE THE MOUNT FLAG COULD BE ARMED. `tree_access.decide`
gives a read-write mount to exactly one answer -- "this session holds this
tree's lease" -- and until now no unattended run ever took one.
`allocate_task_worktree` and `provision_worktree` hand out a directory and
record it; neither acquires anything. So with `WORKSHOP_TREE_LEASE_MOUNT` on,
every sweep would have been read-only inside the worktree it had just been
given, and every unattended run would have failed at its first write. The flag
was not merely unarmed; it was unarmable.

--------------------------------------------------------------------------
THE HOLDER IS THE RUN, AND THE RUN ALREADY HAS AN ID
--------------------------------------------------------------------------
`holder_kind` is `run` (`db.models.LEASE_HOLDER_KINDS`), and `holder_id` is the
child Job id the sweep already uses for everything else about that attempt:

  * `services/tasks.py` claims the TICKET lease under it (measured: every
    `holder_kind='run'` row in the lease table carries a `job_...` id),
  * `scripts/ralph.sh` writes its presence row under it (`PRESENCE_ID`),
  * `services/run_control.py` names its control channel after it
    (`.runtime/control/<run-id>.json`), which is what Queue, Steer and Preempt
    in `services/control.py` reach for.

So Queue, Steer and Preempt find the same holder they were written to find, and
`control.holder_kind_of` resolves it through the presence row that is already
there. Inventing a second id here would have given one run two names and made
the verbs address a holder the lease table had never heard of.

--------------------------------------------------------------------------
ONE IDENTITY FOR THE TREE
--------------------------------------------------------------------------
Asked of `tree_access.subject_id_for`, exactly as `chat_worktree` asks it, and
for the reason that module's header gives: two identities for one subject is two
locks on one door. A run and a chat contending for the same directory have to
name it identically or neither exclusion means anything.

--------------------------------------------------------------------------
RENEWAL IS A WORK EVENT, AND IT NEEDS ITS OWN PROCESS
--------------------------------------------------------------------------
Section 6: "an interactive holder's lease is renewed by its presence; a
non-interactive holder's is renewed by its work events." A chat renews from the
drive loop that is already running. A sweep has no loop that ticks during the
work: `scripts/ralph.sh` calls `heartbeat_task` at the TOP of an iteration and
then blocks in `timeout $PER_ITER_TIMEOUT claude -p ...`, which is bounded at
1500s. Against a 90-second `LEASE_TTL_SEC` that is not a heartbeat gap, it is
sixteen of them: the lease would lapse minutes into every real iteration and the
tree would read as reclaimable while a worker was writing in it, which is worse
than never taking one.

So the renewal is `beat_forever()` -- this module run as `--verb beat`, spawned
by the sweep as a background child and killed when the sweep is done with the
tree. That is the correct place because it is the only thing in the sweep's
process tree that runs on a clock rather than on a boundary, and because its
lifetime is exactly the lifetime of the claim: it is born when the lease is
taken and dies when it is released.

IT WATCHES ITS PARENT AND IT NEVER RELEASES. If the sweep is SIGKILLed the
beater is reparented to init and would otherwise renew a dead run's lease
forever, pinning a tree nobody is in -- so it probes the parent every tick with
`port_registry.process_alive` (the probe section 6 names, which reads
/proc/<pid>/stat and calls a zombie dead) and exits when the parent is gone.
Exits, and does NOT release: section 3's requirement is that a crashed run falls
to the EXISTING expiry-and-fence path, so the lease is left to run out its 90
seconds and the next acquirer takes it through the ordinary upsert. Releasing
here would be a second reclaim mechanism racing the one that already works.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, replace

from services import leases, port_registry, tree_access

log = logging.getLogger(__name__)

__all__ = [
    "SUBJECT_KIND", "RunTree", "plan", "enter", "beat", "beat_forever",
    "leave", "main",
]

SUBJECT_KIND = tree_access.SUBJECT_KIND

# What a run's lease is worth per acquire/renew, and how often the beater ticks.
# Both come from the lease module rather than being re-chosen here: section 5
# fixed them at 90s and 30s for every tree lease, and a run that quietly gave
# itself a longer one would be the orphaned tree the short TTL exists to prevent.
TTL_SEC = leases.LEASE_TTL_SEC
BEAT_SEC = leases.HEARTBEAT_SEC


@dataclass(frozen=True)
class RunTree:
    """What a run holds on the tree it was handed.

    `cwd` is always answered, held or not, for the same reason `chat_worktree`
    answers it: the caller runs somewhere either way, and a bookkeeping layer
    that can leave the caller without a directory is a bookkeeping layer that
    can fail a run.
    """

    cwd: str
    subject_id: str | None = None
    lease: dict | None = None
    # The lease row when somebody ELSE holds this tree.
    blocked_by: dict | None = None
    detail: str = ""

    @property
    def held(self) -> bool:
        return bool(self.lease and self.lease.get("state") == "held")

    @property
    def generation(self) -> int | None:
        if not self.lease:
            return None
        try:
            return int(self.lease["generation"])
        except (KeyError, TypeError, ValueError):
            return None

    def as_dict(self) -> dict:
        return {
            "cwd": self.cwd,
            "subject_id": self.subject_id,
            "lease": self.lease,
            "blocked_by": self.blocked_by,
            "detail": self.detail,
            "held": self.held,
            "generation": self.generation,
        }


def _subject_id(cwd: str) -> str | None:
    """This tree's one lease subject id, or None when it cannot be computed.

    None is not "use the path instead". `tree_access` refuses to guess an
    identity and so does this: a subject we cannot name is a lease we must not
    take, because another participant would name it differently and the
    exclusion would be between two different doors.
    """
    try:
        return tree_access.subject_id_for(cwd)
    except Exception as exc:
        log.warning("run tree: cannot name the subject for %s: %s", cwd, exc)
        return None


def plan(cwd: str, *, run_id: str | None = None) -> RunTree:
    """Who holds this tree right now. Writes nothing."""
    subject_id = _subject_id(cwd)
    tree = RunTree(cwd=cwd, subject_id=subject_id)
    if not subject_id:
        return replace(tree, detail="This tree cannot be named as a lease "
                                    "subject, so nothing can be held on it.")
    try:
        current = leases.get(SUBJECT_KIND, subject_id)
    except leases.LeasesUnavailable:
        return replace(tree, detail="There is no lease store here.")
    except Exception as exc:
        return replace(tree, detail=f"The lease store could not be read "
                                    f"({exc.__class__.__name__}).")
    if not current or current.get("state") != "held":
        return tree
    if run_id and str(current.get("holder_id")) == str(run_id):
        return replace(tree, lease=current)
    return replace(tree, blocked_by=current)


def enter(run_id: str, cwd: str, *, ttl_sec: int = TTL_SEC) -> RunTree:
    """Take the lease on the tree this run was handed.

    NEVER RAISES. Every failure below the subject check -- no lease store, an
    unreachable database, an exception nobody predicted -- comes back as a
    `RunTree` with the right `cwd` and no lease, and the caller decides. That is
    the same contract `chat_worktree.enter` has and it exists for the same
    reason: a sweep on a file-backed project tree with no Postgres anywhere near
    it must still be able to work, exactly as it did before this table existed.

    THE ONE THING IT REFUSES is a tree somebody else is holding: that comes back
    as `blocked_by` with no lease, and the caller must not write there.
    """
    tree = plan(cwd, run_id=run_id)
    if not tree.subject_id or tree.blocked_by:
        return tree
    try:
        taken = leases.acquire(SUBJECT_KIND, tree.subject_id,
                               holder_kind="run", holder_id=str(run_id),
                               ttl_sec=ttl_sec)
    except leases.LeasesUnavailable:
        return replace(tree, detail="There is no lease store here, so this "
                                    "tree is not held by anybody.")
    except Exception as exc:
        log.warning("run tree: acquire failed for %s: %s", tree.subject_id, exc)
        return replace(tree, detail=f"The lease could not be taken "
                                    f"({exc.__class__.__name__}).")
    if taken is None:
        # Beaten to it between the read and the write.
        try:
            holder = leases.get(SUBJECT_KIND, tree.subject_id)
        except Exception:
            holder = None
        return replace(tree, lease=None, blocked_by=holder,
                       detail="Another session took this tree first.")
    return replace(tree, lease=taken)


def beat(subject_id: str, generation: int, *, ttl_sec: int = TTL_SEC) -> bool:
    """Renew a held tree. False means it is no longer ours: we were fenced.

    Marked as a WORK event, because a run that is still running is by definition
    still working: `leases.IDLE_AFTER_SEC` measures `last_work_at`, and a
    renewal that did not move it would paint every long iteration as an idle
    holder ten minutes in.

    A STORE THAT DID NOT ANSWER IS NOT A FENCE -- the same distinction
    `ralph.sh`'s `heartbeat_task` draws and for the same reason. An exception
    returns True (keep going; the lease's own `expires_at` still bounds us);
    only the store answering "your generation is stale" returns False.
    """
    try:
        renewed = leases.renew(SUBJECT_KIND, str(subject_id), int(generation),
                               ttl_sec=ttl_sec, work=True)
    except Exception as exc:
        log.debug("run tree: renew failed for %s: %s", subject_id, exc)
        return True
    return renewed is not None


def leave(subject_id: str, generation: int) -> bool:
    """Give the tree up now, fenced on the generation we took it with.

    RELEASED BY SUBJECT, NOT BY HOLDER, and that is deliberate:
    `leases.release_for_holder` would also drop this run's TICKET lease, which
    has a different owner (`services/tasks.py`), a different TTL and its own
    finalize path. One verb releasing two subjects is how the ticket lease would
    start disappearing from under `tasks.update()`'s fence.
    """
    try:
        return leases.release(SUBJECT_KIND, str(subject_id), int(generation))
    except Exception as exc:
        log.debug("run tree: release failed for %s: %s", subject_id, exc)
        return False


def beat_forever(subject_id: str, generation: int, *, parent_pid: int | None,
                 interval: int = BEAT_SEC, ttl_sec: int = TTL_SEC,
                 log_path: str | None = None) -> int:
    """Renew this tree every `interval` seconds until the run is done with it.

    Returns 0 when the parent went away, 9 when the lease was fenced -- the same
    number `ralph.sh` uses for DISPLACED, so a caller reading the exit code does
    not have to learn a second vocabulary.
    """
    def say(msg: str) -> None:
        line = f"[run-tree-beat] {msg}"
        if log_path:
            try:
                with open(log_path, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        print(line, file=sys.stderr, flush=True)

    while True:
        time.sleep(max(1, int(interval)))
        if parent_pid and not port_registry.process_alive(parent_pid):
            # The run is gone. Leave the lease to EXPIRE rather than releasing
            # it: a crashed holder is what the 90s TTL and the fence are for,
            # and a second reclaim path racing them is how a tree gets handed
            # over while the first holder is still, somehow, writing in it.
            say(f"the run (pid {parent_pid}) is gone; leaving lease "
                f"{subject_id} to expire")
            return 0
        if not beat(subject_id, generation, ttl_sec=ttl_sec):
            say(f"FENCED on {subject_id}: this run no longer holds this tree")
            return 9


# --- the shell entrypoint ----------------------------------------------------
#
# Every participant that holds a tree unattended is a bash process
# (`project_ralph.sh`, `ralph.sh`, `queue_runner.sh`), so a lease reachable only
# from Python is a lease none of them can take. Same reasoning, and the same
# venv re-exec, as `scripts/lease.py`; the shim that finds the right interpreter
# is `scripts/run_lease.py`.

def _emit(payload: dict, code: int) -> int:
    print(json.dumps(payload))
    return code


def main(argv: list[str] | None = None) -> int:
    """Exit codes, because bash reads those far better than it reads JSON:

        0  the verb took
        1  it did not (someone else holds the tree, or we were fenced)
        2  usage error
        3  there is no lease store, or this tree cannot be named as a subject

    3 is separated from 1 for the reason `scripts/lease.py` separates them: "no
    lease could be taken here" is a different fact from "you lost the race", and
    a sweep on a file-backed tree legitimately carries on without one.
    """
    ap = argparse.ArgumentParser(
        prog="run_lease",
        description="Take, renew and release a RUN's lease on the worktree it "
                    "was handed. Prints JSON; read the exit code.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    en = sub.add_parser("enter", help="take the lease on a tree")
    en.add_argument("--run-id", required=True)
    en.add_argument("--dir", required=True)
    en.add_argument("--ttl", type=int, default=TTL_SEC)

    bt = sub.add_parser("beat", help="renew until the run ends (a foreground "
                                     "loop; run it as a background child)")
    bt.add_argument("--subject-id", required=True)
    bt.add_argument("--generation", type=int, required=True)
    bt.add_argument("--parent-pid", type=int, default=0)
    bt.add_argument("--interval", type=int, default=BEAT_SEC)
    bt.add_argument("--ttl", type=int, default=TTL_SEC)
    bt.add_argument("--log", default=None)

    rl = sub.add_parser("release", help="give the tree up now (fenced)")
    rl.add_argument("--subject-id", required=True)
    rl.add_argument("--generation", type=int, required=True)

    gt = sub.add_parser("get", help="who holds this tree")
    gt.add_argument("--dir", required=True)
    gt.add_argument("--run-id", default=None)

    a = ap.parse_args(argv)

    # Without a pinned owner the relational scopes cannot resolve a tenant and
    # every verb would report "no lease store", which is the wrong-store failure
    # `scripts/lease.py` documents. Swallowed rather than required: a checkout
    # with no auth store still gets a usable `get`.
    try:
        from services import ownership
        ownership.pin_operator()
    except Exception:
        pass

    if a.cmd == "enter":
        tree = enter(a.run_id, os.path.realpath(a.dir), ttl_sec=a.ttl)
        if tree.held:
            return _emit(tree.as_dict(), 0)
        if tree.blocked_by:
            return _emit(tree.as_dict(), 1)
        return _emit(tree.as_dict(), 3)
    if a.cmd == "beat":
        return beat_forever(a.subject_id, a.generation,
                            parent_pid=a.parent_pid or None,
                            interval=a.interval, ttl_sec=a.ttl,
                            log_path=a.log)
    if a.cmd == "release":
        ok = leave(a.subject_id, a.generation)
        return _emit({"released": ok, "subject_id": a.subject_id}, 0 if ok else 1)
    if a.cmd == "get":
        tree = plan(os.path.realpath(a.dir), run_id=a.run_id)
        return _emit(tree.as_dict(), 0 if (tree.held or tree.blocked_by) else 1)
    return 2


if __name__ == "__main__":  # `python -m services.run_worktree ...`
    sys.exit(main())
