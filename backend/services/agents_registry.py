"""Unified Agent registry over markdown definition files.

One Agent concept. `runtime` is a field (`builder | assistant`), not a separate
noun. A definition is a markdown file with YAML frontmatter:

    ---
    name: Commute builder
    runtime: builder            # builder | assistant
    purpose: keep the commute app shipping
    capabilities: []            # grant refs (Phase 4 fills these)
    triggers:                   # normalized triggers (services.triggers shape)
      - kind: cron
        cron_expression: "*/20 * * * *"
    limits:
      max_iterations: 20
      budget: {max_wall_s: 3000}
    memory: letta               # assistant memory engine, or null (builders)
    project_slug: commute       # link / project scope (optional)
    letta_agent_id: agent-...   # assistant runtime link to its Letta agent
    ---
    <markdown body: persona / instructions>

Scope:
  * User scope    ~/workshop/agents/<slug>.md
  * Project scope <project_dir>/agents/<slug>.md

DECLARATIVE ONLY. Mutable runtime state (`state`, `claude_session`,
`last_active`, `cwd`) never lives in frontmatter. It stays in
`.runtime/build_agents.json` keyed by slug (the same file build_agents.py owns);
`list_agents` merges it into builder records at read time.

EXCLUSION RULE. masini per-channel Discord agents (created by
`per_channel_agents.py`) are NOT registry members: they get no definition files,
never appear in the roster, and are never created/updated/retired here. They are
identified authoritatively by membership in the channel-agent map.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from services import triggers
from services import build_agents as _build_agents
from services import latitude as _latitude

log = logging.getLogger("workshop.agents_registry")

WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path.home() / "workshop")))
USER_AGENTS_DIR = Path(os.environ.get(
    "WORKSHOP_AGENTS_DIR", str(WORKSHOP_ROOT / "agents")))
PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(WORKSHOP_ROOT / ".runtime" / "projects.json")))
SUBSCRIPTIONS_PATH = Path(os.environ.get(
    "WORKSHOP_SUBSCRIPTIONS", str(WORKSHOP_ROOT / ".runtime" / "agent-subscriptions.json")))
CHANNEL_MAP = Path(os.environ.get(
    "WORKSHOP_CHANNEL_AGENT_MAP", str(WORKSHOP_ROOT / ".runtime" / "channel-agent-map.json")))

RUNTIMES = ("builder", "assistant")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# Shipped agent TEMPLATES (final-plan P4 / roles report 5.1). Tracked source, not
# instance state: a template is never registered and never runs — `list_agents`
# globs `agents/*.md` non-recursively, so nothing in here is ever dispatched. It
# is forked into an instance (see `fork_from_template`), and the fork is what
# runs. `.gitignore` carves `!/agents/_templates/` out of `/agents/*` for exactly
# this, mirroring `projects/_templates/`.
TEMPLATES_DIR = Path(os.environ.get(
    "WORKSHOP_AGENT_TEMPLATES_DIR", str(USER_AGENTS_DIR / "_templates")))
GROUPS_DIR = TEMPLATES_DIR / "groups"

# Built-in app assistants (the Budget/Accounts/Commute/Location chat personas).
# Same file format and same parser as everything else here — that is the entire
# point of the directory existing. They used to be Python: a ContextProvider
# subclass with the persona as a concatenated string literal, the tool list as a
# class attribute, and the model as a constant, which made "what is this agent
# allowed to do" a question you answered by reading code. Now the declaration is
# declared and the subclass keeps only what cannot be: the live snapshot, and
# for Budget the plan-patch applier.
#
# Outside the roster: `list_agents` globs `agents/*.md` NON-recursively, so
# nothing in here is ever dispatched or counted as a roster agent. An app
# assistant is the face of an installed app, reached by opening that app, not a
# standing agent with triggers of its own.
APP_AGENTS_DIR = Path(os.environ.get(
    "WORKSHOP_APP_AGENTS_DIR", str(USER_AGENTS_DIR / "apps")))

# Frontmatter that belongs to the TEMPLATE, not to the instance it produces. It
# is stripped on fork and replaced by a single `template: <slug>@<version>` line.
_TEMPLATE_ONLY_FM = ("template", "template_version", "description")

# Roles the shell dispatches on a schedule read the WHOLE scope, so two of them
# on one scope would both read the same notes past the same watermark and both
# file the same tasks — the dedupe path would spend its life covering for a
# configuration error. This is a uniqueness constraint on a key (the same class of
# thing as a slug being unique), not a permission wall: makers are exempt, because
# several makers on a scope is the concurrency case, already governed by `touches`
# disjointness and the claim fence.
SINGLETON_ROLES = ("triager", "keeper")

# Role taxonomy + per-agent effort/fan-out knobs (Phase O6/O7-config). All three
# are optional frontmatter fields with safe defaults, surfaced in the parsed
# record and re-serialized on write.
#
# `role` TYPES an agent: it selects which HANDLER the dispatcher runs for it
# (scripts/dispatch_triggers.sh ROLE_HANDLERS), it does not change how work is
# assigned (assignment stays `assignee=agent:<slug>`). That one is live.
#
# `depth` and `fanout` ARE NOT. This comment used to read "`depth` is the effort
# tier a sweep runs the agent at; `fanout` bounds native subagent use", and both
# halves were aspirational: no executor reads either field. A sweep's effort tier
# comes from the TICKET's `effort` (project_ralph.sh reads it to pick
# RALPH_ITER_QUICK / _DEEP and the model tier), and nothing anywhere consults
# `fanout` before a subagent is spawned. No agent definition in the tree declares
# either one, so today they are parsed, echoed to the API, and dropped.
#
# They are kept because the ticket-level effort tier is the mechanism that works
# and an agent-level default could route into it later. Until something reads
# them, they must not be presented to the operator as controls that change a run.
# The agent page does not offer them. AutonomyPanel still renders both at PROJECT
# scope; that surface is honest only once a consumer exists.
DEPTHS = ("quick", "standard", "deep")
FANOUTS = ("off", "read-only")
DEFAULT_DEPTH = "standard"
DEFAULT_FANOUT = "read-only"

# `latitude` is decision rights, NOT effort — it is what the worker may do where
# the ticket does not say (services/latitude.py owns the enum + the cascade).
# Distinct from `depth`, which is the effort budget: `strict` + `deep` (think
# hard, then ask) and `open` + `quick` (do the obvious thing, don't ask) are both
# coherent. Unlike depth/fanout, the parsed record keeps None rather than
# defaulting, because None means INHERIT: this agent scope is one level of the
# cascade, not the bottom of it.
LATITUDES = _latitude.LATITUDES
DEFAULT_LATITUDE = _latitude.DEFAULT_LATITUDE

# CLOSED ENUM (autonomy-model 2026-07-18). Governs whether the dispatcher may
# fire this agent on its declared triggers, and whether a completed run commits
# straight to `done` or lands in `in_review` for operator approval.
#   reactive   — never runs in the background. Only manual invocation.
#   supervised — runs on trigger; surfaces results in `in_review` before commit.
#   autonomous — runs on trigger; commits results directly (subject to reviewer gate).
# Default `reactive` per §1.1: background execution is opt-in. Existing agents
# with declared triggers keep working via the compat path in dispatch (C5 will
# flip to strict enforcement).
AUTONOMY = ("reactive", "supervised", "autonomous")
DEFAULT_AUTONOMY = "reactive"

# CLOSED ENUM (final-plan P0). `role` used to be a free-form tag, which meant a
# typo (`role: memoy`) silently disabled an agent's dispatch handler instead of
# failing — the memory-keeper's entire consolidation path hung off an exact string
# match. Roles are now coerced on read and REJECTED on write.
#   maker    — does the work (the ralph sweep). The default for a builder.
#   reviewer — judges someone else's work.
#   triager  — routes/curates inbound work into tasks.
#   keeper   — consolidates memory (the memory-keeper organ).
#   broker   — adjudicates resource access on DEMAND. Never dispatched.
ROLES = ("maker", "reviewer", "triager", "keeper", "broker")

# Back-compat: `role: memory` predates the enum and is live on agents/memory-keeper.md.
# It reads as `keeper` so an existing definition keeps dispatching unchanged.
ROLE_ALIASES = {"memory": "keeper"}

# How an agent may be INTERACTED WITH, as two independent opt-ins. Prior art:
# Linear's agent SDK splits exactly this way (`app:mentionable` /
# `app:assignable`), for the reason that they are different products. Talking to
# an agent and handing it work are unrelated capabilities, and most agents want
# neither -- they are triggered, they work, and they report. Defaulting both to
# False is what keeps a notes-reviewing keeper from showing up in an @-mention
# list where it would only confuse the picker.
#
#   mentionable  -- may be @-mentioned in a chat and replies inline, attributed
#   delegatable  -- may be handed a ticket to work
def _coerce_bool(v: object) -> bool:
    """Frontmatter booleans arrive as real bools from YAML, but a hand-edited
    definition can carry the string. Accept both; anything else is False."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "on", "1")
    return False


