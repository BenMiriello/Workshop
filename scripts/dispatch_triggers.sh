#!/bin/bash
# dispatch_triggers.sh — the autonomy dispatcher (orchestration O3).
#
# Called by cron_agent.sh once per tick, AFTER pre-flight and inside the tick
# budget. Decides which scopes have background work due and launches at most a
# few bounded sweeps (project_ralph.sh) detached. This is what makes a project
# (or agent) with a `loop`/`cron` trigger actually run unattended: before O3,
# project_ralph.sh had exactly one caller (the manual "Run now" endpoint), so a
# loop trigger armed nothing.
#
# Decisions (Python, since it needs the registries + the task store):
#   - Project loop triggers: for each project whose triggers include `loop` and
#     whose mode != off, launch a sweep if it has a ready task, no sweep is
#     already running, and it is not a still-unchanged awaiting_input scope.
#     Cap: one project sweep per tick (cost bound; the per-scope flock makes it
#     safe to raise later).
#   - Agent cron/loop triggers: fire when a cron trigger is due (vs
#     .runtime/trigger-state.json) or a loop trigger's scope has ready tasks, then
#     route by the agent's ROLE through ROLE_HANDLERS (see below). Cap: one agent
#     sweep per tick.
#
# Honors the global autonomy override via scripts/autonomy_gate.sh (skips
# entirely). Never blocks: each sweep is launched detached (setsid) and returns
# immediately.

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP

# Deployment config, WORKSHOP_DEV_MODE among it (ADR 0014).
#
# THE TWO HALVES MUST AGREE. The backend and the frontend get this flag from
# systemd `EnvironmentFile=` lines on workshop-backend.service; cron gets no
# EnvironmentFile at all, so before this line dev mode was ON for the API and
# OFF for everything cron started. One flag that answers differently depending
# on who asks is worse than no flag: it makes the gate unauditable. Any cron
# entrypoint that can reach a run target must source this loader, and today that
# means cron_agent.sh, queue_runner_watchdog.sh and this file.
#
# lib/instance.sh is the existing loader and only fills what is still unset, so
# an explicit `WORKSHOP_DEV_MODE=1 dispatch_triggers.sh` still wins.
source "$WORKSHOP/scripts/lib/instance.sh"

# The shell half of developer mode: dev_mode_on / is_self_dir /
# assert_run_target. Same rule as backend/services/app_runner.py and
# frontend/src/lib/devMode.ts; do not restate the rule anywhere else.
source "$WORKSHOP/scripts/dev_mode_gate.sh"

# One switch, read through services/autonomy_override. Fails closed.
source "$WORKSHOP/scripts/autonomy_gate.sh"
PROJECT_RALPH=$WORKSHOP/scripts/project_ralph.sh
MEMORY_KEEPER=$WORKSHOP/scripts/memory_keeper_run.sh
TRIAGER=$WORKSHOP/scripts/triager_run.sh
LOG=$WORKSHOP/.runtime/dispatch.log

ts() { date -Iseconds; }
log() { echo "[$(ts)] [dispatch] $*" >> "$LOG"; }

# One switch. This file used to hold a SECOND implementation of the same
# question — its own json.load of the overlay, next to a flag-file test — which
# is how the two answers drifted apart in the first place. Both are gone; the
# union (and the fail-closed rule) live in services/autonomy_override, reached
# through scripts/autonomy_gate.sh.
if autonomy_engaged; then
  log "autonomy engaged ($(autonomy_reason)); dispatching nothing"
  exit 0
fi

# The decision engine prints one dispatch per line: "TYPE\tSCOPE\tASSIGNEE\tTOKEN".
# TYPE=project -> SCOPE is a slug; TYPE=agent -> SCOPE is a dir, ASSIGNEE=agent:slug.
# TOKEN is the in-scope grant the job carries (agent dispatches only; empty else).
PLAN=$(cd "$WORKSHOP" && python3 - <<'PYEOF'
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.environ["WORKSHOP"] + "/backend")
from services import tasks, jobs, triggers, agents_registry, usage_ledger  # noqa: E402
from services import dispatch_grants  # noqa: E402
# Developer mode (ADR 0014). `is_self_dir` / `dev_mode_on` here are the SAME two
# predicates the enclosing shell sourced from scripts/dev_mode_gate.sh; that file
# names this module as its other half. Called from Python because the loop that
# needs them is Python, not because there is a second rule.
from services import app_runner  # noqa: E402

