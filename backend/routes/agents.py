import asyncio
import logging

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from routes.api_models import (AgentLimits, AgentTrigger, Job, OkOut, QueueCounts,
                               AwaitingRow, ToolsOut)
from routes import autonomy_models as am
from services import (agents_registry, build_agents, dev_servers, jobs, makers,
                      presence, tasks, tool_registry, triggers, work_driver)
from services import agent_authority
from wsplatform import paths
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE

log = logging.getLogger(__name__)
router = APIRouter()

# ---- Response models -------------------------------------------------------
# ADR 0023 clause 6. Every field below was read off its emitter --
# `agents_registry._record_from_def`, `build_agents.create_agent`,
# `dev_servers.status` -- because a `response_model` FILTERS: an undeclared key
# is dropped from the live response with no error anywhere.
#
# THE TWO AUTONOMY VOCABULARIES, and why one field name carries both.
#
#   Mode   `reactive | supervised | autonomous`  (triggers.AUTONOMY_MODES)
#          May the dispatcher fire this agent in the background at all, and does
#          a finished run commit or land in review? Stored in definition
#          frontmatter under `autonomy:` and read through
#          `agents_registry._coerce_autonomy`, which is TOTAL: anything not in
#          the enum (unset, a typo, a preset word) reads as `reactive`.
#   Preset `off | on-demand | scheduled | continuous`  (triggers.PRESETS)
#          The trigger CADENCE, derived from the trigger list by
#          `triggers.triggers_to_preset`. Not stored on an agent at all.
#
# Both are closed and total, so both are typed as Literals below and neither can
# reach the wire holding the other's value. What they DO share is a name:
#
#   * `Agent.autonomy`      is the MODE   (PUT/POST return the registry record)
#   * `RosterAgent.autonomy` and `AgentDetail.autonomy` are the PRESET
#     (`GET /` and `GET /{id}` overwrite the record's field on the way out), and
#     the MODE is served beside them as `autonomy_mode`.
#
# So one resource serves `autonomy` as two different enums depending on which of
# its four routes you asked. Measured 2026-08-17: 10 agent definition files
# exist and 0 declare `autonomy:`, so every agent currently serves
# `autonomy_mode: "reactive"`, and the frontend already models the two as
# separate fields (`Preset` / `AutonomyMode`, pages/Agents.tsx). Renaming either
# is a wire change that must land with its frontend edit, so this pass types the
# collision truthfully rather than papering over it.

AutonomyMode = Literal["reactive", "supervised", "autonomous"]
AutonomyPreset = Literal["off", "on-demand", "scheduled", "continuous"]


class Agent(BaseModel):
    """An agent as the registry holds it: `agents_registry._record_from_def`,
    plus the execution state `_merge_build_state` folds in for a maker.

    `memory`, `limits` and a trigger stay loosely typed on purpose -- they are
    whatever the definition's YAML frontmatter carries, and narrowing them here
    would drop keys the definition author wrote."""

    id: str
    slug: str
    name: str
    purpose: str = ""
    capabilities: list[str] = []
    triggers: list[AgentTrigger] = []
    limits: AgentLimits | None = None
    role: str | None = None
    # May only NARROW what the role permits; null = no request = the role's set.
    tools: list[str] | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    mentionable: bool = False
    delegatable: bool = False
    autonomy: AutonomyMode = "reactive"
    memory: Any = None
    # Fork provenance `<template-slug>@<version>`: a label, not a link.
    template: str | None = None
    project_slug: str | None = None
    scope: str | None = None
    definition: str = ""
    definition_path: str | None = None
    retired: bool = False
    source: str | None = None
    # ---- merged from build_agents.json for a maker ----
    state: str | None = None
    claude_session: str | None = None
    last_active: str | None = None
    created: str | None = None
    cwd: str | None = None
    branch_slug: str | None = None


# ---- The maker agent's live state ------------------------------------------
#
# EVERY FIELD BELOW IS READ AT REQUEST TIME AND STORED NOWHERE. There is no
# maker store: the arm level is `services/scope_execution`, residency is the
# flock `services/work_driver` probes, activity is `services/presence`, and the
# runs are `services/jobs`. This is four existing facts wearing one face, which
# is the whole design -- see `services/makers.py`'s header.

MakerActivity = Literal["absent", "present", "working"]


class MakerSummary(BaseModel):
    """What the roster needs to answer "what is doing the tickets, and is it".

    `arm_level` IS the scope's execution mode, not a copy of it. Setting it
    through `POST /agents/{id}/maker/execution` writes `scope-execution.json`
    and nothing else; there is no per-agent on/off beside it that could disagree.

    `activity` is deliberately three values and not four. `present` covers both
    "resident, idling on the poll" and "resident, sweeping, has not claimed a
    ticket yet" because the presence channel carries no structural difference
    between them -- only an activity string meant for a human. `activity_detail`
    says exactly that rather than the API picking one and being wrong half the
    time."""

    scope: str
    dir: str | None = None
    # EVERY maker declaring this scope, in slug order, including this one. The
    # registry allows several (`SINGLETON_ROLES` exempts `maker`), so a list of
    # two is a legal state the UI has to be able to show -- not something the
    # backend quietly resolves. It used to be resolved by filename sort order,
    # which is how an app's `<slug>-builder-2` became the maker the operator saw.
    scope_makers: list[str] = []
    arm_level: AutonomyMode
    armed: bool
    # How far auto-execution may advance: `in_review` (supervised) or `done`
    # (autonomous). null when reactive. The supervised ceiling is unchanged.
    ceiling: str | None = None
    # The global brake, above every scope. A maker can be armed and resident and
    # still do nothing because this is engaged, and the UI must be able to say so.
    global_override_engaged: bool = False
    runner_resident: bool
    activity: MakerActivity
    activity_detail: str
    ticket_id: str | None = None
    ticket_title: str | None = None
    ticket_status: str | None = None