def _role_for_runtime(runtime: str) -> str | None:
    """The role a runtime implies when the definition does not state one.

    A builder IS a maker: it runs the ralph sweep, writes the tree and commits
    (primitive-roles 2026-07-18, section 2). Every builder definition predates the
    role enum and so carries `role: None`, which left the entire agent surface
    keyed on `runtime` because that was the only field with a value in it. The
    entity model keys agents on ROLE, so the default belongs here — one rule at
    the read boundary — rather than as a fallback repeated at each display site.
    Assistants imply nothing; an assistant with no role is genuinely unroled.
    """
    return "maker" if runtime == "builder" else None


def _coerce_depth(v: object) -> str:
    return v if v in DEPTHS else DEFAULT_DEPTH


def _coerce_fanout(v: object) -> str:
    return v if v in FANOUTS else DEFAULT_FANOUT


def _coerce_latitude(v: object) -> str | None:
    """Read-side: a canonical latitude, or None meaning inherit from the cascade.
    Writes go through `_validate_latitude`, which raises instead of coercing."""
    return _latitude.coerce(v)


def _coerce_tools(v: object) -> list[str] | None:
    """An explicit `tools:` list, or None meaning "whatever the role allows".

    None and [] are DIFFERENT and the difference is the whole point. None is no
    request, so `agent_authority.narrow_tools` hands back the role's full set;
    [] is a definition asking for no tools at all, which it is entitled to do.
    Collapsing them (`fm.get("tools") or []`) would silently disarm every agent
    that simply did not mention tools.

    This field was documented at two call sites as the way a definition narrows
    its role, and was never parsed — `rec.get("tools")` was always None, so the
    narrowing described in those comments could not happen."""
    if not isinstance(v, list):
        return None
    return [str(t).strip() for t in v if str(t).strip()]


def _coerce_model(v: object) -> str | None:
    """A model tier or name the definition pins, or None meaning inherit.

    An agent that declares a model gets it wherever it runs, instead of running
    on whatever the composer happened to be set to when someone @mentioned it."""
    if not isinstance(v, str):
        return None
    return v.strip() or None


def _coerce_effort(v: object, slug: str = "") -> str | None:
    """An effort level the definition pins, or None meaning inherit the chat's.

    Validated here against the vendor's ladder (`model_caps.EFFORT_LEVELS`), not
    against a model: which rungs a model has is a fact about the turn, and the
    model an agent runs on can differ from the one the chat is set to. The
    per-model check happens at the turn site, where the model is finally known.

    A junk value reads as "declared nothing" and is logged, rather than raising:
    an operator addressing an agent should get an answer, not a stack trace about
    a definition file."""
    from services import model_caps

    if not isinstance(v, str) or not v.strip():
        return None
    level = v.strip()
    if level not in model_caps.EFFORT_LEVELS:
        log.warning("agent %s declares effort %r, which is not a level (%s); ignoring",
                    slug or "?", v, list(model_caps.EFFORT_LEVELS))
        return None
    return level


