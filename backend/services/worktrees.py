"""Git-worktree management for project branches (per ADR-009 section 7).

A "branch" here is a worktree-pinned development branch off a project's main.
A Build agent can pin to one of these branches so multiple agents can work on
the same project without stepping on each other's edits. Merging happens
through the UI: fast-forward when possible, falling back to a regular merge
commit with the neutral committer identity. Worktree + branch are reaped on
success.

The disk concept stays "worktree" (git's term); the URL path stays
/api/worktrees/. The user-facing concept is the branch.

LAYER. This module is branch RECORDS plus policy: which project a slug names,
what a branch is called and where its worktree lives, when a merge or a publish
is allowed and what happens after one. It speaks no git itself. Every git
invocation goes through `services/git_service.py`, which takes a repository path
and knows nothing about projects (section 13.1/13.2 of workspace-ownership-and-
leases-2026-08-22.md). The GitHub remote surface stays in `services/github.py`.

THE RECORD (step B2). Allocating a worktree now also writes a durable row
through `services/worktree_records.py`, and merge, discard and reap move that
row's lifecycle, so the record and the disk cannot silently disagree. Before it,
`list_branches` re-derived everything from `git worktree list` and nothing could
answer who allocated a tree, when, for which ticket, or what became of it -- and
a merge reaped the directory and took the last trace with it.

RECORD-KEEPING IS ADDITIVE AND MUST STAY THAT WAY. Every write goes through
`_record`, which swallows a missing or unreachable store. A worktree without a
database must still allocate: the sweep, the CLI and every agent path predate
this table, and turning "no Postgres" into "no worktree" would break the thing
the record exists to describe. So the record is best-effort by construction,
and its absence is never an allocation failure.

MUTUAL EXCLUSION IS `work_driver.tree_lock`, NOT A NEW LOCK. Section 15 (step
B2) says to fold this into "work_driver's existing per-tree lock rather than
adding a third", so the entry points that create or destroy a tree hold that
lock over the disk change AND the record write together. Two allocations of the
same slug therefore serialize into "one wins, the other gets a clean `already
exists`" rather than racing inside `git worktree add`. The tree that is locked
is the tree being written: the new branch directory for create/allocate/discard,
the project's main worktree for a merge, which is where the checkout and the
merge commit actually land.

Public surface:
  create_branch(project_slug, branch_slug) -> dict
  allocate_task_worktree(project_slug, task_id) -> dict
  worktree_record(project_slug, branch_slug) -> dict | None
  provision_worktree(scope, owner) -> dict
  list_branches(project_slug) -> list[dict]
  branch_slug_for(value) -> str         an arbitrary work id, as a branch slug
  branch_slug_for_work(project_slug, *, title, work_id) -> str
                                        a unit of work, as a FREE branch slug
  reads_as_machine_id(slug) -> bool     is this slug an id rather than a name
  live_branches() -> dict            every tree there is, joined with its record
  merge_branch(project_slug, branch_slug, *, message="") -> dict
  discard_branch(project_slug, branch_slug, *, force=False) -> dict
  diff_branch(project_slug, branch_slug) -> dict
  branch_state(project_slug, branch_slug) -> dict
  publish_branch(project_slug, branch_slug, *, title="", body="") -> dict
  cleanup_merged(project_slug) -> dict
  repo_state(project_slug) -> dict
  branch_dir(project_slug, branch_slug) -> Path
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from services import git_service, work_driver, worktree_records
from services.app_runner import _read_manifest_file as _read_app_manifest, AppError as _AppError
from wsplatform import paths

log = logging.getLogger(__name__)

BRANCHES_ROOT = paths.env_path("WORKSHOP_BRANCHES_ROOT", "branches", base="root")
PROJECTS_ROOT = paths.env_path("WORKSHOP_PROJECTS_ROOT_NEW", "projects", base="root")
# The canonical registry lives at .runtime/projects.json (registry.py is the one
# writer; routes/projects.py reads the same path). A bare projects.json next to
# the projects dir never existed, so every Branches lookup raised "registry
# missing". Honor the same WORKSHOP_REGISTRY_PATH override registry.py uses.
PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_REGISTRY_PATH",
    str(PROJECTS_ROOT.parent / ".runtime" / "projects.json"),
))

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

# WHEN A SLUG IS A MACHINE ID RATHER THAN A NAME: the whole thing is hex, at
# least this long, optionally behind the `t-` prefix `_kebab_branch_slug` puts
# on an id that starts with a digit. Both spellings are the same defect --
# `a89d3bd46dfc48c6bcfe4ca49c639962` and `t-26c69d40` are equally unreadable --
# and the operator named it directly: "all of the branches below have just a
# long sort of string of letters and numbers as the name. so why aren't we
# naming them something that makes more sense or something that fits what the
# branch actually is."
#
# EIGHT, and it is deliberately far STRICTER than the frontend's HEX_ID_MIN of
# 24 (`frontend/src/components/chat/chatBranch.ts`). They are not the same
# number spelled twice; they answer different questions. That one asks "is this
# too wide to show on a 390px bar", so it may only shorten what it is given.
# This one asks "may this become a branch's name at all", and at minting time
# there is always a better answer available, so it refuses far earlier.
#
# Eight is the width of the shortest id this system actually mints: section
# 10.4's no-ticket work id is `uuid4().hex[:8]`, which is where the five
# `t-26c69d40` records on this installation came from. A threshold above it
# would let that exact reported name through, which is the whole point of the
# check. It is also still below any real title: a title of two or more words
# slugifies with a dash in it and cannot match at all, so the only false
# positive available is a ONE-WORD title of eight or more letters spelled out of
# a-f alone, and English has none (`defaced` and `effaced` are seven).
_MACHINE_ID_MIN_HEX = 8
_MACHINE_ID_RE = re.compile(r"^(?:t-)?[0-9a-f]{%d,}$" % _MACHINE_ID_MIN_HEX)

# How much of an unnamed unit of work's id survives into `unnamed-<tail>`.
# Eight characters is short enough to read at a glance and long enough that two
# different units of work do not land on the same tree.
_UNNAMED_ID_KEEP = 8

# Guard against exhausting the volume mid-clone. Override with
# WORKSHOP_MIN_WORKTREE_FREE_BYTES if running on a smaller disk.
MIN_FREE_BYTES: int = int(
    os.environ.get("WORKSHOP_MIN_WORKTREE_FREE_BYTES", str(2 * 1024 * 1024 * 1024))
)

# Fallback (lockfile, dep-dir) pairs used when the app manifest declares none.
# Only pairs whose lockfile is actually present in the project tree are offered.
_PLATFORM_DEFAULT_INPUTS: list[tuple[str, str]] = [
    ("package-lock.json", "node_modules"),
    ("backend/requirements.txt", "backend/.venv"),
    ("requirements.txt", ".venv"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_slug(value: str, label: str) -> str:
    v = (value or "").strip().lower()
    if not SLUG_RE.match(v):
        raise ValueError(f"{label} must be kebab-case alphanumeric, start with a letter, max 48 chars")
    return v


def _kebab_branch_slug(value: str) -> str:
    """Normalize an arbitrary task id into a valid branch slug.

    Lowercase; runs of non-alphanumeric collapse to a single dash; trimmed and
    capped at 48 chars. A value that would not start with a letter (a uuid4 hex
    id can begin with a digit) is prefixed `t-` so it satisfies SLUG_RE.
    """
    v = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not v or not v[0].isalpha():
        v = f"t-{v}" if v else "t"
    return v[:48].rstrip("-")


def reads_as_machine_id(value: str) -> bool:
    """Is this slug an id wearing a name's clothes?

    PUBLISHED BECAUSE IT IS THE PROOF, not just a helper. "No path emits a bare
    hex branch name" is a claim about every writer, so the claim needs one
    predicate every writer is measured against and every test can call. A second
    copy of this regex anywhere else would be the same question answered twice,
    which is what section 13.2 of `workspace-ownership-and-leases-2026-08-22.md`
    forbids.

    Read-only by design: it says what a slug IS, never what to do about it. The
    two callers decide that differently -- `branch_slug_for_work` replaces the
    id with a name it derives, and `create_branch` refuses.
    """
    return bool(_MACHINE_ID_RE.match((value or "").strip().lower()))


def _unnamed_slug(work_id: str) -> str:
    """The slug for a unit of work that arrived with no title of its own.

    A work id is not always opaque -- `only-once` and `sec-a6` are ids too, and
    they already read as the work -- so an id that is a name is kept whole. Only
    an id that `reads_as_machine_id` is replaced, and it is replaced rather than
    truncated: `unnamed-30777f91` says the thing that is actually true (nothing
    named this work) in the one word that says it, where `t-30777f91367b4c7b`
    said only that a machine was here.

    A PURE FUNCTION OF THE WORK ID, with no date and no counter in it, because
    `branch_slug_for_work` relies on that: the untitled path returns its base
    slug undisambiguated so that a second allocation under one id collides and
    raises "already exists" instead of quietly forking a second tree.
    """
    slug = _kebab_branch_slug(work_id)
    if not reads_as_machine_id(slug):
        return slug
    tail = slug[2:] if slug.startswith("t-") else slug
    return f"unnamed-{tail[:_UNNAMED_ID_KEEP]}"


def _project_record(project_slug: str) -> dict:
    """Return the registry record for a project (or raise)."""
    if not PROJECTS_REGISTRY.exists():
        raise ValueError(f"projects registry missing: {PROJECTS_REGISTRY}")
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
    except Exception as e:
        raise ValueError(f"projects registry unreadable: {e}")
    p = (reg.get("projects") or {}).get(project_slug)
    if not p or not p.get("dir"):
        raise ValueError(f"project not found: {project_slug}")
    return p


def _project_dir(project_slug: str) -> Path:
    """Locate the project's main directory via projects.json."""
    p = _project_record(project_slug)
    pd = Path(p["dir"])
    if not pd.exists():
        raise ValueError(f"project directory missing: {pd}")
    return pd