class MakerSession(BaseModel):
    """One live session working this scope, from the presence directory."""

    session_id: str
    session_kind: str
    principal: str | None = None
    activity: str = ""
    scope: str | None = None
    subject_kind: str
    subject_id: str | None = None
    subject_label: str | None = None
    subject_status: str | None = None
    pid: int | None = None
    hostname: str | None = None
    last_beat_at: str | None = None
    expires_at: str | None = None


class MakerState(MakerSummary):
    """The full block: the summary, plus who is here and what has run.

    `runner` is the SAME `QueueRunnerState` the Settings drawer reads from
    `GET /queue/runner` -- the same helper, not a second rendering of it."""

    slug: str | None = None
    name: str | None = None
    label: str | None = None
    # False when this scope is no longer a tree a runner may be pointed at (the
    # project was removed, or developer mode went off). The maker record is kept
    # rather than deleted -- its definition may have been edited -- so the record
    # has to be able to say that its tree is gone.
    runnable: bool = True
    runner: am.QueueRunnerState | None = None
    sessions: list[MakerSession] = []
    recent_runs: list[Job] = []


class MakerExecutionIn(BaseModel):
    """The arm level, written straight through to `scope_execution`."""

    mode: AutonomyMode


class RosterAgent(BaseModel):
    """The roster projection. Deliberately NOT `Agent`: the list route hand-picks
    a narrower set (no `definition` body, no `definition_path`) because the
    roster renders cards, and shipping every agent's full markdown to draw a
    list is the kind of payload ADR 0023 exists to make visible.

    `autonomy` here is the PRESET; `autonomy_mode` is the MODE. See the block
    comment above."""

    id: str | None = None
    slug: str | None = None
    name: str | None = None
    purpose: str | None = None
    triggers: list[AgentTrigger] = []
    limits: AgentLimits | None = None
    autonomy: AutonomyPreset
    autonomy_mode: AutonomyMode
    role: str | None = None
    mentionable: bool = False
    delegatable: bool = False
    capabilities: list[str] = []
    memory: Any = None
    project_slug: str | None = None
    scope: str | None = None
    state: str | None = None
    claude_session: str | None = None
    created_at: str | None = None
    last_active: str | None = None
    source: str | None = None
    # Present ONLY on a maker with a tree, null on every other row. This is what
    # makes "what is doing the tickets" answerable from the list itself, which
    # is where the operator looked for it and did not find it.
    maker: MakerSummary | None = None


class ActivationLink(BaseModel):
    """One condition in the chain that decides whether an agent runs."""

    id: str
    label: str
    ok: bool
    detail: str


class Activation(BaseModel):
    links: list[ActivationLink]
    blocked_by: str | None = None
    runs_unattended: bool
    ever_ran: bool
    last_active: str | None = None


class Authority(BaseModel):
    """What this role may write and the tools that follow from it. A declaration
    rendered on the page, never a guarantee."""

    writes: list[str]
    describes: str
    tools: list[str]


class AgentDetail(Agent):
    """`GET /agents/{id}`: the registry record, enriched.

    Inherits every `Agent` field and then REDECLARES `autonomy` as the preset,
    because that is what the handler puts there. That override is the collision
    documented in the block comment above, made visible in the schema instead of
    hidden in a dict spread."""

    autonomy: AutonomyPreset
    autonomy_mode: AutonomyMode
    queue: QueueCounts
    awaiting: list[AwaitingRow] = []
    linked_ticket_ids: list[str] = []
    # A ticket is addressed by (scope, id); every id above came from ONE store,
    # so the scope rides along rather than being re-derived by the client.
    linked_ticket_scope: str | None = None
    linked_run_ids: list[str] = []
    linked_chat_ids: list[str] = []
    activation: Activation
    authority: Authority


class AgentTemplate(BaseModel):
    """A shipped template. A file, not a registry member: nothing here is
    registered or dispatched until it is forked."""

    template: str
    template_version: int
    description: str = ""
    name: str
    role: str | None = None
    purpose: str = ""
    triggers: list[AgentTrigger] = []
    definition: str = ""


class AgentOverrides(BaseModel):
    """Per-member changes applied to a template when a group is adopted. An open
    bag of any agent-definition field, so the two the shipped groups actually
    use are declared as a floor and `extra="allow"` keeps the rest. The `tended`
    group overrides `triggers` on note-triager and nothing on memory-keeper."""

    model_config = ConfigDict(extra="allow")

    triggers: list[AgentTrigger] | None = None
    limits: AgentLimits | None = None


class AgentGroupMember(BaseModel):
    """One member of a template group: which template to instantiate, under
    which slug if not the template's own, and what to change about it."""

    model_config = ConfigDict(extra="allow")

    template: str
    slug: str | None = None
    overrides: AgentOverrides | None = None