def _coerce_permission_mode(v: object, slug: str = "") -> str | None:
    """A permission mode the definition pins, or None meaning inherit the chat's.

    Validated against `model_caps.OFFERED_MODES`, NOT the full mode list: a
    mention runs inside a chat, and `bypassPermissions` is not a posture this
    system offers a chat (operator instruction, 2026-08-09). The unattended paths
    that legitimately need bypass pass it explicitly and do not read frontmatter.
    The point of the field is an agent being MORE careful than the chat it is
    speaking in, not less.

    Junk (bypass included) is logged and ignored, for the same reason as
    `_coerce_effort`."""
    from services import model_caps

    if not isinstance(v, str) or not v.strip():
        return None
    try:
        mode = model_caps.normalize_permission_mode(v)
    except ValueError:
        mode = None
    if mode is None or mode not in model_caps.OFFERED_MODES:
        log.warning("agent %s declares permission_mode %r, which a chat may not "
                    "choose (%s); ignoring", slug or "?", v,
                    list(model_caps.OFFERED_MODES))
        return None
    return mode


def _validate_latitude(v: object) -> str | None:
    """Write-side: raises ValueError on an unknown latitude so create/update
    surface a 400 rather than writing a value no sweep will honor."""
    return _latitude.validate(v)


def _coerce_role(v: object) -> str | None:
    """Read-side coercion: a canonical role, or None (no role -> default dispatch).

    Aliases resolve to their canonical name. Anything else (unset, junk, a typo)
    is None, which the dispatcher reads as "use the runtime's default handler".
    Writes go through `_validate_role`, which raises instead of coercing, so a
    typo can only ever enter the tree by hand-editing a definition file."""
    if not isinstance(v, str):
        return None
    r = v.strip().lower()
    r = ROLE_ALIASES.get(r, r)
    return r if r in ROLES else None