WORKSHOP = Path(os.environ["WORKSHOP"])
STATE_PATH = WORKSHOP / ".runtime" / "trigger-state.json"
CB_PATH = WORKSHOP / ".runtime" / "circuit-breaker.json"
# Canonical projects registry (moved to .runtime by the pluggable-apps refactor;
# same env override the backend honors).
PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(WORKSHOP / ".runtime" / "projects.json")))

MAX_PROJECT_SWEEPS = 1
MAX_AGENT_SWEEPS = 1

# Circuit breaker (self-healing 2d): after CB_THRESHOLD consecutive sweeps for a
# scope that made zero progress and only failed, skip that scope for a cool-off
# window so a broken scope does not burn the tick budget every dispatch. The
# window grows (base doubling per extra failure, capped) so a persistently broken
# scope backs off further. State is a simple counter file in .runtime for operator
# visibility; the count itself is recomputed from the Jobs log each tick (source
# of truth = jobs.jsonl), so it never drifts.
#
# HALF-OPEN PROBE (a2ff6437): the cool-off does NOT fully reopen the flow, which
# would just re-trigger the failure storm. When the cool-off elapses the breaker
# goes HALF-OPEN and admits EXACTLY ONE probe sweep for the scope. The sweep runs
# detached, so its result is read on a LATER tick from the Jobs log: the first
# completed sweep newer than the probe baseline CLOSES the breaker if it made
# progress (not a failed sweep), or RE-OPENS it with a fresh (longer) cool-off if
# it only failed. Until that probe resolves the scope admits nothing else. The
# per-scope phase + probe marker live in the same circuit-breaker.json store.
CB_THRESHOLD = 3
CB_BASE_SEC = 600      # 10 min after the threshold is first crossed
CB_MAX_SEC = 7200      # 2 h ceiling
NOW = datetime.now(timezone.utc)


def cb_load() -> dict:
    try:
        return json.loads(CB_PATH.read_text())
    except Exception:
        return {}


def cb_save(state: dict) -> None:
    try:
        CB_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CB_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(CB_PATH)
    except Exception:
        pass


CB_STATE = cb_load()


def _coordinator_sweeps(**flt) -> list:
    """Recent completed COORDINATOR sweep Jobs for a scope, newest first (child
    per-task Jobs carry task_id/parent_id and are excluded)."""
    out = []
    for j in jobs.read(limit=50, **flt):
        if j.get("task_id") or j.get("parent_id") or not j.get("ended_at"):
            continue
        out.append(j)
    return out


def _failed_sweep(j: dict) -> bool:
    """A completed sweep that made no progress and only produced failures."""
    try:
        return int(j.get("tasks_failed") or 0) > 0 and int(j.get("tasks_done") or 0) == 0
    except (TypeError, ValueError):
        return False


