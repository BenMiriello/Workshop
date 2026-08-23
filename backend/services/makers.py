"""The maker agent: a scope's ticket-worker persona, as a record you can see.

WHAT A MAKER IS, and this is the design of record, not a local invention.
`docs/platform-redesign/primitive-roles-2026-07-18.md` §2 lists `maker` as
"exists (`ralph.sh`, `project_ralph.sh`)" and §6 says the project loop IS NOT AN
AGENT: "projects work without any definition file." §3's `ROLE_RUNNERS` table
omits `maker` entirely, because a maker is not shell-dispatched on a predicate.

So: THE LOOP IS THE MAKER. `queue_runner.sh` -> `project_ralph.sh` -> `ralph.sh`
is what takes a ready ticket off a scope's queue and drives it to done, and it
does that whether or not any file in `agents/` mentions the scope. This module
does not run tickets and must never start doing so.

WHAT THIS MODULE OWNS is the maker's IDENTITY, which is the part that had no
record: which tree a maker works (`scope_for`), what standing persona the loop
is told to adopt there (`prompt_block`), and what is true about that tree right
now (`summary`, `live_state`). The definition file is how the operator MANAGES
the worker -- editing it is what changes the prompt -- and it is optional.
A scope with no maker definition works its tickets exactly as it does today.

NOTHING HERE CREATES A MAKER ON ITS OWN. There used to be a 60-second
`reconcile_loop` started from the backend lifespan that wrote a definition file
for every runnable scope. That is precisely the auto-provisioning
`agent-roles-research-2026-07-16.md` rejects ("Nothing needs a definition file
for a project to be worked"; "Three agent definitions per new project ... is a
machine for generating noise and cost"), and `routes/projects.create_project`
already refuses it by hand, writing `"agents": []` with that citation. It is
gone. `ensure` below is the one seeding implementation and its only caller is
the operator action that already means "give this project a maker":
`POST /agents/build`, which is what `/agents/new` posts to when the role is
Maker and a project is picked.

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

A REAL DEFINITION FILE, NOT A SYNTHESIZED RECORD. When a maker is created it is
an ordinary `agents/<slug>.md` written through `agents_registry.create`, which
every other agent in the system already is. The alternative -- synthesizing a
record at list time from the scope registry -- was rejected for one reason: the
operator's complaint is that he cannot MANAGE the thing, and managing an agent
means editing its persona and its rules. A synthesized record has no file to
edit, so `PUT /agents/{id}` would have nothing to write and the persona could
never reach the worker. `scripts/ralph.sh` injecting this definition into the
worker's prompt is only possible because the definition is a file.

SEVERAL MAKERS ON ONE SCOPE IS LEGAL, AND IS NOT RESOLVED BY SORT ORDER.
`agents_registry.SINGLETON_ROLES` is `("triager", "keeper")` and deliberately
EXEMPTS `maker` -- its comment says so, because several makers on one scope is
the concurrency case, governed by `touches` disjointness and the claim fence.
An earlier version of this header claimed one-per-scope was "ENFORCED HERE". It
was not, and on the live install `birds` and `budget` each had two, with the
one the UI showed decided by which filename sorted first. So this module does
not enforce it and does not pretend to: `makers_for_scope` returns ALL of them,
`maker_for_scope` returns one only when there IS one, and `prompt_block` injects
nothing at all when a scope has two -- because picking would be the sort order
deciding what an unattended worker is told. The loop still runs; see above.

NO SCOPE IS NAMED HERE. `runnable_scopes()` reads the projects registry and the
developer-mode flag; it is the same enumeration `routes/autonomy.py` served as
`GET /queue/runner/targets`, moved here so the maker seeder and the runner
picker cannot disagree about which trees can run tickets.
"""
from __future__ import annotations

import argparse
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
    "runnable_scopes", "scope_row", "scope_for",
    "makers_for_scope", "maker_for_scope", "ensure",
    "live_state", "summary", "prompt_block", "main",
]

