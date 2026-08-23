"""The maker agent: the thing that works the tickets, as a record you can see.

WHAT WAS WRONG. `agents_registry.list_agents()` returned three records and none
of them worked a ticket. The thing that actually did was an anonymous chain of
shell scripts with no record anywhere -- `queue_runner.sh` -> `project_ralph.sh`
-> `ralph.sh` -> `claude -p` -- so the operator's question, "what is doing the
tickets", had no answer on any screen, and the controls for it lived in a
settings drawer that never says the word agent. `ROLES` has reserved `maker`
since the role enum was closed, and nothing has ever occupied it.

WHAT THIS IS NOT. It is NOT a second autonomy system, and that is the constraint
that decides every line below. This module owns NO state of its own. It gives
four facts that already exist one face and one name:

    is it allowed to run   services/scope_execution   (the arm level)
    is a worker resident   services/work_driver       (the flock on the tree)
    is it working now      services/presence          (the TTL'd channel)
    what has it run        services/jobs              (the Job store)

There is no `makers.json`, no "is this maker on" file, and no place here that
persists a mode. `arm_level` is a call into `scope_execution`; residency is a
call into `work_driver`; activity is a call into `presence`. If a future change
finds itself adding a store here, the store it wants already exists.

A REAL DEFINITION FILE, NOT A SYNTHESIZED RECORD. The record is created by the
reconciler below the same way `work_driver` ensures a runner: an ordinary
`agents/<slug>.md` written through `agents_registry.create`, which every other
agent in the system already is. The alternative -- synthesizing a record at list
time from the scope registry -- was rejected for one reason: the operator's
complaint is that he cannot MANAGE the thing, and managing an agent means
editing its persona and its rules. A synthesized record has no file to edit, so
`PUT /agents/{id}` would have nothing to write and the persona could never reach
the worker. Requirement 5 (`scripts/ralph.sh` injects this definition into the
worker's prompt) is only possible because the definition is a file.

ONE MAKER PER SCOPE, ENFORCED HERE AND NOT BY `_assert_role_unique`.
`agents_registry.SINGLETON_ROLES` is `("triager", "keeper")` and deliberately
EXEMPTS `maker` -- its comment says so, because several makers on one scope is
the concurrency case, governed by `touches` disjointness and the claim fence.
So this module does not get uniqueness for free and does not try to change that
rule for everyone. `ensure` is idempotent because it ASKS FIRST: if any maker
already declares this scope (`maker_for_scope`), nothing is created, and the
maker the operator already had is adopted as the scope's face rather than
duplicated beside it. The slug is derived from the scope too, so even a lost
registry read cannot mint a second file.

NO SCOPE IS NAMED HERE. `runnable_scopes()` reads the projects registry and the
developer-mode flag; it is the same enumeration `routes/autonomy.py` served as
`GET /queue/runner/targets`, moved here so the maker reconciler and the runner
picker cannot disagree about which trees can run tickets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from services import (agents_registry, autonomy_override, build_agents, jobs,
                      presence, scope_execution, task_scope, tasks, work_driver)
from wsplatform import dev_mode, paths

log = logging.getLogger(__name__)

__all__ = [
    "MAKER_SLUG_SUFFIX", "RECONCILE_INTERVAL",
    "runnable_scopes", "scope_row", "slug_for", "scope_for",
    "maker_for_scope", "ensure", "reconcile", "reconcile_loop",
    "live_state", "summary", "prompt_block", "main",
]

# The reconciler runs on the same cadence as the work driver's: a scope is added
# or removed by an operator action, not by a hot loop, so a minute is prompt.
RECONCILE_INTERVAL = 60.0

# Derived, never hand-listed. A scope's maker is `<project-slug>-maker`, so the
# name reads as what it is on the roster and two passes cannot mint two files.
MAKER_SLUG_SUFFIX = "-maker"

# How many Jobs to scan when attributing runs to a scope. The Job store is a
# collapsed read of one JSONL and is cached inside `services.jobs`, so this is a
# bound on the join below rather than on I/O.
_RUNS_SCAN = 500

# What a fresh maker says about itself. Deliberately short and deliberately
# EDITABLE: this is the seed of a document the operator owns, not a constant the
# platform keeps re-asserting. The reconciler writes it once, at create time,
# and never touches the body again -- an edit here does not propagate, for the
# same reason `fork_from_template` has no sync engine.
_SEED_PURPOSE = "works this scope's ready tickets to done"

_SEED_BODY = """\
You are this scope's maker: the agent that takes a ready ticket off this scope's
queue and drives it to done.