def _validate_role(v: object) -> str | None:
    """Write-side validation: canonical role, or None for unset/empty. Raises
    ValueError on an unknown role so create/update surface a 400 rather than
    silently writing an inert agent."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"role must be a string, one of {ROLES}")
    r = v.strip().lower()
    if not r:
        return None
    r = ROLE_ALIASES.get(r, r)
    if r not in ROLES:
        raise ValueError(f"unknown role {v!r}; must be one of {ROLES}")
    return r


def _coerce_autonomy(v: object) -> str:
    """Read-side: a canonical autonomy mode, or DEFAULT_AUTONOMY on anything else.

    Anything not in AUTONOMY (unset, junk, a typo) reads as `reactive`. Reactive
    is the safe default: an agent that does not declare an autonomy mode does
    not run in the background."""
    if isinstance(v, str):
        a = v.strip().lower()
        if a in AUTONOMY:
            return a
    return DEFAULT_AUTONOMY


def _validate_autonomy(v: object) -> str:
    """Write-side: canonical autonomy mode. Raises ValueError on an unknown value
    so create/update surface a 400 rather than silently defaulting."""
    if v is None:
        return DEFAULT_AUTONOMY
    if not isinstance(v, str):
        raise ValueError(f"autonomy must be a string, one of {AUTONOMY}")
    a = v.strip().lower()
    if not a:
        return DEFAULT_AUTONOMY
    if a not in AUTONOMY:
        raise ValueError(f"unknown autonomy {v!r}; must be one of {AUTONOMY}")
    return a

# Kept in sync with routes/agents.py KNOWN_SOURCES — the event-source vocabulary
# the subscriptions file is partitioned by. `discord` is the source a `discord`
# trigger subscribes to (final-plan PM.2); it is fed by scripts/masini_event_bridge.py.
KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles",
                 triggers.DISCORD_SOURCE, "custom"]

DEFAULT_LIMITS = {
    "max_iterations": triggers.DEFAULT_LOOP_MAX_ITERATIONS,
    "budget": dict(triggers.DEFAULT_LOOP_BUDGET),
}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


# ---------------------------------------------------------------------------
# Frontmatter parse / emit
# ---------------------------------------------------------------------------

def _parse(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, m.group(2)


def _emit(fm: dict, body: str) -> str:
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return "---\n" + front + "\n---\n\n" + (body or "").strip() + "\n"


# ---------------------------------------------------------------------------
# Per-channel exclusion set
# ---------------------------------------------------------------------------

def per_channel_ids() -> set[str]:
    """Letta agent ids that belong to masini per-channel agents. These are never
    registry members. Read authoritatively from the channel-agent map."""
    try:
        m = json.loads(CHANNEL_MAP.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()
    return {v for k, v in m.items() if isinstance(v, str) and not k.endswith(".name")}


# ---------------------------------------------------------------------------
# Definition-file discovery
# ---------------------------------------------------------------------------

def _project_dirs() -> dict[str, Path]:
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out: dict[str, Path] = {}
    for slug, p in (reg.get("projects") or {}).items():
        d = p.get("dir") if isinstance(p, dict) else None
        if d:
            out[slug] = Path(d)
    return out


def _record_from_def(path: Path, scope: str, project_slug: str | None = None) -> dict:
    fm, body = _parse(path.read_text())
    slug = path.stem
    runtime = fm.get("runtime") if fm.get("runtime") in RUNTIMES else "assistant"
    letta_id = fm.get("letta_agent_id")
    rec: dict[str, Any] = {
        "slug": slug,
        "runtime": runtime,
        "name": fm.get("name") or slug,
        "purpose": fm.get("purpose") or "",
        # DECLARED, not granted. `capabilities` is a free-text list an agent
        # definition writes about itself; nothing derives a tool or an MCP server
        # from it, and it is deliberately not shown to the model as something it
        # HAS (see chat_context.agent_self_preamble). Echoed to the UI as the
        # description it is.
        "capabilities": fm.get("capabilities") or [],
        "triggers": triggers.definition_triggers(fm),
        "limits": fm.get("limits") or dict(DEFAULT_LIMITS),
        "role": _coerce_role(fm.get("role")) or _role_for_runtime(runtime),
        # May only NARROW what the role permits (agent_authority.narrow_tools).
        # None = no request = the role's full set.
        "tools": _coerce_tools(fm.get("tools")),
        "model": _coerce_model(fm.get("model")),
        # The agent's own TURN POSTURE, read the same way its model is: what it
        # declares wins over the chat it is speaking in, so a reviewer can be
        # careful inside a conversation that is otherwise on Auto. None (the
        # normal case) changes nothing — the chat's own resolution stands.
        "effort": _coerce_effort(fm.get("effort"), slug),
        "permission_mode": _coerce_permission_mode(fm.get("permission_mode"), slug),
        "mentionable": _coerce_bool(fm.get("mentionable")),
        "delegatable": _coerce_bool(fm.get("delegatable")),
        "depth": _coerce_depth(fm.get("depth")),
        "fanout": _coerce_fanout(fm.get("fanout")),
        "latitude": _coerce_latitude(fm.get("latitude")),
        "autonomy": _coerce_autonomy(fm.get("autonomy")),
        "memory": fm.get("memory"),
        # Fork provenance: `<template-slug>@<version>` when this instance came
        # from a template. A LABEL, not a link — nothing syncs from it, and
        # nothing reads it to decide behavior. It exists so a future "your
        # instance is based on v1, v3 is out" nudge is possible without a
        # propagation engine (roles report 5.3: do not build sync now).
        "template": fm.get("template"),
        "project_slug": fm.get("project_slug") or project_slug,
        "letta_agent_id": letta_id,
        "scope": scope,
        "definition": body.strip(),
        "definition_path": str(path),
        "retired": bool(fm.get("retired")),
        "source": "definition",
    }
    rec["id"] = letta_id if (runtime == "assistant" and letta_id) else slug
    return rec


class AppPersonaMissing(RuntimeError):
    """An installed app's assistant has no definition file, or an empty one.

    Raised rather than defaulted. A persona that silently falls back to nothing
    is an assistant with no instructions and no tools that still answers — the
    operator gets a confidently wrong bot instead of a broken one, which is the
    worse of the two failures. `provider_map` already logs and skips a provider
    that fails to build, so a missing file removes that app's assistant from the
    picker and leaves every other one working."""


def app_persona(key: str) -> dict:
    """The declaration for a built-in app's chat assistant.

    Returns `{name, purpose, model, tools, mcp_servers, persona}` — `persona`
    being the body, which is the system prompt. Same parser as the roster
    (`_parse`), so the two cannot drift into different dialects of frontmatter.
    """
    path = APP_AGENTS_DIR / f"{key}.md"
    try:
        text = path.read_text()
    except OSError as e:
        raise AppPersonaMissing(f"no definition for app assistant '{key}' at {path}") from e
    fm, body = _parse(text)
    body = body.strip()
    if not body:
        raise AppPersonaMissing(f"app assistant '{key}' has an empty persona ({path})")
    tools = fm.get("tools")
    servers = fm.get("mcp_servers")
    return {
        "key": key,
        "name": fm.get("name") or key,
        "purpose": fm.get("purpose") or "",
        "model": fm.get("model"),
        "tools": [str(t) for t in tools] if isinstance(tools, list) else [],
        "mcp_servers": [str(s) for s in servers] if isinstance(servers, list) else [],
        "persona": body,
        "definition_path": str(path),
    }


# Claude Code's own subagent directory. These are agents by every definition
# this system uses — a name, a description of when to use them, a tool list, and
# a body that is their system prompt — and they were the last mechanism defining
# agents outside `agents/<slug>.md`. They are not a rival FORMAT (same markdown +
# YAML frontmatter, same parser below); what differed was that they carried none
# of the declaration: no role, no scope, no statement of how they are reached.
#
# They still are not roster agents, and the difference is lifecycle, not
# schema. A roster agent is DISPATCHED: it has triggers, it can run on a
# schedule, it appears as a chat scope. A native subagent is DELEGATED: it only
# ever runs because another agent called it, so it has no triggers to enable and
# no autonomy of its own to grant. Listing them here answers "who can call
# whom", which is the question the roster cannot.
#
# Read-only, and deliberately so. This directory is SHARED with the masini
# Discord bridge; the Workshop reads it and never writes it.
NATIVE_AGENTS_DIR = Path(os.environ.get(
    "CLAUDE_AGENTS_DIR", str(Path.home() / ".claude" / "agents")))

# Surfaces whose agents belong to another tenant. `discord-handler` is the
# masini bridge's per-message dispatcher: it lives in the same user-scope
# directory because Claude Code has only one, but it is the messaging surface's
# agent, not the Workshop's — the same rule that excludes per_channel_agents.py.
# Named rather than silently filtered, so the exclusion is legible.
NATIVE_FOREIGN = {"discord-handler": "masini Discord bridge"}


def native_subagents() -> list[dict]:
    """Claude Code subagents visible to this instance, as declaration records.

    Same parser as everything else here. Each record carries `foreign` naming
    the surface it belongs to when it is not the Workshop's, rather than being
    dropped — an agent that exists and is not ours is worth seeing exactly once.
    """
    out: list[dict] = []
    try:
        paths = sorted(NATIVE_AGENTS_DIR.glob("*.md"))
    except OSError:
        return out
    for p in paths:
        try:
            fm, body = _parse(p.read_text())
        except OSError:
            continue
        slug = fm.get("name") or p.stem
        tools = fm.get("tools")
        out.append({
            "slug": slug,
            "name": slug,
            "description": fm.get("description") or "",
            "purpose": fm.get("purpose") or "",
            "role": _coerce_role(fm.get("role")),
            "scope": fm.get("scope") or "",
            # `['*']` means "every tool", which is worth rendering as that
            # rather than as a one-item list containing an asterisk.
            "tools": [str(t) for t in tools] if isinstance(tools, list) else [],
            "unrestricted_tools": isinstance(tools, list) and "*" in [str(t) for t in tools],
            "delegatable": _coerce_bool(fm.get("delegatable")),
            "mentionable": _coerce_bool(fm.get("mentionable")),
            "foreign": NATIVE_FOREIGN.get(slug),
            "definition": body.strip(),
            "definition_path": str(p),
            "dispatch": "delegated",
            "source": "native",
        })
    return out


def app_persona_keys() -> list[str]:
    """Every app assistant that ships a definition. Used by the tests that pin
    each provider to a file."""
    try:
        return sorted(p.stem for p in APP_AGENTS_DIR.glob("*.md"))
    except OSError:
        return []


def _merge_build_state(rec: dict, state: dict) -> None:
    for k in ("state", "claude_session", "last_active", "created", "cwd", "branch_slug"):
        if k in state:
            rec[k] = state[k]


# Real liveness, computed on read. The persisted `state` in build_agents.json is
# set to "running" on the first chat turn and NEVER reset when the turn finishes
# (finalize only touches last_active), so it ratchets to "running" forever and
# the roster shows every agent that ever ran once as "live". Truth is: an agent
# is working iff a build turn is genuinely in flight, i.e. a turn journal marked
# "running" whose detached subprocess is still alive. We scan the journals once
# per list and downgrade a stale "running" to "idle". Self-contained (no
# build_streaming import) to stay cycle-free.
_TURNS_DIR = WORKSHOP_ROOT / ".runtime" / "turns"


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _live_builder_slugs() -> set[str]:
    """Slugs with a build turn actually in flight right now."""
    live: set[str] = set()
    try:
        dirs = list(_TURNS_DIR.iterdir())
    except OSError:
        return live
    for d in dirs:
        try:
            m = json.loads((d / "meta.json").read_text())
        except Exception:
            continue
        if m.get("status") == "running" and _pid_alive(m.get("pid")):
            slug = m.get("agent_slug")
            if slug:
                live.add(slug)
    return live


def _correct_liveness(rec: dict, live_slugs: set[str]) -> None:
    """Downgrade a stale persisted "running" to "idle" unless a turn is live."""
    if rec.get("state") == "running" and rec.get("slug") not in live_slugs:
        rec["state"] = "idle"


def live_builder_slugs() -> set[str]:
    """Public: slugs with a build turn genuinely in flight right now. Used by the
    detail endpoint to apply the same honest-liveness rule as the roster."""
    return _live_builder_slugs()


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------

def list_agents(*, include_retired: bool = False) -> list[dict]:
    """Every Agent across both scopes and both runtimes. Per-channel agents are
    excluded. Builder runtime state is merged from build_agents.json."""
    excluded = per_channel_ids()
    build_state = (_build_agents._load().get("build_agents") or {})
    live_slugs = _live_builder_slugs()

    defs: dict[str, dict] = {}
    if USER_AGENTS_DIR.exists():
        for p in sorted(USER_AGENTS_DIR.glob("*.md")):
            r = _record_from_def(p, "user")
            defs[r["slug"]] = r
    user_dir = USER_AGENTS_DIR.resolve() if USER_AGENTS_DIR.exists() else USER_AGENTS_DIR
    for pslug, d in _project_dirs().items():
        adir = d / "agents"
        # A self-project (dir == repo root) shares its agents/ dir with the user
        # scope; don't re-read those files as project-scoped and double-count.
        if adir.exists() and adir.resolve() != user_dir:
            for p in sorted(adir.glob("*.md")):
                r = _record_from_def(p, "project", project_slug=pslug)
                defs[r["slug"]] = r

    out: list[dict] = []
    seen: set[str] = set()
    for slug, r in defs.items():
        if r.get("letta_agent_id") in excluded:
            continue
        if r["runtime"] == "builder":
            st = build_state.get(slug)
            if st:
                _merge_build_state(r, st)
            _correct_liveness(r, live_slugs)
        if not include_retired and (r.get("retired") or r.get("state") == "retired"):
            continue
        out.append(r)
        seen.add(slug)

    # NO builder fallback. An agent is what a DEFINITION FILE declares; runtime
    # state is evidence that something ran, and running is not a way to become
    # an agent.
    #
    # This used to synthesize a record from build_agents.json for any slug with
    # no definition file, which is how five of the twelve builders came to exist
    # as "agents" without anyone declaring one: a process ran, wrote its state,
    # and the roster promoted that state to an identity. Everything downstream
    # then treated it as a peer of a real agent — it appeared in the roster, in
    # the chat scope picker, and in the count the operator kept asking about.
    #
    # Their runtime state is untouched and still in build_agents.json (sessions,
    # cwd, last_active), so nothing about their history is lost. They are simply
    # no longer Agents, which is correct: an undeclared process is a Run.
    #
    # To make one of them an agent again, write it a definition file. That is
    # the whole point — it takes an act of declaration.

    out.sort(key=lambda a: a.get("last_active") or a.get("created") or "", reverse=True)
    return out


def get(slug: str) -> dict | None:
    """Resolve an agent by slug, routing id, or linked Letta agent id."""
    for r in list_agents(include_retired=True):
        if slug in (r.get("slug"), r.get("id"), r.get("letta_agent_id")):
            return r
    return None


def all_slugs() -> set[str]:
    slugs = {r["slug"] for r in list_agents(include_retired=True)}
    slugs |= set((_build_agents._load().get("build_agents") or {}).keys())
    return slugs


def mint_slug(name: str, taken: set[str] | None = None) -> str:
    taken = taken if taken is not None else all_slugs()
    base = re.sub(r"[^a-z0-9-]+", "-", (name or "agent").lower()).strip("-")[:48] or "agent"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------

def _path_for(slug: str, project_slug: str | None) -> Path:
    if project_slug:
        d = _project_dirs().get(project_slug)
        if d and d.exists():
            return d / "agents" / f"{slug}.md"
    return USER_AGENTS_DIR / f"{slug}.md"


def _build_fm(*, runtime: str, name: str, purpose: str, capabilities: list,
              triggers_list: list, limits: dict, memory: Any,
              project_slug: str | None, letta_agent_id: str | None,
              role: str | None = None, depth: str | None = None,
              fanout: str | None = None, latitude: str | None = None,
              autonomy: str | None = None, mentionable: bool = False,
              delegatable: bool = False,
              template: str | None = None, retired: bool = False) -> dict:
    fm: dict[str, Any] = {
        "name": name,
        "runtime": runtime,
        "purpose": purpose or "",
        "capabilities": capabilities or [],
        "triggers": [triggers.normalize(t) for t in (triggers_list or [])],
        "limits": limits or dict(DEFAULT_LIMITS),
        "memory": memory,
    }
    # Only emit the role-taxonomy fields when they carry non-default information,
    # so a fresh definition file is not littered with `depth: standard` noise.
    valid_role = _validate_role(role)
    if valid_role:
        fm["role"] = valid_role
    # False is the default and the safe state, so only True is worth writing.
    if mentionable:
        fm["mentionable"] = True
    if delegatable:
        fm["delegatable"] = True
    if depth is not None and _coerce_depth(depth) != DEFAULT_DEPTH:
        fm["depth"] = depth
    if fanout is not None and _coerce_fanout(fanout) != DEFAULT_FANOUT:
        fm["fanout"] = fanout
    # Same emit-only-when-non-default rule, and here it is correctness rather
    # than tidiness: an emitted `latitude: standard` is indistinguishable from a
    # deliberate choice, so it would out-rank nothing but would read as set.
    valid_latitude = _validate_latitude(latitude)
    if valid_latitude and valid_latitude != DEFAULT_LATITUDE:
        fm["latitude"] = valid_latitude
    # Emit `autonomy` only when the agent has opted OUT of the reactive default.
    # An emitted `autonomy: reactive` would read as a deliberate choice but behave
    # exactly like unset, muddying the signal that the operator explicitly wants
    # this agent to stay hands-off.
    valid_autonomy = _validate_autonomy(autonomy) if autonomy is not None else None
    if valid_autonomy and valid_autonomy != DEFAULT_AUTONOMY:
        fm["autonomy"] = valid_autonomy
    if template:
        fm["template"] = template
    if project_slug:
        fm["project_slug"] = project_slug
    if letta_agent_id:
        fm["letta_agent_id"] = letta_agent_id
    if retired:
        fm["retired"] = True
    return fm


def scope_key_for(project_slug: str | None) -> str:
    """The scope an agent's shell dispatch reads, matching the dispatcher's own
    rule (`dispatch_triggers.sh`): a project-linked agent works its project's
    scope, everything else works the workshop scope."""
    return f"project:{project_slug}" if project_slug else "workshop"


def _assert_role_unique(role: str | None, project_slug: str | None,
                        *, exclude_slug: str | None = None) -> None:
    """At most one `triager` and one `keeper` per scope. Raises ValueError naming
    the incumbent, so adopting a group twice is idempotent-by-refusal rather than
    quietly doubling a scope's triage."""
    if role not in SINGLETON_ROLES:
        return
    scope = scope_key_for(project_slug)
    for a in list_agents():
        if a.get("slug") == exclude_slug or a.get("role") != role:
            continue
        if scope_key_for(a.get("project_slug")) == scope:
            raise ValueError(
                f"{scope} already has a {role} ({a['slug']}); one {role} per scope"
            )


