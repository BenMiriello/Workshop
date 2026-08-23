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

CHATS ARE EXEMPT FROM ADMISSION, NOT FROM ACCOUNTING. Interactive chat turns
DO write here (role="chat") so the operator can see what conversation cost, but
they are excluded from the admission window (`ADMISSION_EXCLUDED_ROLES`). They
have to be: chat is declared unmetered and is never gated, so counting its spend
against the autonomous ceiling meant the operator's own conversation refused
every sweep. `compaction` is exempt on the same grounds and for the same
measured reason: it is a chat folding its own history, and it was 83% of the
"autonomous" spend that starved the loop on 2026-08-22. Reporting (`window_usage`, `status`, `timeseries`) still counts
every row; only `can_admit` filters.

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
from wsplatform import paths

WINDOW_S = 5 * 3600  # rolling 5-hour window, matching Claude's usage windows

# Roles whose spend does NOT count toward the autonomous admission ceiling.
# Interactive chat is never gated by admission, so counting it there let the
# operator's own conversation refuse every autonomous sweep. Reporting is
# unaffected: only `can_admit` passes this to `window_usage`.
#
# `compaction` is here for the SAME reason and was missing, which had the SAME
# effect one step removed. Compacting a thread is overhead of an interactive
# conversation, not work anybody chose to spend the autonomy budget on: it is
# the summarizer folding a chat's own history, billed to the chat's scope, and
# the operator cannot decline it. Its writer (`routes/chat.py`, the
# `role="compaction"` record) says in its own comment "Reported, never gated
# (same posture as the turn/voice metering)" -- and it WAS gated, because the
# role was not on this list.
#
# Measured 2026-08-22, with the loop refusing every sweep for hours: of the
# 3,374,976 non-chat tokens the admission window counted, 2,801,736 (83%) were
# compaction over 315 rows, against an autonomous allowance of 3,250,000. So a
# long conversation compacting itself was, on its own, enough to starve the
# autonomous loop to a standstill. Same family as billing a sweep to the
# basename of its directory: a cost attributed to the wrong budget, and the
# symptom is the loop silently doing nothing.
ADMISSION_EXCLUDED_ROLES = ("chat", "compaction")

LEDGER_PATH = paths.env_path("WORKSHOP_USAGE_LEDGER", "usage.jsonl")
BUDGET_CONFIG_PATH = paths.env_path("WORKSHOP_USAGE_BUDGET", "usage-budget.json")

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