This text is your persona and your standing rules. It is injected into every
unattended worker run for this scope (`scripts/ralph.sh`), so editing this file
is how you change what the worker does. Keep it about JUDGEMENT -- what this
scope cares about, what to be careful with, what "done" means here. The loop's
own mechanics (commit before the reviewer, no stubs, emit the sentinel) are
already in the worker's prompt and do not need repeating.

## House rules

- Prefer the smallest change that actually closes the ticket.
- If the ticket is ambiguous, say so on the ticket rather than guessing.
"""


# ---------------------------------------------------------------------------
# The scopes that can run tickets
# ---------------------------------------------------------------------------

def runnable_scopes() -> list[dict]:
    """Every tree a runner may be pointed at: [{scope, project_slug, dir, label}].

    MOVED HERE FROM `routes/autonomy.queue_runner_targets`, which is the only
    behaviour change to that endpoint: it now calls this. Two enumerations of
    "which trees can run tickets" -- one deciding what the picker offers and one
    deciding which makers exist -- is exactly the parallel-system defect this
    work exists to remove.

    `scope` is the SCOPE KEY (`development`, `project:<slug>`), which is what
    `scope_execution` and the presence channel are keyed by. `project_slug` is
    the registry slug, which is what an agent definition links to. They differ
    for the self-project and that difference is the reason both are returned:
    the platform repo's registry slug resolves through `key_for_root` to
    `development`, never to `project:development`.

    The platform repo appears only when developer mode is on. Offering it and
    then having the gate refuse it would be another control that lies.
    """
    out: list[dict] = []
    seen: set[str] = set()
    try:
        data = json.loads(paths.projects_registry().read_text())
    except (OSError, ValueError):
        data = {}
    for slug, raw in sorted((data.get("projects") or {}).items()):
        d = (raw or {}).get("dir")
        if not d:
            continue
        is_self = dev_mode.is_self_dir(d)
        if is_self and not dev_mode.dev_mode_on():
            continue
        # The scope key is the INVERSE of the root, asked of the one function
        # that owns that mapping, so a self-project maps to `development` and a
        # normal project to `project:<slug>` without either being spelled here.
        scope = task_scope.key_for_root(d)
        if not scope:
            continue
        seen.add(scope)
        out.append({
            "scope": scope,
            "project_slug": slug,
            # REALPATH, because the per-tree lock is. `work_driver.ensure` and
            # `work_driver.stop` both `os.path.realpath` their argument before
            # deriving `queue_key`, while `is_resident`/`runner_state` take the
            # string as given -- so a registry entry written through a symlink
            # would start a runner under one key and then probe another, and the
            # UI would report "not running" about a runner it had just started.
            "dir": os.path.realpath(str(d)),
            "label": (raw.get("title") or slug),
        })
    # The platform repo is offerable in developer mode WHETHER OR NOT it has a
    # registry row, which is how the endpoint this replaced behaved: it appended
    # the repo first and then skipped self-dirs in the registry loop. Keeping
    # that means turning developer mode on is sufficient, with no second step.
    # `DEVELOPMENT_SCOPE` is a platform scope name, not an app's, and it is read
    # from the constant that owns it rather than spelled here.
    if task_scope.DEVELOPMENT_SCOPE not in seen and dev_mode.dev_mode_on():
        root = paths.workshop_root()
        out.insert(0, {
            "scope": task_scope.DEVELOPMENT_SCOPE,
            "project_slug": task_scope.DEVELOPMENT_SCOPE,
            "dir": os.path.realpath(str(root)),
            "label": f"{task_scope.DEVELOPMENT_SCOPE} (the Workshop itself)",
        })
    return out


def scope_row(scope: str) -> dict | None:
    """The runnable-scope row for a scope key, or None if it cannot run tickets."""
    for row in runnable_scopes():
        if row["scope"] == scope:
            return row
    return None


def slug_for(project_slug: str) -> str:
    """The deterministic slug of a scope's maker.

    Derived rather than minted, so the reconciler is idempotent even if the
    registry read that `ensure` does first were to fail: a second pass computes
    the same name and `agents_registry.create` refuses a slug that exists.
    """
    base = (project_slug or "").strip().lower()
    return f"{base}{MAKER_SLUG_SUFFIX}"


def scope_for(rec: dict) -> str | None:
    """Which execution scope this maker works, read off its own declaration.

    NO NEW FRONTMATTER FIELD. A maker's tree is its linked project's tree --
    that is what `project_slug` has always meant and what
    `task_scope._maker_root` resolves -- and the scope key is the inverse of
    that tree. So the link the definition already carries is the answer, asked
    through the two functions that own each half of it. A maker with no project
    link has no tree and therefore no execution scope, which is the honest
    answer rather than a default.
    """
    if (rec or {}).get("role") != "maker":
        return None
    project_slug = rec.get("project_slug")
    if not project_slug:
        return None
    try:
        root = build_agents._project_dir(project_slug)
    except Exception:
        root = None
    if root is None:
        return None
    try:
        return task_scope.key_for_root(root)
    except Exception:
        return None


def maker_for_scope(scope: str, *, include_retired: bool = False) -> dict | None:
    """The maker agent that declares this scope, or None.

    THE IDEMPOTENCE CHECK, and it is a search rather than a slug lookup on
    purpose: a maker the operator wrote by hand, with a different name, is still
    this scope's maker, and creating a second one beside it would be the roster
    growing a duplicate of the very thing the operator said he could not find.
    """
    for rec in agents_registry.list_agents(include_retired=include_retired):
        if rec.get("role") != "maker":
            continue
        if scope_for(rec) == scope:
            return rec
    return None


# ---------------------------------------------------------------------------
# The reconciler
# ---------------------------------------------------------------------------

def ensure(row: dict) -> dict | None:
    """Make a maker exist for one runnable scope. Idempotent. Returns the record.

    ADDITIVE, in the same sense as `work_driver.ensure`: it creates what is
    missing and never rewrites, retires or re-seeds what is there. The body is
    the operator's document after the first write.
    """
    scope = row.get("scope")
    project_slug = row.get("project_slug")
    if not scope or not project_slug:
        return None
    existing = maker_for_scope(scope, include_retired=True)
    if existing is not None:
        return existing
    slug = slug_for(project_slug)
    if agents_registry.get(slug) is not None:
        # The slug is taken by something that is not this scope's maker (a
        # retired record, a hand-written agent with another role). Refusing is
        # right: minting `<slug>-maker-2` would put a name on the roster that
        # nobody chose and that no reconcile pass could ever converge on.
        log.warning("makers: %s is taken; not creating a maker for %s", slug, scope)
        return None
    try:
        rec = agents_registry.create(
            slug=slug,
            name=f"{row.get('label') or project_slug} maker",
            purpose=_SEED_PURPOSE,
            project_slug=project_slug,
            role="maker",
            # NOT a trigger and NOT an autonomy mode. A maker does not fire on a
            # schedule: it is resident, and whether it may work is
            # `scope_execution`'s answer for its scope. Declaring `autonomy:` on
            # the definition here would be the second setting this module exists
            # to avoid -- two fields answering "is this maker on", disagreeing.
            triggers_list=[],
            body=_SEED_BODY,
        )
    except ValueError:
        log.warning("makers: could not create a maker for %s", scope, exc_info=True)
        return None
    log.info("makers: created %s for scope %s", slug, scope)
    return rec


def reconcile() -> dict:
    """One pass: every scope that can run tickets has a maker agent.

    The same shape as `work_driver.reconcile` and for the same reason -- a
    record that only appears when someone happens to open a screen is a record
    that is missing when it matters. Never deletes: a scope that goes away
    leaves its maker behind for the operator to retire, because a definition
    file may have been edited and throwing away an edit is not reconciliation.
    """
    rows = runnable_scopes()
    created = 0
    for row in rows:
        before = maker_for_scope(row["scope"], include_retired=True)
        if before is None and ensure(row) is not None:
            created += 1
    return {"scopes": len(rows), "created": created}


async def reconcile_loop(interval: float = RECONCILE_INTERVAL) -> None:
    """Run `reconcile()` forever. Started from the backend lifespan, beside the
    work driver's loop, because they answer the two halves of one question."""
    log.info("maker reconcile loop started (interval=%ss)", interval)
    while True:
        try:
            out = await asyncio.to_thread(reconcile)
            if out.get("created"):
                log.info("makers: created %s maker(s)", out["created"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("maker reconcile pass failed")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Live state: joined at read time, stored nowhere
# ---------------------------------------------------------------------------
#
# EVERY STATE BELOW IS OBSERVABLE. None of them is a flag someone set. The one
# thing the system genuinely cannot see is stated in the field rather than
# guessed at -- see `_ACTIVITY_PRESENT`.

# The activity vocabulary, closed and observable.
#   absent   nothing holds this tree's lock and no live session names this scope
#   present  a worker is here, and nothing it has written says it holds a ticket
#   working  a live session in this scope holds a ticket subject; `working_on` names it
ACTIVITIES = ("absent", "present", "working")

_ACTIVITY_DETAIL = {
    "absent": "no runner holds this tree's lock and no live session names this scope",
    # THE HONEST FIELD. `queue_runner.sh` beats once per sweep iteration and
    # `project_ralph.sh` beats at task boundaries, so between "resident, idling
    # on the poll" and "resident, mid-sweep, has not claimed a ticket yet" the
    # channel carries no structural difference -- only an activity STRING, which
    # is a message to a human and not a state to key on. Saying which of the two
    # it is would be inventing a status the system cannot see, so it says both.
    "present": ("a worker is resident, but nothing it has written claims a "
                "ticket: it is either waiting on its poll or in a sweep that "
                "has not claimed one yet, and the presence channel cannot tell "
                "those apart"),
    "working": "a live session in this scope holds a ticket",
}


def _live_sessions(scope: str) -> list[dict]:
    """The directory rows for this scope. A read-time join that stores nothing."""
    try:
        return presence.directory(scope=scope)
    except Exception:
        return []


def _recent_runs(scope: str, root: Path | None, limit: int) -> list[dict]:
    """Jobs attributable to this scope, newest first.

    ATTRIBUTED STRUCTURALLY, not by a tag the sweep would have to remember to
    write. A child Job carries the `task_id` it worked, and a ticket lives in
    exactly one scope's store, so a Job whose ticket is in THIS store is this
    maker's run. Its coordinator sweep is then pulled in by `parent_id`.

    The project-slug filter is kept beside it because the attended paths
    (`/tasks` run-now, the dispatcher) launch with an explicit `--slug` and
    record it, and those runs are this maker's too. `queue_runner.sh` launches
    with no slug at all, which is why the ticket join has to exist.
    """
    try:
        rows = jobs.read(limit=_RUNS_SCAN)
    except Exception:
        return []
    ticket_ids: set[str] = set()
    if root is not None:
        try:
            # `_load`, not `list_tasks`: the latter calls `reconcile(root)`,
            # which WRITES. Reading who is working must never mutate a store.
            ticket_ids = {t["id"] for t in (tasks._load(Path(root)) or []) if t.get("id")}
        except Exception:
            ticket_ids = set()
    project_slug = scope.split(":", 1)[1] if scope.startswith("project:") else None

    kept: dict[str, dict] = {}
    parents: set[str] = set()
    for r in rows:
        tid = r.get("task_id")
        if tid and tid in ticket_ids:
            kept[r["id"]] = r
            if r.get("parent_id"):
                parents.add(str(r["parent_id"]))
        elif project_slug and r.get("project_slug") == project_slug:
            kept[r["id"]] = r
    for r in rows:
        if r.get("id") in parents:
            kept[r["id"]] = r
    out = sorted(kept.values(), key=lambda r: r.get("started_at") or "", reverse=True)
    return out[:max(1, limit)]


def summary(rec: dict, *, sessions: list[dict] | None = None) -> dict | None:
    """The compact live block that rides on a roster row. None for a non-maker.

    Cheap by construction: one flock probe and a filter over presence rows the
    caller has already read. `sessions` is passed in by the roster route so a
    list of N makers reads the presence channel once rather than N times.
    """
    scope = scope_for(rec)
    if scope is None:
        return None
    row = scope_row(scope)
    root = row["dir"] if row else None
    resident = bool(root) and work_driver.is_resident(root)
    live = [s for s in (sessions if sessions is not None else _live_sessions(scope))
            if s.get("scope") == scope]
    ticket = next((s for s in live if s.get("subject_kind") == "ticket"), None)
    if ticket is not None:
        activity = "working"
    elif resident or live:
        activity = "present"
    else:
        activity = "absent"
    mode = scope_execution.get_mode(scope)
    return {
        "scope": scope,
        "dir": root,
        "arm_level": mode,
        "armed": scope_execution.is_armed(scope),
        "ceiling": scope_execution.stage_ceiling(scope),
        "global_override_engaged": autonomy_override.is_engaged(),
        "runner_resident": resident,
        "activity": activity,
        "activity_detail": _ACTIVITY_DETAIL[activity],
        "ticket_id": (ticket or {}).get("subject_id"),
        "ticket_title": (ticket or {}).get("subject_label"),
        "ticket_status": (ticket or {}).get("subject_status"),
    }


def live_state(rec: dict, *, runs_limit: int = 10) -> dict:
    """Everything the maker's page needs, joined now and stored nowhere.

    Raises ValueError for an agent that is not a maker with a resolvable scope,
    so a caller reports "this agent has no tree" rather than serving a block of
    nulls that reads like a broken worker.
    """
    scope = scope_for(rec)
    if scope is None:
        raise ValueError(
            f"{rec.get('slug')} is not a maker with a tree: only a maker linked "
            "to a project has an execution scope, a runner and a ticket queue")
    base = summary(rec) or {}
    row = scope_row(scope)
    root = row["dir"] if row else None
    runner = work_driver.runner_state(root) if root else None
    return {
        **base,
        "slug": rec.get("slug"),
        "name": rec.get("name"),
        "label": (row or {}).get("label"),
        "runnable": row is not None,
        "runner": runner,
        "sessions": _live_sessions(scope),
        "recent_runs": _recent_runs(scope, Path(root) if root else None, runs_limit),
    }


# ---------------------------------------------------------------------------
# The persona, on its way to the worker
# ---------------------------------------------------------------------------

def prompt_block(scope: str) -> str:
    """This scope's maker persona and rules, as a prompt section. "" when absent.

    THIS IS WHAT MAKES THE RECORD REAL RATHER THAN DECORATIVE.
    `scripts/ralph.sh` injects the returned text into the unattended worker's
    prompt the same way it injects learnings, so editing the maker agent --
    `PUT /agents/{slug}`, or the file itself -- is what changes how the worker
    behaves. An agent whose definition nothing reads is a card on a screen.

    FAILS SOFT, ALWAYS. No definition, no maker, an unreadable registry: the
    answer is the empty string and the worker runs exactly as it does today.
    The caller is an unattended sweep with nobody to answer a traceback.
    """
    try:
        rec = maker_for_scope(scope)
    except Exception:
        return ""
    if not rec:
        return ""
    body = (rec.get("definition") or "").strip()
    if not body:
        return ""
    name = rec.get("name") or rec.get("slug") or "maker"
    return (f"## You are {name}\n\n"
            f"This is the standing persona and rules of the maker agent for this "
            f"scope ({scope}). Where it and the loop rules above disagree about "
            f"HOW to run the loop, the loop rules win; everything about what to "
            f"build and how to judge it is below.\n\n{body}")


# ---------------------------------------------------------------------------
# CLI -- the shell-facing half, for `scripts/ralph.sh`
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="makers",
        description="The maker agent for a scope: its prompt block, its state.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prompt", help="the scope maker's persona as a prompt section")
    p.add_argument("--scope", required=True)

    sub.add_parser("scopes", help="the scopes that can run tickets (JSON)")

    s = sub.add_parser("state", help="a scope maker's live state (JSON)")
    s.add_argument("--scope", required=True)

    sub.add_parser("reconcile", help="ensure a maker exists per runnable scope")

    a = ap.parse_args(argv)
    if a.cmd == "prompt":
        # No trailing newline juggling and no exit code games: the caller
        # captures stdout and treats empty as "nothing to inject".
        sys.stdout.write(prompt_block(a.scope))
        return 0
    if a.cmd == "scopes":
        print(json.dumps(runnable_scopes()))
        return 0
    if a.cmd == "state":
        rec = maker_for_scope(a.scope)
        if rec is None:
            print(json.dumps({"scope": a.scope, "maker": None}))
            return 0
        print(json.dumps(live_state(rec), default=str))
        return 0
    if a.cmd == "reconcile":
        print(json.dumps(reconcile()))
        return 0
    return 2


if __name__ == "__main__":  # `python -m services.makers ...`
    sys.exit(main())