def create(*, slug: str, runtime: str, name: str, purpose: str = "",
           capabilities: list | None = None, triggers_list: list | None = None,
           limits: dict | None = None, memory: Any = None,
           project_slug: str | None = None, letta_agent_id: str | None = None,
           role: str | None = None, depth: str | None = None,
           fanout: str | None = None, latitude: str | None = None,
           autonomy: str | None = None,
           template: str | None = None, body: str = "") -> dict:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("slug must be kebab-case alphanumeric, start with a letter, max 64 chars")
    if runtime not in RUNTIMES:
        raise ValueError(f"runtime must be one of {RUNTIMES}")
    if get(slug) is not None:
        raise ValueError(f"agent already exists: {slug}")
    _assert_role_unique(_validate_role(role), project_slug)
    path = _path_for(slug, project_slug)
    fm = _build_fm(runtime=runtime, name=name, purpose=purpose,
                   capabilities=capabilities or [], triggers_list=triggers_list or [],
                   limits=limits or dict(DEFAULT_LIMITS), memory=memory,
                   project_slug=project_slug, letta_agent_id=letta_agent_id,
                   role=role, depth=depth, fanout=fanout, latitude=latitude,
                   autonomy=autonomy,
                   template=template)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_emit(fm, body))
    regenerate_subscriptions()
    return get(slug)