def _project_repo(project_slug: str) -> dict | None:
    """Return the project's `repo` binding (Phase G1) or None if unbound."""
    try:
        return _project_record(project_slug).get("repo")
    except ValueError:
        return None


def _canonical_branch(project_slug: str, project_dir: Path) -> str:
    """The branch a worktree is cut from and merged back into.

    Bound project: the tracked `repo.default_branch`, because the remote is the
    authority on where work lands. Unbound project: THE BRANCH THE PROJECT'S OWN
    WORKING TREE IS CHECKED OUT ON.

    THAT SECOND CLAUSE USED TO BE `git_service.main_branch`, AND IT IS WHY THE
    WHOLE MECHANISM COULD NOT BE TURNED ON. `main_branch` is a NAME heuristic --
    it answers "main" whenever a ref called `main` resolves, whatever the tree
    is actually doing -- and it is the right answer for a caller that has no
    other information. This caller has better information: the checkout in front
    of it. Measured on this installation 2026-09-04: the shared tree sat on a
    feature branch **368 commits ahead of `main`**, so every worktree cut here
    would have started 368 commits stale, and merging one back would have put
    that stale work on a branch nobody was using. An agent handed a two-week-old
    checkout is not isolated, it is stranded, and no amount of lease correctness
    fixes it.

    It also removes a hazard on the way home: `merge_branch` checks this ref out
    in the project's own working tree, so answering "main" against a tree on
    another branch moved somebody's checkout out from under them as a side
    effect of a merge. Answering with the branch already checked out makes that
    checkout a no-op.

    Falls back to the name heuristic for a detached HEAD or an unreadable tree,
    which is the case it was always right for.
    """
    repo = _project_repo(project_slug)
    if repo and repo.get("default_branch"):
        return repo["default_branch"]
    try:
        current = git_service.status(project_dir).get("branch")
    except Exception:
        current = None
    return str(current) if current else git_service.main_branch(project_dir)


def _branch_name(project_slug: str, branch_slug: str) -> str:
    return f"wt/{project_slug}/{branch_slug}"


def _branch_path(project_slug: str, branch_slug: str) -> Path:
    return BRANCHES_ROOT / project_slug / branch_slug


def _scope_for(project_slug: str) -> str:
    """The scope string this project is addressed by. Internal addressing that
    never appears in the UI (app-is-the-entity-2026-08-20.md); it is stored so
    the record keeps the caller's own form rather than a re-derived guess."""
    return f"project:{project_slug}"


