"""Usage ledger + budget allocator for autonomous background work.

Two separable concerns (kept separate on purpose):

1. ACCOUNTING (sliding-window log). Every autonomous worker run appends one line
   `{ts, project, agent, tokens, job_id}` to `.runtime/usage.jsonl`. Usage over a
   trailing window is the sum of entries newer than `now - window`. Exact and
   directly reportable (a project's share is one sum); no drip/refill worker to
   lag or crash. At personal scale a 5h window holds at most a few hundred
   entries, so a plain log beats a token bucket or GCRA here.

2. ALLOCATION (weighted max-min progressive fill, HTB floor/ceil). Given how much
   each project has already used this window and a small config (app-wide cap
   B_app, and per-project weight/floor/ceil), compute how many tokens each project
   may still spend. Guarantees floors first, then shares the remainder in
   proportion to weight up to each project's ceil. Proportional scale-down falls
   out for free: when demand exceeds the pool, every backlogged project converges
   below nominal, weighted. Single resource (tokens), so this is plain weighted
   max-min, NOT DRF.

CHATS ARE EXEMPT. Interactive chat turns are not Jobs and are never recorded
here, so they are naturally unmetered. Only autonomous sweeps write to the ledger.

Pure stdlib so it is importable both inside the FastAPI app and from shell
writers (`python -m services.usage_ledger record ...`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

WINDOW_S = 5 * 3600  # rolling 5-hour window, matching Claude's usage windows

LEDGER_PATH = Path(os.environ.get(
    "WORKSHOP_USAGE_LEDGER",
    "/home/clawd-a/workshop/.runtime/usage.jsonl",
))
BUDGET_CONFIG_PATH = Path(os.environ.get(
    "WORKSHOP_USAGE_BUDGET",
    "/home/clawd-a/workshop/.runtime/usage-budget.json",
))

# Default budget if no config file exists yet. app_total is the app-wide token
# ceiling per 5h window across ALL autonomous work. Per-project entries are
# optional; a project with no entry uses `default_project`. weight drives the
# proportional share; floor is a guaranteed minimum; ceil caps how much one
# project may borrow when others are idle (null = up to the whole app pool).
_DEFAULT_CONFIG = {
    "app_total": 2_000_000,
    # Headroom kept free so a sweep never starts with a sliver of budget and
    # overshoots (we can't know a task's real cost until it runs). Admission
    # refuses once app_used + this reserve would exceed app_total.
    "nominal_task_tokens": 150_000,
    # Fraction of the app-wide budget autonomous work may consume. The remainder
    # is reserved so the operator's interactive chats always have headroom.
    # Autonomous admission (can_admit) is gated at this fraction; interactive
    # chats are never recorded here, so they are free to use the whole budget.
    # Env WORKSHOP_AUTONOMY_BUDGET_FRACTION overrides this (clamped). (research rec 2)
    "autonomy_budget_fraction": 0.65,
    "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
    "projects": {},
}

# Clamp bounds for the autonomy budget fraction: never below a floor that would
# starve autonomy entirely, never above the full budget (which would leave no
# interactive headroom at all).
_FRACTION_MIN = 0.1
_FRACTION_MAX = 1.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts_iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _parse_ts(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------

def record(project: str | None, tokens: int, *, agent: str | None = None,
           job_id: str | None = None, ts: str | None = None,
           task_id: str | None = None, run: str | None = None,
           role: str | None = None) -> None:
    """Append one usage entry. `project` is the scope the tokens are billed to
    (None -> "unbound"). Safe for concurrent shell + backend writers (append is
    atomic for small lines).

    Optional attribution (all backward-compatible; older entries omitting them
    still load): `task_id` (the ticket the spend served), `run` (the Job/attempt
    id), and `role` ("worker" | "reviewer") so the same scope's worker and
    auxiliary reviewer spend are distinguishable for postmortems. (research rec 3)"""
    entry = {
        "ts": ts or _ts_iso(),
        "project": project or "unbound",
        "tokens": int(max(0, tokens)),
    }
    if agent:
        entry["agent"] = agent
    if job_id:
        entry["job_id"] = job_id
    if task_id:
        entry["task_id"] = task_id
    if run:
        entry["run"] = run
    if role:
        entry["role"] = role
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_entries() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    out: list[dict] = []
    for line in LEDGER_PATH.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def window_usage(now: datetime | None = None, window_s: int = WINDOW_S) -> dict:
    """Token usage over the trailing window: app-wide total + per-project."""
    cutoff = (now or _now()).timestamp() - window_s
    by_project: dict[str, int] = {}
    total = 0
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is None or t < cutoff:
            continue
        tok = int(e.get("tokens") or 0)
        by_project[e.get("project", "unbound")] = by_project.get(e.get("project", "unbound"), 0) + tok
        total += tok
    return {"app": total, "by_project": by_project, "window_s": window_s}


def timeseries(now: datetime | None = None, window_s: int = WINDOW_S,
               buckets: int = 20) -> dict:
    """Cumulative per-project token usage across the trailing window, sampled at
    `buckets` evenly spaced points. Each point carries the running total (from
    window start up to that point) for every project that has usage in the
    window, so a line chart shows how the window's usage accumulated per app and
    the last point equals the current window total. Rolling: the window start is
    `now - window_s`, so entries that have aged out are already excluded."""
    end = (now or _now()).timestamp()
    start = end - window_s
    step = window_s / buckets
    entries = []
    projects: set[str] = set()
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is None or t < start:
            continue
        proj = e.get("project", "unbound")
        projects.add(proj)
        entries.append((t, proj, int(e.get("tokens") or 0)))
    entries.sort(key=lambda x: x[0])
    slugs = sorted(projects)
    points = []
    running: dict[str, int] = {s: 0 for s in slugs}
    idx = 0
    for b in range(1, buckets + 1):
        edge = start + step * b
        while idx < len(entries) and entries[idx][0] <= edge:
            _, proj, tok = entries[idx]
            running[proj] += tok
            idx += 1
        pt = {"t": datetime.fromtimestamp(edge, tz=timezone.utc).isoformat()}
        pt.update({s: running[s] for s in slugs})
        points.append(pt)
    return {"window_s": window_s, "projects": slugs, "points": points}


def prune(now: datetime | None = None, window_s: int = WINDOW_S) -> int:
    """Drop entries older than the window. Opportunistic; returns rows removed."""
    if not LEDGER_PATH.exists():
        return 0
    cutoff = (now or _now()).timestamp() - window_s
    kept, removed = [], 0
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is not None and t >= cutoff:
            kept.append(e)
        else:
            removed += 1
    if removed:
        tmp = LEDGER_PATH.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in kept))
        tmp.replace(LEDGER_PATH)
    return removed


# ---------------------------------------------------------------------------
# Config + allocation
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if BUDGET_CONFIG_PATH.exists():
        try:
            cfg = json.loads(BUDGET_CONFIG_PATH.read_text())
            # Fill any missing top-level keys from the default.
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_CONFIG)


def update_config(app_total: int | None = None,
                  autonomy_budget_fraction: float | None = None) -> dict:
    """Persist the two operator-tunable budget knobs onto the current config and
    write it back atomically. Only the app-wide cap and the autonomy fraction are
    editable here; per-project weights/floors stay a file-level concern. Values
    are validated and clamped so a bad write can neither zero the cap nor starve
    autonomy. Returns the saved config."""
    cfg = load_config()
    if app_total is not None:
        try:
            total = int(app_total)
        except (TypeError, ValueError):
            raise ValueError("app_total must be an integer")
        if total < 1:
            raise ValueError("app_total must be at least 1")
        cfg["app_total"] = total
    if autonomy_budget_fraction is not None:
        try:
            frac = float(autonomy_budget_fraction)
        except (TypeError, ValueError):
            raise ValueError("autonomy_budget_fraction must be a number")
        cfg["autonomy_budget_fraction"] = max(_FRACTION_MIN, min(_FRACTION_MAX, frac))
    BUDGET_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BUDGET_CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(BUDGET_CONFIG_PATH)
    return cfg


def _autonomy_budget_fraction(cfg: dict) -> float:
    """Fraction of the app-wide budget autonomous work may consume before it
    yields to reserved interactive headroom. Env WORKSHOP_AUTONOMY_BUDGET_FRACTION
    wins over the config value; both are clamped to a sane range so a bad value
    can neither starve autonomy nor erase the operator's headroom. (research rec 2)"""
    raw = os.environ.get("WORKSHOP_AUTONOMY_BUDGET_FRACTION")
    if raw is None or str(raw).strip() == "":
        raw = cfg.get("autonomy_budget_fraction",
                      _DEFAULT_CONFIG["autonomy_budget_fraction"])
    try:
        frac = float(raw)
    except (TypeError, ValueError):
        frac = _DEFAULT_CONFIG["autonomy_budget_fraction"]
    return max(_FRACTION_MIN, min(_FRACTION_MAX, frac))