def update(slug: str, *, name: str | None = None, purpose: str | None = None,
           triggers_list: list | None = None, limits: dict | None = None,
           capabilities: list | None = None, memory: Any = None,
           role: str | None = None, depth: str | None = None,
           fanout: str | None = None, latitude: str | None = None,
           autonomy: str | None = None, mentionable: bool | None = None,
           delegatable: bool | None = None,
           body: str | None = None) -> dict:
    """Write changes to the definition file. If the agent has no definition file
    yet (e.g. a builder synthesized from build_agents.json), one is created from
    the current record. Regenerates the event-subscriptions file afterward.

    role/depth/fanout/latitude follow the same convention as the other setters:
    None leaves the field untouched (so a plain triggers edit never disturbs a
    role), a value writes it."""
    rec = get(slug)
    if rec is None:
        raise KeyError(slug)
    real_slug = rec["slug"]
    path = Path(rec["definition_path"]) if rec.get("definition_path") else _path_for(real_slug, rec.get("project_slug"))
    if path.exists():
        fm, existing_body = _parse(path.read_text())
    else:
        fm = _build_fm(
            runtime=rec["runtime"], name=rec["name"], purpose=rec["purpose"],
            capabilities=rec.get("capabilities") or [], triggers_list=rec.get("triggers") or [],
            limits=rec.get("limits") or dict(DEFAULT_LIMITS), memory=rec.get("memory"),
            project_slug=rec.get("project_slug"), letta_agent_id=rec.get("letta_agent_id"),
            role=rec.get("role"), depth=rec.get("depth"), fanout=rec.get("fanout"),
            latitude=rec.get("latitude"), autonomy=rec.get("autonomy"),
            mentionable=bool(rec.get("mentionable")), delegatable=bool(rec.get("delegatable")),
        )
        existing_body = rec.get("definition") or ""

    if name is not None:
        fm["name"] = name
    if purpose is not None:
        fm["purpose"] = purpose
    if triggers_list is not None:
        fm["triggers"] = [triggers.normalize(t) for t in triggers_list]
    if limits is not None:
        fm["limits"] = limits
    if capabilities is not None:
        fm["capabilities"] = capabilities
    if memory is not None:
        fm["memory"] = memory
    if role is not None:
        # empty string clears the role tag; a value sets it (validated: an unknown
        # role raises rather than writing an agent no handler will ever pick up).
        valid_role = _validate_role(role)
        if valid_role:
            # Re-tagging an agent into a singleton role is the other way a scope
            # could grow a second triager, so the constraint is checked on both
            # write paths, not just create.
            _assert_role_unique(valid_role, rec.get("project_slug"), exclude_slug=real_slug)
            fm["role"] = valid_role
        else:
            fm.pop("role", None)
    if depth is not None:
        if _coerce_depth(depth) == DEFAULT_DEPTH:
            fm.pop("depth", None)
        else:
            fm["depth"] = depth
    if fanout is not None:
        if _coerce_fanout(fanout) == DEFAULT_FANOUT:
            fm.pop("fanout", None)
        else:
            fm["fanout"] = fanout
    if latitude is not None:
        # "" clears the scope's latitude (back to inherit); `standard` is never
        # emitted, so setting the default clears it too — a scope that stored its
        # default would read as set while behaving exactly like unset.
        valid_latitude = _validate_latitude(latitude)
        if valid_latitude and valid_latitude != DEFAULT_LATITUDE:
            fm["latitude"] = valid_latitude
        else:
            fm.pop("latitude", None)
    if autonomy is not None:
        # Same rule: "" or the reactive default clears the emitted field so a
        # background-off agent is unambiguously a default agent.
        valid_autonomy = _validate_autonomy(autonomy)
        if valid_autonomy and valid_autonomy != DEFAULT_AUTONOMY:
            fm["autonomy"] = valid_autonomy
        else:
            fm.pop("autonomy", None)
    # Interaction opt-ins: False is the default, so clearing means removing the
    # key rather than writing `mentionable: false`.
    for _flag, _val in (("mentionable", mentionable), ("delegatable", delegatable)):
        if _val is not None:
            if _val:
                fm[_flag] = True
            else:
                fm.pop(_flag, None)
    new_body = existing_body if body is None else body

    fm.setdefault("runtime", rec["runtime"])
    if rec.get("project_slug"):
        fm.setdefault("project_slug", rec["project_slug"])
    if rec.get("letta_agent_id"):
        fm.setdefault("letta_agent_id", rec["letta_agent_id"])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_emit(fm, new_body))
    regenerate_subscriptions()
    return get(real_slug)