# NO SLUG CONVENTION IS DEFINED HERE, deliberately. The previous version minted
# `<project-slug>-maker`, which is a name nobody in the docs chose; it reached
# two of seven live scopes and the other five kept the `<slug>-builder` names
# they already had, so the roster read as two conventions at once. The platform
# already has exactly one slug rule -- `agents_registry.mint_slug`, which
# kebab-cases the NAME the operator typed and disambiguates with `-2` -- and
# `app-is-the-entity-2026-08-20.md` says a slug is a label, never an identity.
# So the caller passes the slug it minted from the operator's name and this
# module does not rename anything.

# How many Jobs to scan when attributing runs to a scope. The Job store is a
# collapsed read of one JSONL and is cached inside `services.jobs`, so this is a
# bound on the join below rather than on I/O.
_RUNS_SCAN = 500

# What a fresh maker says about itself. Deliberately short and deliberately
# EDITABLE: this is the seed of a document the operator owns, not a constant the
# platform keeps re-asserting. `ensure` writes it once, at create time, and
# nothing ever touches the body again -- an edit here does not propagate, for
# the same reason `fork_from_template` has no sync engine.
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
    deciding which trees a maker can be linked to -- is exactly the
    parallel-system defect this work exists to remove.

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


def makers_for_scope(scope: str, *, include_retired: bool = False,
                     roster: list[dict] | None = None) -> list[dict]:
    """EVERY maker agent that declares this scope, in slug order.

    A SEARCH, not a slug lookup, and that is the whole reason five of the seven
    live scopes kept the maker the operator already had instead of growing a
    duplicate beside it: a maker written by hand, under any name, IS this
    scope's maker.

    The list is the honest answer because the registry ALLOWS several. Slug
    order is a display order and nothing else -- explicitly not a precedence
    rule, because that is what filename sort order was when it silently decided
    which of the two Birds makers the UI showed.

    `roster` is a list `list_agents()` already produced. The roster route reads
    it once for the whole page and hands it down, because otherwise drawing N
    maker cards would re-glob and re-parse every definition file N times.
    """
    rows = (agents_registry.list_agents(include_retired=include_retired)
            if roster is None else roster)
    out = [rec for rec in rows
           if rec.get("role") == "maker" and scope_for(rec) == scope]
    out.sort(key=lambda r: r.get("slug") or "")
    return out


def maker_for_scope(scope: str, *, include_retired: bool = False,
                    roster: list[dict] | None = None) -> dict | None:
    """This scope's maker when the scope HAS one -- exactly one. Else None.

    None means one of two things and the caller must be able to live with both:
    the scope has no maker definition (normal -- the loop is the maker and the
    file is optional), or it has several and the platform will not pick. Use
    `makers_for_scope` when you need to tell those apart or show them.
    """
    found = makers_for_scope(scope, include_retired=include_retired, roster=roster)
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------
# Seeding a maker -- only ever because someone asked
# ---------------------------------------------------------------------------

def ensure(*, slug: str, name: str, project_slug: str | None,
           purpose: str | None = None, body: str | None = None) -> dict | None:
    """Give a maker the operator just asked for its definition file. Additive.

    WHO CALLS THIS, AND WHY IT IS THE ONLY CALLER. `POST /agents/build` -- what
    `/agents/new` posts when the role is Maker and a project is picked. That
    request IS the operator saying "this project gets a maker", so it is the
    moment a definition may come into existence, and there is no other.

    The three candidates named in the design were weighed and two are closed:

      * the project-create path is closed BY THAT PATH. `create_project` writes
        `"agents": []` and cites the research doc for it: a definition per new
        project "is a machine for generating noise and cost". Seeding here would
        reintroduce exactly what that comment refuses.
      * adoption is closed by `primitive-roles-2026-07-18.md` §6: "Only
        shell-dispatched roles are templatable (`maker` and `reviewer` are
        invoked by the loop, not the shell)." There is no maker template to
        adopt and there should not be one.
      * an operator action is what is left, and one already existed. Before
        this, `POST /agents/build` wrote an execution row into
        `build_agents.json` and no definition, so the maker the operator had
        just created did not appear on the roster at all (`list_agents` has no
        builder fallback, on purpose: "an undeclared process is a Run"). The
        declaration was the missing half of the operator's own action.

    ADDITIVE, in the same sense as `work_driver.ensure`: it creates what is
    missing and never rewrites, retires or re-seeds what is there. If a
    definition for this slug already exists it is returned untouched -- the body
    is the operator's document from the first write onward.

    NOT one-per-scope. A project may already have a maker; the operator asking
    for a second one is the concurrency case the registry deliberately allows
    (`SINGLETON_ROLES` exempts `maker`). What the second one must not do is
    silently take over the first one's persona injection, and it does not --
    see `prompt_block`.

    Returns the record, or None if it could not be written. NEVER RAISES: the
    caller has already created the build agent by the time this runs, and
    failing the whole request over a definition file would leave the operator
    with a half-made maker and an error.
    """
    slug = (slug or "").strip().lower()
    if not slug:
        return None
    existing = agents_registry.get(slug)
    if existing is not None:
        return existing
    try:
        return agents_registry.create(
            slug=slug,
            name=name or slug,
            purpose=purpose or _SEED_PURPOSE,
            project_slug=project_slug,
            role="maker",
            # NOT a trigger and NOT an autonomy mode. A maker does not fire on a
            # schedule: it is resident, and whether it may work is
            # `scope_execution`'s answer for its scope. Declaring `autonomy:` on
            # the definition here would be the second setting this module exists
            # to avoid -- two fields answering "is this maker on", disagreeing.
            triggers_list=[],
            body=body if body is not None else _SEED_BODY,
        )
    except Exception:
        log.warning("makers: could not write a definition for %s", slug, exc_info=True)
        return None


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