class ScopeDefaults(BaseModel):
    """The non-agent half of adopting a group: what the SCOPE takes on, as
    opposed to what gets instantiated. Both shipped groups set `autonomy` and
    `depth` and nothing else."""

    model_config = ConfigDict(extra="allow")

    autonomy: str | None = None
    depth: str | None = None


class AgentGroup(BaseModel):
    """A template GROUP: a member list WITH per-member overrides plus the
    `scope_defaults` the adopting scope takes on. A member is
    `{template, slug?, overrides?}`; see `AgentGroupMember` above."""

    name: str
    # The picker's one-line explanation of what adopting this group does. It is
    # on both shipped group files and a `response_model` would have DROPPED it:
    # found by validating every live record through this model before shipping,
    # which is the check that makes an under-declared model visible.
    description: str = ""
    members: list[AgentGroupMember] = []
    scope_defaults: ScopeDefaults | None = None


class AgentTemplatesOut(BaseModel):
    templates: list[AgentTemplate]
    groups: list[AgentGroup]


class SkippedMember(BaseModel):
    """A group member that was not forked, with the incumbent named. Adopting a
    group twice is a no-op for a singleton role, and this is how it says so."""

    template: str
    reason: str


class AdoptGroupOut(BaseModel):
    group: str | None = None
    scope_defaults: ScopeDefaults | None = None
    adopted: list[Agent] = []
    skipped: list[SkippedMember] = []
    # Whether the group's non-agent half (its autonomy preset + the roster)
    # reached a project record. False for a user-scope adoption, which has none.
    scope_applied: bool = False


class NativeSubagent(BaseModel):
    """A Claude Code subagent this instance can DELEGATE to. Same markdown and
    same parser as a roster agent; the difference is lifecycle, not schema."""

    slug: str
    name: str
    description: str = ""
    purpose: str = ""
    role: str | None = None
    scope: str = ""
    tools: list[str] = []
    unrestricted_tools: bool = False
    delegatable: bool = False
    mentionable: bool = False
    # Names the surface this agent belongs to when it is not the Workshop's.
    foreign: str | None = None
    definition: str = ""
    definition_path: str | None = None
    dispatch: str
    source: str


class DelegatesOut(BaseModel):
    delegates: list[NativeSubagent]


class BuildAgent(BaseModel):
    """A maker's execution state, out of `.runtime/build_agents.json`. Distinct
    from `Agent`, which is what a DEFINITION FILE declares: running is not a way
    to become an agent."""

    slug: str
    name: str
    purpose: str = ""
    kind: str | None = None
    cwd: str | None = None
    project_slug: str | None = None
    branch_slug: str | None = None
    state: str | None = None
    created: str | None = None
    last_active: str | None = None
    claude_session: str | None = None
    owner_id: int | None = None


class RetireAgentOut(BaseModel):
    ok: bool
    retired: str


class AgentJobsOut(BaseModel):
    """Not the ADR 0023 collection envelope: the key is `jobs`, not
    `items`, and it is what the agent page reads."""

    agent_id: str
    jobs: list[Job]
    count: int


class AgentRunsOut(BaseModel):
    """The per-agent activity feed: the run history plus heartbeat freshness.

    `heartbeat` is this agent's PRESENCE record (`services/presence.py`, step
    B4), which is what the heartbeats channel became; it stays an open dict
    because the channel carries a writer-supplied `meta` tail. `heartbeat_age_s`
    is a float of seconds, which ADR 0023 clause 4 says should be `<thing>_ms`
    as an integer; unchanged for the same reason as every other rename here."""

    agent_id: str
    heartbeat: dict | None = None
    heartbeat_age_s: float | None = None
    liveness: str
    runs: list[Job]
    runs_count: int


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    persona: str = ""
    human: str = ""
    role: str | None = None          # closed enum: maker|reviewer|triager|keeper|broker
    autonomy: str | None = None      # reactive | supervised | autonomous (null = reactive)
    project_slug: str | None = None  # links this agent into a project scope


# ---- Templates + groups (P4) -----------------------------------------------
# Declared BEFORE `GET /{agent_id}` so a literal path never has to compete with
# the slug wildcard.

class FromTemplateIn(BaseModel):
    template: str
    slug: str | None = None       # minted from the template's name when absent
    project: str | None = None    # project scope; absent = the user scope
    # The same open bag of agent-definition fields a group member carries, so it
    # reuses that model rather than restating it as `dict`.
    overrides: AgentOverrides | None = None


class AdoptGroupIn(BaseModel):
    group: str
    project: str | None = None


@router.get("/templates", response_model=AgentTemplatesOut, response_model_exclude_unset=True)
async def list_agent_templates():
    """The shipped agent templates and template groups. Templates are files, not
    registry members: nothing here is registered or dispatched until it is forked
    (POST /agents/from-template)."""
    return {
        "templates": agents_registry.list_templates(),
        "groups": agents_registry.list_groups(),
    }


@router.post("/from-template", response_model=Agent, response_model_exclude_unset=True)
async def create_agent_from_template(body: FromTemplateIn):
    """Fork a template into a real agent. The copy is independent — the only
    trace of its origin is a `template: <slug>@<version>` stamp in frontmatter.
    Nothing syncs afterward, by design (roles report 5.3)."""
    tpl = agents_registry.get_template(body.template)
    if tpl is None:
        raise HTTPException(404, f"unknown template: {body.template}")
    slug = (body.slug or "").strip().lower() or agents_registry.mint_slug(
        f"{body.project}-{tpl['template']}" if body.project else tpl["template"])
    try:
        return agents_registry.fork_from_template(
            body.template, slug, body.project, body.overrides)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/adopt-group", response_model=AdoptGroupOut, response_model_exclude_unset=True)