def retire(slug: str) -> None:
    """Retire an agent. Builders also flip their runtime state via build_agents;
    definition files (if any) get a `retired: true` flag so the roster hides them
    while the declarative record is preserved."""
    rec = get(slug)
    if rec is None:
        return
    if rec["runtime"] == "builder":
        _build_agents.retire(rec["slug"])
    path = Path(rec["definition_path"]) if rec.get("definition_path") else None
    if path and path.exists():
        fm, body = _parse(path.read_text())
        fm["retired"] = True
        path.write_text(_emit(fm, body))
        regenerate_subscriptions()


# ---------------------------------------------------------------------------
# Templates and template groups (final-plan P4 / roles report 5.1-5.6)
# ---------------------------------------------------------------------------
# A template is a plain agent definition (frontmatter + body) plus three
# template-only fields, so it is readable, diffable, and testable as the thing it
# produces. Adopting one FORKS it: the copy is independent and edits are local.
#
# There is deliberately NO SYNC ENGINE. The stated cost is that template
# improvements do not propagate; the `template: <slug>@<version>` stamp makes a
# future "v3 is out" nudge possible. A propagation engine for one operator and a
# handful of agents is exactly the cruft the rules forbid — re-adopt or hand-edit,
# both take a minute. An edited LINK, by contrast, would silently leak one
# project's edit into every other project that adopted the same template, which is
# the worst available surprise.

def _template_path(slug: str) -> Path:
    if not SLUG_RE.match((slug or "").strip().lower()):
        raise ValueError(f"invalid template slug: {slug!r}")
    return TEMPLATES_DIR / f"{slug.strip().lower()}.md"


def list_templates() -> list[dict]:
    """The shipped templates: [{template, template_version, description, name,
    role, runtime, triggers, purpose}]. Never registry members."""
    out: list[dict] = []
    if not TEMPLATES_DIR.is_dir():
        return out
    for p in sorted(TEMPLATES_DIR.glob("*.md")):
        fm, body = _parse(p.read_text())
        out.append({
            "template": fm.get("template") or p.stem,
            "template_version": int(fm.get("template_version") or 1),
            "description": fm.get("description") or "",
            "name": fm.get("name") or p.stem,
            "runtime": fm.get("runtime") if fm.get("runtime") in RUNTIMES else "assistant",
            "role": _coerce_role(fm.get("role")),
            "purpose": fm.get("purpose") or "",
            "triggers": triggers.definition_triggers(fm),
            "definition": body.strip(),
        })
    return out


def get_template(slug: str) -> dict | None:
    for t in list_templates():
        if t["template"] == (slug or "").strip().lower():
            return t
    return None


def list_groups() -> list[dict]:
    """The shipped template GROUPS. A group is not an array: it carries a member
    list WITH per-member overrides plus `scope_defaults` the adopting scope takes
    on. Two are shipped, not a catalog — `minimal` (the default, no agents) and
    `tended` (triager + keeper, the one-click upgrade)."""
    out: list[dict] = []
    if not GROUPS_DIR.is_dir():
        return out
    for p in sorted(GROUPS_DIR.glob("*.json")):
        try:
            g = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(g, dict):
            continue
        g.setdefault("name", p.stem)
        g.setdefault("members", [])
        g.setdefault("scope_defaults", {})
        out.append(g)
    return out