def summary(rec: dict, *, sessions: list[dict] | None = None,
            roster: list[dict] | None = None) -> dict | None:
    """The compact live block that rides on a roster row. None for a non-maker.

    Cheap by construction: one flock probe and a filter over rows the caller has
    already read. `sessions` and `roster` are both passed in by the roster route
    so that drawing N maker cards reads the presence channel once and the agent
    definitions once, rather than N times each.
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
    # WHO ELSE IS A MAKER HERE. Sent on every row because the multi-maker case
    # must be visible rather than resolved: on the live install two scopes had
    # two makers each and the UI showed whichever filename sorted first, which
    # is not a decision anybody made. One entry is the ordinary case; two is a
    # fact the operator gets to see and act on.
    peers = [m.get("slug") for m in makers_for_scope(scope, roster=roster)
             if m.get("slug")]
    return {
        "scope": scope,
        "dir": root,
        "scope_makers": peers,
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
    The caller is an unattended sweep with nobody to answer a traceback. This is
    not a degraded mode -- per `primitive-roles` §6 the loop IS the maker and the
    definition is optional, so "" is the ordinary answer for an unadorned scope.

    TWO MAKERS ON THE SCOPE ALSO GET "", and that is the point of this function
    reading the list rather than the first match. Injecting one of two personas
    into an unattended worker on the strength of which slug sorts first is the
    filename deciding what the machine is told. It is logged loudly instead, so
    the ambiguity shows up as something to fix rather than as a silent choice.
    """
    try:
        found = makers_for_scope(scope)
    except Exception:
        return ""
    if len(found) > 1:
        log.warning(
            "makers: scope %s has %d makers (%s); injecting no persona, because "
            "choosing between them would be slug order deciding what the worker "
            "is told. Retire the ones that are not this scope's maker.",
            scope, len(found), ", ".join(m.get("slug") or "?" for m in found))
        return ""
    rec = found[0] if found else None
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

    m = sub.add_parser("makers", help="every maker declaring a scope (JSON)")
    m.add_argument("--scope", required=True)

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
        found = makers_for_scope(a.scope)
        if len(found) != 1:
            # `maker: null` used to mean only "none". It can also mean "more
            # than one and the platform will not pick", so say which.
            print(json.dumps({"scope": a.scope, "maker": None,
                              "scope_makers": [m.get("slug") for m in found]}))
            return 0
        print(json.dumps(live_state(found[0]), default=str))
        return 0
    if a.cmd == "makers":
        # The multi-maker case, answerable from a shell. `state` shows ONE
        # maker's live block; this says how many there are to choose from.
        print(json.dumps([{"slug": m.get("slug"), "name": m.get("name"),
                           "definition_path": m.get("definition_path")}
                          for m in makers_for_scope(a.scope)]))
        return 0
    return 2


if __name__ == "__main__":  # `python -m services.makers ...`
    sys.exit(main())