def fmt_tokens(n: float) -> str:
    """A token count the way a person reads it: 1972531 -> "1.97M", 150000 ->
    "150K", 900 -> "900".

    Byte-identical to `fmtTokens` in `frontend/src/lib/usage.ts`, deliberately.
    A refusal sentence is composed HERE and rendered THERE, and two spellings of
    the same count in one screen is how a number stops being believed."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "0"
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if a >= 1_000:
        return f"{round(v / 1_000)}K"
    return str(round(v))


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------

def record(project: str | None, tokens: int, *, agent: str | None = None,
           job_id: str | None = None, ts: str | None = None,
           task_id: str | None = None, run: str | None = None,
           role: str | None = None, chat: str | None = None) -> None:
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
    # The originating chat thread, for the per-chat usage breakdown. Only set by
    # interactive turns (role="chat"); autonomous sweeps leave it absent.
    if chat:
        entry["chat"] = chat
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


def usage_for_jobs(job_ids: list[str] | set[str]) -> dict[str, int]:
    """Total tokens recorded against each of `job_ids`.

    The rows have carried `job_id` since the ledger was written; nothing read
    them back that way, so answering "what did this run cost" meant grepping
    usage.jsonl by hand. That is the one number phase 2 of the automation plan
    needs per watched run, and a number nobody can see does not get used.

    Whole-ledger scan, which is the same read `window_usage` already does on
    every admission check, and the file is one line per model call. Batched by
    id rather than offering a single-job lookup so a run page showing a
    coordinator and its children costs one read, not N.
    """
    wanted = {str(j) for j in job_ids if j}
    if not wanted:
        return {}
    out: dict[str, int] = {j: 0 for j in wanted}
    for e in _read_entries():
        jid = str(e.get("job_id") or "")
        if jid in wanted:
            try:
                out[jid] += int(e.get("tokens") or 0)
            except (TypeError, ValueError):
                continue
    return out


def window_rows(now: datetime | None = None, window_s: int = WINDOW_S,
                exclude_roles: Iterable[str] | None = None) -> list[dict]:
    """The ledger rows inside the trailing window, oldest first.

    Exposed because two questions need the ROWS and not just their sum: what the
    window totals (`window_usage`) and WHEN it will total less (`_next_under`,
    which has to know which row ages out first). Sorted here so neither caller
    re-sorts, and so a hand-edited ledger with out-of-order lines still answers
    the second question correctly."""
    cutoff = (now or _now()).timestamp() - window_s
    skip = set(exclude_roles or ())
    rows: list[dict] = []
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is None or t < cutoff:
            continue
        if skip and e.get("role") in skip:
            continue
        rows.append({
            "t": t,
            "project": e.get("project", "unbound"),
            "role": e.get("role"),
            "tokens": int(e.get("tokens") or 0),
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def window_usage(now: datetime | None = None, window_s: int = WINDOW_S,
                 exclude_roles: Iterable[str] | None = None) -> dict:
    """Token usage over the trailing window: app-wide total + per-project.

    `exclude_roles` drops rows whose `role` is in the set. Default None counts
    everything; `can_admit` and the reporting headline both pass
    ADMISSION_EXCLUDED_ROLES so the figure on the screen is the figure the guard
    used."""
    by_project: dict[str, int] = {}
    total = 0
    for r in window_rows(now, window_s=window_s, exclude_roles=exclude_roles):
        by_project[r["project"]] = by_project.get(r["project"], 0) + r["tokens"]
        total += r["tokens"]
    return {"app": total, "by_project": by_project, "window_s": window_s}


def window_excluded(now: datetime | None = None, window_s: int = WINDOW_S) -> dict:
    """The spend the admission guard does NOT count, per role.

    Reported SEPARATELY rather than folded into one total. Chat and compaction
    are the operator's own conversation: real spend he should see, and spend no
    guard will ever refuse a run over. Adding them to the headline is what made
    the Usage page read 265M against a 5M pool while the guard that matters was
    looking at 1.97M."""
    by_role: dict[str, int] = {}
    total = 0
    for r in window_rows(now, window_s=window_s):
        role = r["role"]
        if role not in ADMISSION_EXCLUDED_ROLES:
            continue
        by_role[role] = by_role.get(role, 0) + r["tokens"]
        total += r["tokens"]
    return {"total": total, "by_role": by_role,
            "roles": list(ADMISSION_EXCLUDED_ROLES)}


def timeseries(now: datetime | None = None, window_s: int = WINDOW_S,
               buckets: int = 60,
               exclude_roles: Iterable[str] | None = None) -> dict:
    """Per-interval per-project token usage across the trailing window, in
    `buckets` evenly spaced bins (default 60 -> 5-minute bins over a 5h window).
    Each point carries the tokens billed to each project WITHIN that bin, so a
    line chart shows usage as spikes when sweeps run, not a cumulative climb.
    Rolling: the window start is `now - window_s`, so aged-out entries are
    already excluded. `bin_s` is returned so the client can label the rate.

    `exclude_roles` matches `window_usage`. The Usage page's By App view passes
    ADMISSION_EXCLUDED_ROLES so the chart and the totals above it are the same
    measurement: a chart peaking at 82M in one bin over a bar reading "1.97M of
    5M" is two answers to one question. Chat spend is not lost, it is the By
    Chat view, which is where it is actually legible."""
    end = (now or _now()).timestamp()
    start = end - window_s
    step = window_s / buckets
    skip = set(exclude_roles or ())
    projects: set[str] = set()
    bins: list[dict[str, int]] = [dict() for _ in range(buckets)]
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is None or t < start:
            continue
        if skip and e.get("role") in skip:
            continue
        proj = e.get("project", "unbound")
        projects.add(proj)
        bi = int((t - start) / step)
        if bi < 0:
            bi = 0
        elif bi >= buckets:
            bi = buckets - 1
        bins[bi][proj] = bins[bi].get(proj, 0) + int(e.get("tokens") or 0)
    slugs = sorted(projects)
    points = []
    for b in range(buckets):
        edge = start + step * (b + 1)
        pt: dict = {"t": datetime.fromtimestamp(edge, tz=timezone.utc).isoformat()}
        pt.update({s: bins[b].get(s, 0) for s in slugs})
        points.append(pt)
    return {"window_s": window_s, "bin_s": int(step), "projects": slugs, "points": points}


def chat_timeseries(now: datetime | None = None, window_s: int = WINDOW_S,
                    buckets: int = 60) -> dict:
    """Per-interval per-chat token usage across the trailing window, in `buckets`
    evenly spaced bins. Groups entries by their `chat` field (the originating
    thread ID); entries without a `chat` field are bucketed under '_unbound'.
    Same shape as `timeseries` but keyed by chat rather than project."""
    end = (now or _now()).timestamp()
    start = end - window_s
    step = window_s / buckets
    chats: set[str] = set()
    bins: list[dict[str, int]] = [dict() for _ in range(buckets)]
    for e in _read_entries():
        t = _parse_ts(e.get("ts", ""))
        if t is None or t < start:
            continue
        chat_id = e.get("chat") or "_unbound"
        chats.add(chat_id)
        bi = int((t - start) / step)
        if bi < 0:
            bi = 0
        elif bi >= buckets:
            bi = buckets - 1
        bins[bi][chat_id] = bins[bi].get(chat_id, 0) + int(e.get("tokens") or 0)
    slugs = sorted(chats)
    points = []
    for b in range(buckets):
        edge = start + step * (b + 1)
        pt: dict = {"t": datetime.fromtimestamp(edge, tz=timezone.utc).isoformat()}
        pt.update({s: bins[b].get(s, 0) for s in slugs})
        points.append(pt)
    return {"window_s": window_s, "bin_s": int(step), "chats": slugs, "points": points}


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


def full_window_s(now: datetime | None = None) -> int:
    """Seconds spanning the whole ledger (oldest entry -> now), for an "all time"
    view. Falls back to the standard window on an empty/unparseable ledger."""
    entries = _read_entries()
    stamps = [t for t in (_parse_ts(e.get("ts", "")) for e in entries) if t is not None]
    if not stamps:
        return WINDOW_S
    span = int((now or _now()).timestamp() - min(stamps)) + 60  # +60 so the oldest row lands inside
    return max(span, WINDOW_S)


def status(now: datetime | None = None, window_s: int = WINDOW_S) -> dict:
    """Operator-facing snapshot: app-wide + per-project used/allowance/share.

    `window_s` widens the lookback for the reporting views (24h / 7d / 30d / all
    time). The BUDGET, though, is defined as a rolling 5h cap — it is meaningless
    against a 7-day total — so allowance/remaining are reported ONLY for the
    standard window and are None otherwise. `capped` tells the caller which of the
    two shapes it is holding, so nothing downstream can render a 5h cap as if it
    bounded a 30-day total.

    THE HEADLINE IS WHAT THE GUARD COUNTS. `app_used` and every project's `used`
    exclude ADMISSION_EXCLUDED_ROLES, exactly as `can_admit` does, and the spend
    the guard ignores is reported beside them under `excluded` with its roles
    named. It used to be one total, and on 2026-08-23 that total read 265,430,033
    against a 5,000,000 pool -- 53x the cap, a page that looked like a
    catastrophe -- while the only figure any guard was reading was the 1,972,531
    of autonomous spend underneath it. Same rule as the refusal sentence: the
    number on the screen must be the number the decision used. `excluded` is
    reported on EVERY window, capped or not, so "used" never quietly changes
    meaning when the operator switches to 30 days."""
    cfg = load_config()
    capped = window_s == WINDOW_S
    usage = window_usage(now, window_s=window_s,
                         exclude_roles=ADMISSION_EXCLUDED_ROLES)
    excluded = window_excluded(now, window_s=window_s)
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
            "allowance": round(allow) if capped else None,
            "remaining": round(max(0.0, allow - u)) if capped else None,
            "share_of_app": round(u / usage["app"], 3) if usage["app"] else 0.0,
        }
    return {
        "window_s": usage["window_s"],
        "capped": capped,
        "app_total": cfg.get("app_total") if capped else None,
        "app_used": usage["app"],
        "app_remaining": (
            max(0, int(cfg.get("app_total", 0)) - usage["app"]) if capped else None
        ),
        "excluded": excluded,
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# Admission (the enforcement primitive the dispatcher + sweeps call)
# ---------------------------------------------------------------------------

def _inflight_coordinator_sweeps(exclude_job_id: str | None = None) -> int:
    """Count coordinator sweep Jobs currently `running` (no task_id / parent_id: a
    child task Job carries both). Each holds up to a nominal task's spend that has
    NOT yet landed in the ledger, since billing happens once at ralph exit (up to
    ~20 min later). Best-effort: any failure yields 0 (falls back to the old
    single-nominal reserve). (review F8a)

    `exclude_job_id` drops the CALLER's own sweep from the count. A sweep asking
    "may I start one more task" is already `running`, so without this it is
    counted both by the `1 +` (the task about to start) and by this function —
    reserving two nominal tasks to run one. That is not conservatism, it is the
    same task counted twice, and it deadlocks the queue in a band 150k wide below
    the ceiling where nothing can ever be admitted."""
    try:
        from services import jobs
        running = jobs.read(status="running", limit=1000)
        return sum(1 for r in running
                   if not r.get("task_id") and not r.get("parent_id")
                   and (exclude_job_id is None or r.get("id") != exclude_job_id))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# What a refusal IS
# ---------------------------------------------------------------------------
#
# A REFUSED RUN IS NOT A FAILED RUN AND IT IS NOT A CANCELLED ONE. Nothing is
# broken when this fires: the guard worked. But on 2026-08-23 the operator
# pressed "File and run" twice and got `cancelled 0s` twice, with the only
# explanation sitting in `.runtime/queue-runner.log` on the host. So a refusal
# has to say, on the screen he was already looking at, what refused it and what
# would change it.
#
# ONE PLACE DECIDES THE WORDING, and it is here, because the numbers are here.
# `reason` is the sentence; the log line, the Job's `evidence.halted`, the chat
# run card and the ticket page all print that same string rather than each
# composing a variant of it from the fields beside it. The structured fields
# exist for what a sentence cannot do: `next_admissible_at` is an INSTANT the UI
# renders in the reader's own timezone, and `used`/`limit` are exact where the
# sentence is rounded.

REFUSED_BY = "usage-budget"


def _next_under(rows: list[dict], target: float, window_s: int) -> str | None:
    """When the rolling window drops to `target` on its own, or None if never.

    The window is a sliding sum, so it falls only as rows age out of it. Drop
    rows oldest-first until the remainder fits; the answer is the moment the
    last one dropped leaves the window (its timestamp + the window length).

    An ESTIMATE by construction, and said so wherever it is printed: it assumes
    nothing else spends in the meantime, which a live scope generally does."""
    total = sum(r["tokens"] for r in rows)
    if total <= target:
        return None
    for r in rows:  # oldest first (window_rows sorts)
        total -= r["tokens"]
        if total <= target:
            return datetime.fromtimestamp(r["t"] + window_s, tz=timezone.utc).isoformat()
    return None


def _refusal(kind: str, reason: str, *, scope: str | None, used: float,
             limit: float, window_s: int, next_at: str | None) -> dict:
    """The structured half of a refusal. `reason` is the operator's sentence and
    the only wording; these are the facts under it."""
    return {
        "by": REFUSED_BY,
        "kind": kind,
        "reason": reason,
        "scope": scope,
        "used": int(used),
        "limit": int(limit),
        "window_s": int(window_s),
        "next_admissible_at": next_at,
    }


def admission(scope: str | None, now: datetime | None = None,
              exclude_job_id: str | None = None) -> dict:
    """`can_admit` with its reasoning kept: `{ok, reason, refusal}`.

    `refusal` is None when admitted, and otherwise the structured record that
    rides onto the Job so a refused run can explain itself without anybody
    reading a log file on the host. Admission LOGIC is unchanged; this only
    stopped throwing the explanation away."""
    ok, reason, refusal = _admission(scope, now=now, exclude_job_id=exclude_job_id)
    return {"ok": ok, "reason": reason, "refusal": refusal}


def can_admit(scope: str | None, now: datetime | None = None,
              exclude_job_id: str | None = None) -> tuple[bool, str]:
    """Whether one more autonomous sweep/task may start in `scope` (a project
    slug or agent label). Returns (ok, reason).

    Autonomous work is admitted only up to a RESERVED FRACTION of the app-wide
    budget (default ~0.65), leaving the rest free for the operator's interactive
    chats; autonomous work always yields to that headroom. A per-project `ceil`
    (when set) caps one scope's borrow. We refuse once less than one nominal
    task's worth of the autonomous ceiling remains, so a task never starts on a
    sliver of budget and overshoots (its true cost is unknown until it runs).
    Chat rows are excluded from the window (ADMISSION_EXCLUDED_ROLES), so this
    never gates interactive chat and interactive spend never refuses a sweep. The
    reserved headroom is the chats' to use in full. (research rec 2)

    Kept as the two-value form every existing caller uses. `admission()` is the
    same decision with the structured refusal attached."""
    ok, reason, _ = _admission(scope, now=now, exclude_job_id=exclude_job_id)
    return ok, reason


def _admission(scope: str | None, now: datetime | None = None,
               exclude_job_id: str | None = None) -> tuple[bool, str, dict | None]:
    """The one implementation. Returns (ok, reason, refusal-or-None)."""
    cfg = load_config()
    rows = window_rows(now, exclude_roles=ADMISSION_EXCLUDED_ROLES)
    usage = window_usage(now, exclude_roles=ADMISSION_EXCLUDED_ROLES)
    hours = max(1, round(WINDOW_S / 3600))
    app_total = float(cfg.get("app_total") or 0)
    if app_total <= 0:
        return True, "uncapped (no app_total)", None
    nominal = float(cfg.get("nominal_task_tokens") or _DEFAULT_CONFIG["nominal_task_tokens"])
    # Reserve one nominal task for the run about to start PLUS one per in-flight
    # coordinator sweep whose spend has not billed yet, so N concurrent sweeps
    # cannot collectively overshoot the cap before any of them records. (A sweep
    # that calls admit from inside its own loop counts itself here, which only
    # makes the reserve more conservative: safe.) (review F8a)
    # `1 +` is the task about to start; the rest are OTHER sweeps whose spend has
    # not billed yet. The caller passes its own job id so it is not counted as
    # both.
    reserve = nominal * (1 + _inflight_coordinator_sweeps(exclude_job_id))
    # Autonomous admission ceiling = fraction of the app total. The remainder is
    # interactive headroom (never consumed by autonomous work). At fraction 1.0
    # this collapses to the old full-budget runaway guard.
    fraction = _autonomy_budget_fraction(cfg)
    autonomous_ceiling = app_total * fraction
    if usage["app"] + reserve > autonomous_ceiling:
        reason = (
            f"refused: autonomous work has used {fmt_tokens(usage['app'])} of the "
            f"{fmt_tokens(autonomous_ceiling)} tokens it may spend in a {hours} hour "
            f"window, and the next task needs {fmt_tokens(reserve)} more.")
        return False, reason, _refusal(
            "app_ceiling", reason, scope=scope, used=usage["app"],
            limit=autonomous_ceiling, window_s=WINDOW_S,
            next_at=_next_under(rows, autonomous_ceiling - reserve, WINDOW_S))
    if scope:
        used_scope = usage["by_project"].get(scope, 0)
        scope_rows = [r for r in rows if r["project"] == scope]
        ceil = _project_params(cfg, scope).get("ceil")
        if ceil is not None and used_scope >= float(ceil):
            reason = (
                f"refused: the {scope} scope has used {fmt_tokens(used_scope)} of its "
                f"{fmt_tokens(float(ceil))} token allowance for the last {hours} hours.")
            return False, reason, _refusal(
                "scope_ceiling", reason, scope=scope, used=used_scope,
                limit=float(ceil), window_s=WINDOW_S,
                next_at=_next_under(scope_rows, float(ceil), WINDOW_S))
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
            reason = (
                f"refused: the {scope} scope has used {fmt_tokens(used_scope)} of the "
                f"{fmt_tokens(allowance)} tokens allocated to it for the last "
                f"{hours} hours.")
            return False, reason, _refusal(
                "scope_allocation", reason, scope=scope, used=used_scope,
                limit=allowance, window_s=WINDOW_S,
                next_at=_next_under(scope_rows, allowance, WINDOW_S))
    return True, "ok", None


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
    # A sweep asking on its own behalf passes its coordinator Job id so its own
    # in-flight run is not reserved for twice.
    a.add_argument("--exclude-job", dest="exclude_job", default=None)
    sub.add_parser("status")
    sub.add_parser("prune")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        record(args.project, args.tokens, agent=args.agent, job_id=args.job_id,
               task_id=args.task_id, run=args.run, role=args.role)
    elif args.cmd == "admit":
        # The shell reads BOTH: `.reason` is what it logs and puts on the Job's
        # `evidence.halted`, `.refusal` is the structured record under it.
        verdict = admission(args.project, exclude_job_id=args.exclude_job)
        print(json.dumps(verdict))
        return 0 if verdict["ok"] else 1
    elif args.cmd == "status":
        print(json.dumps(status(), indent=2))
    elif args.cmd == "prune":
        print(json.dumps({"removed": prune()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
