"""Scope-generic Task API (the "Work" surface, plan D4 / sub-decision b).

The task store is per-tree: the workshop root, every project, every registry
assistant scope, and every build agent all keep their own `.tasks.json`. Before
this route there was no cross-scope view, so work parked in an agent scope or the
root was invisible in the UI. This aggregates every scope's tasks into one feed
and exposes CRUD on a single task by its scope-qualified id.

Terminology: the store, CLI, and API all say `task`. The UI may call these
"tickets" in prose, but every identifier here stays `task`.

Scope keys mirror `task_scope.root_for_context`:
  development     -> the platform repo itself; developer mode only (ADR 0014)
  workshop        -> the root inbox (shared/queue), which has no working tree
  project:<slug>  -> projects/<slug>
  agent:<slug>    -> a maker's linked project dir, else .runtime/agent-scopes/<slug>

Cross-scope listing is cheap: `tasks.list_tasks` reconciles, and reconcile
short-circuits on an unchanged export hash, so a sweep across scopes does no work
when nothing moved.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from routes.api_models import (DeletedOut, Job, LabelCount, Readiness, Task,
                               TaskAcceptanceCriterion, TaskLink)
from services import (tasks, task_scope, task_dedup, task_decision, jobs, ownership,
                      work_review, ticket_format,
                      knowledge, standing, permissions, ticket_readiness,
                      latitude as latitude_svc)
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE, Page, PageMeta

router = APIRouter(prefix="/tasks", tags=["tasks"])

_PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(ownership._RUNTIME / "projects.json")))
_AGENT_SCOPES_DIR = ownership._RUNTIME / "agent-scopes"

# The sweep launcher (project_ralph.sh) and the repo root Popen runs from.
_WORKSHOP_ROOT = task_scope.WORKSHOP_ROOT
_SCRIPTS_ROOT = _WORKSHOP_ROOT / "scripts"


# ---- Scope resolution ------------------------------------------------------

def _root_for_scope(scope: str | None) -> Path | None:
    """Resolve a scope key to its task-store root. `task_scope` owns every kind
    (development / workshop / project: / agent:); an unknown scope returns None.

    A scope refused by the developer-mode gate also returns None here, because
    listing paths must not raise. Route handlers call `_require_root` instead,
    which turns the refusal into the sentence that explains it."""
    if not scope:
        return None
    return task_scope.root_for_context(scope)


def _require_root(scope: str | None) -> Path:
    """`_root_for_scope` for a route: a Path, or the HTTP error that says why not.

    Two different failures used to look identical. A scope the gate refuses is
    not an unknown scope, and reporting it as one sent the operator looking for
    a typo in a scope key that is spelled correctly and simply not enabled here
    (ADR 0014)."""
    root = _root_for_scope(scope)
    if root is not None:
        return root
    refusal = task_scope.dev_mode_refusal(scope)
    if refusal:
        raise HTTPException(403, refusal)
    raise HTTPException(400, f"unknown scope: {scope}")


def _with_latitude(rec: dict, scope: str, cache: dict[str, str | None] | None = None) -> dict:
    """Stamp the EFFECTIVE latitude and its source onto an outgoing task.

    The stored `latitude` stays exactly as it is (null = inherit); this ADDS the
    resolved view the UI needs. The source travels with the value because an
    inherited value that renders as if it were set is precisely how the
    null-means-inherit bug becomes invisible on screen."""
    if cache is None:
        scope_lat = latitude_svc.scope_latitude(scope)
    else:
        if scope not in cache:
            cache[scope] = latitude_svc.scope_latitude(scope)
        scope_lat = cache[scope]
    # `resolve` reads the scope through the registry; feed it the cached value by
    # short-circuiting the chain here so a 200-task list does one read per scope.
    value = latitude_svc.coerce(rec.get("latitude"))
    if value:
        source = "task"
    elif scope_lat:
        value, source = scope_lat, "scope"
    else:
        value, source = latitude_svc.DEFAULT_LATITUDE, "default"
    rec["effective_latitude"] = {"value": value, "source": source}
    return rec


def _blocked_by(task: dict, index: dict[str, tuple[dict, str]]) -> list[dict]:
    """Why this ticket is not runnable: the NON-TERMINAL subset of its resolved
    `depends_on`, each stub carrying its own scope.

    Resolved across scopes on purpose, and deliberately more honest than
    `tasks.is_ready`, which matches the scheduler and treats a dependency it
    cannot see as satisfied (see that docstring). So a row can read
    `ready: true` with a non-empty `blocked_by`; that is the documented
    scheduler gap, not a contradiction in this response.

    A dependency id that resolves nowhere is omitted rather than rendered: the
    detail route reports those under `links.missing`, and a list row has no
    place to say "points at a ghost"."""
    out: list[dict] = []
    for dep in task.get("depends_on") or []:
        hit = index.get(str(dep))
        if hit is None:
            continue
        blocker, skey = hit
        if blocker.get("status") in tasks.TERMINAL:
            continue
        out.append(_link_stub(blocker, skey))
    return out


def _with_triage(rec: dict, by_id: dict[str, dict],
                 index: dict[str, tuple[dict, str]]) -> dict:
    """Stamp the three derived triage fields onto an outgoing task.

    HERE, not in the store. `tasks._for_storage` strips only `_PROJECTED_KEYS`
    ("exec", "blocked_request"), so a derived field added inside `_normalize`
    would be written straight into `.tasks.json` on the next persist and become
    a second source of truth that a hand edit could put out of step with the
    status it was derived from. Computing it on the way out means the record on
    disk never carries it.

    `by_id` indexes the ticket's OWN scope (what the scheduler sees); `index`
    spans every scope loaded this request (what a human needs to see)."""
    rec["state_type"] = tasks.state_type(rec.get("status"))
    rec["ready"] = tasks.is_ready(rec, by_id)
    rec["blocked_by"] = _blocked_by(rec, index)
    # What this ticket does not yet say — the same report `_detail` attaches, so a
    # row and the page it opens agree. It used to ride only the detail GET, which
    # meant a list could not show a readiness marker at all without refetching
    # every row. `by_id` is the ticket's own scope, which is the right set for the
    # dependency check: a cross-scope dependency is invisible to the SCHEDULER
    # too, so resolving it here would make a row claim a blocker the queue does
    # not act on. Pure and in-memory (no store read), so it costs one regex pass
    # per row on a list that is already loaded.
    # `by_id.keys()` rather than `set(by_id)`: this runs per ROW, so building a
    # fresh set of every id in the scope each time is O(n^2) on a polled
    # endpoint. Measured at 98 tickets it costs 0.09ms, so this is tidiness
    # rather than a fix, but the shape is the kind that only ever gets worse.
    # `check` only does membership tests on it, and a KeysView supports those.
    rec["readiness"] = ticket_readiness.check(rec, known_ids=by_id.keys())
    return rec


def _project_slugs() -> list[str]:
    if not _PROJECTS_REGISTRY.exists():
        return []
    try:
        reg = json.loads(_PROJECTS_REGISTRY.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return list((reg.get("projects") or {}).keys())


def _scope_roots() -> list[tuple[str, Path]]:
    """Every task scope that actually has a store, deduped by resolved path so an
    agent sharing a project's tree is not counted twice. Priority order:
    development, workshop, projects, agent scopes. Only a scope with an existing
    `.tasks.json` or `TODO.md` is surfaced, keeping the sweep cheap and
    non-destructive (no scope is created just by listing).

    THERE IS NO SEPARATE BUILD-AGENT PASS, and removing it lost nothing. It
    enumerated `build_agents.json` and added a `build:<slug>` scope per row --
    a key nothing else in the system recognised, which `semantic_index` then had
    to translate back on every rebuild. Every one of those agents is linked to a
    project, so its root is the project tree the loop above already claimed, and
    the dedupe collapsed all of them. A maker's tickets live in its project's
    store; that is the same statement, made once.

    Development sorts first — above Workshop — because it is the narrower, more
    specific place and the one a developer is usually in. It is absent entirely
    when developer mode is off (`task_scope.dev_scopes`), which is also what
    makes the dedupe below the thing that keeps the platform repo to ONE name: it
    claims the repo's path first, so a registry row pointing at the same tree
    collapses into it instead of appearing as a second door."""
    seen: set[str] = set()
    order: list[tuple[str, Path]] = []

    def add(scope: str, root: Path | None) -> None:
        if root is None:
            return
        try:
            rp = root.resolve()
        except OSError:
            return
        if not rp.exists():
            return
        if not (rp / tasks.STORE_NAME).exists() and not (rp / tasks.EXPORT_NAME).exists():
            return
        key = str(rp)
        if key in seen:
            return
        seen.add(key)
        order.append((scope, rp))

    for dev_scope in task_scope.dev_scopes():
        add(dev_scope, _root_for_scope(dev_scope))
    add("workshop", _root_for_scope("workshop"))
    for slug in _project_slugs():
        add(f"project:{slug}", _root_for_scope(f"project:{slug}"))
    if _AGENT_SCOPES_DIR.exists():
        for d in sorted(_AGENT_SCOPES_DIR.iterdir()):
            if d.is_dir():
                add(f"agent:{d.name}", d)
    return order


# Public alias: other routers (the inbox reconciler) need the same scope sweep,
# and duplicating it would let the two drift.
scope_roots = _scope_roots


# ---- Aggregate list --------------------------------------------------------

def _csv_filter(raw: str | None, allowed, label: str) -> set[str] | None:
    """A comma-separated filter value, validated. None (or empty) means no filter.

    A single value behaves exactly as it always did, so `?status=blocked` is
    unchanged; the list form is what lets the UI ask for the four statuses of its
    Active view in ONE request. Before this it could not express that, so it
    fetched every ticket in every scope unpaged (bodies, comments and evidence
    inline) on every sort, scope or search change and narrowed in the browser.

    An unrecognised value is a 400 that NAMES it. Silently returning nothing is
    the worse failure: a typo and an empty backlog look identical, and the caller
    reads it as data rather than as a mistake."""
    if raw is None:
        return None
    vals = [v.strip() for v in str(raw).split(",") if v.strip()]
    if not vals:
        return None
    bad = sorted({v for v in vals if v not in allowed})
    if bad:
        raise HTTPException(
            400, f"unknown {label}: {', '.join(bad)} (expected one of "
                 f"{', '.join(sorted(allowed))})")
    return set(vals)


@router.get("", response_model=Page[Task], response_model_exclude_unset=True)
async def list_all(
    status: str | None = None,
    state_type: str | None = None,
    scope: str | None = None,
    labels: str | None = None,
    effort: str | None = None,
    assignee: str | None = None,
    source: str | None = None,
    include_done: bool = True,
    q: str | None = None,
    ready: bool | None = None,
    sort: str = "updated",
    desc: bool | None = None,
    # WAS `limit: int | None = None`, i.e. genuinely unbounded: omitting the
    # parameter returned the ENTIRE task set across every scope, bodies,
    # comments and evidence inline. ADR 0023 clause 2 names this exact
    # signature as the concrete violation that "disappears", and gives the
    # replacement verbatim: `Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)`. Both
    # constants come from `wsplatform.http` so this router cannot invent the
    # ninth default. THIS IS A BEHAVIOUR CHANGE on a live surface: a caller
    # that sent no `limit` used to get everything and now gets 50.
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    offset: int = 0,
) -> Page[Task]:
    """Tasks across every scope, each tagged with its resolved `scope` key.

    Filters: `status` (comma-separated, from the eight task statuses),
    `state_type` (comma-separated, from tasks.STATE_TYPES — the coarse category
    the UI's Active / Backlog / Done views actually mean, expanded to statuses
    here), `scope` (restrict to a single scope key), `labels` (comma-separated,
    a row matches if it carries ANY of them), `effort` (comma-separated, from
    tasks.EFFORTS), `assignee` (comma-separated, `operator` or `agent:<slug>`),
    `source` (comma-separated, from tasks.SOURCE_KINDS: where the ticket came
    FROM),
    `q` (case-insensitive substring of title or body), `ready` (only what the
    scheduler could pick up, or only what it could not).
    Ordering: `sort` (one of tasks.SORT_KEYS, including `queue` = the
    scheduler's own order and `source` = the door it came through) + `desc`.
    Paging: `limit`/`offset`.

    `source` was on every ticket since the store existed and nothing read it: on
    2026-09-01, 132 of 208 open tickets came through the agent door and no view
    anywhere could tell them from the rest. It reports WHERE A TICKET CAME FROM
    and nothing more. It is not a record of what anyone intended, and `unknown`
    is one of its values precisely so it cannot be read as one: see
    tasks.SOURCE_KINDS for the day `operator` doubled as the value for "nobody
    recorded anything" and put 34 tickets under a name that had not filed them.

    `labels` and `effort` exist so the list page's filter chips push DOWN to the
    server like status and scope already did. A chip narrowed in the browser
    reports a `total` for the unnarrowed set and pages against it, so the count
    beside the title contradicts the rows under it and page 2 skips matches.
    `labels` is validated against nothing because a label is free text: an
    unknown one is a legitimate query that matches no rows, unlike an unknown
    status, which is a typo.

    `status` and `state_type` are independent and compose with AND, like every
    other filter here: sending both narrows to their intersection rather than
    letting one quietly win. Either alone is the normal case.

    Every filter is applied BEFORE `sort` and BEFORE paging, so `total` is the
    honest size of the filtered set and page 1 of a filtered list is its real
    first page — not the first page of everything, narrowed afterwards.

    Every row carries the derived `state_type`, `ready` and `blocked_by`
    (`_with_triage`). None of the three is stored.

    `page.total` is the size of the filtered set BEFORE paging. The list used to
    return raw file order with no way to page, so a caller that wanted the newest
    ticket got the oldest and a caller with more than a screenful got an
    arbitrary slice.

    THE ENVELOPE, AND WHAT HAPPENED TO THE OTHER FOUR KEYS. This response was
    `{tasks, count, total, scopes, offset, sort}` -- by ADR 0023's own account
    the best envelope in a 415-route surface, and the only one that separated
    the page from the total. It is SUPERSEDED rather than generalised, per
    clause 1, and each key went somewhere deliberate:

      tasks  -> `items`. An entity-named collection key cannot be typed
                generically, and a generic `Page<T>` in the generated client is
                the whole prize; entity-named keys are also the mechanism that
                produced 52 distinct envelope names across 39 routers.
      total  -> `page.total`, carried forward verbatim. This is the separation
                the ADR says was right here first.
      offset -> `page.offset`. Offset paging survives on this route because it
                already exists (clause 2 grandfathers it and forbids new ones);
                it moves inside `page` so the top level is exactly `items` +
                `page`.
      count  -> DELETED. It was `len(items)`, and a derivable field is a field
                that can disagree with what it describes.
      sort   -> DELETED. It echoed back what the caller sent.
      scopes -> MOVED to `GET /tasks/scopes`, which already existed. It was a
                count of the scopes this request swept, which is not a member of
                this collection, and anything that is not a member of the
                collection is a different resource with its own endpoint. That
                route NAMES the scopes rather than counting them, which is
                strictly more information; the one thing it does not reproduce
                is the narrowing a `?scope=` query applied here, and that is a
                number the caller already knows, because it sent it.
    """
    want_status = _csv_filter(status, tasks.STATUSES, "status")
    want_type = _csv_filter(state_type, tasks.STATE_TYPES, "state_type")
    want_effort = _csv_filter(effort, tasks.EFFORTS, "effort")
    want_source = _csv_filter(source, tasks.SOURCE_KINDS, "source")
    # Free text, so these are split but not validated (see the docstring). An
    # assignee is `operator` or `agent:<slug>`, and the set of agents is open,
    # so there is no closed vocabulary to check against.
    want_labels = {v.strip() for v in (labels or "").split(",") if v.strip()} or None
    want_assignee = {v.strip() for v in (assignee or "").split(",") if v.strip()} or None
    out: list[dict] = []
    lat_cache: dict[str, str | None] = {}
    needle = (q or "").strip().casefold()
    # ONE index per request, built while the scopes are being read anyway.
    # Resolving `blocked_by` per row the way the detail route does would be
    # O(rows x scopes) store loads.
    by_scope: dict[str, dict[str, dict]] = {}
    index: dict[str, tuple[dict, str]] = {}
    for skey, root in _scope_roots():
        if scope and skey != scope:
            continue
        try:
            # The FULL store, even when include_done is false: `ready` and
            # `blocked_by` both have to see a FINISHED dependency to know it is
            # satisfied, and filtering it out here would make every ticket that
            # depends on completed work read as blocked. include_done is applied
            # per row below instead, exactly as list_tasks would.
            items = tasks.list_tasks(root)
        except Exception:
            continue
        by_id = {t["id"]: t for t in items}
        by_scope[skey] = by_id
        for t in items:
            index.setdefault(t["id"], (t, skey))
        for t in items:
            if not include_done and t.get("status") in tasks.TERMINAL:
                continue
            if want_status is not None and t.get("status") not in want_status:
                continue
            if want_type is not None and \
                    tasks.state_type(t.get("status")) not in want_type:
                continue
            if want_effort is not None and t.get("effort") not in want_effort:
                continue
            if want_source is not None and \
                    tasks.source_kind(t) not in want_source:
                continue
            if want_assignee is not None and \
                    str(t.get("assignee") or "") not in want_assignee:
                continue
            # ANY, not ALL: picking two tags means "either of these", which is
            # what a multi-value chip reads as everywhere else in the bar.
            if want_labels is not None and \
                    not (want_labels & {str(x) for x in (t.get("labels") or [])}):
                continue
            if needle:
                hay = f"{t.get('title') or ''}\n{t.get('body') or ''}".casefold()
                if needle not in hay:
                    continue
            rec = dict(t)
            rec["scope"] = skey
            out.append(_with_latitude(rec, skey, lat_cache))

    # A `scope=` query loaded one store, so a dependency living elsewhere is not
    # in the index yet. Pay for the other scopes only when a row actually points
    # at an id nothing loaded resolved — the same trade `_resolve_links` makes.
    unresolved = {str(d) for rec in out for d in (rec.get("depends_on") or [])
                  if str(d) not in index}
    if unresolved:
        for skey, root in _scope_roots():
            if skey in by_scope:
                continue
            try:
                others = tasks.list_tasks(root)
            except Exception:
                continue
            for o in others:
                if o["id"] in unresolved:
                    index.setdefault(o["id"], (o, skey))
                    unresolved.discard(o["id"])
            if not unresolved:
                break

    for rec in out:
        _with_triage(rec, by_scope.get(rec["scope"], {}), index)
    if ready is not None:
        out = [rec for rec in out if rec["ready"] is bool(ready)]
    out = tasks.sort_tasks(out, sort, desc)
    total = len(out)
    start = max(0, int(offset or 0))
    rows = out[start:start + limit]
    return Page[Task](
        items=rows,
        page=PageMeta(limit=limit, total=total, offset=start),
    )


class ScopePickup(BaseModel):
    """Whether work filed to a scope can be run by hand, and whether anything
    will pick it up unattended. See `scope_pickup` for why those are two
    questions and not one."""

    scope: str | None = None
    runnable: bool
    automatic: bool
    why: str


class ScopesOut(BaseModel):
    """The scope keys with a task store, plus their pickup facts.

    This route is also where `GET /tasks`'s retired `scopes` key went: that was
    a COUNT of the scopes a list request swept, and this names them instead.
    ADR 0023 clause 1: anything that is not a member of the collection is a
    different resource with its own endpoint, and this endpoint already existed,
    which is what made the split cheap."""

    scopes: list[str]
    pickup: dict[str, ScopePickup]


@router.get("/scopes", response_model=ScopesOut, response_model_exclude_unset=True)
async def list_scopes() -> dict:
    """The scope keys that currently have a task store, for the create/filter
    controls. Cheap directory + registry scan, no task load.

    `pickup` carries, per scope, whether a ticket filed there can be run by hand
    and whether anything will pick it up unattended (see `scope_pickup`). It is
    additive: `scopes` is unchanged."""
    keys = [skey for skey, _ in _scope_roots()]
    return {"scopes": keys, "pickup": {k: scope_pickup(k) for k in keys}}


class LabelsOut(BaseModel):
    labels: list[LabelCount]


@router.get("/labels", response_model=LabelsOut, response_model_exclude_unset=True)
async def list_labels(scope: str | None = None) -> dict:
    """Every label in use, with how many tickets carry it.

    The list page's tag filter needs the vocabulary to offer, and a label is
    free text with no registry: the only source of truth for "what tags exist"
    is the tickets themselves. Counts come with it so the menu can order by
    what is actually used rather than alphabetically, and so a tag on one
    ticket is visibly that.

    Scoped when `scope` is given, so the menu on a filtered list offers the
    tags that list can actually match."""
    counts: dict[str, int] = {}
    for skey, root in _scope_roots():
        if scope and skey != scope:
            continue
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            for raw in (t.get("labels") or []):
                key = str(raw).strip()
                if key:
                    counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"labels": [{"label": k, "count": n} for k, n in ordered]}


class AssigneesOut(BaseModel):
    assignees: list[LabelCount]


@router.get("/assignees", response_model=AssigneesOut, response_model_exclude_unset=True)
async def list_assignees(scope: str | None = None) -> dict:
    """Every assignee in use, with how many tickets each holds.

    Same shape and same reason as `/labels`: an assignee is `operator` or
    `agent:<slug>`, the set of agents is open, and there is no registry that
    knows which of them hold tickets. The filter menu needs that, and so does
    any surface that wants to offer "assigned to" without inventing a list."""
    counts: dict[str, int] = {}
    for skey, root in _scope_roots():
        if scope and skey != scope:
            continue
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            key = str(t.get("assignee") or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"assignees": [{"label": k, "count": n} for k, n in ordered]}


# ---- Autonomy scopes: REMOVED ----------------------------------------------
# `GET /api/tasks/autonomy-scopes` and its only consumer,
# frontend/src/components/AutonomyScopes.tsx, are both deleted (2026-08-11).
# The component was never imported by any route or page, so the endpoint had
# zero live callers, and the component's row type predated the `development`
# kind this endpoint had learned to emit: a `development` row would have taken
# its agent branch and PUT an autonomy preset to `/api/agents/` with an empty
# slug. A control that is wired wrong and waiting to be switched on is worse
# than no control. Per-scope autonomy still lives where it is edited:
# `GET/PUT /api/projects/{slug}/autonomy` and the agent detail endpoint.


# ---- Create ----------------------------------------------------------------

class TaskAcceptanceIn(BaseModel):
    # Mirrors the store's acceptance shape (services/tasks.py Canonical Task):
    # {"kind": "reviewer"|"command"|"operator", "spec": str|None,
    #  "criteria": [{"desc", "cmd", "expect"}]}. `spec` carries the command line
    # when kind == "command"; reviewer/operator ignore it. `criteria` are the
    # assertions the gate EXECUTES (services/change_gates.CRITERIA_GATE).
    kind: str
    spec: str | None = None
    criteria: list[TaskAcceptanceCriterion] | None = None


class TaskCreateIn(BaseModel):
    scope: str = Field(min_length=1)
    # A caller sends `title` (+ optional `body`), or one `text` blob the store
    # splits. `text` stays for the CLI and older clients; at least one is required.
    title: str | None = Field(default=None, max_length=tasks.TITLE_MAX)
    body: str | None = Field(default=None, max_length=8000)
    text: str | None = Field(default=None, max_length=12000)
    labels: list[str] | None = None
    priority: int = 0
    effort: str | None = None  # s|m|l hint; set post-create, never a gate
    # code|judgement|external, or null for "not yet classified". Unclassified is
    # NOT eligible for unattended dispatch: the store fails it closed rather than
    # guessing (tasks.EXECUTIONS).
    execution: str | None = None
    # strict|standard|open, or null to inherit from the scope (latitude.py).
    latitude: str | None = None
    # HOW A REVIEWER WILL KNOW THIS IS DONE, and it is not optional. A ticket
    # filed with no command and no runnable criterion is refused; see
    # services/ticket_format for the rule and the refusal wording. Typed rather
    # than free-form so the store gets the shape it normalizes.
    acceptance: TaskAcceptanceIn | None = None


class TaskCreateOut(BaseModel):
    """The filed task plus the dedup verdict that produced it.

    `task` is null only when dedup declined to create one (an exact match with
    nothing to return); `decision` is exact | merge | related | distinct, and
    `matched_id` / `cosine` describe what it matched against."""

    task: Task | None = None
    decision: str | None = None
    matched_id: str | None = None
    cosine: float | None = None
    message: str | None = None


@router.post("", response_model=TaskCreateOut, response_model_exclude_unset=True)
async def create(body: TaskCreateIn) -> dict:
    """File a task into a scope through the dedup path, so a manual ticket
    dedupes against open tasks exactly like a chat-filed one. Returns the task
    plus the dedup decision (exact | merge | related | distinct).

    REFUSES a ticket with no executable acceptance, 400, carrying the sentence
    that says what would satisfy it (services/ticket_format.acceptance_refusal).
    A ticket whose finish line is prose leaves the worker to invent one and the
    reviewer to guess at it, which is how 195 open tickets came to have none.
    """
    root = _require_root(body.scope)
    # Dedup embeds the whole ticket, so the pair is joined back into one blob
    # here rather than teaching the embedder about the split.
    if body.title is not None:
        text = tasks.join_text(body.title, body.body or "")
    elif body.text:
        text = body.text
    else:
        raise HTTPException(400, "title or text required")
    if not text.strip():
        raise HTTPException(400, "title or text required")
    try:
        res = task_dedup.dedup_add(
            root,
            text=text,
            priority=int(body.priority or 0),
            labels=body.labels or [],
            source={"kind": "operator", "ref": "work-ui"},
            scoped_by="operator",
            acceptance=(body.acceptance.model_dump(exclude_none=True)
                        if body.acceptance else None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    task = dict(res["task"]) if res.get("task") else None
    # `effort` is a hint the create path did not carry; stamp it on a freshly
    # created task (not a merge, which would overwrite the existing one's).
    # `latitude` rides the same post-create stamp: an override belongs to the new
    # ticket, and a merge must not silently retune the ticket it merged into.
    if task is not None and (body.effort or body.latitude or body.execution) and \
            res.get("decision") not in ("exact", "merge"):
        try:
            updated = tasks.update(root, task["id"], effort=body.effort,
                                   execution=body.execution,
                                   latitude=body.latitude)
            if updated:
                task = dict(updated)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if task is not None:
        task = _detail(task, body.scope, root)
    # Signal the resident queue runner so it wakes immediately instead of
    # waiting out the full poll interval.
    try:
        (_WORKSHOP_ROOT / ".runtime" / "queue-runner.wake").touch()
    except OSError:
        pass
    return {
        "task": task,
        "decision": res.get("decision"),
        "matched_id": res.get("matched_id"),
        "cosine": res.get("cosine"),
        "message": res.get("message"),
    }


# ---- Single task -----------------------------------------------------------

class TaskPatchIn(BaseModel):
    status: str | None = None
    # `title` and `body` patch independently: editing a title must not require
    # resending the whole description. `text` remains accepted for callers that
    # hold one blob (the CLI, an older client) and the store splits it; sending
    # both is a 400 rather than a silent precedence rule.
    title: str | None = None
    body: str | None = None
    text: str | None = None
    labels: list[str] | None = None
    priority: int | None = None
    assignee: str | None = None
    effort: str | None = None
    # "" clears back to "not yet classified", which is INELIGIBLE for unattended
    # dispatch, not "code". An unrecognized value coerces to null in the store.
    execution: str | None = None
    # "" clears the override back to inherit; an unknown value is a 400.
    latitude: str | None = None
    # Advanced fields tasks.update() already supports; None leaves each unchanged.
    acceptance: TaskAcceptanceIn | None = None
    max_attempts: int | None = None
    depends_on: list[str] | None = None
    relates_to: list[str] | None = None
    # WHY this transition, recorded as evidence. Triage's decline verb: cancelling
    # a ticket has always been possible and has never had to say why, and an
    # unexplained cancel is indistinguishable from a mistake three weeks later.
    # Never required — readiness is a checklist here, never a veto — and only
    # meaningful alongside a status change, which is what it describes.
    reason: str | None = None


def _detail(task: dict, scope: str, root: Path) -> dict:
    """THE shape of a single task on the wire. Every route that returns one task
    returns this, and nothing builds its own.

    It exists because they used to. A mutating route hand-assembled
    `dict(task) + scope + latitude` and stopped there, so the record a PATCH,
    /approve, /split or /duplicate-of handed back was missing `state_type`,
    `ready`, `blocked_by`, `links`, `readiness` and `origin` that the detail GET
    had put there. `TicketDetail.saveEdits` does `setTask(await r.json())`, so a
    save visibly emptied the resolved dependencies and the readiness report until
    the next poll refilled them. One helper means the next route added here cannot
    forget: forgetting now requires deliberately not calling it.

    Reads the store again (`list_tasks`) because the caller has just written to
    it; `reconcile` short-circuits on an unchanged export hash, so a read after a
    write is cheap.
    """
    rec = dict(task)
    rec["scope"] = scope
    rec["origin"] = _origin_for(task)
    # What this ticket does not yet say. Computed here rather than in the store so
    # the store stays free of policy (and so `tasks` and `ticket_readiness` do not
    # import each other). Advisory: only `blocking()` refuses a run.
    siblings = tasks.list_tasks(root)
    rec["readiness"] = ticket_readiness.check(
        task, known_ids={x["id"] for x in siblings})
    rec["links"] = _resolve_links(task, scope, siblings)
    # Same three derived fields as the list rows, so a card and the page it opens
    # never disagree. `links.depends_on` is already resolved across scopes here,
    # so `blocked_by` is its non-terminal subset rather than a second resolution
    # pass: `_resolve_links` has done the cross-scope scan this route pays for.
    rec["state_type"] = tasks.state_type(rec.get("status"))
    rec["ready"] = tasks.is_ready(task, {x["id"]: x for x in siblings})
    rec["blocked_by"] = [d for d in rec["links"]["depends_on"]
                         if d.get("status") not in tasks.TERMINAL]
    return _with_latitude(rec, scope)


@router.get("/{scope}/{task_id}", response_model=Task, response_model_exclude_unset=True)
async def get_one(scope: str, task_id: str) -> dict:
    root = _require_root(scope)
    t = tasks.get_task(root, task_id)
    if t is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return _detail(t, scope, root)


def _link_stub(t: dict, skey: str) -> dict:
    """The shape a linked ticket is rendered as: enough to show a row and build a
    working href, and nothing else."""
    return {"id": t["id"], "scope": skey, "title": t.get("title") or "",
            "status": t.get("status") or "todo"}


def _resolve_links(task: dict, scope: str, siblings: list[dict]) -> dict:
    """Resolve this ticket's graph edges into renderable stubs.

    Two things were broken without this. First, `depends_on` / `relates_to` hold
    BARE task ids with no scope, and the frontend built every href from the
    CURRENT ticket's scope, so a cross-scope dependency linked to a 404. Second,
    `parent` is written by the split endpoint and `children` is derived, so
    nothing rendered either: splitting a ticket produced a parent that showed no
    children and children that showed no way home.

    Same-scope ids resolve from `siblings` (already loaded). Anything still
    unresolved is looked for across the other scopes, which is the only way a
    bare id can be turned into a link at all. An id that resolves nowhere comes
    back under `missing` so the page can say so instead of linking into space.
    """
    want = {str(d) for d in (task.get("depends_on") or [])}
    want |= {str(r) for r in (task.get("relates_to") or [])}
    if task.get("parent"):
        want.add(str(task["parent"]))

    found: dict[str, dict] = {}
    children: list[dict] = []
    for s in siblings:
        if s["id"] in want:
            found[s["id"]] = _link_stub(s, scope)
        if s.get("parent") and str(s["parent"]) == task["id"]:
            children.append(_link_stub(s, scope))

    # Only pay for the cross-scope scan when something is still unaccounted for.
    # Once inside it, do NOT stop early on `missing` emptying: the same pass is
    # what finds a child filed into another scope, and short-circuiting on the
    # dependency count would silently skip the rest of the scopes for children.
    missing = want - set(found)
    if missing:
        for skey, root in _scope_roots():
            if skey == scope:
                continue
            try:
                others = tasks.list_tasks(root)
            except Exception:
                continue
            for o in others:
                if o["id"] in missing:
                    found[o["id"]] = _link_stub(o, skey)
                    missing.discard(o["id"])
                # A child can be split into a different scope in principle; the
                # split endpoint does not do that today, but resolving it costs
                # nothing on a scan we are already making.
                if o.get("parent") and str(o["parent"]) == task["id"]:
                    children.append(_link_stub(o, skey))

    def pick(ids) -> list[dict]:
        return [found[str(i)] for i in (ids or []) if str(i) in found]

    parent_id = str(task["parent"]) if task.get("parent") else None
    return {
        "parent": found.get(parent_id) if parent_id else None,
        "children": children,
        "depends_on": pick(task.get("depends_on")),
        "relates_to": pick(task.get("relates_to")),
        "missing": sorted(missing),
    }


def _origin_for(task: dict) -> dict | None:
    """Resolve a ticket's `source` into something a UI can link to.

    Every ticket already records where it came from — `{kind: "chat", ref:
    <thread id>}` when it was filed from a conversation — and nothing ever
    surfaced it. So a ticket opened from the inbox was a dead end: the
    conversation that produced it, and the reasoning behind it, existed and
    were unreachable. Resolving the id to a title and a route here (rather than
    making the client know how chat URLs are built) keeps that knowledge in one
    place.
    """
    src = task.get("source") or {}
    if src.get("kind") != "chat" or not src.get("ref"):
        return None
    from services import chat_store
    try:
        t = chat_store.get_thread(src["ref"])
    except Exception:
        t = None
    if t is None:
        # The chat was deleted. Say so rather than rendering a link that 404s.
        return {"kind": "chat", "id": src["ref"], "title": None, "context_key": None}
    return {
        "kind": "chat",
        "id": t.get("id"),
        "title": t.get("title") or "Chat",
        "context_key": t.get("context_key") or "general",
    }


_ACCEPTANCE_KINDS = {"reviewer", "command", "operator"}


def _pending_tracker_fields(root: Path, task_id: str, body: TaskPatchIn) -> dict | None:
    """The tracker-owned fields (title/body/labels) this patch would change.

    Resolved BEFORE the local write, because write-through sends them to the
    remote first. Returns None when the patch touches none of them. Raises the
    same 400s `tasks.update` would, so an invalid edit is refused here instead of
    being sent to a tracker and then rejected locally.
    """
    if body.title is None and body.body is None and body.text is None \
            and body.labels is None:
        return None
    if body.text is not None and (body.title is not None or body.body is not None):
        raise HTTPException(400, "pass text, or title/body, not both")
    pending: dict = {}
    if body.text is not None:
        title, split_body = tasks.split_text(body.text)
        if not title:
            raise HTTPException(400, "text required")
        pending["title"], pending["body"] = title, split_body
    else:
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(400, "title required")
            pending["title"] = title
        if body.body is not None:
            pending["body"] = body.body
    if body.labels is not None:
        pending["labels"] = list(body.labels)
    return pending


@router.patch("/{scope}/{task_id}", response_model=Task, response_model_exclude_unset=True)
async def patch_one(scope: str, task_id: str, body: TaskPatchIn) -> dict:
    root = _require_root(scope)
    if all(v is None for v in (
        body.status, body.title, body.body, body.text, body.labels,
        body.priority, body.assignee, body.effort, body.execution,
        body.latitude, body.acceptance, body.max_attempts, body.depends_on,
        body.relates_to, body.reason,
    )):
        raise HTTPException(400, "no fields to update")
    # A reason explains a transition, so it needs one to explain. Refused rather
    # than dropped: a decline reason that silently went nowhere is worse than no
    # reason at all, because the operator believes it was recorded.
    reason = (body.reason or "").strip()
    if reason and body.status is None:
        raise HTTPException(400, "reason describes a status change; send status too")
    # Acceptance. THE SECOND DOOR, and it was open twice over.
    #
    # It rebuilt the record as `{kind, spec}`, so editing a ticket through this
    # route silently DELETED its `criteria` -- the runnable assertions are the
    # main form acceptance actually takes (`project_tasks.py --criterion` files
    # `{kind: reviewer, spec: null, criteria: [...]}`, and change_gates executes
    # them), so a save from the UI turned a checkable ticket into an unchecked
    # one. And it accepted any of the three kinds with nothing runnable in it,
    # which is the shape the CREATE door refuses, so a filing could be walked
    # back through here one request later.
    #
    # Both are closed the same way: carry the whole record, and hold a PATCH to
    # the rule the create path uses. Clearing is refused for the same reason -- a
    # ticket that had a finish line and now has none is the regression the
    # ratchet counts, and it must not be reachable by sending an empty kind.
    acceptance_arg = None
    if body.acceptance is not None:
        kind = (body.acceptance.kind or "").strip()
        if kind not in _ACCEPTANCE_KINDS:
            raise HTTPException(
                400, f"acceptance kind must be one of {sorted(_ACCEPTANCE_KINDS)}")
        acceptance_arg = {"kind": kind, "spec": body.acceptance.spec}
        if body.acceptance.criteria is not None:
            acceptance_arg["criteria"] = [
                c.model_dump(exclude_none=True) for c in body.acceptance.criteria]
        refusal = ticket_format.acceptance_refusal(acceptance_arg)
        if refusal:
            raise HTTPException(400, refusal)
    # Write-through, per §4 of docs/platform-redesign/
    # ticket-lifecycle-and-integration-2026-08-10.md: "A local edit to a
    # tracker-owned field goes to the remote first and the local copy is updated
    # from the response. If the remote write fails, the edit is rejected with the
    # error." So the push happens BEFORE tasks.update and a refusal 502s instead
    # of leaving a local state the remote has never seen. Silent no-op for a
    # ticket with no link, a scope in `import` mode, and a box with no tracker.
    from services import tracker_sync
    pending = _pending_tracker_fields(root, task_id, body)
    if pending:
        try:
            tracker_sync.push_text(scope, root, task_id, pending=pending, strict=True)
        except tracker_sync.RemoteWriteError as e:
            raise HTTPException(
                502, f"the tracker refused this edit, so it was not saved: {e}")
    try:
        t = tasks.update(
            root, task_id,
            status=body.status,
            title=body.title,
            body=body.body,
            text=body.text,
            labels=body.labels,
            priority=body.priority,
            # `set_assignee`, never `assignee`: the legacy parameter is also the
            # owner FILTER, so naming a new assignee here used to match nothing and
            # 404 the whole patch — the operator's title, body and status edits went
            # with it. This route is the operator's, so it filters by nothing.
            set_assignee=body.assignee,
            effort=body.effort,
            execution=body.execution,
            latitude=body.latitude,
            acceptance=acceptance_arg,
            max_attempts=body.max_attempts,
            depends_on=body.depends_on,
            relates_to=body.relates_to,
            # One write, so a ticket can never end up cancelled with the reason
            # for it missing. `blocked_reason` is the field the list and the card
            # already read for "why is this not moving", so a stopped status gets
            # the sentence there too and not only in the evidence trail.
            evidence=({"kind": "decision", "ref": f"{body.status}: {reason}"}
                      if reason else None),
            blocked_reason=(reason if reason and body.status in
                            ("cancelled", "parked", "blocked") else None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if t is None:
        raise HTTPException(404, f"task not found: {task_id}")
    if body.status is not None:
        # Workshop OWNS status, so a local transition transitions the remote —
        # the tracker only learns the result. Unlike the text edit above this is
        # best-effort and never rejects the local write: the state machine is
        # ours, and refusing a transition because github was down would stop work
        # over a courtesy. A failure lands on the ticket as `sync-error`
        # evidence, which is why the task is re-read here rather than returning
        # the pre-push copy.
        tracker_sync.push_status(scope, root, task_id)
        t = tasks.get_task(root, task_id) or t
    return _detail(t, scope, root)


@router.delete("/{scope}/{task_id}", response_model=DeletedOut, response_model_exclude_unset=True)
async def delete_one(scope: str, task_id: str) -> dict:
    root = _require_root(scope)
    if not tasks.remove(root, task_id):
        raise HTTPException(404, f"task not found: {task_id}")
    return {"deleted": True}


class TaskCommentIn(BaseModel):
    text: str
    # Who is speaking. Defaults to the operator because that is who is pressing
    # the button; an agent posting here names itself.
    author: str | None = None


@router.post("/{scope}/{task_id}/comments", response_model=Task, response_model_exclude_unset=True)
async def add_task_comment(scope: str, task_id: str, body: TaskCommentIn) -> dict:
    """Append a comment to a ticket's discussion.

    This is discussion, not spec. An answer a worker must ACT on goes into the
    ticket body via the question path (`tasks.append_clarification`), because a
    worker is handed the ticket text and nothing else.
    """
    root = _require_root(scope)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "comment text required")
    rec = tasks.add_comment(root, task_id, text=text,
                            author=(body.author or "operator").strip() or "operator")
    if rec is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return _detail(rec, scope, root)


@router.delete("/{scope}/{task_id}/comments/{comment_id}", response_model=Task, response_model_exclude_unset=True)
async def delete_task_comment(scope: str, task_id: str, comment_id: str) -> dict:
    root = _require_root(scope)
    rec = tasks.delete_comment(root, task_id, comment_id)
    if rec is None:
        raise HTTPException(404, "comment not found")
    return _detail(rec, scope, root)


class TaskJobsOut(BaseModel):
    """The runs this ticket produced. NOT the ADR 0023 collection envelope: the
    shape is left as it is because changing it is a wire change to a route
    TicketDetail reads, and the ADR's one non-optional migration rule is that
    such a change lands with its frontend edit in the same commit."""

    jobs: list[Job]
    count: int


@router.get("/{scope}/{task_id}/jobs", response_model=TaskJobsOut, response_model_exclude_unset=True)
async def task_jobs(scope: str, task_id: str) -> dict:
    """The Jobs (runs) this task produced, enriched like the /jobs feed so each
    run carries its title, status, and receipt summary."""
    recs = jobs.read(task_id=task_id, limit=200)
    try:
        from routes.jobs import _enrich, _name_map
        names = _name_map()
        cache: dict = {}
        enriched = [_enrich(r, names, cache) for r in recs]
    except Exception:
        enriched = recs
    return {"jobs": enriched, "count": len(enriched)}


# ---- Review (the block loop) -----------------------------------------------
# Work that stopped on its own is reviewed here. This is a DASHBOARD behind the
# stop, not a gate in front of the work: nothing routes through it, nothing waits
# on it for permission to start, and deleting the whole surface would not stop a
# single task from running. It exists because a task that blocked needs an answer
# to become runnable again, and the answer needs somewhere to be given.
#
# Five verbs, each ending the review of that block. Job resume is deliberately not
# among them: the Task is the durable state, the blocked job's context is stale by
# construction (it was assembled before the operator spent an hour deciding), and
# `tasks.update` already released the lease when the task blocked. A released task
# is simply `todo`, and the next sweep claims it fresh, reading the ticket, the
# evidence trail, and the answer.


class TaskReviewIn(BaseModel):
    # approve | deny | grant-and-continue | mint-rule | dismiss
    action: str = Field(min_length=1)
    # The operator's sentence. Recorded as evidence on every verb: it is what the
    # next attempt reads to know what was decided and why.
    note: str | None = None
    # grant-and-continue: defaults come from the block's `proposal`, so the common
    # case is one click with an empty body.
    store_uri: str | None = None
    verbs: list[str] | None = None
    subtree: bool | None = None
    ttl_hours: float | None = None
    # mint-rule
    rule: str | None = None
    # Defaults to the task's own scope. `global` is a deliberate act, never a
    # default: a global learning taxes every job in every scope forever.
    rule_scope: str | None = None


_REVIEW_ACTIONS = ("accept", "send-back", "approve", "deny", "grant-and-continue",
                   "mint-rule", "dismiss")

# Wire name -> the verb in `services/task_decision`, which owns what each one
# does. `approve` is kept as the wire name for releasing a BLOCKED ticket
# because two correct callers already send it (TaskPanel labels its button
# "Release"; TicketDetail only shows the box when the ticket is blocked), and
# it is refused outright on a finished one — see `review_task`.
_DECISION_VERB = {
    "accept": task_decision.ACCEPT,
    "send-back": task_decision.SEND_BACK,
    "approve": task_decision.ANSWER,
    "grant-and-continue": task_decision.ANSWER,
    "mint-rule": task_decision.ANSWER,
    "deny": task_decision.CANCEL,
    "dismiss": task_decision.DISMISS,
}


def _learnings_scope(scope: str) -> str:
    """The memory scope a task's scope mints a standing note into.
    `knowledge.scope_for_context`
    owns the mapping (a build agent linked to a project shares that project's
    scope), so a rule lands where that scope's chats and sweeps will read it."""
    return knowledge.scope_for_context(scope)


def _grantee(task: dict, scope: str) -> str:
    """Who the grant is minted FOR. The task's delegate when it has one, else the
    scope itself. Descriptive: `permissions.check` gates on the token, the uri and
    the verb, so this records intent rather than enforcing it."""
    a = str(task.get("assignee") or "").strip()
    return a if a.startswith("agent:") else scope


class TaskMergeOut(BaseModel):
    """The merge that acceptance performed. `merged` is always True here: a
    ticket with nothing to merge omits the field entirely rather than reporting
    a merge that did not happen."""

    merged: bool
    commit: str
    change_id: str
    reason: str = ""


class TaskReviewOut(BaseModel):
    """What the review verb did. `grant` (the minted standing grant),
    `learning` (the recorded rule) and `promotion` (whether that rule has now
    proved itself) are present only for the verb that produces them.

    `grant` and `learning` stay `dict`: they are whole records owned by
    `services/permissions.py` and `services/standing.py`, and narrowing them
    from this router is how a response_model silently drops a field its owner
    added."""

    action: str
    task: Task
    grant: dict | None = None
    learning: dict | None = None
    promotion: bool | None = None
    # What accepting actually did to the repository. Present only when the
    # ticket had a change and it merged, so a caller can say "merged as <sha>"
    # instead of re-fetching the change to find out. Undeclared, it was computed
    # and then silently dropped by response_model_exclude_unset.
    merged: TaskMergeOut | None = None


@router.post("/{scope}/{task_id}/review", response_model=TaskReviewOut, response_model_exclude_unset=True)
async def review_task(scope: str, task_id: str, body: TaskReviewIn) -> dict:
    """Answer a waiting task. Returns {action, task, grant?, learning?, promotion?}.

    A ticket waits in one of two states and they ask different questions, so the
    verbs are split by which one they answer. `services/task_decision` owns what
    each does; this route owns the side effects that only a review can produce.

    FINISHED (`in_review`) — the work is done, is it right?
      accept             take the work; the task is `done`.
      send-back          return it for rework; the note becomes the instruction.

    STUCK (`blocked`) — the worker cannot proceed.
      approve            release the block; the task is `todo` and runs again.
      grant-and-continue mint the standing grant the block asked for, then release.
      mint-rule          record the standing answer as a learning IN THE TASK'S
                         SCOPE, then release (the rule IS the answer).

    EITHER
      deny               the work should not happen; the task is cancelled.
      dismiss            leave the block in place, off the review queue.

    `approve` is REFUSED on a finished ticket. It used to be accepted there and
    released it back to `todo`, which un-accepted committed work and refunded an
    attempt; the caller has to say which of accept/send-back it meant.
    """
    action = (body.action or "").strip()
    if action not in _REVIEW_ACTIONS:
        raise HTTPException(400, f"action must be one of {list(_REVIEW_ACTIONS)}")
    root = _require_root(scope)
    task = tasks.get_task(root, task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")

    request = task.get("awaiting") or {}
    note = (body.note or "").strip()
    out: dict = {"action": action}
    verb = _DECISION_VERB[action]
    question = str(request.get("ask") or request.get("question") or "")

    # The refusal that stops the 2026-08-22 defect returning. `approve` on a
    # finished ticket is ambiguous between "take it" and "release it", and the
    # release reading silently un-accepted committed work. Neither reading is
    # guessed at: the caller says which.
    if action == "approve" and task.get("status") == "in_review":
        raise HTTPException(
            409,
            "this ticket is finished and waiting on a decision, not blocked. "
            "Use action=accept to take the work, or action=send-back with a note "
            "to return it for rework.")

    if action in ("dismiss", "deny", "accept", "send-back"):
        # ACCEPTING FINISHED WORK MERGES IT (step C5 of changes-and-review).
        #
        # A supervised sweep now leaves its branch unmerged and opens a change,
        # so the trunk does not carry the work until a person takes it. That
        # makes acceptance the merge, and it has to happen BEFORE the status
        # moves: a ticket marked `done` over a branch that did not land is
        # exactly the self-reported completion this design removes. A ticket
        # with no change (work committed straight to the tree, or a file-backed
        # scope) merges nothing and proceeds unchanged, which is every ticket
        # that existed before this shipped.
        if action == "accept":
            try:
                merge = work_review.merge_for_task(
                    task_id, message=f"Merge change for {task_id}")
            except work_review.MergeRefused as e:
                raise HTTPException(
                    409,
                    f"this work cannot be merged, so it is not accepted: {e}. "
                    "Send it back with a note, or resolve the branch first.")
            if merge["merged"]:
                out["merged"] = merge
        try:
            updated = tasks.apply_decision(root, task_id, verb, note=note, question=question)
        except tasks.DecisionRefused as e:
            raise HTTPException(409, str(e))

    else:
        # approve / grant-and-continue / mint-rule all RELEASE. Everything durable
        # they create happens first, so a mint failure never releases a task on a
        # promise it did not keep.
        if action == "grant-and-continue":
            proposal = request.get("proposal") or {}
            store_uri = (body.store_uri or proposal.get("uri") or "").strip()
            verbs = body.verbs or ([proposal["verb"]] if proposal.get("verb") else [])
            if not store_uri:
                raise HTTPException(400, "grant-and-continue needs a store_uri (the block proposed none)")
            caveats: dict = {}
            if body.ttl_hours:
                caveats["expiry"] = (
                    datetime.now(timezone.utc) + timedelta(hours=float(body.ttl_hours))
                ).isoformat()
            try:
                grant = permissions.mint_grant(
                    from_app="operator",
                    to=_grantee(task, scope),
                    store_uri=store_uri,
                    verbs=verbs,
                    caveats=caveats,
                    subtree=bool(body.subtree),
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            out["grant"] = grant
            # The grant id lands on the TASK, in the evidence trail the next
            # attempt reads. No second journal: evidence already records what the
            # loop needs to know.
            tasks.update(root, task_id, evidence={
                "kind": "grant",
                "ref": f"{grant['id']}: {', '.join(grant['verbs'])} on {store_uri}"
                       + (f". {note}" if note else ""),
            })

        elif action == "mint-rule":
            rule = (body.rule or note).strip()
            if not rule:
                raise HTTPException(400, "mint-rule needs rule text")
            lscope = (body.rule_scope or "").strip() or _learnings_scope(scope)
            # The provenance source carries the ORIGIN (`review`) and the BLOCK it
            # came from, so two answers to two different blocks count as two
            # independent confirmations and re-answering one block does not.
            try:
                rec = standing.record(rule, source=f"review:{scope}:{task_id}",
                                      scope=lscope)
            except ValueError as e:
                raise HTTPException(400, str(e))
            out["learning"] = rec
            out["promotion"] = standing.is_stable(lscope, rec.get("note_id") or "")
            tasks.update(root, task_id, evidence={
                "kind": "review",
                "ref": f"rule ({lscope}): {rule}",
            })

        # What the operator actually said, for the body. A worker is handed the
        # ticket's text and nothing else, so an answer recorded anywhere else is
        # invisible to the attempt it was meant to unblock — the task would stop
        # and ask the same question again. Every releasing action produces one,
        # because every one of them IS an answer: a note, a granted permission,
        # or a rule.
        answer = note
        if action == "grant-and-continue" and out.get("grant"):
            g = out["grant"]
            granted = f"granted {', '.join(g['verbs'])} on {g['store_uri']}"
            answer = f"{granted}. {note}" if note else granted
        elif action == "mint-rule":
            answer = (body.rule or note).strip()

        # `restore_attempt`: the block was ANSWERED, not failed — a task that
        # stopped to ask a good question twice must not be parked as though it
        # had failed twice.
        #
        # EXCEPT when no attempt was ever spent. The queue triager raises an
        # `underspecified` block from a review pass, with no run claimed and no
        # attempt consumed, so refunding one here would CREDIT an attempt the
        # ticket never had. It floors at zero so nothing breaks visibly, but a
        # ticket triaged and answered twice quietly earns back two real attempts
        # against its park cap — the cap silently stops meaning what it says.
        raised_by = str((request or {}).get("raised_by") or "")
        try:
            updated = tasks.apply_decision(
                root, task_id, verb, note=answer, question=question,
                restore_attempt=not raised_by.startswith("agent:queue-triager"))
        except tasks.DecisionRefused as e:
            raise HTTPException(409, str(e))

    if updated is None:
        raise HTTPException(404, f"task not found: {task_id}")
    out["task"] = _detail(updated, scope, root)
    return out


# ---- Triage: mark duplicate -------------------------------------------------

class DuplicateOfIn(BaseModel):
    target_id: str = Field(min_length=1)
    # Defaults to this ticket's own scope, which is the common case: two takes on
    # the same work usually get filed into the same box.
    target_scope: str | None = None


class MarkDuplicateOut(BaseModel):
    """The folded ticket in its cancelled state, and a stub of the canonical one
    it now points at."""

    task: Task
    canonical: TaskLink


@router.post("/{scope}/{task_id}/duplicate-of", response_model=MarkDuplicateOut, response_model_exclude_unset=True)
async def mark_duplicate(scope: str, task_id: str, body: DuplicateOfIn) -> dict:
    """Fold this ticket into the canonical one: link, cancel, and say so.

    Triage has had accept (`/approve`) and decline (cancel) and never had this,
    even though the automatic dedup path (`task_dedup.dedup_add`) has recorded
    merges as evidence since it shipped. This is the manual entry point that
    machinery never had — the same three effects, driven by a person who can see
    that two tickets are one piece of work when the embeddings could not.

    The link is `relates_to`, not `depends_on`: a duplicate does not gate the
    canonical ticket's readiness, and putting it in the scheduling graph would
    make cancelling it look like unblocking work.
    """
    root = _require_root(scope)
    target_scope = (body.target_scope or "").strip() or scope
    target_root = _require_root(target_scope)
    target_id = (body.target_id or "").strip()
    if target_id == task_id and target_scope == scope:
        raise HTTPException(400, "a ticket cannot be a duplicate of itself")
    dup = tasks.get_task(root, task_id)
    if dup is None:
        raise HTTPException(404, f"task not found: {task_id}")
    canonical = tasks.get_task(target_root, target_id)
    if canonical is None:
        raise HTTPException(404, f"task not found: {target_id}")

    where = "" if target_scope == scope else f" in {target_scope}"
    ref = f"duplicate of {target_id}{where}: {canonical.get('title') or ''}".strip()
    rel = list(dup.get("relates_to") or [])
    if target_id not in rel:
        rel.append(target_id)
    # One write: the link, the cancellation and the record of why are a single
    # act, and a ticket cancelled without the link would be a dead end.
    updated = tasks.update(root, task_id, status="cancelled", relates_to=rel,
                           blocked_reason=ref,
                           evidence={"kind": "duplicate-of", "ref": ref})
    if updated is None:
        raise HTTPException(404, f"task not found: {task_id}")

    # The back-link only when both tickets share a store. Cross-scope, this would
    # be a second write to a second tree that can fail on its own, leaving one
    # ticket cancelled and the other unlinked with nothing recording the gap.
    # The forward link still resolves for display: `_resolve_links` scans scopes.
    if target_scope == scope:
        canon_rel = list(canonical.get("relates_to") or [])
        if task_id not in canon_rel:
            canon_rel.append(task_id)
        tasks.update(root, target_id, relates_to=canon_rel,
                     evidence={"kind": "duplicate-from",
                               "ref": f"{task_id}: {dup.get('title') or ''}".strip()})
        canonical = tasks.get_task(target_root, target_id) or canonical

    return {"task": _detail(updated, scope, root),
            "canonical": _link_stub(canonical, target_scope)}


# ---- Promotion (prose that proved itself) ----------------------------------
# ---- Run now ---------------------------------------------------------------
# Chat/UI "run this task now": open a Job and launch a detached single-task sweep
# (project_ralph.sh --task-id) so work starts in seconds instead of waiting for
# the ~20-minute cron. The sweep owns claim/lease/fence/acceptance/receipt/budget
# admission; --force marks a deliberate operator action that legitimately bypasses
# the paused-autonomy flag, exactly like the existing per-project run endpoint.


def _sweep_dir_str(scope: str) -> str | None:
    """The DIR string the sweep keys its per-tree flock on, matching how
    project_ralph.sh resolves it: a project sweep reads the registry `dir` string
    verbatim; a --dir sweep uses the path we pass. Returns None for an unknown
    scope. Used only for the busy pre-check; a mismatch degrades to launching and
    letting the sweep's own flock abort cleanly, never to a wrong answer."""
    if scope.startswith("project:"):
        slug = scope.split(":", 1)[1]
        try:
            reg = json.loads(_PROJECTS_REGISTRY.read_text())
            d = ((reg.get("projects") or {}).get(slug) or {}).get("dir")
            if d:
                return str(d)
        except (json.JSONDecodeError, OSError):
            pass
    root = _root_for_scope(scope)
    return str(root) if root is not None else None


def _tree_busy(dir_str: str) -> bool:
    """True when a sweep already holds this tree's flock. Mirrors the exact lock
    path project_ralph.sh derives (md5 of `echo "$DIR"`, first 12 hex chars) and
    probes it non-blocking. The probe releases immediately; the launched sweep
    re-locks and aborts cleanly if we lose the (tiny) race."""
    digest = hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]
    lock = Path(f"/tmp/workshop-project-ralph-{digest}.lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def scope_is_runnable(scope: str | None) -> bool:
    """Can a worker actually be launched against this scope?

    `launch_task_run` accepts development / project / agent / build and rejects
    everything else with a 400. A general chat resolves to `general`, which is
    NOT runnable — so "file and run" from a general chat could only ever file.
    Exposed as a predicate so the UI can say that BEFORE the operator commits,
    rather than reporting it as a footnote on a success message afterwards.

    `workshop` is not runnable, and that is the definition rather than an
    omission (ADR 0014 §4.1): it is the root organising layer — notes, the
    inbox, triage, routing — and it has no code tree for a sweep to run in. A
    workshop ticket that turns out to need code is re-scoped to whatever owns
    that code, which is the triage step and the root scope's actual job.

    `development` is runnable only when developer mode is on, because its
    working tree IS the platform repo; the resolution itself is gated in
    `task_scope.root_for_context`, so this answers False by consequence rather
    than by naming the flag a second time."""
    if not scope:
        return False
    if scope == "development":
        return _root_for_scope("development") is not None
    return scope.startswith("project:") or scope.startswith("agent:")


def _queue_runner_covers(dir_str: str) -> bool:
    """True when a resident queue runner currently holds this tree's runner lock.

    Mirrors the lock path scripts/queue_runner.sh derives (md5 of `echo "$DIR"`,
    first 12 hex chars). A held lock means a runner process is alive for the tree;
    an unheld one means nothing is polling it right now."""
    digest = hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]
    lock = Path(f"/tmp/workshop-queue-runner-{digest}.lock")
    if not lock.exists():
        return False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False  # we took it, so no runner holds it
    except OSError:
        return True
    finally:
        os.close(fd)


def scope_pickup(scope: str | None) -> dict:
    """How work filed to `scope` actually gets run: {scope, runnable, automatic, why}.

    Two different questions the UI has been conflating into one:

      runnable   a worker CAN be launched here by hand ("Run now", "File and run").
      automatic  something picks the ticket up WITHOUT the operator pressing anything.

    `automatic` is false far more often than it looks. One resident queue runner
    covers the platform repo and nothing else; the cron dispatcher fires only
    scopes carrying a `loop` trigger (projects) or a `cron`/`loop` trigger
    (agents), and today zero registered projects carry one; and the autonomy
    brake disables both paths wholesale. So a ticket filed to `project:<slug>`
    sits until someone presses Run. That is a fine answer, but the operator has
    to be told it BEFORE filing, which is what `why` is for.
    """
    if not scope_is_runnable(scope):
        why = task_scope.dev_mode_refusal(scope) or (
            "the workshop scope is the root inbox: work is filed and triaged here, "
            "then routed to the scope that owns the code. Re-scope this ticket to "
            "run it." if scope == "workshop" else
            "this scope cannot run tickets; file it somewhere runnable")
        return {"scope": scope, "runnable": False, "automatic": False, "why": why}

    from services import autonomy_override

    try:
        if autonomy_override.is_engaged():
            reason = (autonomy_override.read() or {}).get("reason") or "no reason recorded"
            return {"scope": scope, "runnable": True, "automatic": False,
                    "why": f"the autonomy brake is engaged, so nothing runs unattended ({reason})"}
    except Exception:
        pass

    if scope == "development":
        if _queue_runner_covers(str(_WORKSHOP_ROOT)):
            return {"scope": scope, "runnable": True, "automatic": True, "why": ""}
        return {"scope": scope, "runnable": True, "automatic": False,
                "why": "no queue runner is polling the platform repo right now"}

    if scope.startswith("project:"):
        slug = scope.split(":", 1)[1]
        try:
            reg = json.loads(_PROJECTS_REGISTRY.read_text())
            autonomy = ((reg.get("projects") or {}).get(slug) or {}).get("autonomy")
        except (json.JSONDecodeError, OSError):
            autonomy = None
        # The STORED trigger list, the same one the dispatcher loops over, and
        # the same `cron or loop` test the agent branch below applies. This used
        # to ask `autonomy.mode`, a second encoding that had no value for
        # `scheduled`: a project the operator had just put on a cron stored
        # mode="manual", so this told them "set its autonomy to continuous"
        # about a scope the dispatcher was already sweeping on a cadence.
        kinds = {(t or {}).get("kind") for t in ((autonomy or {}).get("triggers") or [])}
        if kinds & {"cron", "loop"}:
            return {"scope": scope, "runnable": True, "automatic": True, "why": ""}
        return {"scope": scope, "runnable": True, "automatic": False,
                "why": f"no runner covers {slug}; set its autonomy to scheduled or "
                       "continuous, or press Run"}

    if scope.startswith("agent:"):
        slug = scope.split(":", 1)[1]
        kinds = set()
        try:
            from services import agents_registry
            for a in agents_registry.list_agents():
                if a.get("slug") == slug or a.get("id") == slug or a.get("id") == f"agent:{slug}":
                    kinds = {(t or {}).get("kind") for t in (a.get("triggers") or [])}
                    break
        except Exception:
            kinds = set()
        if kinds & {"cron", "loop"}:
            return {"scope": scope, "runnable": True, "automatic": True, "why": ""}
        return {"scope": scope, "runnable": True, "automatic": False,
                "why": f"{slug} has no cron or loop trigger, so the dispatcher never fires it"}

    return {"scope": scope, "runnable": True, "automatic": False,
            "why": "no runner sweeps build agent trees; press Run to work this ticket"}


def launch_task_run(scope: str, task_id: str, *, chat_id: str | None = None) -> dict:
    """Open a manual Job and Popen a detached single-task sweep against `scope`
    for exactly `task_id`. Shared by the /tasks run endpoint and the chat
    spawn-and-run path so the Popen pattern lives in one place.

    Scope -> launcher (dir resolved via task_scope.root_for_context):
      project:<slug> -> project_ralph.sh <slug> --force --job-id --task-id
      development    -> ... --dir <platform repo> --force --job-id --task-id
      agent:<slug>   -> ... --dir <scope root> --assignee agent:<slug> --force ...

    A ticket that is BROKEN is refused HERE rather than in the route, because
    the chat "file and run" path calls this function directly and so used to
    launch a run for a ticket the /run endpoint would have rejected. One gate,
    every caller.

    CHOKEPOINT 2 of the developer-mode gate (ADR 0014), for the same reason: this
    is the single Popen for ticket runs, and the chat path reaches it without
    passing the route. `_require_root` has already refused a scope that names the
    platform repo, and `assert_run_target` re-asks the question of the resolved
    directory — belt and braces, because the cost is one comparison and the
    failure mode is an autonomous sweep rewriting the running application.

    Raises HTTPException(403) when the target is the platform repo and developer
    mode is off, (409) when a sweep already holds the tree, (400) on an unknown
    scope or a broken ticket, (404) when the ticket is gone, (500) when the
    driver is missing. Returns {ok, job_id, scope, task_id}."""
    from wsplatform import dev_mode

    root = _require_root(scope)
    try:
        dev_mode.assert_run_target(root, f"a {scope} ticket run")
    except dev_mode.DevModeRefusal as e:
        raise HTTPException(e.status, e.message)
    task = tasks.get_task(root, task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    # A draft is a SUGGESTION the operator has not agreed to. `next_ready` already
    # skips it, so no sweep can pick one up; this closes the other door, the
    # explicit "run now". Without it the approval model would hold for autonomous
    # work and quietly not hold for a button.
    if task.get("status") == "draft":
        raise HTTPException(409, "this ticket is a draft; approve it before running it")
    known = {t["id"] for t in tasks.list_tasks(root)}
    broken = ticket_readiness.blocking(ticket_readiness.check(task, known_ids=known))
    if broken:
        raise HTTPException(400, "; ".join(m["why"] for m in broken))
    script = _SCRIPTS_ROOT / "project_ralph.sh"
    if not script.exists():
        raise HTTPException(500, f"driver missing: {script}")

    dir_str = _sweep_dir_str(scope)
    if dir_str and _tree_busy(dir_str):
        raise HTTPException(409, f"a sweep is already running against {scope}; try again when it finishes")

    # `chat_id` is the run's ORIGIN, and it was the one link in the job record
    # that nothing ever wrote: 0 of 2753 job records carry one, measured
    # 2026-08-12, even though the chat's "file and run" path calls this function
    # with the thread id sitting in its own scope. Without it a run cannot say
    # which conversation asked for it, which is the link the run page's "edit in
    # the original chat" action needs.
    start_kwargs: dict = {"trigger": "manual", "status": "running", "task_id": task_id}
    if chat_id:
        start_kwargs["chat_id"] = chat_id
    if scope.startswith("project:"):
        slug = scope.split(":", 1)[1]
        start_kwargs["project_slug"] = slug
        cmd_tail = [slug]
    elif scope == "development":
        start_kwargs["project_slug"] = "development"
        cmd_tail = ["--dir", str(root)]
    elif scope.startswith("agent:"):
        slug = scope.split(":", 1)[1]
        start_kwargs["agent_id"] = f"agent:{slug}"
        cmd_tail = ["--dir", str(root), "--assignee", f"agent:{slug}"]
    else:
        raise HTTPException(400, f"scope not runnable: {scope}")

    job = jobs.start(**start_kwargs)
    try:
        log_dir = root / ".runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        logf: object = open(log_dir / "ralph.log", "a")  # noqa: SIM115 (handed to child)
    except OSError:
        logf = subprocess.DEVNULL
    cmd = ["bash", str(script), *cmd_tail, "--force", "--job-id", job["id"], "--task-id", task_id]
    # S2 execution boundary (2026-08-21, work-loop-core-design-2026-08-21.md).
    # An operator-triggered run is attended by definition: this function is the
    # single Popen behind Run-now and the chat file-and-run path, both explicit
    # operator actions. So authorize the headless worker to write. ralph.sh reads
    # this flag to pass --permission-mode bypassPermissions; without it the worker
    # silently auto-denies every write (measured 2026-08-14). AUTONOMOUS background
    # sweeps do NOT come through here -- they are launched by the shell dispatchers
    # (dispatch_triggers.sh / queue_runner.sh) -- so they stay write-disabled until
    # S3 enables them deliberately. The in-run boundary is ralph's irreversible-
    # action deny gate (push/publish/delete/sudo) plus git-revertability.
    env = dict(os.environ)
    env["WORKSHOP_UNATTENDED_WRITES"] = "1"
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_WORKSHOP_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    finally:
        # Popen dups the fd into the child, so the parent's copy is ours to
        # release. Without this, every run launched here leaked one descriptor
        # for the life of the backend. DEVNULL is a sentinel int, not a file.
        if hasattr(logf, "close"):
            logf.close()
    # The Job record has to exist before the child can write to it (the id is on
    # its command line), so the pid can only be attached after the Popen. Attach
    # it: without a pid, reconcile_stuck cannot probe the process and degrades to
    # a two-hour age ceiling, which is how manual and chat-launched runs came to
    # sit at `running` for hours after they died.
    try:
        jobs.record_pid(job["id"], proc.pid)
    except OSError:
        pass
    return {"ok": True, "job_id": job["id"], "scope": scope, "task_id": task_id}


class TaskApproveIn(BaseModel):
    # Approve and immediately run, for the case where the operator read the draft
    # and wants it started. Two clicks collapsed into one, not a different rule.
    run: bool = False


class TaskRunOut(BaseModel):
    """A launched sweep: the Job opened for it and what it was launched against.

    `ok` is ADR 0023 clause 7 debt; see `routes/api_models.OkOut`."""

    ok: bool
    job_id: str
    scope: str
    task_id: str


class TaskApproveOut(BaseModel):
    """The approved ticket, the PRE-approval readiness report the operator is
    being told about (advisory, it never refuses the approval), and the run when
    `run: true` asked for one."""

    task: Task
    readiness: Readiness
    run: TaskRunOut | None = None


@router.post("/{scope}/{task_id}/approve", response_model=TaskApproveOut, response_model_exclude_unset=True)
async def approve_task(scope: str, task_id: str, body: TaskApproveIn | None = None) -> dict:
    """Approve a draft: the operator agrees this work should happen. `draft` ->
    `todo`, which is the only thing that makes a ticket eligible to be run.

    The readiness report rides along in the response but does NOT refuse the
    approval. The operator is the authority here: the system's job is to say what
    the ticket does not answer, and the operator's is to decide whether that
    matters. Refusing their own approval would make the checklist an obstacle
    instead of advice, which is how the last set of hygiene signals got ignored.
    """
    root = _require_root(scope)
    task = tasks.get_task(root, task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    if task.get("status") not in ("draft", "todo"):
        raise HTTPException(409,
                            f"only a draft can be approved; this ticket is {task.get('status')}")

    readiness = ticket_readiness.check(
        task, known_ids={t["id"] for t in tasks.list_tasks(root)})
    updated = task
    if task.get("status") == "draft":
        tasks.update(root, task_id, evidence={
            "kind": "approval",
            "ref": "approved by the operator" if readiness["ok"]
                   else f"approved by the operator ({ticket_readiness.summary(readiness)})",
        })
        updated = tasks.update(root, task_id, status="todo") or task
        # Approval is also the PUBLISH gate: a ticket becomes real work and
        # reaches the external tracker at the same moment, for the same reason.
        # This is why there is no "should this go to the tracker?" setting. Never
        # raises — a tracker that is down must not block an approval.
        from services import tracker_sync
        published = tracker_sync.publish(scope, root, task_id)
        if published is not None:
            updated = published

    # `readiness` at the top level is the PRE-approval report the operator is being
    # told about; `task.readiness` is the post-write one. They agree in every case
    # but a concurrent edit, and the top-level key is the one this route promised.
    out: dict = {"task": _detail(updated, scope, root),
                 "readiness": readiness}
    if body and body.run:
        out["run"] = launch_task_run(scope, task_id)
    return out


@router.post("/{scope}/{task_id}/run", response_model=TaskRunOut, response_model_exclude_unset=True)
async def run_task(scope: str, task_id: str) -> dict:
    """Run exactly this task now. 409 if a sweep already holds the tree. Single
    operator: no confirmation gate, no allowlist.

    A ticket that is BROKEN — no title, or a dependency that does not resolve —
    is refused here rather than burning a run to discover it. Merely
    underspecified tickets are not refused: measured against the live stores when
    this shipped, 0 of 40 open tickets passed every readiness check, so a full
    gate would have stopped all work rather than improving any of it. The rest of
    the check is reported, not enforced (services/ticket_readiness).

    The scope/404/readiness checks all live in `launch_task_run` now, so the
    chat's "file and run" path cannot skip them."""
    return launch_task_run(scope, task_id)
