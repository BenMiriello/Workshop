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
    "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
    "projects": {},
}


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
           job_id: str | None = None, ts: str | None = None) -> None:
    """Append one usage entry. `project` is the scope the tokens are billed to
    (None -> "unbound"). Safe for concurrent shell + backend writers (append is
    atomic for small lines)."""
    entry = {
        "ts": ts or _ts_iso(),
        "project": project or "unbound",
        "tokens": int(max(0, tokens)),
    }
    if agent:
        entry["agent"] = agent
    if job_id:
        entry["job_id"] = job_id
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

def can_admit(scope: str | None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether one more autonomous sweep/task may start in `scope` (a project
    slug or agent label). Returns (ok, reason).

    The app-wide cap is the runaway guard; a per-project `ceil` (when set) caps
    one scope's borrow. We refuse once less than one nominal task's worth of
    app-wide headroom remains, so a task never starts on a sliver of budget and
    overshoots the ceiling (its true cost is unknown until it runs). Chats are
    never recorded here, so this never gates interactive chat."""
    cfg = load_config()
    usage = window_usage(now)
    app_total = float(cfg.get("app_total") or 0)
    if app_total <= 0:
        return True, "uncapped (no app_total)"
    reserve = float(cfg.get("nominal_task_tokens") or _DEFAULT_CONFIG["nominal_task_tokens"])
    if usage["app"] + reserve > app_total:
        return False, f"app budget exhausted: {usage['app']}/{int(app_total)} tokens in {usage['window_s']//3600}h"
    if scope:
        ceil = _project_params(cfg, scope).get("ceil")
        if ceil is not None and usage["by_project"].get(scope, 0) >= float(ceil):
            return False, f"project ceil reached: {scope} {usage['by_project'].get(scope, 0)}/{int(float(ceil))}"
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
    a = sub.add_parser("admit")  # exit 0 = admit, 1 = deny (for shell gating)
    a.add_argument("--project", default=None)
    sub.add_parser("status")
    sub.add_parser("prune")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        record(args.project, args.tokens, agent=args.agent, job_id=args.job_id)
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