def get_group(name: str) -> dict | None:
    for g in list_groups():
        if g.get("name") == (name or "").strip().lower():
            return g
    return None


def fork_from_template(template_slug: str, target_slug: str,
                       project_slug: str | None = None,
                       overrides: dict | None = None) -> dict:
    """Copy a template's definition into a scope as an independent agent.

    FORK, NOT LINK: nothing about the returned agent reads back to the template
    afterward. The only trace is a `template: <slug>@<version>` frontmatter stamp,
    which is provenance for a human, not a pointer for a machine.

    `overrides` may set any of name / purpose / triggers / limits / role / depth /
    fanout / latitude / definition (the body). Everything it does not name comes
    from the template verbatim.
    """
    path = _template_path(template_slug)
    if not path.exists():
        raise ValueError(f"unknown template: {template_slug}")
    fm, body = _parse(path.read_text())
    ov = overrides or {}

    version = int(fm.get("template_version") or 1)
    stamp = f"{fm.get('template') or template_slug}@{version}"
    for k in _TEMPLATE_ONLY_FM:
        fm.pop(k, None)

    return create(
        slug=target_slug,
        runtime=ov.get("runtime") or (fm.get("runtime") if fm.get("runtime") in RUNTIMES else "assistant"),
        name=ov.get("name") or fm.get("name") or target_slug,
        purpose=ov.get("purpose") if ov.get("purpose") is not None else (fm.get("purpose") or ""),
        capabilities=ov.get("capabilities") if ov.get("capabilities") is not None else (fm.get("capabilities") or []),
        triggers_list=ov.get("triggers") if ov.get("triggers") is not None else triggers.definition_triggers(fm),
        limits=ov.get("limits") if ov.get("limits") is not None else (fm.get("limits") or dict(DEFAULT_LIMITS)),
        memory=ov.get("memory") if ov.get("memory") is not None else fm.get("memory"),
        project_slug=project_slug,
        role=ov.get("role") if ov.get("role") is not None else fm.get("role"),
        depth=ov.get("depth") if ov.get("depth") is not None else fm.get("depth"),
        fanout=ov.get("fanout") if ov.get("fanout") is not None else fm.get("fanout"),
        latitude=ov.get("latitude") if ov.get("latitude") is not None else fm.get("latitude"),
        template=stamp,
        body=ov.get("definition") if ov.get("definition") is not None else body,
    )


def adopt_group(name: str, project_slug: str | None = None) -> dict:
    """Fork every member of a group into a scope, applying each member's
    overrides. Returns {group, scope_defaults, adopted: [...], skipped: [...]}.

    Idempotent by the one-per-(scope, role) constraint rather than by a bespoke
    check: adopting `tended` twice refuses the second triager and reports it as
    skipped, so the operator gets one triager and a plain explanation instead of
    two agents racing the same watermark.

    `scope_defaults` is RETURNED, not applied here — the registry owns agent
    definitions, not the project registry. The route that adopts on a project's
    behalf applies them.
    """
    g = get_group(name)
    if g is None:
        raise ValueError(f"unknown template group: {name}")
    adopted: list[dict] = []
    skipped: list[dict] = []
    taken = all_slugs()
    for m in g.get("members") or []:
        tpl = (m or {}).get("template")
        if not tpl:
            continue
        target = m.get("slug") or mint_slug(f"{project_slug}-{tpl}" if project_slug else tpl, taken)
        try:
            rec = fork_from_template(tpl, target, project_slug, m.get("overrides") or {})
        except ValueError as e:
            skipped.append({"template": tpl, "reason": str(e)})
            continue
        taken.add(rec["slug"])
        adopted.append(rec)
    return {
        "group": g.get("name"),
        "scope_defaults": g.get("scope_defaults") or {},
        "adopted": adopted,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Event-subscriptions regeneration (masini-sensitive)
# ---------------------------------------------------------------------------

def regenerate_subscriptions() -> dict:
    """Rebuild `.runtime/agent-subscriptions.json` from definition frontmatter.

    UNION, never a wholesale overwrite: registry-derived event subscriptions PLUS
    any pre-existing entries whose agent_id is NOT registry-managed (per-channel
    and other non-registry agents) preserved verbatim. The file and its readers
    (cron event-drain + event_dispatch.py) are unchanged; only the writer's
    source expands.
    """
    try:
        existing = json.loads(SUBSCRIPTIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        existing = {}

    agents = list_agents()
    registry_ids = {a["id"] for a in agents if a.get("id")}

    fresh: dict[str, list[str]] = {s: [] for s in KNOWN_SOURCES}
    for a in agents:
        aid = a.get("id")
        if not aid:
            continue
        for t in a.get("triggers") or []:
            if t.get("kind") == "event":
                for src in t.get("event_sources") or []:
                    if src in fresh and aid not in fresh[src]:
                        fresh[src].append(aid)
            elif t.get("kind") == triggers.DISCORD_SOURCE:
                # A `discord` trigger subscribes its agent to the `discord`
                # source. `channels` narrows WHICH messages should wake it, but
                # the subscriptions file is a source->agent_ids index with no
                # room for a selector, so the channel filter is applied by the
                # consumer against the event body, not here. A trigger the
                # normalizer disabled (no channels) is not wired at all — it can
                # never match, so subscribing it would only produce wakeups the
                # consumer must then throw away.
                if t.get("disabled_reason"):
                    continue
                src = triggers.DISCORD_SOURCE
                if src in fresh and aid not in fresh[src]:
                    fresh[src].append(aid)

    out: dict[str, Any] = {}
    for k, v in existing.items():
        if isinstance(k, str) and k.startswith("_"):
            out[k] = v
    out.setdefault("_schema", "source (string) -> list of letta agent_ids subscribed")
    out.setdefault("_known_sources", list(KNOWN_SOURCES))

    for src in KNOWN_SOURCES:
        prior = existing.get(src) or []
        preserved = [aid for aid in prior if aid not in registry_ids]
        out[src] = list(dict.fromkeys(preserved + fresh.get(src, [])))

    SUBSCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTIONS_PATH.write_text(json.dumps(out, indent=2))
    return out