def _project_params(cfg: dict, slug: str) -> dict:
    p = dict(cfg.get("default_project", _DEFAULT_CONFIG["default_project"]))
    p.update(cfg.get("projects", {}).get(slug, {}))
    return p


def plan_budgets(used_by_project: dict[str, int], demanders: Iterable[str],
                 cfg: dict) -> dict[str, float]:
    """Weighted max-min progressive fill with HTB floor/ceil.

    Returns each demanding project's TOTAL token allowance for this window (its
    used amount counts against it, so remaining = allowance - used). `demanders`
    is the set of projects with pending work; only they compete for the shared
    remainder, but every known project's floor is reserved first.

    Squeeze and borrow both fall out of the one loop:
      - squeeze: when the pool is small, the per-weight increment shrinks and
        every demander lands below nominal, weighted.
      - borrow:  ceil > floor lets a backlogged project take idle projects'
        unclaimed share, up to its ceil.
    """
    app_total = float(cfg.get("app_total", _DEFAULT_CONFIG["app_total"]))
    demanders = list(demanders)
    # Union of configured projects + demanders + anyone with usage, so floors are
    # honored even for projects not currently demanding.
    known = set(demanders) | set(used_by_project) | set(cfg.get("projects", {}))

    params = {s: _project_params(cfg, s) for s in known}
    floors = {s: float(params[s].get("floor") or 0) for s in known}

    # If configured floors over-subscribe the app pool (misconfig), scale them
    # down proportionally so we never promise more than exists.
    reserved = sum(floors.values())
    if reserved > app_total and reserved > 0:
        scale = app_total / reserved
        floors = {s: f * scale for s, f in floors.items()}
        reserved = app_total

    # Start every demander at its floor (guaranteed minimum).
    budget: dict[str, float] = {s: floors.get(s, 0.0) for s in demanders}

    # Pool = app total minus ALL reserved floors (idle projects keep their floor
    # reserved; only the leftover is shareable).
    pool = max(0.0, app_total - reserved)

    def ceil_of(s: str) -> float:
        c = params[s].get("ceil")
        return app_total if c is None else float(c)

    active = [s for s in demanders if budget[s] < ceil_of(s)]
    # Progressive fill: hand out the pool in proportion to weight, clamping at
    # each ceil, until the pool is empty or everyone is capped.
    guard = 0
    while pool > 1e-6 and active and guard < 10_000:
        guard += 1
        wsum = sum(float(params[s].get("weight") or 0) for s in active)
        if wsum <= 0:
            break
        inc = pool / wsum  # tokens per unit weight this round
        newly_capped = []
        for s in active:
            want = inc * float(params[s].get("weight") or 0)
            room = ceil_of(s) - budget[s]
            grant = min(want, room)
            budget[s] += grant
            pool -= grant
            if budget[s] >= ceil_of(s) - 1e-6:
                newly_capped.append(s)
        if not newly_capped:
            break  # everyone took their full share; pool exhausted evenly
        active = [s for s in active if s not in newly_capped]
    return budget


