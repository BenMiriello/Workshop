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
from services import model_prices, token_usage

WINDOW_S = 5 * 3600  # rolling 5-hour window, matching Claude's usage windows

# Row schema version. v1 rows carry a total and nothing else; v2 rows carry the
# four raw components, the model, and the price version, which is what makes a
# past row re-evaluable under a different weight or price table.
ROW_VERSION = 2

COMPONENT_KEYS = ("input", "cache_read", "cache_creation", "output")

# What the `tokens` column HOLDS. Named rather than assumed, because for months
# it held three incompatible quantities at once and they were summed.
#
#   effective       rate-limit shaped: cache reads at token_usage.CACHE_READ_WEIGHT.
#                   This is what admission compares against, and it is the unit
#                   the worker has always written. Every v2 writer writes it, so
#                   the window is one unit for the first time.
#   spend           volume billed: all four components at parity, over CALLS.
#   raw-frames      v1 chat rows: a sum over stream FRAMES, so duplicated per
#                   content block AND counting cache reads at parity. Not
#                   convertible to anything; roughly an order of magnitude above
#                   `effective` on the same work.
#   estimate        a chars/4 guess, from a writer that gets no usage block back.
#   unknown         a v1 row whose writer we cannot identify from its role.
UNIT_EFFECTIVE = "effective"
UNIT_SPEND = "spend"
UNIT_RAW_FRAMES = "raw-frames"
UNIT_ESTIMATE = "estimate"
UNIT_UNKNOWN = "unknown"

# Which unit a PRE-v2 row must have been written in, inferred from its role.
# This is inference about writers we can read, not a guess: `build_streaming`
# summed frames for role="chat", `ralph.sh` weighted cache reads at 0.1 for
# role="worker" and role="reviewer", and the compaction path in `routes/chat.py`
# has never had a usage block to parse.
_V1_UNIT_BY_ROLE = {
    "chat": UNIT_RAW_FRAMES,
    "worker": UNIT_EFFECTIVE,
    "reviewer": UNIT_EFFECTIVE,
    "compaction": UNIT_ESTIMATE,
}