def _parse_iso(s):
    """Parse an ISO8601 timestamp to an aware UTC datetime, or None."""
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def circuit_open(scope_key: str, **flt) -> tuple[bool, str, bool]:
    """Return (open, reason, half_open). Three persisted phases per scope:
      - CLOSED: leading failed-sweep run < CB_THRESHOLD -> normal dispatch
        (open=False, half_open=False).
      - OPEN: run >= threshold and NOW is inside the cool-off from the most
        recent failure -> scope skipped (open=True).
      - HALF-OPEN: cool-off elapsed -> admit EXACTLY ONE probe sweep
        (open=False, half_open=True). The caller marks it in-flight via
        cb_mark_probe once it actually dispatches. Because the probe runs
        detached, its outcome is read on a LATER tick: the first completed sweep
        newer than the probe baseline closes the breaker if it made progress, or
        re-opens it (fresh, longer cool-off) if it only failed."""
    sweeps = _coordinator_sweeps(**flt)
    n = 0
    last_ended = None
    for j in sweeps:
        if _failed_sweep(j):
            n += 1
            if last_ended is None:
                last_ended = j.get("ended_at")
        else:
            break

    # Resolve a probe admitted on an earlier tick. The caller checks running_job()
    # before us, so an unfinished (still-running) probe never reaches here; if we
    # are here with an in-flight marker, either the probe completed or its process
    # died without writing a Job.
    probe = (CB_STATE.get(scope_key) or {}).get("probe") or {}
    if probe.get("in_flight"):
        newest = sweeps[0] if sweeps else None
        newest_end = _parse_iso(newest.get("ended_at")) if newest else None
        baseline = _parse_iso(probe.get("baseline_ended"))
        if newest is not None and newest_end is not None and (
                baseline is None or newest_end > baseline):
            if not _failed_sweep(newest):
                # Probe SUCCEEDED (the sweep made progress) -> close.
                CB_STATE.pop(scope_key, None)
                return False, "", False
            # Probe FAILED -> fall through to the open computation below, which
            # now counts this fresh failure and restarts the cool-off from it.
        else:
            # No sweep newer than the baseline: the prior admit produced no Job
            # (process died, or a downstream gate blocked it). Re-admit one probe.
            return False, "half-open: re-admitting probe (no prior result)", True

    if n < CB_THRESHOLD or not last_ended:
        CB_STATE.pop(scope_key, None)
        return False, "", False
    cooldown = min(CB_BASE_SEC * (2 ** (n - CB_THRESHOLD)), CB_MAX_SEC)
    end_dt = _parse_iso(last_ended)
    if end_dt is None:
        return False, "", False
    open_until = end_dt + timedelta(seconds=cooldown)
    if NOW < open_until:
        CB_STATE[scope_key] = {
            "phase": "open",
            "consecutive_failures": n,
            "opened_at": end_dt.isoformat(),
            "open_until": open_until.isoformat(),
            "updated": NOW.isoformat(),
            "probe": {"in_flight": False},
        }
        return True, f"{n} consecutive failed sweeps; cooling off until {open_until.isoformat()}", False
    # Cool-off elapsed -> HALF-OPEN: admit exactly one probe sweep. baseline_ended
    # is the newest failed sweep at admit time; any sweep newer than it is the
    # probe's result, read on a later tick.
    CB_STATE[scope_key] = {
        "phase": "half_open",
        "consecutive_failures": n,
        "opened_at": end_dt.isoformat(),
        "open_until": open_until.isoformat(),
        "updated": NOW.isoformat(),
        "probe": {"in_flight": False, "baseline_ended": last_ended},
    }
    return False, f"half-open: admitting one probe after cool-off ({n} prior failures)", True


def cb_mark_probe(scope_key: str) -> None:
    """Record that a half-open probe sweep was actually dispatched, so the NEXT
    tick reads its outcome instead of admitting another. Called only after the
    sweep is appended to the plan."""
    ent = CB_STATE.get(scope_key)
    if not ent:
        return
    probe = ent.setdefault("probe", {})
    probe["in_flight"] = True
    probe["admitted_at"] = NOW.isoformat()
    ent["updated"] = NOW.isoformat()

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}

def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)

def has_ready(root: Path, assignee=None) -> bool:
    try:
        return tasks.next_ready(root, assignee=assignee) is not None
    except Exception:
        return False

def running_job(**flt) -> bool:
    try:
        return bool(jobs.read(status="running", limit=1, **flt))
    except Exception:
        return False

def awaiting_unchanged(slug: str, root: Path) -> bool:
    """Skip a scope whose last Job is awaiting_input and whose task store has not
    changed since it ended (so we don't re-open an identical sweep every tick)."""
    try:
        recent = jobs.read(project_slug=slug, limit=1)
        if not recent or recent[0].get("status") != "awaiting_input":
            return False
        ended = recent[0].get("ended_at") or ""
        store = root / ".tasks.json"
        if not store.exists():
            return True
        import datetime as _dt
        end_dt = _dt.datetime.fromisoformat(ended) if ended else None
        mtime = _dt.datetime.fromtimestamp(store.stat().st_mtime, _dt.timezone.utc)
        if end_dt and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=_dt.timezone.utc)
        return bool(end_dt and mtime <= end_dt)
    except Exception:
        return False

plan = []
state = load_state()
now = None  # triggers.due uses utcnow by default

# Close any phantom 'running' coordinator Jobs whose sweep process died (crash,
# reboot, OOM). Otherwise running_job() below skips that scope forever and wedges
# it permanently. Idempotent + cheap; belt-and-suspenders with the liveness
# watchdog cron so the dispatcher does not depend on that cron being installed.
# (review F7)
try:
    jobs.reconcile_stuck()
except Exception:
    pass

