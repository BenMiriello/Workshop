"""Which tree an interactive chat's turn runs in, and the lease it holds on it.

Connection C-3 of `docs/platform-redesign/EXECUTION-PLAN-2026-08-27.md`: the
one that was missing. `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
built the lease (B3), the record (B2), presence (B4), the read-only mount (B1),
the holder UI (B5), the verbs (B6/B7) and the resolution table (B8) -- and then
nothing gave an interactive chat a lease, so every one of those pieces was
reachable only from a test. This module is the caller they were waiting for.

--------------------------------------------------------------------------
HOLDING A TREE IS OPT-IN, AND THE OPT-IN IS PICKING THE WORK
--------------------------------------------------------------------------
Section 10 is the whole argument and it is a resolution table, not a toggle:

    1. Opened from a ticket, run or branch -> that one. Inherited, shown,
       changeable.
    2. Otherwise -> the app's default ref, READ-ONLY, with a staleness banner.
       Most sessions never leave this state.

So a chat holds a tree when, and only when, it is PINNED to a branch. Nothing
here pins one on a chat's behalf, and a chat with no pin resolves to exactly the
directory it resolved to before this module existed, takes no lease, writes no
presence, and behaves identically. That is section 10.2's "most sessions never
leave this state" expressed as a default that cannot surprise anybody: the
question "which branch does this chat go in" is never asked, because the answer
follows from what was opened.

Automatic-for-every-project-chat was the alternative and section 7 rules it out
in one line -- "A chat never owns a tree. Sessions are disposable; trees are
not." Leasing a tree for every project chat would make opening six chats about
one thing cost six contended trees, which is the cost section 7 promises is
zero.

--------------------------------------------------------------------------
THE PIN IS DURABLE; THE LEASE IS NOT
--------------------------------------------------------------------------
Two different lifetimes, and folding them together is what would orphan trees:

  * The PIN is thread state (a project chat) or agent-record state (a maker).
    It survives restarts and answers "where does this chat work".
  * The LEASE is taken when a turn starts and released when it ends. It answers
    "who may write to this tree RIGHT NOW", which is a question only a running
    turn has. Section 6's join rule -- an interactive holder's lease is renewed
    by its presence, and presence expiry releases it immediately -- is honoured
    by writing presence for exactly as long as the turn runs, and by
    `presence.release()` at the end, which already calls
    `leases.release_for_holder`.

A crashed backend leaves both to expire on their own clocks (90s of lease TTL,
then `tree_access.decide` reads `expired` and the next turn gets a read-only
mount). Nothing new reclaims anything; the existing expiry and fence path does.

--------------------------------------------------------------------------
ONE IDENTITY FOR THE TREE
--------------------------------------------------------------------------
`services/tree_access.subject_id_for` decides what a tree IS as a lease subject
(the worktree record id when a record exists for the path, else the realpath),
and this module asks it rather than deriving a second answer. Two identities for
one subject is two locks on one door -- which is the exact defect that module's
header was written to prevent, so re-deriving one here would reintroduce it in
the first caller.

THE MOUNT IS STILL THE ENFORCEMENT. Taking a lease here does not stop a second
writer; `WORKSHOP_TREE_LEASE_MOUNT` does, and nothing in this file reads or sets
it. What this file changes is that the lease the mount asks about can now exist.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from typing import Any

from services import (build_agents, chat_context, git_service, leases,
                      presence, session_defaults, tree_access, worktrees)

log = logging.getLogger(__name__)

__all__ = [
    "TreeState", "plan", "enter", "beat", "leave", "branch_options",
    "pin_offer", "apply_choice", "set_pin", "diff", "merge", "SUBJECT_KIND",
]

SUBJECT_KIND = tree_access.SUBJECT_KIND

# A turn's presence must outlive the gap between two heartbeats, or a chat that
# is plainly working would look absent and section 6's passive rule would expire
# its own lease under it. Three heartbeats of slack, matching the lease TTL's
# own ratio (`leases.LEASE_TTL_SEC` is 90 against a 30s beat).
PRESENCE_TTL_SEC = leases.LEASE_TTL_SEC


@dataclass(frozen=True)
class TreeState:
    """Where this chat's turns run, and what it holds there.

    `cwd` IS ALWAYS ANSWERED, including for a chat that holds nothing, so the
    caller never has to branch on whether this module had an opinion. When there
    is no pin it is the directory the turn would have used anyway.
    """

    cwd: str
    project_slug: str | None = None
    scope: str | None = None
    branch_slug: str | None = None
    branch: str | None = None
    # `branch` (a pin) or `app_default` (the shared tree), from
    # `session_defaults.resolve`. Never invented here.
    source: str = "app_default"
    read_only: bool = True
    detail: str = ""
    subject_id: str | None = None
    lease: dict | None = None
    # The lease row when somebody ELSE holds this tree. The caller turns this
    # into the queue/steer/take-over offer that already exists; nothing here
    # decides for the operator.
    blocked_by: dict | None = None
    # Set when THIS enter() call preempted a prior holder via snapshot-and-fence.
    # Carries {"holder_kind", "holder_id", "wip_ref"} so the transcript can say
    # who was displaced and where the snapshot landed.
    preempted: dict | None = None

    @property
    def pinned(self) -> bool:
        return bool(self.branch_slug)

    @property
    def held(self) -> bool:
        return bool(self.lease and self.lease.get("state") == "held")

    @classmethod
    def from_dict(cls, data: dict | None) -> "TreeState | None":
        """Rebuild one from `as_dict`, e.g. off a turn journal after a restart.

        `generation` IS WHAT SURVIVES AND IT DOES NOT MOVE. `leases.renew` is
        fenced on the generation and does not bump it (only `acquire` does), so
        a turn adopted after a backend restart can go on renewing the same lease
        with the record it started with -- and if it was fenced meanwhile, the
        renew simply returns None and the turn stops claiming it.
        """
        if not data:
            return None
        known = {f: data.get(f) for f in (
            "cwd", "project_slug", "scope", "branch_slug", "branch", "source",
            "read_only", "detail", "subject_id", "lease", "blocked_by",
            "preempted")}
        if not known.get("cwd"):
            return None
        return cls(**known)

    def as_dict(self) -> dict:
        return {
            "cwd": self.cwd,
            "project_slug": self.project_slug,
            "scope": self.scope,
            "branch_slug": self.branch_slug,
            "branch": self.branch,
            "source": self.source,
            "read_only": self.read_only,
            "detail": self.detail,
            "subject_id": self.subject_id,
            "lease": self.lease,
            "blocked_by": self.blocked_by,
            "preempted": self.preempted,
            "pinned": self.pinned,
            "held": self.held,
        }


# --- reading the pin ---------------------------------------------------------

def _project_of(provider: Any) -> str | None:
    """The project whose branches this chat can be pinned to, or None.

    A project chat's own slug; a maker agent's linked project. Anything else
    (a data chat, a freestanding agent with a scratch dir) has no branch space
    and therefore can never hold a tree.

    READ THE SLUG, NOT THE KEY. This used to require `key.startswith("project:")`
    before trusting `slug`, and that is a real bug rather than a style point:
    `DevelopmentContextProvider` is a `ProjectContextProvider` that deliberately
    keys itself `development` instead of `project:development` (ADR 0014, so the
    picker groups the platform's own source above the projects). So the one chat
    that cuts more branches than any other answered `can_pin: false`, the tree
    pill rendered nothing at all on it, and eleven of its branches were visible
    only from /branches. `is_build_provider` above is already the membership
    test; the key is a routing detail and never was the question.

    THE MAKER IS THE ONE EXCLUSION, because `slug` does not mean the same thing
    on both build providers: on a project provider it IS the project, on a maker
    agent it is the AGENT and the project is `project_slug`. A maker with no
    linked project has no branch space, and must stay None rather than pinning
    itself to a project named after itself.
    """
    if not chat_context.is_build_provider(provider):
        return None
    # A maker names its project explicitly. No name, no project.
    slug = getattr(provider, "project_slug", None)
    if slug:
        return str(slug)
    if str(getattr(provider, "key", "")).startswith("agent:"):
        return None
    # Everything else that reaches here IS a project provider, and `slug` is
    # which project it is.
    return str(getattr(provider, "slug", "")) or None


# PUBLIC ALIAS. `build_streaming` needs the same answer on the write-blocked
# path and had grown its own copy of the rule, which was the pre-fix version of
# it. One name, so the next caller asks rather than re-derives.
project_of = _project_of


def _pinned_branch(thread: dict | None, provider: Any) -> str | None:
    """Which branch this chat is pinned to, from whichever record owns the pin.

    A MAKER'S PIN IS ON THE AGENT, NOT ON THE THREAD, and that is not an
    inconsistency: a maker agent is one worker with one working tree, its cwd is
    already resolved from that pin (`build_agents.create_agent`), and every chat
    addressed to it is the same worker. A project chat is the other shape --
    many chats, one shared tree -- so its pin is per conversation.
    """
    key = str(getattr(provider, "key", "") or "")
    if key.startswith("agent:"):
        try:
            return build_agents.get_agent(getattr(provider, "slug", "")).get("branch_slug") or None
        except Exception:
            return None
    return (thread or {}).get("branch_slug") or None


def _default_ref(project_slug: str | None) -> str:
    """The trunk's own name, for the read-only line section 10.2 asks for.

    Asked of the branches layer, whose policy naming is (section 13.2), so a
    repo whose trunk is `master` is not described as `main`.

    ONE WORD, AND IT IS THE REF'S OWN. This used to answer "the shared tree" for
    a chat with no project, which put a second term for one place into a
    sentence that already had the first: the operator, on the chat's own tree
    section, "We have one that's called 'shared tree main'. So it means we're
    working on main. Why are there two terms here? Is 'shared tree' the name of
    the work tree, or is it something else?" It was neither -- it was `main`
    again. With no project there is no trunk to name, so this gives what
    `worktrees.default_ref` gives when it cannot resolve one: `HEAD`, which is
    true of every repository and is git's word rather than an invented one."""
    if not project_slug:
        return "HEAD"
    return worktrees.default_ref(project_slug)


def _subject_id(cwd: str) -> str | None:
    """This tree's one lease subject id, or None when it cannot be computed.

    None is not "use the path instead": `tree_access` refuses to guess an
    identity, and so does this. A subject we cannot name is a lease we must not
    take, because a second participant would name it differently.
    """
    try:
        return tree_access.subject_id_for(cwd)
    except Exception as exc:
        log.warning("chat tree: cannot name the subject for %s: %s", cwd, exc)
        return None


# --- the read ----------------------------------------------------------------

def plan(thread: dict | None, provider: Any, *, fallback_cwd: str,
         holder_id: str | None = None) -> TreeState:
    """Where this chat's next turn would run, and who holds it. Writes nothing.

    Serves `GET /chat/threads/{id}/tree` and is the first half of `enter`, so
    the pill the operator reads and the directory the turn spawns in can never
    be two different answers.
    """
    project_slug = _project_of(provider)
    branch_slug = _pinned_branch(thread, provider)
    default_ref = _default_ref(project_slug)
    # The chat's own id IS its holder id: the lease is taken under the thread id,
    # which is also what `scoped_exec` asks the mount layer about. `enter` passes
    # the id it was called with rather than relying on the record carrying one,
    # so a caller that already knows who is asking cannot be told its own lease
    # belongs to somebody else.
    holder_id = str(holder_id or (thread or {}).get("id") or "")

    if not (project_slug and branch_slug):
        # SECTION 10.2, and the reason this branch exists at all: an unpinned
        # chat is the common case and it must come out of here holding nothing,
        # at the same cwd it has always used.
        state = session_defaults.resolve(app_default_ref=default_ref)
        return TreeState(cwd=fallback_cwd, project_slug=project_slug,
                        scope=(f"project:{project_slug}" if project_slug else None),
                        source=state["source"], read_only=state["read_only"],
                        detail=state["detail"])

    try:
        path = worktrees.branch_dir(project_slug, branch_slug)
    except ValueError as exc:
        return TreeState(cwd=fallback_cwd, project_slug=project_slug,
                        scope=f"project:{project_slug}",
                        detail=f"This chat is pinned to a branch that cannot be "
                               f"named ({exc}), so it is running in {default_ref}.")
    if not os.path.isdir(path):
        # A pin whose tree was merged or discarded. Falling back is right and
        # saying so is the other half of it: silently running somewhere else is
        # how work lands on the wrong branch.
        return TreeState(cwd=fallback_cwd, project_slug=project_slug,
                        scope=f"project:{project_slug}", branch_slug=branch_slug,
                        detail=f"The branch {branch_slug} has no tree any more, so "
                               f"this chat is running in {default_ref}.")

    record = None
    try:
        record = worktrees.worktree_record(project_slug, branch_slug)
    except Exception:
        record = None
    branch = (record or {}).get("branch") or f"wt/{project_slug}/{branch_slug}"
    state = session_defaults.resolve(
        opened_from={"kind": "branch", "ref": branch}, app_default_ref=default_ref)
    subject_id = _subject_id(str(path))
    tree = TreeState(
        cwd=str(path), project_slug=project_slug, scope=f"project:{project_slug}",
        branch_slug=branch_slug, branch=branch, source=state["source"],
        read_only=state["read_only"], detail=state["detail"], subject_id=subject_id)
    if not subject_id:
        return replace(tree, detail="This tree cannot be named as a lease subject, "
                                    "so nothing can be held on it.")
    try:
        current = leases.get(SUBJECT_KIND, subject_id)
    except Exception:
        current = None
    if not current or current.get("state") != "held":
        return tree
    # `lease` MEANS "THIS CHAT HOLDS IT" AND `blocked_by` MEANS "SOMEBODY ELSE
    # DOES", and conflating them is not cosmetic: `held` is what the ref pill
    # colours `ok / Yours` and what the route reports, so a chat reading a tree
    # another chat is writing would have been told it held the thing it is
    # about to be refused a write on. Measured on the second chat in the
    # end-to-end run before this branch existed.
    if holder_id and str(current.get("holder_id")) == holder_id:
        return replace(tree, lease=current)
    return replace(tree, blocked_by=current)


# --- the write ---------------------------------------------------------------

def _preempt_holder(tree: TreeState, holder: dict) -> dict:
    """Snapshot the holder's uncommitted work and fence their lease.

    Called only from an attended operator turn. Returns a dict that describes
    what was taken and from whom, suitable for the transcript notice.
    """
    holder_id = str(holder.get("holder_id") or "")
    holder_kind = str(holder.get("holder_kind") or "")
    wip_ref: str | None = None
    if tree.cwd and tree.subject_id and holder_id:
        # `git stash create`, WHICH THIS USED TO SHELL OUT TO DIRECTLY, CAPTURES
        # TRACKED MODIFICATIONS ONLY. The holder being displaced is mid-turn,
        # and a mid-turn agent's work is mostly files git has never seen, so the
        # snapshot that justifies taking the tree away from somebody was empty
        # in exactly the case it mattered -- and `wip_ref` came back None, which
        # reads in the transcript as "nothing was lost". §9 of the leases design
        # already named the mechanism for this and `git_service.snapshot`
        # already implements it (`commit-tree` over a throwaway index, HEAD and
        # the real index untouched); this had a second, weaker copy inline.
        try:
            snap = git_service.snapshot(
                tree.cwd, ref=f"refs/wip/{holder_id}",
                message=f"wip snapshot for {holder_kind or 'holder'} {holder_id}",
                include_untracked=True)
            if snap.get("ok") and snap.get("sha"):
                wip_ref = snap.get("ref")
        except Exception as exc:
            log.warning("chat tree: snapshot failed for %s: %s", holder_id, exc)
        try:
            leases.fence(SUBJECT_KIND, tree.subject_id)
        except Exception as exc:
            log.warning("chat tree: fence failed for %s: %s", tree.subject_id, exc)
    return {"holder_kind": holder_kind, "holder_id": holder_id,
            "wip_ref": wip_ref, "branch": tree.branch or ""}


def enter(thread_id: str, thread: dict | None, provider: Any, *,
          fallback_cwd: str, activity: str = "",
          preempt: bool = False) -> TreeState:
    """Resolve the tree for a turn and take the lease on it, if it is pinned.

    NEVER RAISES AND NEVER BLOCKS A TURN. Everything below the pin check is
    bookkeeping: a lease store that is down, a subject that cannot be named, an
    unexpected exception -- all of them come back as a `TreeState` with the right
    `cwd` and no lease, and the turn runs exactly as it does today. The mount is
    the enforcement, and with `WORKSHOP_TREE_LEASE_MOUNT` off there is nothing
    for a missing lease to break.

    When `preempt=True` (always the case on the attended operator path), a
    blocked tree is taken by snapshot-and-fence: the holder's uncommitted work
    is stashed to refs/wip/<holder_id>, their lease is expired, and this turn
    acquires normally. `tree.preempted` carries the displaced holder so the
    transcript can say who was taken from.

    When `preempt=False` (the unattended run path, handled by run_worktree),
    a busy tree is returned as `blocked_by` and the run queues.
    """
    tree = plan(thread, provider, fallback_cwd=fallback_cwd,
                holder_id=str(thread_id))
    if not tree.pinned or not tree.subject_id:
        return tree
    holder = tree.blocked_by
    if holder:
        if not preempt:
            # Section 8: the answer to a busy tree is a verb, not a silent
            # second writer. The turn still runs (read-only once the flag is
            # on); what it does not get is the lease.
            return replace(tree, lease=None, blocked_by=holder,
                           detail=f"{holder.get('holder_kind')} "
                                  f"{holder.get('holder_id')} holds this tree.")
        snap = _preempt_holder(tree, holder)
        tree = replace(tree, blocked_by=None, preempted=snap)
    # PRESENCE FIRST, THEN THE LEASE, and the order is load-bearing rather than
    # tidy. Section 6's passive rule is applied on READ: an interactive holder
    # with no live presence reads as `expired` however fresh its `expires_at`
    # is. Acquiring before beating therefore produces a lease that the very next
    # read -- including `tree_access.decide`, three lines before the spawn --
    # calls expired, which is a read-only mount for the session that just took
    # the tree. Measured exactly that way before this line moved.
    _beat(thread_id, tree, activity=activity)
    try:
        taken = leases.acquire(SUBJECT_KIND, tree.subject_id,
                               holder_kind="chat", holder_id=str(thread_id))
    except leases.LeasesUnavailable:
        return replace(tree, detail="There is no lease store here, so this tree "
                                    "is not held by anybody.")
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        log.warning("chat tree: acquire failed for %s: %s", tree.subject_id, exc)
        return tree
    if taken is None:
        try:
            holder = leases.get(SUBJECT_KIND, tree.subject_id)
        except Exception:
            holder = None
        # Beaten to it between the read and the write. Withdraw the presence we
        # wrote a moment ago: this session is not working here after all, and a
        # presence claiming a subject it does not hold is what puts a phantom
        # row in the directory.
        leave(thread_id)
        return replace(tree, lease=None, blocked_by=holder,
                       detail="Another session took this tree first.")
    return replace(tree, lease=taken)


def beat(thread_id: str, tree: TreeState, *, activity: str = "") -> bool:
    """Renew a held tree. False means it is no longer ours, i.e. we were fenced.

    The renewal is marked as a WORK event, because a turn that is still running
    is by definition still working: `leases.IDLE_AFTER_SEC` measures
    `last_work_at`, and a heartbeat that did not move it would paint every long
    turn as an idle holder ten minutes in.
    """
    if not tree.held or not tree.subject_id or not tree.lease:
        return False
    _beat(thread_id, tree, activity=activity)
    try:
        renewed = leases.renew(SUBJECT_KIND, tree.subject_id,
                               int(tree.lease["generation"]), work=True)
    except Exception as exc:
        log.debug("chat tree: renew failed for %s: %s", tree.subject_id, exc)
        # A blip is not a fence. The lease's own `expires_at` still bounds it.
        return True
    return renewed is not None


def _beat(thread_id: str, tree: TreeState, *, activity: str = "") -> None:
    """Write this chat's presence on the tree it is working.

    Section 6's renewal channel for an interactive holder, and also what puts
    the chat in `GET /presence/directory` so the ref pill and the directory
    sheet can name it. Best effort: a presence channel that cannot be written
    must not end a turn.
    """
    try:
        presence.beat(str(thread_id), session_kind="chat",
                      activity=activity or "working in a chat",
                      subject_kind=SUBJECT_KIND, subject_id=tree.subject_id,
                      scope=tree.scope, ttl_sec=PRESENCE_TTL_SEC,
                      meta={"branch": tree.branch or "", "cwd": tree.cwd})
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("chat tree: presence beat failed for %s: %s", thread_id, exc)


def leave(thread_id: str) -> None:
    """The turn is over: withdraw presence, which releases whatever it held.

    ONE CALL, NOT TWO. `presence.release` already calls
    `leases.release_for_holder` -- that is the write half of section 6's join
    rule and it is the reason closing a chat frees a tree immediately instead of
    in TTL seconds. Releasing the lease here as well would be a second
    implementation of the same rule, and the one that got missed on some path.
    """
    try:
        presence.release(str(thread_id))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("chat tree: release failed for %s: %s", thread_id, exc)


# --- changing the pin (section 10.3) ------------------------------------------

def branch_options(project_slug: str | None) -> list[dict]:
    """The branches this chat could move to. Empty is an answer, not an error."""
    if not project_slug:
        return []
    try:
        return worktrees.list_branches(project_slug)
    except Exception:
        return []


def pin_offer(project_slug: str | None, *, current_ref: str,
              ticket_id: str | None = None) -> dict:
    """Section 10.3's offer, for a chat that is choosing where to work.

    The same function the EROFS handler calls, with the same default (a NEW
    branch) and the same closed set of joinable branches, because "the operator
    picked a branch from the menu" and "the operator's write was refused" are
    the same decision arriving through two doors. One rule, one place.
    """
    return session_defaults.first_write_offer(
        current_ref=current_ref, ticket_id=ticket_id,
        existing_branches=[b["branch_slug"] for b in branch_options(project_slug)])


def apply_choice(project_slug: str, offer: dict, action: str, *,
                 branch: str | None = None, title: str | None = None,
                 allocated_by: str | None = None) -> dict:
    """Take one of the offered actions and return the branch to pin.

    `session_defaults.choose` is what validates it, and it is the ONLY way to
    join: a branch that was not in the offer raises rather than being created by
    the back door, which is section 10.3's rule that joining is never automatic.
    Naming a new branch is `services/worktrees.py`'s policy and is not
    re-derived here (section 13.2).

    `title` IS THE NAME OF THE WORK, and passing it is what §3 of
    `worktree-as-the-place-2026-08-28.md` asks for: "no worktree is ever created
    without a human-readable name". This path used to slugify the work id and
    nothing else, which is why five of the records on this installation are
    called `t-26c69d40` and one is called `d4875115`. The title is the chat's
    own title; when a chat genuinely has none the work id is still used, because
    a nameless tree beats no tree.
    """
    chosen = session_defaults.choose(offer, action, branch=branch)
    if chosen["action"] == "join":
        return {"branch_slug": str(chosen["branch"]), "created": False}
    # NORMALIZED BY THE BRANCHES LAYER, not here. Section 10.4's work id is
    # `uuid4().hex[:8]`, which starts with a digit more often than not, and
    # `create_branch` requires a slug that starts with a letter.
    work_id = str(chosen.get("ticket_id") or chosen.get("work_id") or "")
    display_name = (title or "").strip() or None
    slug = worktrees.branch_slug_for_work(project_slug, title=display_name,
                                          work_id=work_id)
    created = worktrees.create_branch(project_slug, slug,
                                      allocated_by=allocated_by,
                                      display_name=display_name)
    return {"branch_slug": created["branch_slug"], "created": True,
            "display_name": display_name,
            "branch": created["branch"], "dir": created["dir"]}


# --- the return trip ----------------------------------------------------------
#
# A chat that writes gets a worktree without being asked
# (`build_streaming._auto_create_worktree_for_chat`). Until these three verbs
# existed that was a one-way door: the work was isolated, committed, and then
# unreachable, because the only merge path in the system runs through a TICKET
# (`work_review.merge_for_task` off `POST /tasks/{scope}/{id}/review`) and a
# chat's own worktree has no ticket. `worktrees.merge_branch` and
# `worktrees.diff_branch` were already built and had zero callers above the
# ticket path -- so the missing half was never the merge, it was the chat being
# able to ask for one.

def set_pin(thread_id: str, provider: Any, branch_slug: str | None) -> bool:
    """Move this chat's pin, or clear it. False means the record is gone.

    ONE RULE, ONE PLACE. Where the pin lives depends on what is being pinned --
    a maker agent is one worker with one tree, so its pin is on the AGENT and
    every chat addressed to it moves together; a project chat is one of many
    against a shared tree, so its pin is on the conversation. `_pinned_branch`
    above already encodes that split for the read, and the route encoded it a
    second time for the write. Two copies of a rule is how the read and the
    write end up disagreeing about where a chat works, so the write asks here.
    """
    from services import chat_store
    key = str(getattr(provider, "key", "") or "")
    if key.startswith("agent:"):
        build_agents.set_branch(getattr(provider, "slug", ""), branch_slug)
        return True
    return chat_store.set_thread_branch(thread_id, branch_slug) is not None


def _uncommitted(cwd: str) -> list[str]:
    """Paths this worktree has changed and not committed. Empty when clean.

    UNTRACKED COUNTS. `git status --porcelain` reports a new file as `??`, and
    a new file is the overwhelmingly common thing a chat turn leaves behind --
    so a definition of "uncommitted" that only saw modifications would call a
    tree full of brand-new work clean, and everything downstream (the merge
    guard, the diff's `mergeable`) would agree with it.
    """
    try:
        return [str(e.get("path") or "")
                for e in (git_service.status(cwd).get("entries") or [])
                if e.get("path")]
    except Exception as exc:  # pragma: no cover - a status that cannot be read
        log.warning("chat tree: cannot read status of %s: %s", cwd, exc)
        return []


def diff(thread: dict | None, provider: Any, *, fallback_cwd: str) -> dict:
    """What this chat has done in its worktree, and whether it can go home.

    NOT CALLED `review`, deliberately. "Review" already names one specific thing
    in this system -- the ticket gate at `POST /tasks/{scope}/{id}/review`, the
    Change it opens, and `services/work_review.py` -- and a second meaning for
    it would be one concept wearing two words, which is the naming defect this
    whole design was asked to stop repeating. This is a git diff of a worktree
    against its base, so it is called `diff`.

    Answers for EVERY chat, including one that holds nothing: an unpinned chat
    comes back `{"pinned": False}` with the reason, so the caller renders one
    shape rather than branching on whether this module had an opinion (the same
    contract `plan` keeps).

    THE BASE REF IS REPORTED, NOT ASSUMED. `worktrees.merge_branch` checks the
    project's canonical branch out in the project's OWN working tree before it
    merges, so a merge moves that checkout. Naming `base_ref` here is what lets
    somebody see that before pressing the button rather than after.
    """
    tree = plan(thread, provider, fallback_cwd=fallback_cwd)
    out: dict = {
        "pinned": tree.pinned,
        "project_slug": tree.project_slug,
        "branch_slug": tree.branch_slug,
        "branch": tree.branch,
        "cwd": tree.cwd,
        "uncommitted": [],
        "files": [],
        # git's `--shortstat` LINE, which is a string. This was `{}`, so the
        # field came back as an empty object on every early return below and as
        # a string on the one path that reads the diff. Found by declaring the
        # response model (routes/chat_models.ThreadTreeDiff); nothing reads it
        # yet, so the shape could be made consistent rather than preserved.
        "summary": "",
        "ahead": 0,
        "behind": 0,
        "base_ref": "",
        "mergeable": False,
        "reason": "",
    }
    if not (tree.pinned and tree.project_slug):
        out["reason"] = ("This chat is not on a branch of its own, so there is "
                         "nothing to merge back. A branch is made the first "
                         "time it writes.")
        return out
    try:
        state = worktrees.branch_state(tree.project_slug, tree.branch_slug or "")
    except Exception as exc:
        out["reason"] = f"This branch cannot be read ({exc})."
        return out
    out["base_ref"] = state.get("base_ref") or ""
    out["ahead"] = int(state.get("ahead") or 0)
    out["behind"] = int(state.get("behind") or 0)
    if not state.get("exists"):
        out["reason"] = "This chat's branch no longer resolves, so there is nothing to merge."
        return out
    out["uncommitted"] = _uncommitted(tree.cwd)
    try:
        diff = worktrees.diff_branch(tree.project_slug, tree.branch_slug or "")
        out["files"] = diff.get("files") or []
        out["summary"] = diff.get("summary") or ""
    except Exception as exc:
        out["reason"] = f"The diff could not be read ({exc})."
        return out
    if not out["ahead"] and not out["uncommitted"]:
        out["reason"] = (f"Nothing has been done on this branch yet: it is "
                         f"level with "
                         f"{out['base_ref'] or _default_ref(tree.project_slug)}.")
        return out
    out["mergeable"] = True
    return out


def merge(thread_id: str, thread: dict | None, provider: Any, *,
          fallback_cwd: str, message: str = "") -> dict:
    """Merge this chat's worktree back into the shared tree and unpin the chat.

    Returns `{"ok": bool, "reason": str, "commit": str, "committed": [paths]}`.

    UNCOMMITTED WORK IS COMMITTED FIRST, NOT REFUSED AND NOT DROPPED. A merge
    moves the BRANCH, and `worktrees.merge_branch` reaps the directory on
    success -- so anything the chat wrote and did not commit would go with the
    directory. Refusing instead would be safe and useless: an interactive chat
    routinely ends a turn with edits it has not committed, which is exactly the
    state somebody is in when they ask for this. Committing is the only option
    of the three that loses nothing, and a commit is recoverable in a way a
    deleted directory is not.

    THE PROJECT'S OWN TREE MUST BE CLEAN, and that is the one hard refusal.
    `merge_branch` checks the base ref out in the project's working tree; git
    will carry uncommitted changes across a checkout when they do not conflict,
    which would silently move somebody else's in-progress edits onto another
    branch. There is no version of that which is the caller's intent, so it is
    named and declined.

    NOT ROUTED THROUGH `work_review.merge_change`, WHICH CALLS ITSELF "THE
    SINGLE MERGE PATH", and the exception is the absence of a Change rather than
    a second opinion about merging. That function's job is to pin a change's
    base sha and record the outcome ON THE CHANGE ROW; a chat's worktree has no
    change and opening one for it would mint a review artifact for work the
    person asking is looking at right now. Nor does this consult the merge
    gates, and that is the same answer the existing human path gives: `POST
    /tasks/{scope}/{id}/review` with `accept` merges without reading a gate too.
    Gates gate AUTHORITY THAT IS NOT A PERSON (`services/auto_merge.py`), which
    is what `merge-policy-levels-2026-08-24.md` §4.1 means by "the policy
    decides only WHO is allowed to say land it without reading it". Somebody
    asking for this has read it.

    THE PIN IS CLEARED ON SUCCESS. The directory is gone, and a pin to a tree
    that is not there resolves through `plan`'s fallback with an apologetic
    detail line forever. Clearing it puts the chat back on the shared tree,
    where its next write makes a fresh worktree -- which is the whole cycle.
    """
    state = diff(thread, provider, fallback_cwd=fallback_cwd)
    if not state["mergeable"]:
        return {"ok": False, "reason": state["reason"] or "There is nothing to merge.",
                "commit": "", "committed": []}
    project_slug = str(state["project_slug"])
    branch_slug = str(state["branch_slug"])

    try:
        project_dirty = _uncommitted(str(worktrees.project_dir(project_slug)))
    except Exception as exc:
        return {"ok": False, "reason": f"The project's own tree cannot be read ({exc}).",
                "commit": "", "committed": []}
    if project_dirty:
        return {
            "ok": False, "commit": "", "committed": [],
            "reason": (f"{project_slug} has {len(project_dirty)} uncommitted "
                       f"change(s) in its own tree, and merging checks "
                       f"{state['base_ref']} out there. Commit or stash them "
                       f"first: {', '.join(project_dirty[:8])}"),
        }

    committed: list[str] = []
    if state["uncommitted"]:
        text = (message or "").strip() or f"Work from this chat in {branch_slug}"
        # STAGE FIRST. `commit -a` picks up modifications to TRACKED files and
        # nothing else, so a turn whose whole output is new files would have
        # been reported as committed and then reaped with the directory. The
        # add is what makes the claim true.
        try:
            added = git_service.add_all(state["cwd"])
            done = git_service.commit(state["cwd"], text)
        except Exception as exc:
            return {"ok": False, "commit": "", "committed": [],
                    "reason": f"This branch's own changes could not be committed ({exc})."}
        if not added["ok"] or not done["ok"]:
            detail = (added.get("detail") or done.get("detail") or "").strip()
            return {"ok": False, "commit": "", "committed": [],
                    "reason": f"This branch's own changes could not be committed: {detail[:300]}"}
        committed = list(state["uncommitted"])

    try:
        result = worktrees.merge_branch(project_slug, branch_slug, message=message)
    except Exception as exc:
        return {"ok": False, "commit": "", "committed": committed,
                "reason": f"The merge failed ({exc})."}
    if not result.get("ok"):
        return {"ok": False, "commit": "", "committed": committed,
                "reason": str(result.get("reason") or "The merge was refused.")}

    try:
        set_pin(thread_id, provider, None)
    except Exception as exc:  # pragma: no cover - the merge already landed
        log.warning("chat tree: merged %s but could not unpin %s: %s",
                    branch_slug, thread_id, exc)
    return {"ok": True, "reason": "", "commit": str(result.get("commit") or ""),
            "committed": committed, "base_ref": state["base_ref"]}
