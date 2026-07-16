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
#   - Agent cron/loop triggers (builder runtime): fire a scope sweep when a cron
#     trigger is due (vs .runtime/trigger-state.json) or a loop trigger's scope
#     has ready tasks. Cap: one agent sweep per tick. Assistant-runtime agents
#     need a data-tier turn runner (not the ralph sweep); logged + skipped here
#     for a later phase.
#
# Honors the global AUTONOMY_DISABLED flag (skips entirely). Never blocks: each
# sweep is launched detached (setsid) and returns immediately.

set -u

WORKSHOP=/home/clawd-a/workshop
AUTONOMY_FLAG=$WORKSHOP/.runtime/AUTONOMY_DISABLED
PROJECT_RALPH=$WORKSHOP/scripts/project_ralph.sh
MEMORY_KEEPER=$WORKSHOP/scripts/memory_keeper_run.sh
LOG=$WORKSHOP/.runtime/dispatch.log

ts() { date -Iseconds; }
log() { echo "[$(ts)] [dispatch] $*" >> "$LOG"; }

if [[ -f "$AUTONOMY_FLAG" ]]; then
  log "global autonomy off-switch present; dispatching nothing"
  exit 0
fi

# The decision engine prints one dispatch per line: "TYPE\tSCOPE\tASSIGNEE".
# TYPE=project -> SCOPE is a slug; TYPE=agent -> SCOPE is a dir, ASSIGNEE=agent:slug.
PLAN=$(cd "$WORKSHOP" && python3 - <<'PYEOF'
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/clawd-a/workshop/backend")
from services import tasks, jobs, triggers, agents_registry, usage_ledger  # noqa: E402

WORKSHOP = Path("/home/clawd-a/workshop")
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


def circuit_open(scope_key: str, **flt) -> tuple[bool, str]:
    """Return (open, reason). Counts the leading run of failed sweeps for the
    scope; once it reaches CB_THRESHOLD, the scope is skipped until the cool-off
    window (from the most recent failure) elapses. Records state for visibility."""
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
    if n < CB_THRESHOLD or not last_ended:
        CB_STATE.pop(scope_key, None)
        return False, ""
    cooldown = min(CB_BASE_SEC * (2 ** (n - CB_THRESHOLD)), CB_MAX_SEC)
    try:
        end_dt = datetime.fromisoformat(last_ended)
    except (TypeError, ValueError):
        return False, ""
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    open_until = end_dt + timedelta(seconds=cooldown)
    CB_STATE[scope_key] = {
        "consecutive_failures": n,
        "open_until": open_until.isoformat(),
        "updated": NOW.isoformat(),
    }
    if NOW < open_until:
        return True, f"{n} consecutive failed sweeps; cooling off until {open_until.isoformat()}"
    return False, ""

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
for slug, p in sorted(projects.items()):
    if proj_started >= MAX_PROJECT_SWEEPS:
        break
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
        opened, cb_reason = circuit_open(f"project:{slug}", project_slug=slug)
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
        plan.append(("project", slug, ""))
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
    opened, cb_reason = circuit_open(f"project:{slug}", project_slug=slug)
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
    plan.append(("project", slug, ""))
    proj_started += 1

# --- Agent cron/loop triggers (builder runtime) ----------------------------
agent_started = 0
try:
    agents = agents_registry.list_agents()
except Exception:
    agents = []

for a in agents:
    if agent_started >= MAX_AGENT_SWEEPS:
        break
    slug = a.get("slug")
    runtime = a.get("runtime")
    trigs = a.get("triggers") or []
    if not slug or not trigs:
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
        continue
    if runtime != "builder":
        # Assistant-runtime CONSOLIDATION dispatch (O6): the memory-consolidation
        # organ is the one assistant agent the dispatcher drives. An agent with
        # `role: memory` whose cron trigger is due runs one memory_keeper_run.sh
        # pass (detached), honoring MAX_AGENT_SWEEPS + the usage budget. Every
        # other assistant agent remains skipped exactly as before.
        if a.get("role") == "memory":
            ok, reason = usage_ledger.can_admit(f"agent:{slug}")
            if not ok:
                print(f"skip-budget\tagent:{slug}\t{reason}", file=sys.stderr)
                continue
            pslug = a.get("project_slug")
            scope_key = f"project:{pslug}" if pslug else "workshop"
            plan.append(("memory", scope_key, f"agent:{slug}"))
            _fire_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for _k in fired_cron_keys:
                state[_k] = _fire_ts
            agent_started += 1
            continue
        # Other assistant-runtime agents: non-code data-tier turn runner is a
        # later phase. Log and skip so we do not silently drop it.
        print(f"skip-assistant\t{slug}\t", file=sys.stderr)
        continue
    # Resolve the builder's scope dir (linked project dir else its cwd).
    from services import build_agents
    try:
        rec = build_agents.get_agent(slug)
    except Exception:
        continue
    pslug = rec.get("project_slug")
    root = None
    if pslug:
        root = build_agents._project_dir(pslug)
    if root is None and rec.get("cwd"):
        root = Path(rec["cwd"])
    if root is None or not Path(root).is_dir():
        continue
    if running_job(agent_id=f"agent:{slug}"):
        continue
    opened, cb_reason = circuit_open(f"agent:{slug}", agent_id=f"agent:{slug}")
    if opened:
        print(f"skip-circuit\tagent:{slug}\t{cb_reason}", file=sys.stderr)
        continue
    if not has_ready(Path(root), assignee=f"agent:{slug}"):
        continue
    ok, reason = usage_ledger.can_admit(f"agent:{slug}")
    if not ok:
        print(f"skip-budget\tagent:{slug}\t{reason}", file=sys.stderr)
        continue
    plan.append(("agent", str(root), f"agent:{slug}"))
    # Consume the cron firing ONLY now that it is actually dispatched; a firing
    # skipped by any gate above stays "due" for the next tick instead of being
    # silently lost until the next cron occurrence. (review F16)
    _fire_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for _k in fired_cron_keys:
        state[_k] = _fire_ts
    agent_started += 1

save_state(state)
cb_save(CB_STATE)
for typ, scope, assignee in plan:
    print(f"{typ}\t{scope}\t{assignee}")
PYEOF
)

# Launch each planned sweep detached so the tick never blocks on a sweep.
COUNT=0
while IFS=$'\t' read -r TYPE SCOPE ASSIGNEE; do
  [[ -z "$TYPE" ]] && continue
  if [[ "$TYPE" == "project" ]]; then
    log "launch project sweep: $SCOPE"
    setsid nohup "$PROJECT_RALPH" "$SCOPE" >/dev/null 2>&1 &
  elif [[ "$TYPE" == "agent" ]]; then
    log "launch agent sweep: dir=$SCOPE assignee=$ASSIGNEE"
    setsid nohup "$PROJECT_RALPH" --dir "$SCOPE" --assignee "$ASSIGNEE" >/dev/null 2>&1 &
  elif [[ "$TYPE" == "memory" ]]; then
    log "launch memory consolidation: scope=$SCOPE assignee=$ASSIGNEE"
    setsid nohup bash "$MEMORY_KEEPER" "$SCOPE" >/dev/null 2>&1 &
  fi
  COUNT=$((COUNT+1))
done <<< "$PLAN"

log "dispatched $COUNT sweep(s)"
exit 0