# --- Project loop triggers -------------------------------------------------
try:
    reg = json.loads(PROJECTS_REGISTRY.read_text())
    projects = reg.get("projects") or {}
except Exception:
    projects = {}

proj_started = 0
DEV_MODE = app_runner.dev_mode_on()
for slug, p in sorted(projects.items()):
    if proj_started >= MAX_PROJECT_SWEEPS:
        break

    # DOOR 7 (design §2). The `self: true` registry row — slug `development`,
    # dir = the platform repo — reaches this loop as an ordinary candidate, and
    # until this branch existed nothing here told it apart from `birds`. It was
    # closed only by DATA: that row's autonomy mode happens to be `manual`, so
    # the mode test below skipped it. A stored value someone can change from the
    # UI is not a gate. §8 decision 2: the dispatcher iterates projects always,
    # `development` only when dev mode is on.
    #
    # Gated on the resolved DIRECTORY, not the slug, because the platform repo is
    # reachable under at least five names and a name closes one door. Any future
    # registry row pointing at the repo root is caught by the same line.
    #
    # A safety interlock, not a security boundary: anyone with a shell here edits
    # the source directly. See ADR 0014.
    #
    # `p.get("dir") and` matters: is_self_dir("") resolves to the cwd, and this
    # block runs with cwd = the repo root, so a row with no dir would read as
    # self. The `self` flag is honored too, the same pair app_runner.is_self_project
    # uses, read off the row already in hand instead of re-opening the registry.
    _pdir = p.get("dir")
    if not DEV_MODE and (p.get("self") or (_pdir and app_runner.is_self_dir(_pdir))):
        print(f"skip-dev-mode\tproject:{slug}\t"
              f"{p.get('dir')} is the Workshop's own source tree; "
              f"set WORKSHOP_DEV_MODE=1 to dispatch against it (ADR 0014)",
              file=sys.stderr)
        continue

    autonomy = p.get("autonomy") or {}

    # --- Preset-aware dispatch (O6/O7-config) ---------------------------------
    # A scope with an explicit `autonomy` PRESET is dispatched by the preset's
    # rule: off / on-demand are never background-swept (only an explicit Run-now
    # sweeps them); scheduled sweeps on its cron cadence when ready; continuous
    # sweeps every tick when ready (today's behavior). A scope with NO preset
    # field falls through to the EXISTING mode/trigger logic below, byte-for-byte
    # unchanged, so an unbound project sweeps exactly as before.
    preset = autonomy.get("preset")
    if preset is not None:
        if preset in ("off", "on-demand"):
            continue
        fired_keys = []
        if preset == "scheduled":
            due = False
            for t in (autonomy.get("triggers") or []):
                if t.get("kind") == "cron":
                    key = f"project:{slug}:cron:{t.get('cron_expression','')}"
                    if triggers.due(t.get("cron_expression", ""), state.get(key), now):
                        due = True
                        fired_keys.append(key)
            if not due:
                continue
        elif preset != "continuous":
            continue
        pdir = p.get("dir")
        if not pdir or not Path(pdir).is_dir():
            continue
        root = Path(pdir)
        if running_job(project_slug=slug):
            continue
        opened, cb_reason, cb_half_open = circuit_open(f"project:{slug}", project_slug=slug)
        if opened:
            print(f"skip-circuit\tproject:{slug}\t{cb_reason}", file=sys.stderr)
            continue
        if awaiting_unchanged(slug, root):
            continue
        if not has_ready(root):
            continue
        ok, reason = usage_ledger.can_admit(slug)
        if not ok:
            print(f"skip-budget\tproject:{slug}\t{reason}", file=sys.stderr)
            continue
        plan.append(("project", slug, "", ""))
        if cb_half_open:
            cb_mark_probe(f"project:{slug}")
        # Consume the cron firing only now that it is actually dispatched (F16).
        _pf = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for _k in fired_keys:
            state[_k] = _pf
        proj_started += 1
        continue

    trigs = triggers.project_triggers(autonomy)
    if not any(t.get("kind") == "loop" for t in trigs):
        continue
    if (autonomy.get("mode") or "off") == "off":
        continue
    pdir = p.get("dir")
    if not pdir or not Path(pdir).is_dir():
        continue
    root = Path(pdir)
    if running_job(project_slug=slug):
        continue
    opened, cb_reason, cb_half_open = circuit_open(f"project:{slug}", project_slug=slug)
    if opened:
        print(f"skip-circuit\tproject:{slug}\t{cb_reason}", file=sys.stderr)
        continue
    if awaiting_unchanged(slug, root):
        continue
    if not has_ready(root):
        continue
    ok, reason = usage_ledger.can_admit(slug)
    if not ok:
        print(f"skip-budget\tproject:{slug}\t{reason}", file=sys.stderr)
        continue
    plan.append(("project", slug, "", ""))
    if cb_half_open:
        cb_mark_probe(f"project:{slug}")
    proj_started += 1

