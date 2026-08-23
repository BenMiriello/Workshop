"""Step B1: the mount follows the lease.

`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` §4.

    Holding the lease means getting the tree mounted read-write. Not holding it
    means getting the SAME tree mounted read-only.

THE LEASE IS BOOKKEEPING; THE MOUNT IS ENFORCEMENT, and getting that the other
way round is the error §4 exists to correct. A fencing token works because a
storage layer validates it, and for a working tree the storage layer is the
filesystem and git, which validate no generation at all: a holder whose lease
lapsed mid-test will `git add -A && git commit` into the tree and nothing stops
it. Nothing in `services/leases.py` protects a tree. What excludes a second
writer is `--ro-bind` instead of `--bind`, which is this module plus the four
lines it adds to `services/scoped_exec.wrap`.

§4 says what that buys, and all three are the reason this is worth the risk:

  1. Exclusion that survives a misbehaving holder -- revocation is just that the
     next turn does not get a read-write mount.
  2. An exact write-detection event. §14.1 of the sibling branches doc wanted
     "branch on write" and could not have it: a turn is a detached process
     inside bwrap and the backend cannot observe individual writes, and "first
     write" is not even classifiable, because the harness writes session and
     temp files constantly. Against a read-only mount the first GENUINE write
     fails `EROFS`, which is exact, cheap and observable. `detect_erofs` is that
     half, and §10.3 says what to do with it: offer a NEW branch.
  3. A safe default reader state: a session with no lease physically cannot
     damage the tree it is reading.

--------------------------------------------------------------------------
DEFAULT OFF, AND FAIL CLOSED, FOR THE REASON SEC-A1 DID THE SAME
--------------------------------------------------------------------------
`services/scoped_exec.py` is the sandbox EVERY live turn spawns inside,
including the operator's own chat. When the uid layer changed how turns spawn it
shipped behind a flag that fails closed on a broken precondition, was not turned
on, and left its attended verification to the operator. This does the same:

  * The flag is `WORKSHOP_TREE_LEASE_MOUNT` (`scoped_exec.tree_lease_mounts()`),
    unset means OFF, and with it off the spawn path is byte-for-byte what it is
    today -- this module is not even imported.
  * With it ON, the ONLY path to a read-write mount is an affirmative answer to
    one question: does THIS session hold the worktree lease on THIS tree. Every
    other outcome -- no lease, someone else's lease, an unreadable lease store,
    no relational store at all, an exception nobody predicted -- is read-only.
    There is no code path here that turns "I could not tell" into read-write.

That last point has a consequence worth saying out loud rather than discovering:
on an installation with NO relational store, the flag being on means every tree
is read-only, because nobody can hold a lease. That is not a bug in this module,
it is what "the mount is the enforcement" means when the bookkeeping is missing,
and it is one of the things the operator's attended verification has to look at
before this is turned on anywhere.

--------------------------------------------------------------------------
THE SELF-REPO IS NOT CARVED OUT (§14.3 of the branches doc)
--------------------------------------------------------------------------
`scoped_exec.turn_user` carves the platform's own repo out of the UID layer, and
it would be easy to copy that here. §14.3 says the opposite, explicitly: the
platform repo "is where agents collide most -- this session alone measured a
concurrent worker resetting the shared tree and discarding another agent's
uncommitted edits", the post-commit guardrail for it already exists, and so it
"should be first, not last". So the self-repo is an ordinary subject here and
gets no exemption.

The cost of that honesty is the sharpest edge on this whole step, and the
operator should know it before flipping the flag: with the flag on, a chat in
the platform repo that holds no worktree lease gets `/workspace` read-only, and
that includes the operator's own chat. Nothing here turns the flag on.

--------------------------------------------------------------------------
WHICH SUBJECT ID A TREE IS
--------------------------------------------------------------------------
`lease.subject_id` is TEXT (see the migration's own note), and two things in
this repo already put different values in it for `subject_kind='worktree'`:
`routes/control.py` passes a worktree RECORD id, while `presence` addresses a
worktree by branch slug. Two identities for one subject is two locks on one
door, so this module picks ONE, deterministically:

    the worktree record id when a record exists for this path, else the
    realpath of the tree itself

and refuses to guess. If the record store cannot be read, the identity is
unknown, and an unknown identity is read-only -- not a second candidate to try.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# The lease subject kind a tree is addressed by (`db.models.LEASE_SUBJECT_KINDS`).
SUBJECT_KIND = "worktree"

__all__ = [
    "SUBJECT_KIND", "MountDecision", "enabled", "subject_id_for", "decide",
    "writable", "detect_erofs", "erofs_path", "first_write_offer",
]


@dataclass(frozen=True)
class MountDecision:
    """What the mount should be, and why -- the "why" is not decoration.

    A read-only mount is going to make a turn fail at its first write, and the
    operator (or an EROFS handler) needs to be able to tell "you do not hold
    this tree" from "the lease store is down" without re-deriving it.
    """

    mode: str                 # "rw" | "ro"
    reason: str
    subject_id: str | None = None
    lease: dict | None = None
    flag_on: bool = True

    @property
    def read_only(self) -> bool:
        return self.mode != "rw"


def enabled() -> bool:
    """Whether the mount follows the lease. Unset flag means no: today's path."""
    from services import scoped_exec
    return scoped_exec.tree_lease_mounts()


# --- the subject --------------------------------------------------------------

