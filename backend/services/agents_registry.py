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
import os
import re
from pathlib import Path
from typing import Any

import yaml

from services import triggers
from services import build_agents as _build_agents

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

# Kept in sync with routes/agents.py KNOWN_SOURCES — the event-source vocabulary
# the subscriptions file is partitioned by.
KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"]

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
        "capabilities": fm.get("capabilities") or [],
        "triggers": triggers.definition_triggers(fm),
        "limits": fm.get("limits") or dict(DEFAULT_LIMITS),
        "memory": fm.get("memory"),
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


def _record_from_build_state(slug: str, raw: dict) -> dict:
    """Synthesize a builder record from build_agents.json when no definition file
    exists yet (pre-migration, or a builder whose triggers were never edited)."""
    project_slug = raw.get("project_slug")
    return {
        "slug": slug,
        "runtime": "builder",
        "name": raw.get("name") or slug,
        "purpose": raw.get("purpose") or "",
        "capabilities": [],
        "triggers": [],
        "limits": dict(DEFAULT_LIMITS),
        "memory": None,
        "project_slug": project_slug,
        "letta_agent_id": None,
        "scope": "project" if project_slug else "user",
        "definition": "",
        "definition_path": None,
        "retired": raw.get("state") == "retired",
        "source": "build_state",
        "id": slug,
    }


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

    # Builder fallback: build agents without a definition file yet.
    for slug, st in build_state.items():
        if slug in seen:
            continue
        r = _record_from_build_state(slug, st)
        _merge_build_state(r, st)
        _correct_liveness(r, live_slugs)
        if not include_retired and (r.get("retired") or r.get("state") == "retired"):
            continue
        out.append(r)

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
              retired: bool = False) -> dict:
    fm: dict[str, Any] = {
        "name": name,
        "runtime": runtime,
        "purpose": purpose or "",
        "capabilities": capabilities or [],
        "triggers": [triggers.normalize(t) for t in (triggers_list or [])],
        "limits": limits or dict(DEFAULT_LIMITS),
        "memory": memory,
    }
    if project_slug:
        fm["project_slug"] = project_slug
    if letta_agent_id:
        fm["letta_agent_id"] = letta_agent_id
    if retired:
        fm["retired"] = True
    return fm


def create(*, slug: str, runtime: str, name: str, purpose: str = "",
           capabilities: list | None = None, triggers_list: list | None = None,
           limits: dict | None = None, memory: Any = None,
           project_slug: str | None = None, letta_agent_id: str | None = None,
           body: str = "") -> dict:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("slug must be kebab-case alphanumeric, start with a letter, max 64 chars")
    if runtime not in RUNTIMES:
        raise ValueError(f"runtime must be one of {RUNTIMES}")
    if get(slug) is not None:
        raise ValueError(f"agent already exists: {slug}")
    path = _path_for(slug, project_slug)
    fm = _build_fm(runtime=runtime, name=name, purpose=purpose,
                   capabilities=capabilities or [], triggers_list=triggers_list or [],
                   limits=limits or dict(DEFAULT_LIMITS), memory=memory,
                   project_slug=project_slug, letta_agent_id=letta_agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_emit(fm, body))
    regenerate_subscriptions()
    return get(slug)


def update(slug: str, *, name: str | None = None, purpose: str | None = None,
           triggers_list: list | None = None, limits: dict | None = None,
           capabilities: list | None = None, memory: Any = None,
           body: str | None = None) -> dict:
    """Write changes to the definition file. If the agent has no definition file
    yet (e.g. a builder synthesized from build_agents.json), one is created from
    the current record. Regenerates the event-subscriptions file afterward."""
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