# --- Agent cron/loop triggers: the ROLE dispatch table ---------------------
# Before this table the dispatcher hardcoded `if a.get("role") == "memory"` and
# printed skip-assistant for everything else, so FIVE registered agents with live
# triggers never ran once. An agent's `role` (closed enum, validated in
# agents_registry) now selects its handler:
#
#   keeper   -> one memory_keeper_run.sh consolidation pass (what `role: memory`
#               did; the alias still resolves here, so memory-keeper.md is
#               untouched and keeps running).
#   maker    -> the bounded ralph sweep over the agent's scope (what a builder
#               already did; now reachable by role, not only by runtime).
#   triager  -> one note-triage pass over the agent's scope (triager_run.sh): the
#               notes modified since the scope's watermark are each decided
#               task/link/escalate/none. Gated on a stat-only ready predicate, so
#               a scope with no new notes costs nothing.
#   reviewer -> same: reviewer.sh is a gate inside a sweep, not a standalone
#               runner. No dispatch until one exists.
#   broker   -> NEVER dispatches. A broker is demand-only: it is called when
#               something needs adjudication, it does not wake up on a timer.
#
# Default (no role): builder runtime -> maker; assistant runtime -> the assistant
# loop (still unbuilt; skip-assistant, as before).
#
# Each handler takes (a, slug, ctx) and returns (type, scope, assignee) or None.
# A handler owns ALL of its own gates (liveness, circuit breaker, readiness,
# budget), so returning a candidate means the dispatch is certain — that is what
# lets the shared tail below commit the cron firing and the probe mark exactly
# when work actually launches (F16).
agent_started = 0
try:
    agents = agents_registry.list_agents()
except Exception:
    agents = []


def _budget_ok(slug: str) -> bool:
    ok, reason = usage_ledger.can_admit(f"agent:{slug}")
    if not ok:
        print(f"skip-budget\tagent:{slug}\t{reason}", file=sys.stderr)
    return ok


def _dispatch_keeper(a, slug, ctx):
    """Memory consolidation (O6). One pass for the agent's scope."""
    if not _budget_ok(slug):
        return None
    pslug = a.get("project_slug")
    scope_key = f"project:{pslug}" if pslug else "workshop"
    return ("memory", scope_key, f"agent:{slug}")


def _dispatch_maker(a, slug, ctx):
    """The bounded ralph sweep over the agent's own scope dir."""
    from services import build_agents
    try:
        rec = build_agents.get_agent(slug)
    except Exception:
        return None
    if not rec:
        return None
    pslug = rec.get("project_slug")
    root = None
    if pslug:
        root = build_agents._project_dir(pslug)
    if root is None and rec.get("cwd"):
        root = Path(rec["cwd"])
    if root is None or not Path(root).is_dir():
        return None
    if running_job(agent_id=f"agent:{slug}"):
        return None
    opened, cb_reason, cb_half_open = circuit_open(f"agent:{slug}", agent_id=f"agent:{slug}")
    if opened:
        print(f"skip-circuit\tagent:{slug}\t{cb_reason}", file=sys.stderr)
        return None
    if not has_ready(Path(root), assignee=f"agent:{slug}"):
        return None
    if not _budget_ok(slug):
        return None
    ctx["half_open"] = cb_half_open
    ctx["scope_key"] = f"project:{pslug}" if pslug else f"agent:{slug}"
    ctx["scope_path"] = str(root)
    return ("agent", str(root), f"agent:{slug}")