def _record_id_for_path(real: str) -> str | None:
    """The worktree record whose path IS this tree, or None if none is.

    RAISES when the record store cannot answer. That is the whole point: a
    caller that cannot tell whether a record exists cannot compute the canonical
    subject id, and `decide` turns that into a read-only mount rather than into
    a fallback identity that a second participant might not agree with.
    """
    from services import worktree_records
    for row in worktree_records.list_records(limit=500):
        path = row.get("path")
        if not path:
            continue
        try:
            if os.path.realpath(path) == real:
                return str(row["id"])
        except OSError:
            continue
    return None


def subject_id_for(root: str) -> str:
    """The one lease subject id for this tree. May raise (see above)."""
    real = os.path.realpath(root)
    return _record_id_for_path(real) or real


# --- the decision -------------------------------------------------------------

def decide(root: str, *, session_id: str | None,
           holder_kind: str | None = None) -> MountDecision:
    """Read-write iff `session_id` holds this tree's worktree lease.

    NEVER RAISES, and never returns "rw" on a path it did not fully verify. The
    caller is `scoped_exec.wrap`, which is three lines away from spawning the
    operator's chat; an exception there is a turn that does not run at all, and
    a wrong "rw" is the exclusion silently not existing. Both are worse than a
    read-only mount with a reason attached.
    """
    if not enabled():
        return MountDecision("rw", "the mount does not follow the lease "
                             "(WORKSHOP_TREE_LEASE_MOUNT is off)", flag_on=False)
    if not root:
        return MountDecision("ro", "no tree to mount")
    if not session_id:
        # A turn with no session identity cannot hold a lease, so it reads. This
        # is the shape §10.2 calls the common case, not an error.
        return MountDecision("ro", "this turn carries no session id, so it "
                             "holds nothing")
    try:
        subject_id = subject_id_for(root)
    except Exception as exc:
        return MountDecision("ro", f"the worktree record store could not say "
                             f"what this tree is ({exc.__class__.__name__}), "
                             f"so it is read-only")
    try:
        from services import leases
        lease = leases.get(SUBJECT_KIND, subject_id)
    except Exception as exc:
        return MountDecision(
            "ro", f"the lease store could not be read ({exc.__class__.__name__}), "
            f"so it is read-only", subject_id=subject_id)
    if lease is None:
        return MountDecision("ro", "nobody holds this tree", subject_id=subject_id)
    if lease.get("state") != "held":
        return MountDecision("ro", "this tree's lease has expired",
                             subject_id=subject_id, lease=lease)
    if str(lease.get("holder_id")) != str(session_id):
        return MountDecision("ro", "another session holds this tree",
                             subject_id=subject_id, lease=lease)
    if holder_kind and lease.get("holder_kind") != holder_kind:
        return MountDecision(
            "ro", "the lease on this tree is held under a different session "
            "kind", subject_id=subject_id, lease=lease)
    return MountDecision("rw", "this session holds the lease",
                         subject_id=subject_id, lease=lease)


def writable(root: str, *, session_id: str | None,
             holder_kind: str | None = None) -> bool:
    return not decide(root, session_id=session_id,
                      holder_kind=holder_kind).read_only


# --- the EROFS event (§4.2) ---------------------------------------------------
#
# What the platform actually sees is a tool result. The turn is detached inside
# bwrap, so the backend never observes the syscall; it observes the string the
# failing tool printed. These are the forms this repo's tools produce for errno
# 30, checked against python's `strerror` and coreutils' message.

_EROFS_MARKERS = (
    "read-only file system",
    "errno 30",
    "erofs",
)

# The path in the message, when there is one. Deliberately anchored on the
# MOUNTPOINT: a path outside it is a granted location and is not this event.
_PATH_RE = re.compile(r"(/workspace(?:/[^\s'\":,)]*)*)")


def detect_erofs(text: str | None) -> bool:
    """Whether this tool output is the first-write event §4 describes.

    Substring matching, and that is a deliberate ceiling on the claim: this
    recognises the message, not the syscall. A false positive costs an offer the
    operator can decline; a false negative costs nothing that was not already
    lost. Neither is allowed to cost a crash, which is why the caller treats
    this as advisory.
    """
    if not text:
        return False
    low = str(text).lower()
    return any(m in low for m in _EROFS_MARKERS)


def erofs_path(text: str | None) -> str | None:
    """The `/workspace/...` path the failed write was aimed at, best effort."""
    if not text:
        return None
    m = _PATH_RE.search(str(text))
    return m.group(1) if m else None


def first_write_offer(text: str | None, *, current_ref: str,
                      ticket_id: str | None = None,
                      existing_branches: tuple[str, ...] | list[str] = (),
                      ) -> dict | None:
    """Turn an EROFS tool result into §10.3's offer, or None if it is not one.

    NOT A CRASH AND NOT AN AUTOMATIC BRANCH. The write failed; what the platform
    owes the operator is the choice, defaulted to the safe side. The default is
    a NEW branch and joining an existing one is only ever a choice someone makes
    -- `services/session_defaults.first_write_offer` is where that rule lives
    and why.
    """
    if not detect_erofs(text):
        return None
    from services import session_defaults
    offer = session_defaults.first_write_offer(
        current_ref=current_ref, ticket_id=ticket_id,
        existing_branches=existing_branches)
    offer["blocked_path"] = erofs_path(text)
    return offer