def status(now: datetime | None = None) -> dict:
    """Operator-facing snapshot: app-wide + per-project used/allowance/share."""
    cfg = load_config()
    usage = window_usage(now)
    used = usage["by_project"]
    # Treat every project with recent usage or config as a "demander" for the
    # share view (so the operator sees allowances even when nothing is pending).
    demanders = set(used) | set(cfg.get("projects", {}))
    budgets = plan_budgets(used, demanders, cfg)
    projects = {}
    for s in sorted(demanders):
        u = used.get(s, 0)
        allow = budgets.get(s, 0.0)
        projects[s] = {
            "used": u,
            "allowance": round(allow),
            "remaining": round(max(0.0, allow - u)),
            "share_of_app": round(u / usage["app"], 3) if usage["app"] else 0.0,
        }
    return {
        "window_s": usage["window_s"],
        "app_total": cfg.get("app_total"),
        "app_used": usage["app"],
        "app_remaining": max(0, int(cfg.get("app_total", 0)) - usage["app"]),
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# Admission (the enforcement primitive the dispatcher + sweeps call)
# ---------------------------------------------------------------------------

def _inflight_coordinator_sweeps() -> int:
    """Count coordinator sweep Jobs currently `running` (no task_id / parent_id: a
    child task Job carries both). Each holds up to a nominal task's spend that has
    NOT yet landed in the ledger, since billing happens once at ralph exit (up to
    ~20 min later). Best-effort: any failure yields 0 (falls back to the old
    single-nominal reserve). (review F8a)"""
    try:
        from services import jobs
        running = jobs.read(status="running", limit=1000)
        return sum(1 for r in running
                   if not r.get("task_id") and not r.get("parent_id"))
    except Exception:
        return 0


def can_admit(scope: str | None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether one more autonomous sweep/task may start in `scope` (a project
    slug or agent label). Returns (ok, reason).

    Autonomous work is admitted only up to a RESERVED FRACTION of the app-wide
    budget (default ~0.65), leaving the rest free for the operator's interactive
    chats; autonomous work always yields to that headroom. A per-project `ceil`
    (when set) caps one scope's borrow. We refuse once less than one nominal
    task's worth of the autonomous ceiling remains, so a task never starts on a
    sliver of budget and overshoots (its true cost is unknown until it runs).
    Chats are never recorded here, so this never gates interactive chat, and the
    reserved headroom is theirs to use in full. (research rec 2)"""
    cfg = load_config()
    usage = window_usage(now)
    app_total = float(cfg.get("app_total") or 0)
    if app_total <= 0:
        return True, "uncapped (no app_total)"
    nominal = float(cfg.get("nominal_task_tokens") or _DEFAULT_CONFIG["nominal_task_tokens"])
    # Reserve one nominal task for the run about to start PLUS one per in-flight
    # coordinator sweep whose spend has not billed yet, so N concurrent sweeps
    # cannot collectively overshoot the cap before any of them records. (A sweep
    # that calls admit from inside its own loop counts itself here, which only
    # makes the reserve more conservative: safe.) (review F8a)
    reserve = nominal * (1 + _inflight_coordinator_sweeps())
    # Autonomous admission ceiling = fraction of the app total. The remainder is
    # interactive headroom (never consumed by autonomous work). At fraction 1.0
    # this collapses to the old full-budget runaway guard.
    fraction = _autonomy_budget_fraction(cfg)
    autonomous_ceiling = app_total * fraction
    if usage["app"] + reserve > autonomous_ceiling:
        return False, (
            f"interactive headroom reserved: autonomous budget {int(round(fraction * 100))}% used "
            f"({usage['app']}/{int(autonomous_ceiling)} tokens in {usage['window_s']//3600}h; "
            f"app cap {int(app_total)}, reserve {int(reserve)})")
    if scope:
        used_scope = usage["by_project"].get(scope, 0)
        ceil = _project_params(cfg, scope).get("ceil")
        if ceil is not None and used_scope >= float(ceil):
            return False, f"project ceil reached: {scope} {used_scope}/{int(float(ceil))}"
        # Enforce the weighted max-min allocation so configured floors/weights/ceil
        # actually gate (they were previously display-only; review F10). A scope
        # that has already spent its computed allowance for this window yields to
        # others. Demanders = every scope with usage or config, plus this one
        # (mirrors status()'s view). With the default config (weight 1, floor 0,
        # ceil null) a lone scope's allowance equals app_total, so this never bites
        # until the operator sets real weights/floors/ceils.
        demanders = set(usage["by_project"]) | set(cfg.get("projects", {})) | {scope}
        allowance = plan_budgets(usage["by_project"], demanders, cfg).get(scope)
        if allowance is not None and used_scope >= allowance:
            return False, (f"scope allocation reached: {scope} "
                           f"{int(used_scope)}/{int(allowance)} tokens this window")
    return True, "ok"


# ---------------------------------------------------------------------------
# CLI shim (for shell writers: python -m services.usage_ledger record ...)
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="usage_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--project", default=None)
    r.add_argument("--tokens", type=int, required=True)
    r.add_argument("--agent", default=None)
    r.add_argument("--job-id", default=None)
    r.add_argument("--task-id", default=None)
    r.add_argument("--run", default=None)
    r.add_argument("--role", default=None)
    a = sub.add_parser("admit")  # exit 0 = admit, 1 = deny (for shell gating)
    a.add_argument("--project", default=None)
    sub.add_parser("status")
    sub.add_parser("prune")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        record(args.project, args.tokens, agent=args.agent, job_id=args.job_id,
               task_id=args.task_id, run=args.run, role=args.role)
    elif args.cmd == "admit":
        ok, reason = can_admit(args.project)
        print(json.dumps({"ok": ok, "reason": reason}))
        return 0 if ok else 1
    elif args.cmd == "status":
        print(json.dumps(status(), indent=2))
    elif args.cmd == "prune":
        print(json.dumps({"removed": prune()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