def _dispatch_triager(a, slug, ctx):
    """One note-triage pass over the agent's scope (P4/D5, the ant).

    Gates, in cost order: the READY PREDICATE first (a stat-only scan for notes
    modified since the scope's triage watermark), because a scope with nothing new
    must cost a directory listing and not a budget check, a Job read, or a model
    call; then liveness; then budget. A triager over a quiet scope is a no-op that
    never wakes anything up.

    No circuit breaker here: a triage pass files tasks, it does not work them, so
    it produces no coordinator sweep Job for the breaker to count. Its own
    termination bound is the per-pass note/task caps inside the runner."""
    pslug = a.get("project_slug")
    scope_key = f"project:{pslug}" if pslug else "workshop"
    try:
        from services import triager
        if not triager.has_due_notes(scope_key):
            return None
    except Exception as e:
        print(f"triage-predicate-failed\tagent:{slug}\t{e}", file=sys.stderr)
        return None
    if running_job(agent_id=f"agent:{slug}"):
        return None
    if not _budget_ok(slug):
        return None
    return ("triage", scope_key, f"agent:{slug}")


def _dispatch_reviewer(a, slug, ctx):
    """Judge someone else's work. reviewer.sh runs INSIDE a sweep as its
    completion gate; there is no standalone reviewer runner to dispatch to yet."""
    print(f"skip-no-runner\treviewer\t{slug}", file=sys.stderr)
    return None


def _dispatch_assistant_loop(a, slug, ctx):
    """The non-code data-tier turn runner: still unbuilt. Logged, not dropped."""
    print(f"skip-assistant\t{slug}\t", file=sys.stderr)
    return None


ROLE_HANDLERS = {
    "keeper": _dispatch_keeper,
    "triager": _dispatch_triager,
    "reviewer": _dispatch_reviewer,
    "maker": _dispatch_maker,
    # broker: absent on purpose. Demand-only, never dispatched.
}


def _handler_for(a):
    """The handler an agent's role selects, or None when it must not dispatch."""
    role = a.get("role")            # already coerced by agents_registry (alias-resolved)
    if role == "broker":
        return None
    if role in ROLE_HANDLERS:
        return ROLE_HANDLERS[role]
    return _dispatch_maker if a.get("runtime") == "builder" else _dispatch_assistant_loop


for a in agents:
    if agent_started >= MAX_AGENT_SWEEPS:
        break
    slug = a.get("slug")
    trigs = a.get("triggers") or []
    # Per-agent autonomy gate (C5, autonomy-model 2026-07-18 §4). Uses
    # triggers.effective_autonomy which honors the workshop override overlay
    # then reads the agent's own `autonomy` field. A reactive agent — the
    # default for a fresh agent per autonomy-model §1.1 — never fires
    # background triggers, only manual invocations. One-time default flip
    # applied here so existing agents that shipped with declared triggers
    # keep working: an agent whose stored autonomy is the (default) reactive
    # AND which carries a non-empty triggers list is treated as `supervised`
    # for the purposes of this tick. The operator promotes to autonomous
    # explicitly through the AutonomyPanel; until then supervised runs land
    # in `in_review`.
    try:
        eff = triggers.effective_autonomy(slug or "")
    except Exception:
        eff = "reactive"
    if eff == "reactive" and trigs:
        eff = "supervised"
    if eff == "reactive":
        print(f"skip-reactive\t{slug}\t(autonomy=reactive; manual invocation only)", file=sys.stderr)
        continue
    if not slug or not trigs:
        # A registered agent with NO triggers can never be dispatched. Silent
        # until now, which is why all 20 builder agents were invisible: they
        # carry `triggers: []`, so `maker` - a fully built handler - has never
        # fired once. Say so, rather than dropping them without a word.
        if slug and not trigs:
                print(f"skip-no-triggers\t{slug}\t(role={a.get('role') or 'none'}; registered but undispatchable)", file=sys.stderr)
        continue
    fire = False
    fired_cron_keys = []  # committed to state only if this agent is dispatched (F16)
    for t in trigs:
        if t.get("kind") == "cron":
            key = f"agent:{slug}:cron:{t.get('cron_expression','')}"
            if triggers.due(t.get("cron_expression", ""), state.get(key), now):
                fire = True
                fired_cron_keys.append(key)
        elif t.get("kind") == "loop":
            fire = fire or True  # loop fires when the scope has ready tasks (checked below)
    if not fire:
        # Distinguish "not due yet" from "can NEVER fire here". This loop only
        # matches `cron` and `loop`; an agent whose triggers are all some other
        # kind is dropped every tick, forever, with no trace. That is why the
        # triager (the bridge agent, trigger kind `discord`) has never run despite 530
        # lines of implemented handler behind it - .runtime/triager/ does not
        # exist. An unroutable trigger kind is a wiring bug, not a schedule.
        kinds = {t.get("kind") for t in trigs}
        if not (kinds & {"cron", "loop"}):
            print(f"skip-unroutable\t{slug}\t(role={a.get('role') or 'none'} "
                  f"trigger-kinds={sorted(k for k in kinds if k)}; dispatcher fires cron/loop only)",
                  file=sys.stderr)
        continue

    handler = _handler_for(a)
    if handler is None:
        continue
    ctx = {"half_open": False, "scope_key": None, "scope_path": None}
    candidate = handler(a, slug, ctx)
    if candidate is None:
        continue
    typ, scope, assignee = candidate

    # The job's in-scope grant: an agent is not the operator, so it carries what
    # it was handed rather than inheriting the operator's ambient reach. GENEROUS
    # by design — read+write over its OWN scope, i.e. exactly what it already
    # reaches. Best-effort: a mint failure must never wedge the dispatcher, it
    # just means the sweep runs without a token (today's behavior).
    token = ""
    if typ == "agent":
        try:
            token = dispatch_grants.mint_in_scope_grant(
                slug, ctx["scope_key"] or f"agent:{slug}",
                scope_path=ctx["scope_path"],
            )["id"]
        except Exception as e:
            print(f"grant-mint-failed\tagent:{slug}\t{e}", file=sys.stderr)

    plan.append((typ, scope, assignee, token))
    if ctx["half_open"]:
        cb_mark_probe(f"agent:{slug}")
    # Consume the cron firing ONLY now that it is actually dispatched; a firing
    # skipped by any gate above stays "due" for the next tick instead of being
    # silently lost until the next cron occurrence. (review F16)
    _fire_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for _k in fired_cron_keys:
        state[_k] = _fire_ts
    agent_started += 1