def _record(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one record write, best-effort.

    THE ONE PLACE THE RECORD IS ALLOWED TO BE ABSENT. Every worktree operation
    here has to work on an installation with no relational store, so a missing
    or unreachable database is not an error -- it is "this tree is unremembered
    today". Any other failure is logged rather than raised for the same reason:
    a bug in bookkeeping must not be able to fail an allocation or, worse, leave
    a merge half-done after the branch has already been reaped.

    Returns the writer's result, or None when nothing was written.
    """
    try:
        return fn(*args, **kwargs)
    except worktree_records.WorktreesUnavailable:
        return None
    except Exception:
        log.warning("worktree record write failed (%s); the tree itself is "
                    "unaffected", getattr(fn, "__name__", fn), exc_info=True)
        return None


def worktree_record(project_slug: str, branch_slug: str) -> dict | None:
    """The live record for a branch, or None when there is none.

    PUBLISHED SO THE LAYER ABOVE DOES NOT REACH PAST THIS MODULE. A change wants
    the `worktree_id` of the branch it was opened for, and resolving it by
    reaching into `services/worktree_records.py` directly would be a second
    definition of "which record is this branch's", which is the drift section
    13.2 exists to prevent. Best-effort: no store means no record, not an error.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    return _record(worktree_records.record_for_branch, project_slug, branch_slug)


def _record_id(project_slug: str, branch_slug: str) -> str | None:
    row = _record(worktree_records.record_for_branch, project_slug, branch_slug)
    return None if row is None else row["id"]


def create_branch(project_slug: str, branch_slug: str, *,
                  allocated_by: str | None = None,
                  display_name: str | None = None) -> dict:
    """Create a new worktree-pinned branch off the project's main.

    Returns a record describing the new branch: project, branch_slug, full
    git ref name, dir, the creation timestamp, and `worktree_id` when a record
    was written.

    THIS IS THE NO-TICKET PATH, and section 10.4 makes it first-class:
    "writing with no ticket is common and not second-class: it gets a branch a
    ticket can adopt later". So the record is written with `ticket_id` NULL
    rather than with the branch slug pressed into service as one.

    IT ALSO TAKES A SLUG FROM OUTSIDE, which is why the machine-id refusal is
    here as well as in `branch_slug_for_work`. This is the other door:
    `routes/worktrees.py` hands it whatever the request body carried, so a
    caller that skipped the naming rule -- or a caller that reached for a work
    id because it had one -- would otherwise mint exactly the branch names this
    module now exists to prevent. Two writers, one rule, and the rule is a
    refusal rather than a rename because a caller that named a branch after an
    id has a name it did not pass, and silently substituting one would hide that.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    if reads_as_machine_id(branch_slug):
        raise ValueError(
            f"branch slug {branch_slug!r} is a machine id, not a name. A branch "
            f"is named after its work: pass the work's title to "
            f"branch_slug_for_work and use the slug it returns.")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    with work_driver.tree_lock(target):
        if target.exists():
            raise ValueError(f"branch dir already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            git_service.worktree_add(project_dir, target, branch=branch)
        except git_service.GitError as e:
            stderr = (e.stderr or "").strip()[:200]
            raise ValueError(f"git worktree add failed (exit {e.returncode}): {stderr}")
        base_ref = _canonical_branch(project_slug, project_dir)
        record = _record(
            worktree_records.record_allocation,
            project_slug=project_slug, scope=_scope_for(project_slug),
            branch_slug=branch_slug, branch=branch, path=str(target),
            base_ref=base_ref,
            base_sha=git_service.rev_parse(project_dir, branch, verify=True),
            ticket_id=None, allocated_by=allocated_by,
            display_name=display_name)
    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "dir": str(target),
        "created": _now(),
        "worktree_id": None if record is None else record["id"],
    }


def allocate_task_worktree(project_slug: str, task_id: str, *,
                           title: str | None = None,
                           ticket_id: str | None = None,
                           allocated_by: str | None = None,
                           scope: str | None = None) -> dict:
    """Allocate an isolated, locked worktree for one task (Phase G3 concurrency).

    Creates branch wt/<slug>/<name> in its own worktree, off
    origin/<default_branch> when the project is bound (so an agent starts from
    the canonical remote base), else off the local canonical branch (today's
    behavior). The worktree is locked so a crashed agent's stale index cannot
    wedge sibling worktrees.

    The branch slug comes from `branch_slug_for_work`, which is the naming rule
    for every path that makes a tree: the title when there is one, so the branch
    reads as the work rather than as a hex id, with a short deterministic suffix
    only when that name is already taken.

    Returns {branch, branch_slug, dir, worktree_id}.

    THE TICKET DEFAULTS TO THE TASK ID because for this entry point they are the
    same thing. A caller with a different ticket passes `ticket_id` explicitly;
    `create_branch` is the no-ticket path (section 10.4). `allocated_by` records
    WHO asked, and `scope` lets the caller keep its own addressing rather than
    have one re-derived for it.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    display_name = (title or "").strip() or None
    branch_slug = branch_slug_for_work(project_slug, title=display_name,
                                       work_id=task_id)
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)

    with work_driver.tree_lock(target):
        if target.exists():
            raise ValueError(f"worktree dir already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)

        repo = _project_repo(project_slug)
        if repo:
            base = repo.get("default_branch") or git_service.main_branch(project_dir)
            start_point = f"origin/{base}"
        else:
            start_point = _canonical_branch(project_slug, project_dir)

        try:
            git_service.worktree_add(project_dir, target, branch=branch,
                                     start_point=start_point)
        except git_service.GitError as e:
            stderr = (e.stderr or "").strip()[:200]
            raise ValueError(f"git worktree add failed (exit {e.returncode}): {stderr}")

        # Lock the worktree so a crashed agent's stale index cannot block others.
        git_service.worktree_lock(project_dir, target)

        # `base_ref` on the record is WHAT THIS TREE WAS CUT FROM, which is the
        # start point above and not necessarily the ref it will merge into: a
        # bound project cuts from `origin/<default>` and merges into
        # `<default>`. The merge target is `branch_state()`'s `base_ref` and is
        # computed when it is needed, because it can move; the cut point cannot.
        record = _record(
            worktree_records.record_allocation,
            project_slug=project_slug, scope=scope or _scope_for(project_slug),
            branch_slug=branch_slug, branch=branch, path=str(target),
            base_ref=start_point,
            base_sha=git_service.rev_parse(project_dir, branch, verify=True),
            ticket_id=(ticket_id if ticket_id is not None else task_id),
            allocated_by=allocated_by,
            display_name=display_name)

    return {"branch": branch, "branch_slug": branch_slug, "dir": str(target),
            "worktree_id": None if record is None else record["id"],
            "display_name": display_name}


def list_branches(project_slug: str) -> list[dict]:
    """List active branches for a project by parsing `git worktree list`.

    Only returns worktrees whose branch starts with wt/<project_slug>/.
    The main worktree is excluded. `display_name` is joined from records,
    best-effort: absent when there is no relational store.

    EVERY ROW CARRIES `default_ref`, THE TRUNK'S OWN NAME, because a control
    that lists branches also has to offer the trunk and has to call it
    something. The two controls that do this disagreed: the chat toolbar's pill
    names it by its ref (`main`) and the build agent's picker called it "Shared
    tree", which is a term nothing else in the product uses and which the
    operator asked about directly -- "Is 'shared tree' the name of the work
    tree, or is it something else?". It was neither; it was a second word for
    `main`. Naming the trunk is this module's policy (`default_ref`, section
    13.2), so the answer travels with the list rather than being invented once
    per control, and a repo whose trunk is `master` is not described as `main`.
    It repeats per row because the wire shape here is a bare list; it is
    computed once.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    try:
        entries = git_service.worktree_list(project_dir)
    except git_service.GitError:
        return []
    trunk = _canonical_branch(project_slug, project_dir) or "HEAD"
    prefix = f"wt/{project_slug}/"
    items: list[dict] = []
    for entry in entries:
        branch = entry.get("branch") or ""
        if not branch.startswith(prefix):
            continue
        items.append({
            "project_slug": project_slug,
            "branch_slug": branch[len(prefix):],
            "branch": branch,
            "dir": entry.get("dir", ""),
            "head": entry.get("head"),
            # Carried because `allocate_task_worktree` LOCKS every tree it makes,
            # and a locked tree is one `git worktree remove` and `git worktree
            # prune` both refuse. A caller deciding whether a tree can be reaped
            # has to know that; re-deriving it meant a second `git worktree list`.
            "locked": bool(entry.get("locked")),
            "display_name": None,
            "default_ref": trunk,
        })
    # Enrich with display_name from records, best-effort.
    recs_list = _record(worktree_records.list_records,
                        project_slug=project_slug) or []
    recs = {r["branch_slug"]: r.get("display_name") for r in recs_list}
    for item in items:
        item["display_name"] = recs.get(item["branch_slug"])
    return items


# ---------------------------------------------------------------------------
# The joined view: what is ACTUALLY there
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. `/branches` answered from the RECORD TABLE alone, and the
# table only ever has a row for a tree that was allocated through the record
# path. Measured 2026-08-27 on this installation: eleven real worktrees on disk,
# every one of them abandoned, and the page that exists to find abandoned trees
# showed none of them, because none had a record. Absence is the one answer a
# monitoring view must never fabricate, so the live view starts from the DISK --
# which is the only thing that can say a tree is there -- and joins the record
# onto it for everything the disk cannot say (who allocated it, for which
# ticket, what became of it).
#
# THREE WAYS A BRANCH TREE CAN EXIST, and the join has to name all three or it
# is telling the same lie in a new direction:
#
#   PRESENCE_TREE    git lists the worktree and its directory is there. The
#                    ordinary case, with or without a record.
#   PRESENCE_ORPHAN  the directory is under the branches root and git does not
#                    know it. That is what a teardown that fell through to
#                    `rmtree` + `prune` leaves behind, and nothing else in the
#                    system can see it: it has no branch, no metadata and no
#                    record, so it is invisible to git AND to the table.
#   PRESENCE_GONE    a live record whose directory is not on disk. The record
#                    outlived the tree; there is nothing to work in.
#
# THIS IS A READ AND IT NEVER WRITES. It would be easy to "reconcile" here --
# close the gone records, prune the orphans -- and that would make a page load
# destroy state. Reconciliation is a verb the operator invokes (`discard_branch`),
# not a side effect of looking.
PRESENCE_TREE = "tree"
PRESENCE_ORPHAN = "orphan"
PRESENCE_GONE = "gone"


def _registry_project_slugs() -> list[str]:
    """Every project slug the registry names. Empty when there is no registry."""
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
    except Exception:
        return []
    return sorted((reg.get("projects") or {}).keys())


def _tree_rows(project_slug: str) -> list[dict]:
    """Every branch tree of one project, from git and from the directory.

    TWO SUBPROCESSES PER PROJECT, NOT PER TREE: one `git worktree list` and one
    `git for-each-ref`. The directory scan is a listdir. This runs on a page that
    polls, so the cost has to be per project.
    """
    prefix = f"wt/{project_slug}/"
    try:
        project_dir = _project_dir(project_slug)
    except ValueError:
        project_dir = None

    base_ref = ""
    tips: dict[str, dict] = {}
    entries: list[dict] = []
    if project_dir is not None:
        try:
            entries = git_service.worktree_list(project_dir)
        except git_service.GitError:
            entries = []
        tips = git_service.branch_tips(project_dir, pattern=f"{prefix}*")
        try:
            base_ref = _canonical_branch(project_slug, project_dir)
        except Exception:
            base_ref = ""

    rows: list[dict] = []
    known_slugs: set[str] = set()
    for entry in entries:
        branch = entry.get("branch") or ""
        if not branch.startswith(prefix):
            continue
        branch_slug = branch[len(prefix):]
        known_slugs.add(branch_slug)
        path = entry.get("dir") or str(_branch_path(project_slug, branch_slug))
        tip = tips.get(branch) or {}
        rows.append({
            "project_slug": project_slug,
            "branch_slug": branch_slug,
            "branch": branch,
            "path": path,
            "base_ref": base_ref,
            "head_sha": entry.get("head") or tip.get("sha"),
            "tip_at": tip.get("committed_at"),
            "locked": bool(entry.get("locked")),
            # `prunable` is git's own word for "the directory this record points
            # at is not there any more", and it is the same fact as the
            # directory check beside it. Either one is enough to say GONE.
            "presence": (PRESENCE_TREE
                         if Path(path).is_dir() and not entry.get("prunable")
                         else PRESENCE_GONE),
        })

    # The directories git lost. Named for the branch they WOULD carry, because
    # the path is the canonical one for that slug; `presence` is what says the
    # ref behind that name does not exist.
    root = BRANCHES_ROOT / project_slug
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in known_slugs:
                continue
            rows.append({
                "project_slug": project_slug,
                "branch_slug": child.name,
                "branch": _branch_name(project_slug, child.name),
                "path": str(child),
                "base_ref": base_ref,
                "head_sha": None,
                "tip_at": None,
                "locked": False,
                "presence": PRESENCE_ORPHAN,
            })
    return rows


def _joined(tree: dict | None, record: dict | None) -> dict:
    """One row of the live view: the disk, with the record joined onto it.

    THE DISK WINS ON IDENTITY (path, branch, where it is) and the RECORD WINS ON
    INTENT (ticket, who allocated it, status), because each side is the only one
    that can answer its half. A row with no record carries `recorded: false` and
    a null `status` rather than a plausible-looking `allocated`: "no record"
    and "a record that says allocated" are different facts and this is a view
    that exists because they were conflated.
    """
    if tree is None:                      # a record whose tree is not there
        assert record is not None
        return {
            **record,
            "recorded": True,
            "presence": PRESENCE_GONE,
            "head_sha": None,
            "tip_at": record.get("updated_at"),
            "locked": False,
        }
    if record is None:                    # a tree nothing remembered
        return {
            "id": f"tree:{tree['project_slug']}/{tree['branch_slug']}",
            "app_id": None,
            "project_slug": tree["project_slug"],
            "scope": _scope_for(tree["project_slug"]),
            "branch_slug": tree["branch_slug"],
            "branch": tree["branch"],
            "path": tree["path"],
            "base_ref": tree["base_ref"],
            "base_sha": None,
            "ticket_id": None,
            "allocated_by": None,
            "display_name": None,
            "status": None,
            "merged_sha": None,
            "removed_at": None,
            "created_at": None,
            "updated_at": None,
            "recorded": False,
            "presence": tree["presence"],
            "head_sha": tree["head_sha"],
            "tip_at": tree["tip_at"],
            "locked": tree["locked"],
        }
    return {
        **record,
        "path": tree["path"],             # the disk knows where it actually is
        "recorded": True,
        "presence": tree["presence"],
        "head_sha": tree["head_sha"],
        "tip_at": tree["tip_at"] or record.get("updated_at"),
        "locked": tree["locked"],
    }


def _sort_key(row: dict) -> str:
    """Newest activity first, and a row with no timestamp sorts last rather than
    first: an unknown age must not be able to look like this second."""
    return row.get("tip_at") or row.get("updated_at") or row.get("created_at") or ""


def live_branches(*, project_slug: str | None = None, ticket_id: str | None = None,
                  app_id: str | None = None, limit: int = 200) -> dict:
    """Every branch tree that exists, joined with the record that describes it.

    `{"worktrees": [...], "records_available": bool}`. `records_available` is
    False when this installation has no reachable relational store: the trees are
    still real and still listed, they simply carry no ticket, no allocator and no
    status. That is a different answer from "no trees", and the caller renders it
    as one.

    THE RECORD LAYER IS BEST-EFFORT HERE, unlike `worktree_records.list_records`
    which raises. That inversion is deliberate and is the whole point of this
    function: a record read that fails takes the intent out of the answer, not
    the answer itself. The trees came from git.
    """
    records = _record(worktree_records.list_records, live_only=True, limit=1000)
    records_available = records is not None
    by_key: dict[tuple[str, str], dict] = {
        (r["project_slug"], r["branch_slug"]): r for r in (records or [])
    }

    rows: list[dict] = []
    matched: set[tuple[str, str]] = set()
    for slug in _registry_project_slugs():
        if project_slug and slug != project_slug:
            continue
        for tree in _tree_rows(slug):
            key = (tree["project_slug"], tree["branch_slug"])
            matched.add(key)
            rows.append(_joined(tree, by_key.get(key)))

    for key, record in by_key.items():
        if key in matched:
            continue
        if project_slug and key[0] != project_slug:
            continue
        rows.append(_joined(None, record))

    if ticket_id:
        rows = [r for r in rows if r.get("ticket_id") == ticket_id]
    if app_id:
        rows = [r for r in rows if str(r.get("app_id") or "") == str(app_id)]

    rows.sort(key=_sort_key, reverse=True)
    return {"worktrees": rows[:max(1, min(int(limit), 1000))],
            "records_available": records_available}


def diff_branch(project_slug: str, branch_slug: str) -> dict:
    """Diff the branch against the project's main branch.

    Returns a summary line (shortstat-style) plus per-file unified diffs.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    main = _canonical_branch(project_slug, project_dir)
    summary = git_service.diff_stat(project_dir, main, branch, three_dot=True) \
        or "no changes vs main"
    files: list[dict] = []
    for entry in git_service.diff_name_status(project_dir, main, branch, three_dot=True):
        path = entry["path"]
        files.append({
            "path": path,
            "status": entry["status"],
            "diff": git_service.diff(project_dir, main, branch, three_dot=True,
                                     paths=[path]),
        })
    return {"summary": summary, "files": files}


def diff_refs(project_slug: str, base: str, head: str) -> dict:
    """Diff two refs in the project, in the same shape as `diff_branch`.

    PUBLISHED SO A MERGED CHANGE KEEPS ITS DIFF. Merging reaps the worktree and
    deletes the branch, so asking for a merged change's diff BY BRANCH answers
    "no files", which reads as "this changed nothing" for work that in fact
    landed. The commits are still in the trunk, so the review record is
    recoverable as base..merged_sha; it just cannot be addressed by a branch
    that no longer exists. Two-dot, not three: after the merge the base contains
    the change, so a merge-base diff would correctly report nothing.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    summary = git_service.diff_stat(project_dir, base, head) or "no changes"
    files: list[dict] = []
    for entry in git_service.diff_name_status(project_dir, base, head):
        path = entry["path"]
        files.append({
            "path": path,
            "status": entry["status"],
            "diff": git_service.diff(project_dir, base, head, paths=[path]),
        })
    return {"summary": summary, "files": files}


def branch_state(project_slug: str, branch_slug: str) -> dict:
    """Where a branch stands against the base it will merge into.

    `{project_slug, branch_slug, branch, base_ref, head_sha, ahead, behind,
    exists}`. `exists` is False when the ref does not resolve at all, and then
    `head_sha` is None and the counts are zero.

    PUBLISHED FOR THE LAYER ABOVE. `services/changes.py` has to ask "is this
    branch ahead of its base, and at what sha" before it can open a change, and
    the two things that answer it here -- which project a slug names, and what
    this project's canonical base ref is -- are branch-record policy that lives
    in this module (`_project_dir`, `_canonical_branch`). Exposing the answer
    is what stops the layer above reaching into those internals or growing its
    own second definition of "the base branch", which is the drift
    workspace-ownership-and-leases-2026-08-22.md sec 13.2 exists to prevent.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    base = _canonical_branch(project_slug, project_dir)
    head_sha = git_service.rev_parse(project_dir, branch, verify=True)
    counts = ({"ahead": 0, "behind": 0} if head_sha is None
              else git_service.ahead_behind(project_dir, base, branch))
    return {
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "branch": branch,
        "base_ref": base,
        # The base as a SHA, resolved now. A ref name moves; a merged change
        # needs the commit the base stood at BEFORE it absorbed the change, or
        # its own diff becomes unrecoverable (see changes.diff_for).
        "base_sha": git_service.rev_parse(project_dir, base, verify=True),
        "head_sha": head_sha,
        "ahead": counts["ahead"],
        "behind": counts["behind"],
        "exists": head_sha is not None,
    }


def merge_branch(project_slug: str, branch_slug: str, *, message: str = "") -> dict:
    """Merge the branch into the project's main worktree.

    Tries fast-forward first. If that fails (divergence), falls back to a
    regular merge commit under the neutral committer identity. On success the
    worktree is removed and the branch is deleted.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    main = _canonical_branch(project_slug, project_dir)

    # THE TREE BEING WRITTEN IS THE PROJECT'S MAIN WORKTREE, not the branch's:
    # the checkout and the merge commit land there. So that is what the per-tree
    # lock covers, and two merges into the same base serialize instead of
    # interleaving a checkout with someone else's merge.
    with work_driver.tree_lock(project_dir):
        # Bound projects merge through the remote: the branch was published
        # (pushed + PR opened), so squash-merge the PR and delete the remote
        # branch, then fast-forward the local main. This runs AFTER the
        # reviewer/operator gate (the same gate that authorizes any merge).
        # Unbound projects keep the local-only merge below.
        repo = _project_repo(project_slug)
        if repo:
            from services import github
            try:
                github.merge_pr(str(project_dir), head=branch, base=main)
            except ValueError as e:
                return {"ok": False, "reason": str(e)[:400]}
            head = git_service.rev_parse(project_dir, "HEAD") or ""
            _reap_worktree(project_dir, project_slug, branch_slug, branch,
                           merged_sha=head)
            return {"ok": True, "commit": head}

        # Make sure we're operating from main in the project's main worktree.
        co = git_service.checkout(project_dir, main)
        if not co["ok"]:
            return {"ok": False, "reason": f"checkout {main} failed: {co['detail'][:200]}"}

        # Fast-forward when the history allows it, a merge commit under the
        # neutral identity when it does not. git_service aborts a failed merge
        # for us, so the working tree is clean either way.
        result = git_service.merge(
            project_dir, branch,
            message=message.strip() or f"Merge branch {branch_slug} into {main}",
        )
        if not result["ok"]:
            return {"ok": False, "reason": result["detail"][:400]}

        _reap_worktree(project_dir, project_slug, branch_slug, branch,
                       merged_sha=result["commit"] or "")
        return {"ok": True, "commit": result["commit"] or ""}


def _tear_down_tree(project_dir: Path, target: Path, branch: str) -> None:
    """Take one worktree off the disk and delete its local ref. Idempotent.

    UNLOCK FIRST, and that is not tidiness. `allocate_task_worktree` LOCKS every
    tree it makes, so a crashed agent's stale index cannot wedge its siblings --
    and `git worktree remove --force` flatly refuses a locked worktree ("cannot
    remove a locked working tree"), as does `git worktree prune`. Without the
    unlock, teardown fell through to `shutil.rmtree`, git's administrative
    metadata went on saying the branch was checked out at a directory that no
    longer existed, `git branch -D` then failed with "cannot delete branch
    checked out at ...", and re-allocating the same slug died on "a branch named
    ... already exists". Every allocated (as opposed to hand-created) branch was
    in that state after its merge.

    PRUNE AFTER the fallback rmtree, for the same reason: a directory deleted
    behind git's back leaves metadata that only prune clears, and the branch
    delete below cannot succeed while it is there.
    """
    git_service.worktree_unlock(project_dir, target)
    git_service.worktree_remove(project_dir, target, force=True)
    if target.exists():
        # If worktree remove still balked, clean up the leftover dir so a
        # re-create with the same slug works.
        shutil.rmtree(target, ignore_errors=True)
    git_service.worktree_prune(project_dir)
    git_service.branch_delete(project_dir, branch, force=True)


def _reap_worktree(project_dir: Path, project_slug: str, branch_slug: str,
                   branch: str, *, merged_sha: str | None = None) -> None:
    """Remove the branch worktree and delete its local ref after a merge.

    THE RECORD CLOSES HERE, not at the two call sites, because this is the one
    function that knows the tree has actually left the disk. Both callers reach
    a merged branch (`merge_branch` merged it; `cleanup_merged` found it already
    merged upstream), so the record's terminal state is `merged` either way, and
    it is written LAST -- after the directory is gone -- so a record can never
    claim a tree was reaped while the tree is still there.

    Does NOT take the per-tree lock: both callers already hold it, and flock is
    per open-file-description rather than per process, so a nested acquisition
    would block against itself.
    """
    target = _branch_path(project_slug, branch_slug)
    _tear_down_tree(project_dir, target, branch)
    record_id = _record_id(project_slug, branch_slug)
    if record_id is not None:
        _record(worktree_records.mark_merged, record_id,
                commit=(merged_sha or None))


def _snapshot_before_teardown(target: Path, branch_slug: str, *,
                              project_dir: Path | None = None,
                              branch: str | None = None,
                              base_ref: str | None = None) -> dict:
    """Put whatever is uncommitted in *target* somewhere it can be got back.

    THE RULE: REAPING A TREE MAY NOT DESTROY WORK NOBODY RECORDED. A dirty tree
    is snapshotted to `refs/wip/<branch_slug>` -- a real ref in the project's
    object store, so the commit survives `git gc` and is recoverable with
    `git show`/`git cherry-pick` long after the directory is gone. That is the
    same mechanism `services/control.py` uses when it takes a tree away from a
    live holder, and it is deliberately the same ref namespace: there is one
    place to look for work a machine took, not two.

    UNTRACKED FILES ARE INCLUDED, because they are most of what is at stake. An
    abandoned tree's uncommitted content is typically files that were never
    added, and a tracked-only snapshot would record nothing while reporting
    success.

    A CLEAN TREE IS NOT AUTOMATICALLY SAFE. Teardown deletes the branch, so a
    branch that never reached its base loses its commits with it. Given
    `project_dir`/`branch`/`base_ref` this points the same ref at the tip in
    that case, and does nothing when the branch is already contained in its base.

    Returns `{snapshottable, dirty, entries, ref, sha, detail}`:

      `snapshottable` False means git cannot speak for this directory at all --
      it is not a repository, which is exactly the ORPHAN case (a teardown that
      fell through to `rmtree` leaves a directory with no git metadata). Nothing
      can be saved and nothing can be promised, so the caller has to decide
      rather than being told it was fine.
    """
    out = {"snapshottable": False, "failed": False, "dirty": False,
           "entries": 0, "ref": None, "sha": None, "detail": ""}
    if not target.exists():
        out["detail"] = "the directory is not there"
        return out
    if not git_service.is_repo(target):
        out["detail"] = f"not a git repository: {target}"
        return out
    out["snapshottable"] = True
    try:
        status = git_service.status(target)
    except git_service.GitError as e:
        out["snapshottable"] = False
        out["detail"] = (e.stderr or str(e)).strip()[:300]
        return out
    out["entries"] = len(status.get("entries") or [])
    out["dirty"] = not status.get("clean")
    ref = f"refs/wip/{branch_slug}"
    if not out["dirty"]:
        # A CLEAN TREE CAN STILL BE CARRYING WORK. Teardown deletes the branch
        # ref (`git branch -D`), so commits that never reached the base become
        # unreachable and are gone at the next gc. There is nothing to stash, so
        # the save is just a ref: point `refs/wip/<slug>` at the tip and the
        # commits stay recoverable. A branch already contained in its base has
        # nothing to keep and gets no ref, which is what stops every routine
        # merge-then-reap from leaving one behind.
        if project_dir is not None and branch and base_ref:
            try:
                contained = git_service.is_ancestor(project_dir, branch, base_ref)
            except Exception:
                contained = True
            head = git_service.rev_parse(target, "HEAD")
            if not contained and head and git_service.update_ref(target, ref, head):
                out["ref"] = ref
                out["sha"] = head
        return out
    snap = git_service.snapshot(
        target, ref=ref,
        message=f"workshop reap snapshot: {branch_slug}",
        include_untracked=True)
    if not snap.get("ok"):
        out["failed"] = True
        out["detail"] = snap.get("detail") or "snapshot failed"
        return out
    # `ok` with no sha means the staged tree matched HEAD's after all, so there
    # was nothing to record. That is a success, not a refusal: gating the reap on
    # a sha would wedge on a tree porcelain calls dirty and git has nothing to
    # commit for.
    out["ref"] = snap.get("ref") if snap.get("sha") else None
    out["sha"] = snap.get("sha")
    return out


def discard_branch(project_slug: str, branch_slug: str, *,
                   reason: str = "", force: bool = False) -> dict:
    """Force-remove a branch worktree and delete its git ref.

    THE WORK IS SNAPSHOTTED FIRST. Before this, discarding a tree with
    uncommitted changes in it deleted them, and `/branches` -- whose whole
    subject is abandoned trees holding uncommitted work -- had no verb it could
    offer that did not risk destroying the thing it was pointing at. Now a dirty
    tree is committed to `refs/wip/<branch_slug>` (see
    `_snapshot_before_teardown`) and the returned `snapshot_ref`/`snapshot_sha`
    say where it went, so "reap" is a recoverable act rather than a deletion.

    A FAILED SNAPSHOT IS A REFUSAL, not a warning. `ok: False` with `detail`
    saying why, and the tree is left exactly as it was: a dirty tree the next
    reader can see beats work nobody can get back. `services/control.py` takes
    the same position on the same trade.

    `force` IS FOR THE ONE CASE NOTHING CAN SPEAK FOR: a directory with no git
    metadata at all, which is what a teardown that fell through to `rmtree`
    leaves behind. There is no snapshot to take and no way to know what is in
    it, so removing it is a plain deletion and the caller has to say so rather
    than have it done quietly on its behalf.

    The record moves to `discarded` rather than being deleted: what happened to
    a branch is exactly the question the record exists to answer, and a row that
    disappears when the work is thrown away answers it for every branch except
    the interesting ones. `reason` rides the timeline event when the caller has
    one.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    branch = _branch_name(project_slug, branch_slug)
    target = _branch_path(project_slug, branch_slug)
    with work_driver.tree_lock(target):
        base_ref = None
        try:
            base_ref = _canonical_branch(project_slug, project_dir)
        except Exception:
            base_ref = None
        snap = _snapshot_before_teardown(target, branch_slug,
                                         project_dir=project_dir, branch=branch,
                                         base_ref=base_ref)
        if not snap["snapshottable"] and target.exists() and not force:
            return {
                "ok": False, "removed": False,
                "detail": f"{snap['detail']}; nothing can say what is in this "
                          f"directory, so removing it would be a plain "
                          f"deletion. Pass force to remove it anyway.",
                "snapshot_ref": None, "snapshot_sha": None,
                "dirty_entries": 0,
            }
        if snap["failed"] and not force:
            return {
                "ok": False, "removed": False,
                "detail": f"the tree has uncommitted work and it could not be "
                          f"snapshotted ({snap['detail']}), so it was left "
                          f"alone.",
                "snapshot_ref": None, "snapshot_sha": None,
                "dirty_entries": snap["entries"],
            }
        _tear_down_tree(project_dir, target, branch)
        record_id = _record_id(project_slug, branch_slug)
        if record_id is not None:
            _record(worktree_records.mark_discarded, record_id, reason=reason)
            if snap["sha"]:
                _record(worktree_records.add_event, record_id, kind="snapshot",
                        sha=snap["sha"], detail=snap["ref"])
    return {"ok": True, "removed": True, "detail": "",
            "snapshot_ref": snap["ref"], "snapshot_sha": snap["sha"],
            "dirty_entries": snap["entries"]}


def publish_branch(project_slug: str, branch_slug: str, *,
                   title: str = "", body: str = "") -> dict:
    """Push the branch to origin and open a PR against the canonical branch.

    OUTWARD-FACING: pushes to GitHub and opens a PR. Only valid for a project
    bound to a GitHub remote. Runs `git push -u origin wt/<slug>/<branch>` then
    `gh pr create` via github.open_pr. Returns {branch, remote_url, pr}.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    project_dir = _project_dir(project_slug)
    repo = _project_repo(project_slug)
    if not repo:
        raise ValueError("project is not connected to GitHub; connect it first")
    branch = _branch_name(project_slug, branch_slug)
    remote_url = repo["remote_url"]
    base = repo.get("default_branch") or git_service.main_branch(project_dir)

    from services import github

    # Rebase the branch onto the canonical base before pushing so conflicts
    # surface here as ordinary git conflicts rather than at merge time. A
    # conflicting rebase leaves the worktree clean (github.rebase_onto aborts)
    # and we return without pushing so the caller can park the task, not loop.
    worktree_dir = _branch_path(project_slug, branch_slug)
    rebase = github.rebase_onto(str(worktree_dir), base)
    if not rebase.get("ok"):
        return {
            "ok": False,
            "conflict": bool(rebase.get("conflict")),
            "branch": branch,
            "base": base,
            "detail": rebase.get("detail", ""),
        }

    push = git_service.push(project_dir, "origin", branch, set_upstream=True)
    if not push["ok"]:
        raise ValueError(f"git push failed: {push['detail'][:300]}")

    pr_title = title.strip() or f"{branch_slug} ({project_slug})"
    pr_body = body.strip() or f"Publishes {branch} into {base}."
    pr = github.open_pr(
        str(project_dir), remote=remote_url, base=base, head=branch,
        title=pr_title, body=pr_body,
    )
    return {"ok": True, "branch": branch, "remote_url": remote_url, "base": base, "pr": pr}


def cleanup_merged(project_slug: str) -> dict:
    """Remove worktrees whose branch is already merged into the canonical base.

    For each wt/<slug>/ branch, if the branch tip is an ancestor of the local
    canonical branch, the worktree is force-removed, its local ref deleted, and
    stale worktree metadata pruned. Returns {removed, kept} (branch slugs).
    Best-effort per branch: a branch still ahead of the base is kept.

    CANONICAL "MERGED" CHECK: `git_service.is_ancestor(project_dir, branch, main)`
    against the LOCAL canonical branch. gc.reap_merged_worktrees uses the same
    check. Two definitions existed briefly: this function used github.is_merged
    against origin/<base> while the GC used the local-main check. Local main wins
    because (1) it works for both bound and unbound projects — merge_branch's own
    github.merge_pr fast-forwards local main to origin/<base> after every remote
    merge, so both refs agree after the fact; (2) it needs no network call or
    fetched origin ref; (3) the GC is the broader path and had the right answer.
    gc.reap_merged_worktrees is the CANONICAL site; this function calls the same
    check so one definition exists.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    main = _canonical_branch(project_slug, project_dir)

    removed: list[str] = []
    kept: list[str] = []
    for wt in list_branches(project_slug):
        branch = wt["branch"]
        branch_slug = wt["branch_slug"]
        if git_service.is_ancestor(project_dir, branch, main):
            with work_driver.tree_lock(project_dir):
                _reap_worktree(project_dir, project_slug, branch_slug, branch,
                               merged_sha=git_service.rev_parse(
                                   project_dir, main, verify=True))
            removed.append(branch_slug)
        else:
            kept.append(branch_slug)
    git_service.worktree_prune(project_dir)
    return {"removed": removed, "kept": kept}


def repo_state(project_slug: str) -> dict:
    """Canonical-state view for the Repo panel and agent context (Phase G3).

    For a bound project: default branch, remote URL, each active worktree with
    its assigned task (= branch_slug), ahead/behind vs origin/<default_branch>,
    and the matched open PR (joined by headRefName), plus all open PRs. Every
    piece is best-effort and degrades to null/empty; an unbound project returns
    {bound: False} without raising.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    project_dir = _project_dir(project_slug)
    repo = _project_repo(project_slug)
    if not repo:
        return {"bound": False}
    base = repo.get("default_branch") or git_service.main_branch(project_dir)
    remote_url = repo.get("remote_url")

    from services import github
    open_prs = github.list_open_prs(remote_url) if remote_url else []
    pr_by_head = {
        pr.get("headRefName"): pr for pr in open_prs if pr.get("headRefName")
    }

    worktrees_out: list[dict] = []
    for wt in list_branches(project_slug):
        branch = wt["branch"]
        branch_slug = wt["branch_slug"]
        wdir = wt.get("dir") or str(_branch_path(project_slug, branch_slug))
        worktrees_out.append({
            "branch_slug": branch_slug,
            "dir": wdir,
            "task_id": branch_slug,
            "ahead_behind": github.ahead_behind(wdir, base),
            "pr": pr_by_head.get(branch),
        })

    return {
        "bound": True,
        "default_branch": base,
        "remote_url": remote_url,
        "worktrees": worktrees_out,
        "open_prs": open_prs,
    }


def branch_slug_for(value: str) -> str:
    """Turn an arbitrary work id into a branch slug this module will accept.

    PUBLISHED BECAUSE NAMING IS THIS MODULE'S POLICY (section 13.2 of
    `workspace-ownership-and-leases-2026-08-22.md`): "no service imports another
    service's policy", and a second slugifier elsewhere is the same branch name
    derived two ways. `allocate_task_worktree` already normalized on the way in
    and `create_branch` did not, so the no-ticket path in section 10.4 -- whose
    work id is `uuid4().hex[:8]` and therefore starts with a digit about six
    times in ten -- could not name a branch at all.
    """
    return _kebab_branch_slug(value)


def _slug_is_taken(project_slug: str, branch_slug: str) -> bool:
    """Is this slug already spoken for, on disk or by a live record?

    THE DISK IS THE AUTHORITY and the record is the second opinion. A directory
    under the branches root means `git worktree add` will refuse, which is the
    hard constraint. A live record with no directory is `PRESENCE_GONE`, and
    reusing its slug would collide with the partial unique index on live rows --
    the allocation would still succeed (records are best-effort) but it would go
    unrecorded, which is the one outcome `worktree_records` exists to prevent.

    Best-effort on the record half: no database is not a taken slug.
    """
    if _branch_path(project_slug, branch_slug).exists():
        return True
    return _record(worktree_records.record_for_branch,
                   project_slug, branch_slug) is not None


def _disambiguators(work_id: str):
    """Short, deterministic suffixes for a slug that is already taken.

    The first is five characters of the work id, so the pair (title, work_id)
    always lands on the same branch name -- a retry of the same allocation asks
    for the same slug rather than accumulating trees. The numbered ones after it
    exist because the five-character prefix can itself repeat.
    """
    normalized = _kebab_branch_slug(work_id)
    if normalized.startswith("t-"):
        normalized = normalized[2:]
    tail = normalized[:5].strip("-") or "x"
    yield tail
    for n in range(2, 100):
        yield f"{tail}-{n}"


def branch_slug_for_work(project_slug: str, *, title: str | None = None,
                         work_id: str = "") -> str:
    """The branch slug for one unit of work: named after the work, not its id.

    THIS IS THE RULE FROM §3 OF `worktree-as-the-place-2026-08-28.md` -- "no
    worktree is ever created without a human-readable name" -- and it lives here
    rather than in each caller for the reason `branch_slug_for` gives: naming is
    this module's policy and a second derivation elsewhere is the same branch
    name computed two ways. `allocate_task_worktree` (the ticket path) and
    `chat_worktree.apply_choice` (the offer path) both go through it, which is
    why the offer path stopped minting `t-<hex>`.

    A title gives `chat-toolbar-fixes`; no title falls back to the work id,
    which is what an unnamed unit of work has and is better than refusing to
    make a tree. A slug that is already taken gets a short deterministic
    suffix -- only on collision, so the common case reads as the work.

    THERE ARE EXACTLY TWO CASES AND NEITHER OF THEM IS A HEX WALL. Either a
    title names the work, or `_unnamed_slug` does; the branch is never handed
    the raw id. This is stated as a two-branch `if` rather than as "slugify
    whatever we have, then check it" ON PURPOSE: the old single expression
    `_kebab_branch_slug(title or work_id)` could not tell the two apart
    afterwards, and it emitted `t-<hex>` for an id starting with a digit and a
    BARE 32-hex name for one starting with a letter. Three of the six trees on
    this installation were named that second way and nothing caught it, because
    a bare hex name is indistinguishable from a very unlucky title unless you
    know which input it came from. Here we know.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    # A blank title is the same fact as no title -- a chat that has not been
    # named yet carries "" or a run of spaces, not None -- and slugifying it
    # would name the tree `t`.
    title = (title or "").strip() or None
    # A title that is itself an id is not a name. It happens: a chat auto-titled
    # from its own thread id, an importer that put the ticket id in the title
    # field. Such a title is dropped here rather than trusted, so the `unnamed`
    # path catches it too and there is no way past this function with a hex wall.
    if title and reads_as_machine_id(_kebab_branch_slug(title)):
        title = None
    label = "title" if title else "work_id"
    base = _validate_slug(
        _kebab_branch_slug(title) if title else _unnamed_slug(work_id), label)
    if not _slug_is_taken(project_slug, base):
        return base
    if not title:
        # The id path keeps its old contract: an id names exactly one tree, so
        # a second allocation under the same id is a caller error and
        # `create_branch`/`allocate_task_worktree` raise "already exists" on it.
        # Only a TITLE can legitimately repeat across two units of work.
        return base
    for suffix in _disambiguators(work_id):
        candidate = (base[:47 - len(suffix)].rstrip("-") + "-" + suffix)[:48]
        if not _slug_is_taken(project_slug, candidate):
            return _validate_slug(candidate, label)
    raise ValueError(
        f"no free branch slug for {title!r} in {project_slug}: "
        f"{base!r} and every disambiguation of it are taken")


def default_ref(project_slug: str) -> str:
    """The trunk this project's branches are cut from, by name.

    PUBLISHED FOR THE SAME REASON `branch_dir` IS. Section 13.2 of
    `workspace-ownership-and-leases-2026-08-22.md` puts branch naming policy in
    this module and forbids another service importing it, and section 10.2 of
    the same doc needs the trunk's NAME to say what a read-only session is
    reading. Callers were resolving it by reaching for `_project_dir` and
    `git_service` themselves, which is that rule broken twice in one line -- and
    it would have printed `main` at a repo whose trunk is `master`.

    Best-effort by design: an unregistered project, a missing directory or a git
    that will not answer all give `HEAD`, which is true of every repository.
    """
    try:
        return _canonical_branch(project_slug, _project_dir(project_slug)) or "HEAD"
    except Exception:
        return "HEAD"


def project_dir(project_slug: str) -> Path:
    """This project's own canonical working tree. May raise (see `_project_dir`).

    PUBLISHED FOR THE SAME REASON `branch_slug_for` WAS: where a project lives
    is this module's path policy (section 13.2 of
    `workspace-ownership-and-leases-2026-08-22.md`, "no service imports another
    service's policy"), and a caller that needs it was otherwise going to reach
    for `_project_dir` or re-read the registry itself -- which is one path
    spelled twice. `chat_worktree.merge` needs it because merging checks the
    base ref out in THIS tree, so it has to look at this tree before it starts.
    """
    return _project_dir(project_slug)


def branch_dir(project_slug: str, branch_slug: str) -> Path:
    """Public helper: return the on-disk path for a (project, branch) pair.

    Used by services/build_agents.py to resolve cwd when a build agent is
    pinned to a branch.
    """
    project_slug = _validate_slug(project_slug, "project_slug")
    branch_slug = _validate_slug(branch_slug, "branch_slug")
    return _branch_path(project_slug, branch_slug)


# ---------------------------------------------------------------------------
# Provisioning — §13.2 of branches-worktrees-and-environments-2026-08-20.md
# ---------------------------------------------------------------------------

def provision_worktree(scope: str, owner: str, *,
                       title: str | None = None,
                       ticket_id: str | None = None,
                       allocated_by: str | None = None) -> dict:
    """Allocate and provision an isolated worktree for a unit of work.

    Given a scope (``project:<slug>`` or bare slug) and an owner id, creates
    a git worktree via ``allocate_task_worktree`` then hardlink-clones each
    dependency directory from the canonical project tree into the new worktree.
    The clone is keyed by the hash of the lockfile that produced it; a
    ``.dep-contract.json`` sibling records the read-only contract so any
    reinstall path knows to ``rm -rf`` before writing, never writing into shared
    inodes.

    Raises ``ValueError`` with a clear message if free disk on the project
    volume is below ``MIN_FREE_BYTES`` before any work is done.

    Returns the ``allocate_task_worktree`` dict (including ``worktree_id``)
    extended with::

        provisioned: list[str]  # dep dirs successfully cloned (relative paths)
        skipped: list[str]      # dep dirs absent from canonical tree (not an error)
    """
    project_slug = _scope_to_project_slug(scope)
    project_dir = _project_dir(project_slug)

    free = _free_bytes(project_dir)
    if free < MIN_FREE_BYTES:
        raise ValueError(
            f"preflight failed: {free / (1024 ** 3):.1f} GiB free on "
            f"{project_dir} — need ≥{MIN_FREE_BYTES // (1024 ** 3)} GiB"
        )

    result = allocate_task_worktree(project_slug, owner, title=title,
                                    ticket_id=ticket_id,
                                    allocated_by=allocated_by, scope=scope)
    worktree_dir = Path(result["dir"])

    provisioned: list[str] = []
    skipped: list[str] = []
    for lockfile_rel, dep_rel in _workspace_inputs(project_dir):
        src = project_dir / dep_rel
        if not src.exists() or not src.is_dir():
            skipped.append(dep_rel)
            continue
        lhash = _lockfile_hash(project_dir / lockfile_rel) if lockfile_rel else None
        dst = worktree_dir / dep_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _hardlink_clone(src, dst)
        _write_dep_contract(dst, lockfile_hash=lhash, cloned_from=str(src))
        provisioned.append(dep_rel)

    result["provisioned"] = provisioned
    result["skipped"] = skipped

    # PROVISIONED IS A DIFFERENT STATE FROM ALLOCATED, and the difference is
    # real to whoever picks the tree up: an allocated tree is a source checkout
    # with no dependencies, a provisioned one can be built. Recorded after the
    # clones, so the status can never run ahead of the disk.
    #
    # Deliberately NOT inside the allocation's lock: `allocate_task_worktree`
    # refuses a directory that already exists, so the tree cloned into here was
    # created by this call and nothing else can be in it. Re-taking the lock
    # around the clone would block against itself (flock is per open file
    # description, not per process).
    if result.get("worktree_id"):
        _record(worktree_records.mark_provisioned, result["worktree_id"],
                provisioned=provisioned, actor=allocated_by)
    return result


def _scope_to_project_slug(scope: str) -> str:
    """``project:<slug>`` or bare slug → slug."""
    if ":" in scope:
        kind, _, slug = scope.partition(":")
        if kind != "project":
            raise ValueError(
                f"provision_worktree only supports project scopes, got {scope!r}"
            )
        return slug
    return scope


def _workspace_inputs(project_dir: Path) -> list[tuple[str | None, str]]:
    """Return (lockfile_rel_or_None, dep_dir_rel) pairs to clone into a worktree.

    Reads ``workspace_inputs`` from the app manifest (workshop.app.json) if
    declared; otherwise falls back to ``_PLATFORM_DEFAULT_INPUTS`` filtered to
    entries whose lockfile is actually present in the project tree.

    An entry without a ``lockfile`` key produces ``(None, dir)``; the clone
    contract is written with a null hash (asset and model-weight dirs are
    supported, not only dependency installs).
    """
    try:
        raw = _read_app_manifest(project_dir)
    except _AppError as e:
        raise ValueError(str(e)) from e

    declared = (raw or {}).get("workspace_inputs")
    if declared is not None:
        result: list[tuple[str | None, str]] = []
        for entry in declared:
            if isinstance(entry, dict) and entry.get("dir"):
                result.append((entry.get("lockfile") or None, str(entry["dir"])))
        return result

    return [
        (lockfile, dep)
        for lockfile, dep in _PLATFORM_DEFAULT_INPUTS
        if (project_dir / lockfile).exists()
    ]


def _free_bytes(path: Path) -> int:
    """Available bytes on the volume containing *path*."""
    st = os.statvfs(path)
    return st.f_frsize * st.f_bavail


def _lockfile_hash(lockfile: Path) -> str | None:
    """First 16 hex chars of the SHA-256 of *lockfile*; None if absent."""
    if not lockfile.exists():
        return None
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()[:16]


def _hardlink_clone(src: Path, dst: Path) -> None:
    """``cp -al src dst`` — hardlink-clone *src* tree to *dst* (must not exist)."""
    if dst.exists():
        shutil.rmtree(dst)
    r = subprocess.run(
        ["cp", "-al", str(src), str(dst)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise ValueError(
            f"hardlink-clone failed ({src} → {dst}): "
            f"{(r.stderr or r.stdout or '').strip()[:300]}"
        )


def _write_dep_contract(dep_dir: Path, *, lockfile_hash: str | None,
                        cloned_from: str) -> None:
    """Write ``<dep_dir>.dep-contract.json`` recording the read-only contract.

    The contract states that this directory is a hardlink-clone whose inodes
    are shared with *cloned_from*.  Any tool that needs to reinstall must
    ``rm -rf`` the directory first so it never writes into the shared inodes.
    """
    contract_path = dep_dir.parent / (dep_dir.name + ".dep-contract.json")
    contract_path.write_text(
        json.dumps({
            "readonly": True,
            "lockfile_hash": lockfile_hash,
            "cloned_from": cloned_from,
            "reinstall": "rm -rf this directory, then run the real install command",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