# Roles whose spend does NOT count toward the autonomous admission ceiling.
# Interactive chat is never gated by admission, so counting it there let the
# operator's own conversation refuse every autonomous sweep. Reporting is
# unaffected: only `can_admit` passes this to `window_usage`.
#
# `compaction` is here for the SAME reason and was missing, which had the SAME
# effect one step removed. Compacting a thread is overhead of an interactive
# conversation, not work anybody chose to spend the autonomy budget on: it is
# the summarizer folding a chat's own history, billed to the chat's scope, and
# the operator cannot decline it. Its writer said in its own comment "Reported,
# never gated (same posture as the turn/voice metering)" -- and it WAS gated,
# because the role was not on this list.
#
# THAT WRITER NO LONGER EXISTS. Measured 2026-09-04: the four live writers are
# `build_streaming` (role=chat), `voice_worker` (role=chat), `scripts/ralph.sh`
# (role=worker) and `scripts/reviewer.sh` (role=reviewer); nothing writes
# role=compaction any more. The role stays on this list regardless, because
# 1,813 historical rows carry it and dropping it would silently change what
# every past window totals.
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
           role: str | None = None, chat: str | None = None,
           components: dict | None = None, model: str | None = None,
           calls: int | None = None, source: str | None = None,
           unit: str | None = None, cost_usd: float | None = None,
           models: dict | None = None,
           vendor_cost_usd: float | None = None) -> None:
    """Append one usage entry. `project` is the scope the tokens are billed to
    (None -> "unbound"). Safe for concurrent shell + backend writers (append is
    atomic for small lines).

    Optional attribution (all backward-compatible; older entries omitting them
    still load): `task_id` (the ticket the spend served), `run` (the Job/attempt
    id), and `role` ("worker" | "reviewer") so the same scope's worker and
    auxiliary reviewer spend are distinguishable for postmortems. (research rec 3)

    `components` IS THE LOAD-BEARING ADDITION and the reason for `v: 2`.
    `tokens` is a DERIVED field: it is whichever of the four quantities in
    `services/token_usage` this writer chose, and a total cannot be revisited.
    Storing the four raw components means a past row can be re-evaluated under a
    different weight or a different price table by replaying the file, which is
    precisely what could not be done with the 6.5 billion tokens of pre-v2
    history: there is no arithmetic that recovers components from a sum, and the
    journals they came from are pruned ten turns at a time.

    `model` and `price_version` exist so `cost_usd` is recomputable rather than
    frozen. The version stamped here is the table in force at WRITE time, so a
    later price change adds a table and never rewrites what an old row cost.

    THE POSITIONAL `(project, tokens)` SIGNATURE IS LOAD-BEARING. A test
    monkeypatches it positionally on purpose and several call sites use it that
    way; everything added here is keyword-only."""
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
    comp = _clean_components(components)
    if comp is not None:
        entry["v"] = ROW_VERSION
        entry["components"] = comp
        entry["unit"] = unit or UNIT_EFFECTIVE
        if model:
            entry["model"] = model
            entry["price_version"] = model_prices.CURRENT_VERSION
        # PER-MODEL COMPONENTS, when the envelope reported them. A turn that
        # delegates to a cheaper model spends on two price sheets, and one
        # `model` label on a summed row prices the whole thing at whichever
        # model happened to be larger. With this the row prices exactly.
        clean_models = _clean_model_map(models)
        if clean_models:
            entry["models"] = clean_models
            entry.setdefault("price_version", model_prices.CURRENT_VERSION)
        # The vendor's OWN dollar figure, kept beside ours rather than instead
        # of it. Ours is recomputable under a corrected table; theirs is the
        # truth on the day. On four real turns they agreed to the last decimal
        # (see tests/test_model_prices.py), so a disagreement is a signal that
        # the table has drifted, and it can only be seen if both are stored.
        if vendor_cost_usd is not None:
            try:
                entry["vendor_cost_usd"] = round(float(vendor_cost_usd), 8)
            except (TypeError, ValueError):
                pass
        if calls is not None:
            try:
                entry["calls"] = max(0, int(calls))
            except (TypeError, ValueError):
                pass
        # WHICH SOURCE THE ROW USED, never an `or` that hides the choice. The
        # CLI's `result` envelope and the deduplicated frame sum disagreed by 3x
        # on the one turn where both were seen, and the reason is unverified. A
        # row that names its source can be re-read once that is settled; a row
        # that silently picked one cannot.
        if source:
            entry["source"] = source
        if cost_usd is None:
            cost_usd = _price_components(comp, model, clean_models)
        if cost_usd is not None:
            try:
                entry["cost_usd"] = round(float(cost_usd), 6)
            except (TypeError, ValueError):
                pass
    elif unit:
        # No components (a chars/4 estimate, say). Still say what the number is,
        # so an estimate is never mistaken for a measurement.
        entry["unit"] = unit
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _clean_components(components: dict | None) -> dict[str, int] | None:
    """The four named component counts, or None if nothing usable was passed.

    Tolerant for the same reason `token_usage.parse` is: this arrives from
    another process through a JSON file, and a surprising shape must yield "no
    components" rather than an exception on a turn the operator already had."""
    if not isinstance(components, dict):
        return None
    out: dict[str, int] = {}
    for k in COMPONENT_KEYS:
        try:
            v = int(components.get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        out[k] = v if v > 0 else 0
    return out if any(out.values()) else None


def _clean_model_map(models: dict | None) -> dict[str, dict[str, int]] | None:
    """`{model_id: components}` with every component validated, or None."""
    if not isinstance(models, dict):
        return None
    out: dict[str, dict[str, int]] = {}
    for mid, comp in models.items():
        if not isinstance(mid, str) or not mid:
            continue
        c = _clean_components(comp if isinstance(comp, dict) else None)
        if c:
            out[mid] = c
    return out or None


def _price_components(components: dict | None, model: str | None,
                      models: dict | None = None) -> float | None:
    """Dollars for a row's components, priced per model where we have that.

    Returns None when NOTHING can be priced. A partially priceable row — two
    models, one of them absent from the table — returns the priced part, which
    is an undercount and is reported as such by the `unpriced_*` counters
    beside it, because dropping the whole row would be a larger error than
    reporting the part we can defend."""
    if models:
        total = 0.0
        priced = False
        for mid, comp in models.items():
            c = model_prices.cost_usd(comp, mid)
            if c is not None:
                total += c
                priced = True
        return total if priced else None
    if components and model:
        return model_prices.cost_usd(components, model)
    return None


def _read_entries() -> list[dict]:
    """Every ledger row, NORMALIZED so a caller never has to guess a row's unit.

    A row written before components existed carries no `v` and no `unit`, and
    its `tokens` column holds a different quantity depending on which writer
    produced it: chat summed raw usage over stream FRAMES (inflated ~2x by
    duplicates and ~7x by counting cache reads at parity), the worker already
    weighted cache reads at 0.1, and compaction stored a chars/4 estimate. They
    were added together for months.

    NOTHING IS RESCALED HERE. The measured chat-to-weighted factor is 11.8x-13.6x
    on two turns, which is a measurement and not a constant, and multiplying by
    it would produce a number that looks recoverable and is not. The unit is
    NAMED instead, and `window_usage(unit=...)` refuses to add what it cannot
    express, reporting the remainder separately. A visible gap is honest."""
    if not LEDGER_PATH.exists():
        return []
    out: list[dict] = []
    for line in LEDGER_PATH.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(_normalize_row(e))
    return out


def _normalize_row(e: dict) -> dict:
    """One raw row with `v` and `unit` filled in from what it must have been."""
    if not e.get("v"):
        e["v"] = 1
    if not e.get("unit"):
        e["unit"] = _V1_UNIT_BY_ROLE.get(e.get("role") or "", UNIT_UNKNOWN)
    return e


def row_tokens(row: dict, unit: str | None = None) -> int | None:
    """`row`'s token count expressed in `unit`, or None if it cannot be.

    `unit=None` returns the stored number verbatim, which is what every reader
    did before units were named and is what the reporting views still want when
    they are not making a cross-unit claim.

    A v2 row can be expressed in ANY unit, because it stored the components. A
    v1 row can only be expressed in the one unit it was written in."""
    try:
        stored = int(row.get("tokens") or 0)
    except (TypeError, ValueError):
        stored = 0
    if unit is None:
        return stored
    comp = row.get("components")
    if isinstance(comp, dict):
        u = token_usage.Usage(
            input=int(comp.get("input") or 0),
            cache_read=int(comp.get("cache_read") or 0),
            cache_creation=int(comp.get("cache_creation") or 0),
            output=int(comp.get("output") or 0))
        if unit == UNIT_EFFECTIVE:
            return token_usage.effective(u)
        if unit == UNIT_SPEND:
            return token_usage.spend(u)
        return None
    return stored if row.get("unit") == unit else None


def row_cost_usd(row: dict) -> float | None:
    """What `row` cost in dollars, or None if it cannot be priced.

    Recomputed from the stored components at the row's OWN price version rather
    than trusting the stored figure, so a corrected price table fixes history in
    place. The stored `cost_usd` is kept as the vendor's own answer where one
    was reported and is preferred only when there are no components to price."""
    models = row.get("models")
    if isinstance(models, dict) and models:
        total = 0.0
        priced = False
        for mid, comp in models.items():
            c = model_prices.cost_usd(comp, mid, row.get("price_version"))
            if c is not None:
                total += c
                priced = True
        if priced:
            return total
    comp = row.get("components")
    model = row.get("model")
    if isinstance(comp, dict) and model:
        c = model_prices.cost_usd(comp, model, row.get("price_version"))
        if c is not None:
            return c
    raw = row.get("cost_usd")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
            # Carried through so a caller can re-express or price the row
            # without a second pass over the file.
            "v": e.get("v"),
            "unit": e.get("unit"),
            "components": e.get("components"),
            "models": e.get("models"),
            "model": e.get("model"),
            "price_version": e.get("price_version"),
            "cost_usd": e.get("cost_usd"),
            "vendor_cost_usd": e.get("vendor_cost_usd"),
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def window_usage(now: datetime | None = None, window_s: int = WINDOW_S,
                 exclude_roles: Iterable[str] | None = None,
                 unit: str | None = None) -> dict:
    """Token usage over the trailing window: app-wide total + per-project.

    `exclude_roles` drops rows whose `role` is in the set. Default None counts
    everything; `can_admit` and the reporting headline both pass
    ADMISSION_EXCLUDED_ROLES so the figure on the screen is the figure the guard
    used.

    `unit` REFUSES TO ADD WHAT IT CANNOT EXPRESS. Passing one restricts the sum
    to rows expressible in that unit and returns the rest under `unconvertible`,
    which the Usage page can show as "before the meter was fixed". Passing None
    keeps the historical behaviour of summing the stored column verbatim, which
    is what every existing caller wants and which is honest only because the
    same rows are what every existing threshold was set against."""
    by_project: dict[str, int] = {}
    total = 0
    unconvertible = 0
    unconvertible_rows = 0
    for r in window_rows(now, window_s=window_s, exclude_roles=exclude_roles):
        n = row_tokens(r, unit)
        if n is None:
            unconvertible += int(r["tokens"])
            unconvertible_rows += 1
            continue
        by_project[r["project"]] = by_project.get(r["project"], 0) + n
        total += n
    out = {"app": total, "by_project": by_project, "window_s": window_s}
    if unit is not None:
        out["unit"] = unit
        out["unconvertible"] = unconvertible
        out["unconvertible_rows"] = unconvertible_rows
    return out


def window_spend_usd(now: datetime | None = None, window_s: int = WINDOW_S,
                     exclude_roles: Iterable[str] | None = None) -> dict:
    """What the window COST, in dollars, app-wide and per project.

    `unpriced_rows` and `unpriced_tokens` are reported beside the total and are
    not folded into it. A row is unpriced when it predates components or names a
    model the price table does not carry, and calling that $0.00 would report a
    system spending money as spending none. The dollar figure is an estimate of
    value consumed at published API rates, not an invoice."""
    by_project: dict[str, float] = {}
    total = 0.0
    priced_rows = 0
    unpriced_rows = 0
    unpriced_tokens = 0
    for r in window_rows(now, window_s=window_s, exclude_roles=exclude_roles):
        c = row_cost_usd(r)
        if c is None:
            unpriced_rows += 1
            unpriced_tokens += int(r["tokens"])
            continue
        priced_rows += 1
        by_project[r["project"]] = by_project.get(r["project"], 0.0) + c
        total += c
    return {
        "app_usd": round(total, 6),
        "by_project": {k: round(v, 6) for k, v in by_project.items()},
        "priced_rows": priced_rows,
        "unpriced_rows": unpriced_rows,
        "unpriced_tokens": unpriced_tokens,
        "price_version": model_prices.CURRENT_VERSION,
        "window_s": window_s,
    }


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
        # WHAT IT COST, beside what it consumed. Reported for every window,
        # capped or not, and carrying its own unpriced counts so a gap in the
        # price table reads as a gap rather than as thrift. Dollars are an
        # estimate at published API rates, not an invoice, and that is stated
        # once beside the totals rather than on every figure.
        "usd": window_spend_usd(now, window_s=window_s,
                                exclude_roles=ADMISSION_EXCLUDED_ROLES),
        "usd_excluded": window_spend_usd(
            now, window_s=window_s,
            exclude_roles=[r for r in _all_roles(now, window_s)
                           if r not in ADMISSION_EXCLUDED_ROLES]),
    }