save_state(state)
cb_save(CB_STATE)
for typ, scope, assignee, token in plan:
    print(f"{typ}\t{scope}\t{assignee}\t{token}")
PYEOF
)

# Launch each planned sweep detached so the tick never blocks on a sweep.
COUNT=0
while IFS=$'\t' read -r TYPE SCOPE ASSIGNEE TOKEN; do
  [[ -z "$TYPE" ]] && continue
  if [[ "$TYPE" == "project" ]]; then
    log "launch project sweep: $SCOPE"
    setsid nohup "$PROJECT_RALPH" "$SCOPE" >/dev/null 2>&1 &
  elif [[ "$TYPE" == "agent" ]]; then
    # Door 5 (design §2): an agent dispatch names a DIRECTORY, so the shell gate
    # applies verbatim here. `_dispatch_maker` takes the dir from the agent's
    # build-agent record, and four retired `workshop-builder*` records carry the
    # repo root. This is the last point before a real sweep starts.
    if ! assert_run_target "$SCOPE" "dispatch/agent:${ASSIGNEE#agent:}" 2>>"$LOG"; then
      log "refused agent sweep: dir=$SCOPE assignee=$ASSIGNEE (developer mode off; ADR 0014)"
      continue
    fi
    log "launch agent sweep: dir=$SCOPE assignee=$ASSIGNEE grant=${TOKEN:0:8}"
    setsid nohup "$PROJECT_RALPH" --dir "$SCOPE" --assignee "$ASSIGNEE" --grant-token "${TOKEN:-}" >/dev/null 2>&1 &
  elif [[ "$TYPE" == "memory" ]]; then
    log "launch memory consolidation: scope=$SCOPE assignee=$ASSIGNEE"
    setsid nohup bash "$MEMORY_KEEPER" "$SCOPE" >/dev/null 2>&1 &
  elif [[ "$TYPE" == "triage" ]]; then
    # The agent slug carries the definition body whose `## Rules` section the pass
    # runs BEFORE the model, so it travels with the dispatch.
    log "launch note triage: scope=$SCOPE assignee=$ASSIGNEE"
    setsid nohup bash "$TRIAGER" "$SCOPE" --agent "${ASSIGNEE#agent:}" >/dev/null 2>&1 &
  fi
  COUNT=$((COUNT+1))
done <<< "$PLAN"

log "dispatched $COUNT sweep(s)"
exit 0