async def adopt_agent_group(body: AdoptGroupIn):
    """Adopt a template group into a scope: fork each member with its overrides,
    then apply the group's `scope_defaults` to the project's autonomy record.
    Adopting twice is a no-op for members that would violate the one-per-(scope,
    role) constraint; they come back in `skipped` with the incumbent named."""
    try:
        res = agents_registry.adopt_group(body.group, body.project)
    except ValueError as e:
        raise HTTPException(404, str(e))
    res["scope_applied"] = _apply_group_to_project(
        body.project, res.get("scope_defaults") or {},
        [a["slug"] for a in res.get("adopted") or []])
    return res


def _apply_group_to_project(project_slug: str | None, defaults: dict,
                            adopted: list[str]) -> bool:
    """Land a group's non-agent half on the project record: its `scope_defaults`
    (the autonomy preset) and the adopted slugs on the scope's resident roster.
    A group's `scope_defaults.depth`, if a template still carries one, is ignored:
    project autonomy has no depth any more, because no sweep ever read it.
    A group carries settings, not just a member list (ask D4); this is where the
    settings half goes. No project named, nothing to apply (a user-scope adoption
    has no project record to write).

    The autonomy record is the same shape PUT /projects/{slug}/autonomy writes, so
    the preset expands into its trigger list exactly as it does there — a group
    must not be able to produce a project state the autonomy page cannot read."""
    if not project_slug or (not defaults and not adopted):
        return False
    preset = defaults.get("autonomy")
    if preset is not None and preset not in triggers.PRESETS:
        raise HTTPException(400, f"unknown autonomy preset in group: {preset}")
    from routes import projects as projects_route
    data = projects_route._load_registry()
    proj = (data.get("projects") or {}).get(project_slug)
    if not proj:
        raise HTTPException(404, f"project not found: {project_slug}")
    autonomy = dict(proj.get("autonomy") or {})
    if preset is not None:
        norm = triggers.preset_to_triggers(preset, limits=autonomy.get("limits") or None)
        autonomy.update({
            "preset": preset,
            "triggers": norm,
        })
    proj["autonomy"] = autonomy
    roster = [s for s in (proj.get("agents") or []) if isinstance(s, str)]
    proj["agents"] = roster + [s for s in adopted if s not in roster]
    projects_route._save_registry(data)
    return True


def _drop_from_project_roster(rec: dict) -> None:
    """Take a retired agent off its project's resident roster, so the roster is
    the scope's live residents rather than everyone who ever lived here."""
    slug, project_slug = rec.get("slug"), rec.get("project_slug")
    if not slug or not project_slug:
        return
    from routes import projects as projects_route
    data = projects_route._load_registry()
    proj = (data.get("projects") or {}).get(project_slug)
    if not proj or slug not in (proj.get("agents") or []):
        return
    proj["agents"] = [s for s in proj["agents"] if s != slug]
    projects_route._save_registry(data)


@router.get("/", response_model=list[RosterAgent], response_model_exclude_unset=True)
async def list_agents():
    """One unified roster: every Agent across both scopes, sourced from the
    agents registry (definition files + a maker's execution state).
    Per-channel bridge agents are excluded by the registry."""
    reg = agents_registry.list_agents()

    # The presence channel is read ONCE for the whole roster, not once per maker:
    # `directory()` globs a directory and joins each row to its ticket, so N
    # makers asking separately would be N scans of the same files. Everything
    # else `makers.summary` needs is a single flock probe and a dict lookup.
    sessions = presence.directory() if any(a.get("role") == "maker" for a in reg) else []

    out = [agents_registry.roster_projection(a, sessions=sessions, roster=reg) for a in reg]
    out.sort(key=lambda a: a.get("last_active") or a.get("created_at") or "", reverse=True)
    return out


@router.get("/delegates", response_model=DelegatesOut, response_model_exclude_unset=True)
async def list_delegates():
    """Claude Code subagents this instance can DELEGATE to.

    Deliberately not part of the roster above, and the difference is lifecycle,
    not schema — same markdown, same frontmatter, same parser. A roster agent is
    DISPATCHED: it has triggers, it can run on a schedule, it is a chat scope. A
    delegate only ever runs because another agent called it, so there is no
    trigger to enable and no autonomy of its own to grant. Merging the two lists
    would put rows in the roster with every control permanently inert.

    This answers the question the roster cannot: who can call whom.
    """
    return {"delegates": agents_registry.native_subagents()}


@router.get("/tools", response_model=ToolsOut, response_model_exclude_unset=True)
async def list_tools():
    """The tools an agent's allowlist may name, with what each one is.

    Served from `services/tool_registry` — the same enumeration `GET /tools`
    returns, because there is one set of tools, and an agent editor asking
    "what can I grant" and a tools screen asking "what exists" are the same
    question.

    This route used to return a hardcoded five-string list (`filesystem`,
    `google-docs`, `playwright`, `ollama`, `web`). Audited 2026-08-23: those
    strings were not CLI tool names and not MCP server names, nothing enforced
    them, and no frontend read them — a vocabulary used nowhere else in the
    tree. They are deleted rather than translated, per
    `docs/platform-redesign/tools-are-declared-2026-08-23.md` §1.

    LISTING IS NOT GRANTING. `services/llm_router.tool_flags` remains the only
    thing that puts a tool on an agent's argv.
    """
    return {"tools": [t.to_dict() for t in tool_registry.catalog()]}