def _all_roles(now: datetime | None, window_s: int) -> list[str]:
    """Every role present in the window. Used to invert an exclusion set so the
    excluded spend can be priced with the same function that prices the rest,
    rather than a second near-copy of it."""
    return sorted({str(r.get("role") or "")
                   for r in window_rows(now, window_s=window_s)})


def remaining_for_scope(scope: str | None, now: datetime | None = None,
                        spent_since: int = 0) -> dict:
    """How much of the autonomous budget `scope` may still spend, right now.

    THE QUESTION `can_admit` CANNOT ANSWER. Admission returns (ok, reason); a
    run that wants to stop AT the line rather than after crossing it needs a
    quantity, and the only place one existed was `status()[projects][scope]
    [remaining]`, which is None on any window but the standard one.

    `spent_since` IS NOT OPTIONAL FOR A LIVE RUN, and leaving it at 0 is the
    single easiest way to get this wrong. A worker bills the ledger ONCE, when
    it exits. Mid-run the ledger has never heard of the run, so a bare query is
    stale by the entire run so far — which for a long task is most of the
    answer. A caller mid-flight passes what it has spent and is not yet billed
    for.

    Returns both the app-wide and the per-scope remainder plus `remaining`, the
    binding one, so a caller compares against a single number and the reason it
    binds is still legible."""
    cfg = load_config()
    usage = window_usage(now, exclude_roles=ADMISSION_EXCLUDED_ROLES)
    spent = max(0, int(spent_since or 0))
    app_total = float(cfg.get("app_total") or 0)
    fraction = _autonomy_budget_fraction(cfg)
    if app_total <= 0:
        return {"scope": scope, "uncapped": True, "remaining": None,
                "app_remaining": None, "scope_remaining": None,
                "window_s": WINDOW_S, "spent_since": spent}
    autonomous_ceiling = app_total * fraction
    app_remaining = max(0.0, autonomous_ceiling - usage["app"] - spent)
    scope_remaining = None
    if scope:
        used_scope = usage["by_project"].get(scope, 0) + spent
        demanders = set(usage["by_project"]) | set(cfg.get("projects", {})) | {scope}
        allowance = plan_budgets(usage["by_project"], demanders, cfg).get(scope)
        if allowance is not None:
            scope_remaining = max(0.0, float(allowance) - used_scope)
        ceil = _project_params(cfg, scope).get("ceil")
        if ceil is not None:
            by_ceil = max(0.0, float(ceil) - used_scope)
            scope_remaining = by_ceil if scope_remaining is None else min(
                scope_remaining, by_ceil)
    binding = app_remaining if scope_remaining is None else min(
        app_remaining, scope_remaining)
    return {
        "scope": scope,
        "uncapped": False,
        "remaining": int(binding),
        "app_remaining": int(app_remaining),
        "scope_remaining": None if scope_remaining is None else int(scope_remaining),
        "autonomous_ceiling": int(autonomous_ceiling),
        "app_used": int(usage["app"]),
        "spent_since": spent,
        "window_s": WINDOW_S,
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

def _usage_from_file(path: str) -> dict:
    """Components + model out of a file holding a CLI result envelope.

    THE TOLERANT LADDER LIVES HERE NOW, not in shell. `ralph.sh` captured
    `claude -p --output-format json` with `2>&1`, so a stray warning line can
    land in the file and break a whole-file parse; it grew a three-step parse
    ladder inline in a `python3 -c` heredoc, and so did the session-id read and
    the result-text read beside it. One of those three is enough to maintain.

    Returns zeros rather than raising on anything unreadable: a metering read is
    never allowed to be the thing that fails a run."""
    out: dict = {"components": None, "model": None, "source": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return out
    obj = None
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError:
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("{") and '"usage"' in line:
                try:
                    obj = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if obj is None:
            i = txt.find('{"type"')
            if i >= 0:
                try:
                    obj = json.loads(txt[i:])
                except json.JSONDecodeError:
                    obj = None
    if not isinstance(obj, dict):
        return out
    return usage_from_envelope(obj)


# The `result` envelope keys, camelCase, that hold the same four counts the
# streaming `usage` block spells in snake_case.
_MODEL_USAGE_KEYS = {
    "input": "inputTokens",
    "output": "outputTokens",
    "cache_read": "cacheReadInputTokens",
    "cache_creation": "cacheCreationInputTokens",
}


def usage_from_envelope(obj: dict) -> dict:
    """Components, per-model breakdown, model and vendor cost from one envelope.

    `modelUsage` IS THE SOURCE ON A RESULT ENVELOPE, AND `usage` IS NOT.
    Measured 2026-09-04 over every surviving turn journal on this machine: FOUR
    of four `result` envelopes carry `usage` with all four counts at ZERO, while
    `modelUsage` on the same line carries the real per-model counts and
    `total_cost_usd` carries real dollars ($3.01, $3.71, $0.013, $3.15).

    That is not a rounding problem, it is the meter reading zero. Everything
    that reads `result.usage` bills nothing: `ralph.sh`'s per-iteration parse
    (so a worker run records 0 tokens and admission compares against an empty
    window), and `build_streaming`'s `spend_result`, which is why its `or` chain
    always falls through to the frame sum. `counting-tokens-2026-08-25.md`
    section 9 asks whether `result.usage` excludes sidechain usage; on this
    build the answer is narrower and worse — it excludes everything.

    `modelUsage` is also the better source for a second reason: it is keyed BY
    MODEL, which is exactly what pricing needs, and a turn that delegates to a
    cheaper model reports both legs separately. Its own `costUSD` per model is
    the vendor's answer and it is free.

    Falls back to `usage` when there is no `modelUsage` — an `assistant` stream
    frame has one and not the other."""
    out: dict = {"components": None, "models": None, "model": None,
                 "source": None, "vendor_cost_usd": None}
    mu = obj.get("modelUsage")
    if isinstance(mu, dict) and mu:
        models: dict[str, dict] = {}
        total = token_usage.Usage()
        for mid, v in mu.items():
            if not isinstance(v, dict) or not isinstance(mid, str):
                continue
            u = token_usage.Usage(**{
                k: _nonneg(v.get(src)) for k, src in _MODEL_USAGE_KEYS.items()})
            if u == token_usage.Usage():
                continue
            models[mid] = u.as_dict()
            total = total + u
        if models:
            out["models"] = models
            out["components"] = total.as_dict()
            out["source"] = "result"
            # The heaviest leg names the row. A delegate on a cheaper model is
            # still priced exactly, from `models`; this is only the label.
            out["model"] = max(models, key=lambda k: sum(models[k].values()))
    if out["components"] is None:
        u = obj.get("usage")
        if isinstance(u, dict):
            parsed = token_usage.parse(u)
            if parsed != token_usage.Usage():
                out["components"] = parsed.as_dict()
                out["source"] = "result" if obj.get("type") == "result" else "frames"
                out["model"] = _model_of(obj)
    try:
        c = obj.get("total_cost_usd")
        out["vendor_cost_usd"] = None if c is None else float(c)
    except (TypeError, ValueError):
        out["vendor_cost_usd"] = None
    return out


def accumulate_usage(path: str) -> dict:
    """Sum a jsonl of `read-usage` outputs into one run's totals.

    Per-model, because a run whose iterations used different models (a `--quick`
    task pins a cheaper one) spends on two price sheets, and a summed row with
    one label prices it all as whichever was larger.

    `calls` counts ITERATIONS that reported usage, not API calls. It is the
    per-step cost predictor `70860d58` uses: the cost of this run's own last
    step is a far better predictor of its next step than any whole-task
    percentile, and the measured spread over 168 real runs (24,559 to 1,045,598,
    a 43x range) is what makes the whole-task estimate useless."""
    out: dict = {"components": None, "models": None, "model": None,
                 "source": "result", "vendor_cost_usd": None, "calls": 0}
    total = token_usage.Usage()
    models: dict[str, token_usage.Usage] = {}
    vendor = 0.0
    saw_vendor = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        comp = _clean_components(e.get("components"))
        if comp is None:
            continue
        out["calls"] += 1
        total = total + token_usage.Usage(**comp)
        per_model = _clean_model_map(e.get("models"))
        if per_model:
            for mid, c in per_model.items():
                models[mid] = models.get(mid, token_usage.Usage()) + \
                    token_usage.Usage(**c)
        elif isinstance(e.get("model"), str) and e["model"]:
            mid = e["model"]
            models[mid] = models.get(mid, token_usage.Usage()) + \
                token_usage.Usage(**comp)
        v = e.get("vendor_cost_usd")
        if v is not None:
            try:
                vendor += float(v)
                saw_vendor = True
            except (TypeError, ValueError):
                pass
    if total == token_usage.Usage():
        return out
    out["components"] = total.as_dict()
    if models:
        out["models"] = {k: v.as_dict() for k, v in models.items()}
        out["model"] = max(out["models"],
                           key=lambda k: sum(out["models"][k].values()))
    if saw_vendor:
        out["vendor_cost_usd"] = vendor
    return out


def _nonneg(v: object) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _model_of(obj: dict) -> str | None:
    """The model id a CLI envelope names, wherever it put it.

    An `assistant` frame carries it on the nested `message`. `<synthetic>` is
    the CLI's own placeholder on records that report no usage at all, and it is
    not a model."""
    m = obj.get("model")
    if not isinstance(m, str) or not m:
        msg = obj.get("message")
        m = msg.get("model") if isinstance(msg, dict) else None
    if isinstance(m, str) and m and m != "<synthetic>":
        return m
    return None


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="usage_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--project", default=None)
    # NOT required any more: with --usage-json the components ARE the input and
    # the token count is derived from them here, so the 0.1 cache-read weight
    # stops being restated in shell. Passing --tokens still works and still wins.
    r.add_argument("--tokens", type=int, default=None)
    r.add_argument("--agent", default=None)
    r.add_argument("--job-id", default=None)
    r.add_argument("--task-id", default=None)
    r.add_argument("--run", default=None)
    r.add_argument("--role", default=None)
    r.add_argument("--chat", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--calls", type=int, default=None)
    r.add_argument("--source", default=None)
    # A file holding either a raw vendor usage block or a whole CLI result
    # envelope containing one. A FILE and not an argument: a usage block arrives
    # inside a multi-megabyte JSON envelope, and passing it through argv is how
    # a shell writer ends up re-implementing the parse.
    r.add_argument("--usage-json", dest="usage_json", default=None)
    # A jsonl file of `read-usage` outputs, one per iteration. A multi-iteration
    # run bills ONCE at exit, so the components have to be accumulated across
    # iterations somewhere; accumulating them in shell would mean shell adding
    # per-model dictionaries, which is how the 0.1 weight ended up restated in
    # bash in the first place.
    r.add_argument("--usage-accum", dest="usage_accum", default=None)
    ru = sub.add_parser("read-usage")  # parse only; records nothing
    ru.add_argument("--usage-json", dest="usage_json", required=True)
    rem = sub.add_parser("remaining")
    rem.add_argument("--project", default=None)
    # What this run has spent and not yet billed. See remaining_for_scope.
    rem.add_argument("--spent-since", dest="spent_since", type=int, default=0)
    a = sub.add_parser("admit")  # exit 0 = admit, 1 = deny (for shell gating)
    a.add_argument("--project", default=None)
    # A sweep asking on its own behalf passes its coordinator Job id so its own
    # in-flight run is not reserved for twice.
    a.add_argument("--exclude-job", dest="exclude_job", default=None)
    sub.add_parser("status")
    sub.add_parser("prune")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        comp = None
        models = None
        vendor = None
        model = args.model
        calls = args.calls
        source = args.source
        if args.usage_json:
            parsed = _usage_from_file(args.usage_json)
            comp = parsed["components"]
            models = parsed["models"]
            vendor = parsed["vendor_cost_usd"]
            model = model or parsed["model"]
            if source is None and comp is not None:
                source = parsed["source"]
        elif args.usage_accum:
            parsed = accumulate_usage(args.usage_accum)
            comp = parsed["components"]
            models = parsed["models"]
            vendor = parsed["vendor_cost_usd"]
            model = model or parsed["model"]
            if calls is None and parsed["calls"]:
                calls = parsed["calls"]
            if source is None and comp is not None:
                source = parsed["source"]
        tokens = args.tokens
        if tokens is None:
            if comp is None:
                # NOT a silent zero. A caller that asked for a derived count and
                # got an unreadable envelope must learn that here, because the
                # alternative is a run that bills nothing and looks free.
                print(json.dumps({"error": "record needs --tokens or a readable "
                                           "--usage-json", "tokens": 0}))
                return 2
            tokens = token_usage.effective(token_usage.Usage(**comp))
        record(args.project, tokens, agent=args.agent, job_id=args.job_id,
               task_id=args.task_id, run=args.run, role=args.role,
               chat=args.chat, components=comp, model=model, calls=calls,
               source=source, models=models, vendor_cost_usd=vendor)
        # Echo what was written so a shell caller can log the derived figure
        # rather than recomputing it and disagreeing by a token.
        print(json.dumps({"tokens": int(tokens), "components": comp,
                          "model": model, "models": models,
                          "vendor_cost_usd": vendor,
                          "cost_usd": _price_components(comp, model,
                                                        _clean_model_map(models))}))
    elif args.cmd == "read-usage":
        parsed = _usage_from_file(args.usage_json)
        comp = parsed["components"]
        parsed["tokens"] = (0 if comp is None
                            else token_usage.effective(token_usage.Usage(**comp)))
        print(json.dumps(parsed))
        # Exit 1 on "nothing measurable here", so a caller can tell an
        # unreadable envelope from a genuinely free iteration. Those are
        # different facts and only one of them is a bug.
        return 0 if comp is not None else 1
    elif args.cmd == "remaining":
        print(json.dumps(remaining_for_scope(args.project,
                                             spent_since=args.spent_since)))
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
