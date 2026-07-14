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
from pathlib import Path

sys.path.insert(0, "/home/clawd-a/workshop/backend")
from services import tasks, jobs, triggers, agents_registry  # noqa: E402

WORKSHOP = Path("/home/clawd-a/workshop")
STATE_PATH = WORKSHOP / ".runtime" / "trigger-state.json"
# Canonical projects registry (moved to .runtime by the pluggable-apps refactor;
# same env override the backend honors).
PROJECTS_REGISTRY = Path(os.environ.get(
    "WORKSHOP_PROJECTS_REGISTRY", str(WORKSHOP / ".runtime" / "projects.json")))

MAX_PROJECT_SWEEPS = 1
MAX_AGENT_SWEEPS = 1

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
    if awaiting_unchanged(slug, root):
        continue
    if not has_ready(root):
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
    for t in trigs:
        if t.get("kind") == "cron":
            key = f"agent:{slug}:cron:{t.get('cron_expression','')}"
            if triggers.due(t.get("cron_expression", ""), state.get(key), now):
                fire = True
                state[key] = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).replace(microsecond=0).isoformat()
        elif t.get("kind") == "loop":
            fire = fire or True  # loop fires when the scope has ready tasks (checked below)
    if not fire:
        continue
    if runtime != "builder":
        # Assistant-runtime background execution = a data-tier turn runner; that
        # is a later phase. Log and skip so we do not silently drop it.
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
    if not has_ready(Path(root), assignee=f"agent:{slug}"):
        continue
    plan.append(("agent", str(root), f"agent:{slug}"))
    agent_started += 1

save_state(state)
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
  fi
  COUNT=$((COUNT+1))
done <<< "$PLAN"

log "dispatched $COUNT sweep(s)"
exit 0