# RETIRED: eighteen of the twenty `/agents/build/...` routes (patch, get,
# session/reset, tree, file get/put, brief get/put, interventions get/post,
# diff, diff/revert-file, preview status/start/stop/log, commit, and the list
# read).
#
# They were the API of a SECOND ENTITY, and there is no second entity:
# threads-branches-and-pages-2026-08-29.md section 1 settles that a thread is
# the one thing, every thread is chattable, and "agent" describes a thread whose
# `armed` is not `off`. Their only caller was one page (`pages/AgentBuild.tsx`),
# deleted in the same change, and every capability they carried is served by a
# route addressed by WORKSPACE rather than by agent slug: `/files` for the tree
# and the file bytes (the brief and the interventions log are files in that
# tree), `/git` for diff, revert and commit, `/base-apps/run` for the preview.
#
# TWO SURVIVE, and both are the operator's own action rather than a view.
# `POST /build` is the one call that brings a maker DEFINITION into existence
# (`services/makers.ensure`, whose header says so and names the two candidates
# it closed), and `POST /build/{slug}/retire` is how that ends. Deleting them
# would have removed a capability instead of a duplicate.
#
# `services/build_agents.py` and `services/makers.py` STAY. They are read by
# scripts/dispatch_triggers.sh and scripts/ralph.sh, which is server-side work
# with no HTTP surface; what went is the door, not the room.


class BuildAgentCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=64)
    purpose: str = ""
    project_slug: str | None = None
    branch_slug: str | None = None


@router.post("/build", response_model=BuildAgent, response_model_exclude_unset=True)
async def create_build_agent(body: BuildAgentCreate):
    """Create a maker with a working tree. THIS IS THE OPERATOR ACTION THAT
    BRINGS A MAKER DEFINITION INTO EXISTENCE, and the only one.

    `/agents/new` posts here when the role is Maker and a project is picked. It
    used to write only an execution row into `build_agents.json`, and since
    `agents_registry.list_agents` has no builder fallback on purpose ("an
    undeclared process is a Run"), the maker the operator had just created never
    appeared on the roster. The declaration was the missing half of his own
    action; `makers.ensure` writes it, with the slug and name he supplied.

    There is no reconciler behind this and there must not be one: a scope with
    no maker definition still works its tickets, because the loop is the maker
    (`services/makers.py` header, `primitive-roles-2026-07-18.md` sec 6)."""
    try:
        rec = build_agents.create_agent(
            slug=body.slug,
            name=body.name,
            purpose=body.purpose,
            project_slug=body.project_slug,
            branch_slug=body.branch_slug,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Additive and never raising: the build agent exists by now, so a definition
    # that could not be written is a maker without a persona, not a failed
    # create. It is logged inside `ensure`.
    makers.ensure(slug=rec["slug"], name=body.name, purpose=body.purpose or None,
                  project_slug=body.project_slug)
    return rec


@router.post("/build/{slug}/retire", response_model=OkOut, response_model_exclude_unset=True)
async def retire_build_agent(slug: str):
    build_agents.retire(slug)
    return {"ok": True}


def _agent_scope_key(rec: dict) -> str | None:
    """The task scope this agent's tickets live in, as a scope KEY.

    A key, not a path, because the UI has to be able to LINK to it and a
    filesystem path is not a link. This was only ever derived as a path, so the
    agent detail page listed ticket ids it had no way to address: every badge
    under "Tickets (N)" pointed at `/work?ticket=<id>`, a query string nothing
    parses. Returning the key is what lets those become real ticket links.
    """
    pslug = rec.get("project_slug")
    if pslug:
        return f"project:{pslug}"
    slug = rec.get("slug") or rec.get("id")
    return f"agent:{slug}" if slug else None


def _agent_scope_root(rec: dict) -> Path | None:
    """The tree this agent's tasks live in: its linked project dir, else a
    maker's own cwd. An agent with no project link and no tree of its own has an
    empty queue snapshot.

    Resolved from the scope key through task_scope, so the tree the server reads
    and the scope the UI links to cannot disagree."""
    from services import task_scope
    key = _agent_scope_key(rec)
    root = task_scope.root_for_context(key) if key else None
    if root is None and rec.get("role") == "maker" and rec.get("cwd"):
        # A maker that predates the build-agents registry still has a tree.
        root = rec["cwd"]
    return Path(root) if root else None


def _agent_queue_snapshot(rec: dict) -> dict:
    """Queue counts (`ready`/`doing`/`in_review`/`blocked`) + pending `awaiting`
    payloads for an agent's scope. Mirrors the project autonomy snapshot; returns
    zeros when the agent has no resolvable task tree."""
    empty = {"queue": {"ready": 0, "doing": 0, "in_review": 0, "blocked": 0,
                       "parked": 0, "todo": 0}, "awaiting": []}
    root = _agent_scope_root(rec)
    if root is None:
        return empty
    try:
        items = tasks.list_tasks(root, include_done=True)
    except Exception:
        return empty
    by_id = {t.get("id"): t for t in items}
    tally = {"todo": 0, "doing": 0, "in_review": 0, "blocked": 0,
             "parked": 0, "done": 0, "cancelled": 0}
    awaiting: list[dict] = []
    ready = 0
    for t in items:
        st = t.get("status")
        if st in tally:
            tally[st] += 1
        if st == "todo":
            deps = t.get("depends_on") or []
            if all((by_id.get(d) or {}).get("status") in tasks.TERMINAL
                   for d in deps if d in by_id):
                ready += 1
        aw = t.get("awaiting")
        if isinstance(aw, dict) and aw.get("question"):
            awaiting.append({
                "id": t.get("id"),
                "status": st,
                "text": (t.get("text") or "").split("\n")[0][:140],
                "question": aw.get("question"),
                "since": aw.get("since"),
            })
    return {
        "queue": {
            "ready": ready,
            "doing": tally["doing"],
            "in_review": tally["in_review"],
            "blocked": tally["blocked"],
            "parked": tally["parked"],
            "todo": tally["todo"],
        },
        "awaiting": awaiting,
    }


@router.get("/{agent_id}", response_model=AgentDetail, response_model_exclude_unset=True)
async def get_agent(agent_id: str):
    """Unified detail, resolved from the registry (by slug or routing id). The
    persona/instructions live in the definition body; there is no separate
    memory layer to enrich from. Enriched with the autonomy
    preset (classified from the agent's triggers) plus the scope's queue counts
    and any pending `awaiting` prompts, plus the cross-link ids the UI uses to
    render the Involved-in section (B1)."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    snap = _agent_queue_snapshot(rec)
    # Cross-link denormalization (B1). Small, best-effort passes over the task
    # store + jobs feed + chat store. Bounded by limits so a big project cannot
    # make this endpoint slow. Fail-open: any error yields an empty list rather
    # than a 500 (the detail page must always render, even without cross-links).
    linked_ticket_ids: list[str] = []
    linked_run_ids: list[str] = []
    linked_chat_ids: list[str] = []
    try:
        root = _agent_scope_root(rec)
        if root is not None:
            items = tasks.list_tasks(root, include_done=True)
            slug = rec.get("slug") or ""
            assignee = f"agent:{slug}" if slug else None
            for t in items[:200]:
                tid = t.get("id")
                if not tid:
                    continue
                a = (t.get("assignee") or "").strip()
                if assignee and a == assignee:
                    linked_ticket_ids.append(tid)
                elif not a and root is not None:
                    # scope-implied membership: no explicit assignee, agent works
                    # its scope by default. Include so the agent has something to
                    # show; the UI can differentiate assigned vs scope-implied.
                    linked_ticket_ids.append(tid)
    except Exception:
        pass
    try:
        from services import jobs as _jobs
        job_recs = _jobs.read(agent_id=rec.get("id"), limit=100)
        linked_run_ids = [j.get("id") for j in job_recs if j.get("id")]
    except Exception:
        pass
    try:
        from services import chat_store as _cs
        slug = rec.get("slug") or ""
        if slug:
            # Cheap contextKey-prefix scan: chats whose thread key names this
            # agent's scope. Not a graph query; just what is easy to compute
            # without a schema change.
            ck = f"agent:{slug}"
            threads = _cs.list_threads()
            for th in threads:
                if (th.get("context_key") or "").startswith(ck):
                    linked_chat_ids.append(th.get("id"))
    except Exception:
        pass
    return {
        **rec,
        "autonomy": triggers.triggers_to_preset(rec.get("triggers") or []),
        # See list_agents comment. Autonomy-model per-agent MODE lives here until
        # C7 flips the field name.
        "autonomy_mode": rec.get("autonomy") or agents_registry.DEFAULT_AUTONOMY,
        "mentionable": bool(rec.get("mentionable")),
        "delegatable": bool(rec.get("delegatable")),
        "queue": snap["queue"],
        "awaiting": snap["awaiting"],
        "linked_ticket_ids": linked_ticket_ids[:50],
        # Every id above came from ONE store, so they share one scope. Sent
        # because a ticket is addressed by (scope, id) and the client cannot
        # re-derive the scope without duplicating the routing rule above.
        "linked_ticket_scope": _agent_scope_key(rec),
        "linked_run_ids": linked_run_ids[:50],
        "linked_chat_ids": linked_chat_ids[:50],
        "activation": _activation_chain(rec),
        # What this role may write, and the tools that follow from it. Rendered
        # on the page so the contract is legible without reading the table.
        "authority": {
            "writes": sorted(agent_authority.writes_for_role(rec.get("role"))),
            "describes": agent_authority.describe(rec.get("role")),
            "tools": agent_authority.narrow_tools(rec.get("role"), rec.get("tools")),
        },
    }


def _activation_chain(rec: dict) -> dict:
    """Why this agent does or does not run, as an ordered chain of conditions.

    "Autonomy is on" has never been answerable from this page, because the answer
    is a conjunction spread across three places: a global brake, the agent's own
    declared mode, and whether anything can trigger it. An agent that has never
    run therefore rendered as empty panels with no explanation, which reads as a
    broken page rather than as a correctly-idle agent.

    So the chain is returned in order, each link with its own verdict, and the
    FIRST failing link is named. This is a read-only explanation of state that
    already exists; it grants nothing and gates nothing.
    """
    from services import autonomy_override

    brake = autonomy_override.read()
    autonomy = rec.get("autonomy") or agents_registry.DEFAULT_AUTONOMY
    triggers = rec.get("triggers") or []
    # A manual-only trigger set cannot start anything on its own, so it is not a
    # way in. Distinguish "no triggers" from "only manual".
    auto_triggers = [t for t in triggers if isinstance(t, dict) and t.get("kind") != "manual"]

    links = [
        {
            "id": "brake",
            "label": "Global autonomy brake is clear",
            "ok": not brake.get("engaged"),
            "detail": (brake.get("reason") or "engaged") if brake.get("engaged") else "clear",
        },
        {
            "id": "autonomy",
            "label": "Agent may run unattended",
            "ok": autonomy in ("supervised", "autonomous"),
            "detail": autonomy,
        },
        {
            "id": "trigger",
            "label": "Something can start it",
            "ok": bool(auto_triggers),
            "detail": (", ".join(sorted({str(t.get("kind")) for t in auto_triggers}))
                       if auto_triggers else
                       ("manual invocation only" if triggers else "no triggers declared")),
        },
    ]
    blocking = next((l for l in links if not l["ok"]), None)
    return {
        "links": links,
        "blocked_by": blocking["id"] if blocking else None,
        "runs_unattended": blocking is None,
        "ever_ran": bool(rec.get("last_active")),
        "last_active": rec.get("last_active"),
    }


class AgentDefinitionUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    triggers: list[AgentTrigger] | None = None
    limits: AgentLimits | None = None
    capabilities: list[str] | None = None
    role: str | None = None            # closed enum: maker|reviewer|triager|keeper|broker
                                       # ("" clears it; unknown -> 400)
    autonomy: str | None = None        # per-agent mode: reactive | supervised | autonomous
                                       # (autonomy-model 2026-07-18; "" or the
                                       # reactive default clears the emitted field)
    # Interaction opt-ins, independent of each other and of role. Both default
    # False: an agent is not conversational and not delegatable until it is
    # deliberately made so.
    mentionable: bool | None = None     # may be @-mentioned in a chat
    delegatable: bool | None = None     # may be handed a ticket
    definition: str | None = None  # the markdown body (persona / instructions)


@router.put("/{agent_id}", response_model=Agent, response_model_exclude_unset=True)
async def update_agent_definition(agent_id: str, body: AgentDefinitionUpdate):
    """Write the declarative definition (name/purpose/triggers/limits/body) to the
    agent's markdown file via the registry. Trigger writes land in frontmatter and
    regenerate `.runtime/agent-subscriptions.json` as a union (non-registry
    entries preserved). The definition file is the backing store for autonomy."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    try:
        return agents_registry.update(
            rec["slug"],
            name=body.name,
            purpose=body.purpose,
            triggers_list=body.triggers,
            limits=body.limits,
            capabilities=body.capabilities,
            role=body.role,
            autonomy=body.autonomy,
            mentionable=body.mentionable,
            delegatable=body.delegatable,
            body=body.definition,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/", response_model=Agent, response_model_exclude_unset=True)
async def create_agent(body: AgentCreate):
    """Create an Agent in the registry (a markdown definition file). The
    persona/instructions become the definition body; there is no separate memory
    engine to provision. A maker with a working tree is created via POST
    /agents/build. Triggers and limits are edited afterward on the detail page
    (PUT /agents/{id})."""
    parts: list[str] = []
    if body.persona.strip():
        parts.append(body.persona.strip())
    if body.human.strip():
        parts.append("## Operator context\n\n" + body.human.strip())
    try:
        return agents_registry.create(
            slug=agents_registry.mint_slug(body.name),
            name=body.name,
            memory="notes",
            role=body.role,
            autonomy=body.autonomy,
            project_slug=body.project_slug,
            body="\n\n".join(parts),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}", response_model=RetireAgentOut, response_model_exclude_unset=True)
async def delete_agent(agent_id: str):
    """Retire an agent from the registry: it leaves the roster while the
    declarative definition is preserved (retired flag). A maker also flips its
    execution state."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    agents_registry.retire(rec["slug"])
    _drop_from_project_roster(rec)
    return {"ok": True, "retired": rec["slug"]}


# ---- The maker agent: what is doing the tickets ----------------------------
#
# THE CONTROLS DELEGATE, THEY DO NOT REIMPLEMENT. Every write below goes through
# `services/work_driver`'s shared intent surface -- the same three functions
# `routes/autonomy.py`'s queue-runner endpoints call -- so the agent-level
# control and the Settings-drawer control cannot drift apart. In particular the
# driver's own rule is preserved exactly: START ARMS the scope and STOP DISARMS
# it, because the reconciler derives the runner from the arm level and would
# otherwise undo either button within a minute.
#
# These are two-segment paths, so they never fall into the `/{agent_id}`
# catch-all, which matches a single segment only.



def _maker(agent_id: str) -> dict:
    """Resolve an agent and insist it is a maker with a tree, or 404/409.

    409 rather than 404 for a real agent that is not a maker: the agent exists,
    the sub-resource does not, and reporting it as missing would send a caller
    looking for a typo in a slug that is correct."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        raise HTTPException(404, f"unknown agent: {agent_id}")
    if rec.get("role") != "maker":
        raise HTTPException(
            409, f"{rec.get('slug')} is a {rec.get('role') or 'roleless'} agent, "
                 "not a maker: only a maker works a scope's ticket queue")
    if makers.scope_for(rec) is None:
        raise HTTPException(
            409, f"{rec.get('slug')} is a maker with no tree: link it to a "
                 "project and it gets that project's queue, its runner and its "
                 "arm level")
    return rec


def _maker_state(rec: dict) -> dict:
    try:
        return makers.live_state(rec)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/{agent_id}/maker", response_model=MakerState, response_model_exclude_unset=True)
async def get_maker_state(agent_id: str):
    """Everything about this maker that is true right now, joined at read time.

    The four questions the UI has to ask, and where each answer comes from:
      is it allowed to run   `arm_level` / `armed` / `ceiling`  scope_execution
      is a worker here       `runner_resident` / `runner`       work_driver flock
      is it working, on what `activity` / `ticket_*` / `sessions`  presence
      what has it run        `recent_runs`                      the Job store
    Nothing here is persisted, and nothing here is a flag someone set."""
    return _maker_state(_maker(agent_id))


@router.post("/{agent_id}/maker/execution", response_model=MakerState, response_model_exclude_unset=True)
async def set_maker_execution(agent_id: str, body: MakerExecutionIn):
    """Set this maker's arm level.

    THIS WRITES `scope-execution.json` AND NOTHING ELSE. The maker's arm level
    IS its scope's execution mode -- there is no per-agent copy of it, so there
    is nothing that can disagree with the same setting reached from the chat's
    Work settings. Setting it also makes the mechanism follow immediately
    (arming ensures a runner, disarming stops one) via the same shared service
    `POST /agents/autonomy/scope-execution` uses."""
    rec = _maker(agent_id)
    scope = makers.scope_for(rec)
    row = makers.scope_row(scope)
    root = (row or {}).get("dir") or paths.workshop_root()
    await asyncio.to_thread(work_driver.set_execution, scope, root, body.mode)
    return _maker_state(rec)


@router.post("/{agent_id}/maker/runner/start", response_model=MakerState, response_model_exclude_unset=True)
async def start_maker_runner(agent_id: str):
    """Put a worker on this maker's tree. ARMS the scope first (supervised, if
    it was not armed at all), because the runner is derived from the arm level:
    a start that only spawned would be undone by the next reconcile pass. A
    scope already at `autonomous` is not downgraded."""
    rec = _maker(agent_id)
    scope = makers.scope_for(rec)
    row = makers.scope_row(scope)
    if row is None:
        raise HTTPException(
            409, f"{rec.get('slug')}'s tree is not a runnable target: the "
                 "project is gone, or developer mode is off for the platform repo")
    await asyncio.to_thread(work_driver.start_work, scope, row["dir"])
    return _maker_state(rec)


@router.post("/{agent_id}/maker/runner/stop", response_model=MakerState, response_model_exclude_unset=True)
async def stop_maker_runner(agent_id: str):
    """Take the worker off this maker's tree. DISARMS the scope first, then
    signals the runner, so the reconciler does not bring it straight back. The
    sweep's own lease/reclaim path returns a half-worked ticket to the queue, so
    stopping mid-run loses no tickets."""
    rec = _maker(agent_id)
    scope = makers.scope_for(rec)
    row = makers.scope_row(scope)
    root = (row or {}).get("dir") or paths.workshop_root()
    await asyncio.to_thread(work_driver.stop_work, scope, root)
    return _maker_state(rec)


@router.get("/{agent_id}/jobs", response_model=AgentJobsOut, response_model_exclude_unset=True)
async def agent_jobs(agent_id: str,
                     limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)):
    """Job history filtered to this agent, sourced from the unified Job store
    (`services.jobs` / `.runtime/jobs.jsonl`). Declared as a two-segment path so
    it never falls into the `/{agent_id}` catch-all. Interactive chat turns are
    not Jobs and never appear here."""
    records = jobs.read(agent_id=agent_id, limit=limit)
    return {"agent_id": agent_id, "jobs": records, "count": len(records)}


@router.get("/{agent_id}/runs", response_model=AgentRunsOut, response_model_exclude_unset=True)
async def agent_runs(agent_id: str,
                     limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)):
    """Recent activity feed for an agent: cron-runs JSONL (filtered) + its
    presence record + a freshness flag. Powers per-agent activity cards on the
    Agents page.

    READ THROUGH `services/presence.py` (step B4), not by parsing the file here.
    This route used to open `.runtime/heartbeats/{id}.json` itself and call a
    record older than 120 seconds unresponsive; that threshold was a second,
    private copy of an expiry rule the record can now state. The agent's
    session id in that channel IS its agent id -- the writer is
    `scripts/heartbeat.sh <agent_id>` -- so the lookup is unchanged, and a
    record written before B4 still reads (`presence._tolerate`).
    """
    heartbeat = presence.get(agent_id)
    heartbeat_age_s = presence.beat_age_s(heartbeat)

    # Recent Jobs for this agent, sourced from the unified Job store
    # (.runtime/jobs.jsonl) rather than the retired agent-runs.jsonl. Same
    # response shape; the frontend reads `runs`.
    runs = jobs.read(agent_id=agent_id, limit=limit)

    return {
        "agent_id": agent_id,
        "heartbeat": heartbeat,
        "heartbeat_age_s": heartbeat_age_s,
        # The same three words as before, now decided by the record's own
        # expiry (and by the accelerator, where a record opted into one)
        # instead of by a threshold spelled only here.
        "liveness": ("no-heartbeat" if not heartbeat
                     else "alive" if presence.is_live(heartbeat)
                     else "unresponsive"),
        "runs": runs,
        "runs_count": len(runs),
    }
