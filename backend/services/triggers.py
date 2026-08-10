"""Unified trigger vocabulary.

One vocabulary describes when any Agent or Project runs. A trigger has a `kind`:

    manual  — only runs when a person presses "run now"
    cron    — runs on a cron schedule (carries `cron_expression`)
    event   — runs when a subscribed source fires (carries `event_sources`)
    discord — runs when an inbound Discord message lands on a named channel
              (carries `channels`). A narrow spelling of `event` over the
              `discord` source; see the note below.
    loop    — an unattended self-refiring loop; carries `max_iterations`,
              `budget`, and a `completion_gate` reference (the reviewer that
              decides "done") so the loop is bounded and cannot run forever.

Normalized trigger shape (a plain dict, JSON-friendly):

    {
      "kind": "manual" | "cron" | "event" | "discord" | "loop",
      "cron_expression": "<expr>",   # kind == cron
      "event_sources": ["plaid"],    # kind == event
      "channels": ["1506..."],       # kind == discord
      "max_iterations": 20,          # kind == loop
      "budget": {"max_wall_s": 3000},# kind == loop
      "completion_gate": "reviewer"  # kind == loop
    }

Why `discord` is its own kind rather than `event` + `event_sources: [discord]`
(final-plan PM.2): an `event` trigger fans an agent out over a WHOLE source, but
a Discord agent is per-CHANNEL — a bridge agent answers its own channels and must
not wake on every guild message. The `channels` list is the part `event_sources`
cannot express. It reduces to the same delivery path: the bridge POSTs the
inbound envelope to `/events/discord`, it lands in `.runtime/events.jsonl`, and
`agents_registry.regenerate_subscriptions` wires the subscription — so no new
transport, only a new selector.

DISPATCH NOTE. `discord` is deliberately NOT a kind the cron dispatcher fires:
`scripts/dispatch_triggers.sh` only fires `cron` (when due) and `loop` (when the
scope has ready tasks). A `discord` trigger is armed by an inbound message or it
is armed by nothing at all — which is what makes declaring one on an agent inert
until something actually posts a Discord event to the bus.

Phase 1 backing stores are adapted, not replaced:

  * Project triggers live in the project registry, translated to/from the old
    `autonomy.mode {off, manual, continuous}` field.
  * Agent triggers are READ from existing Letta metadata (`autonomy_mode`,
    `cron_expression`, `event_sources`) and presented in trigger shape.

The agent-side adapter is deliberately a thin seam: `agent_triggers(meta)` takes
a metadata-shaped dict. Phase 2 swaps the backing store to markdown definition
frontmatter by calling the same function with the frontmatter dict (same keys),
so nothing downstream of the adapter changes.
"""
from __future__ import annotations

from typing import Any

from datetime import datetime, timedelta, timezone

from services import jobs

KINDS = ("manual", "cron", "event", "discord", "loop")

# The event-bus source name a `discord` trigger subscribes to. Kept as a constant
# because three files have to agree on the spelling (this module, the
# subscriptions writer in agents_registry, and the bridge's POST target).
DISCORD_SOURCE = "discord"


# ---------------------------------------------------------------------------
# Cron scheduling (dispatcher). croniter if importable, else a self-contained
# 5-field matcher with a conservative firing window. Used by dispatch_triggers.
# ---------------------------------------------------------------------------

def _field_match(spec: str, value: int) -> bool:
    """Match one cron field (`*`, `*/n`, `a-b`, `a,b`, `a`) against a value."""
    for part in spec.split(","):
        part = part.strip()
        if part in ("*", "?"):
            return True
        step = 1
        if "/" in part:
            base, _, s = part.partition("/")
            try:
                step = int(s)
            except ValueError:
                step = 1
            part = base or "*"
        if part in ("*", ""):
            if value % step == 0:
                return True
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
            continue
        try:
            if int(part) == value:
                return True
        except ValueError:
            continue
    return False


def _cron_match(expr: str, dt: datetime) -> bool:
    fields = (expr or "").split()
    if len(fields) != 5:
        return False
    minute, hour, dom, mon, dow = fields
    cron_dow = (dt.weekday() + 1) % 7  # cron: 0=Sun..6=Sat (Python: 0=Mon..6=Sun)
    return (
        _field_match(minute, dt.minute)
        and _field_match(hour, dt.hour)
        and _field_match(dom, dt.day)
        and _field_match(mon, dt.month)
        and (_field_match(dow, cron_dow) or _field_match(dow, 7 if cron_dow == 0 else cron_dow))
    )


def due(cron_expression: str, last_fired_iso: str | None, now: datetime | None = None,
        *, fresh_window_min: int = 30, lookback_cap_min: int = 1500) -> bool:
    """Is a cron trigger due? True if it would have fired in (last_fired, now].
    Prefers croniter; falls back to scanning minutes backward from now to
    last_fired (capped). A never-fired trigger only fires if it matched within
    the last `fresh_window_min` minutes (one dispatcher tick), so a fresh daily
    cron does not fire immediately mid-day."""
    if not cron_expression:
        return False
    now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    try:
        from croniter import croniter  # type: ignore
        base = _parse_iso(last_fired_iso) or (now - timedelta(minutes=fresh_window_min))
        nxt = croniter(cron_expression, base).get_next(datetime)
        return nxt <= now
    except Exception:
        last = _parse_iso(last_fired_iso)
        floor = last if last is not None else (now - timedelta(minutes=fresh_window_min))
        t = now
        for _ in range(lookback_cap_min):
            if t <= floor:
                break
            if _cron_match(cron_expression, t):
                return True
            t -= timedelta(minutes=1)
        return False


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    except ValueError:
        return None

# Default loop bounds. These mirror the Ralph sweep caps in project_ralph.sh so a
# `continuous` project translates to a loop that is actually bounded.
DEFAULT_LOOP_MAX_ITERATIONS = 20
DEFAULT_LOOP_BUDGET = {"max_wall_s": 3000}
DEFAULT_COMPLETION_GATE = "reviewer"


def make_trigger(
    kind: str,
    *,
    cron_expression: str | None = None,
    event_sources: list[str] | None = None,
    channels: list[str] | None = None,
    max_iterations: int | None = None,
    budget: dict | None = None,
    completion_gate: str | None = None,
) -> dict:
    """Build a normalized trigger dict, filling loop defaults."""
    if kind not in KINDS:
        raise ValueError(f"unknown trigger kind: {kind!r}")
    t: dict[str, Any] = {"kind": kind}
    if kind == "discord":
        # Channel ids are Discord snowflakes; carry them as strings. YAML happily
        # parses a bare 19-digit snowflake as an int, and an int would never
        # match the string chat_id the bridge posts, so coerce rather than trust
        # the frontmatter's type.
        t["channels"] = [str(c) for c in (channels or [])]
        if not t["channels"]:
            # Same rule as an empty cron expression: a trigger that can never
            # match is disabled, and it says so out loud instead of being wired
            # to nothing in silence.
            t["disabled_reason"] = "discord trigger has no channels"
    elif kind == "cron":
        # An empty expression is never due, so it disables the agent outright.
        # Keep the field permissive (the editor PUTs a blank row before the
        # operator types an expression) but mark it, so the dispatcher can say
        # so out loud instead of skipping in silence.
        t["cron_expression"] = cron_expression or ""
        if not t["cron_expression"]:
            t["disabled_reason"] = "cron trigger has no expression"
    elif kind == "event":
        t["event_sources"] = list(event_sources or [])
    elif kind == "loop":
        t["max_iterations"] = int(max_iterations) if max_iterations is not None else DEFAULT_LOOP_MAX_ITERATIONS
        t["budget"] = budget if budget is not None else dict(DEFAULT_LOOP_BUDGET)
        t["completion_gate"] = completion_gate or DEFAULT_COMPLETION_GATE
    return t


def normalize(raw: dict) -> dict:
    """Coerce a loosely-shaped trigger dict (e.g. from a PUT body) into the
    canonical shape, filling defaults for loops."""
    return make_trigger(
        raw.get("kind", "manual"),
        # `cron` is the spelling every hand-authored definition on disk uses
        # (agents/*.md). Reading only `cron_expression` silently produced an
        # empty expression, and due("") is False, so those agents never fired.
        cron_expression=raw.get("cron_expression") or raw.get("cron"),
        event_sources=raw.get("event_sources"),
        channels=raw.get("channels"),
        max_iterations=raw.get("max_iterations"),
        budget=raw.get("budget"),
        completion_gate=raw.get("completion_gate"),
    )


# ---------------------------------------------------------------------------
# Project adapter — project registry `autonomy.mode` <-> triggers
# ---------------------------------------------------------------------------
#   off        -> [] plus an implicit manual capability (represented as a single
#                 manual trigger so the UI always has one row)
#   manual     -> [manual]
#   continuous -> [loop]  (bounded by max_iterations + budget + reviewer gate)

def project_triggers(autonomy: dict | None) -> list[dict]:
    """Translate a project's `autonomy` config to a trigger list."""
    mode = ((autonomy or {}).get("mode")) or "off"
    if mode == "continuous":
        return [make_trigger("loop")]
    # both "off" and "manual" present only a manual trigger; "off" means the
    # background cron will not pick it up (no loop), manual-only otherwise.
    return [make_trigger("manual")]


def project_mode_from_triggers(triggers: list[dict]) -> str:
    """Reverse the translation: a loop trigger means `continuous`; a manual-only
    list means `manual`; an empty list means `off`."""
    kinds = {(t or {}).get("kind") for t in (triggers or [])}
    if "loop" in kinds:
        return "continuous"
    if not triggers:
        return "off"
    return "manual"


# ---------------------------------------------------------------------------
# Autonomy PRESET model (Phase O6/O7-config)
# ---------------------------------------------------------------------------
# The operator-facing autonomy control is a single preset, not a raw trigger
# list. A preset expands DETERMINISTICALLY into the concrete trigger list (plus
# a budget bundle carried on the loop trigger). This is the new PRIMARY control;
# the old `autonomy.mode {off, manual, continuous}` still works as a back-compat
# shadow (see mode_to_preset / preset_to_mode below).
#
#   preset        triggers      dispatcher behavior                    default for
#   ----------    ----------    ------------------------------------   -----------
#   off           []            never background-swept                 assistant/general/app chats
#   on-demand     [manual]      swept only by an explicit "Run now"    project + builder scopes
#   scheduled     [cron]        swept on the cron cadence when ready   opt-in per scope
#   continuous    [loop]        swept every tick when ready, bounded   opt-in per scope
#
# The empty-list encoding of `off` (rather than a lone manual trigger) keeps the
# inverse `triggers_to_preset` unambiguous and matches project_mode_from_triggers
# (empty -> off). A manual "Run now" never consults triggers to decide whether it
# may run (it uses --force), so `off` losing its manual trigger changes nothing
# about the operator's ability to launch a sweep by hand.

PRESETS = ("off", "on-demand", "scheduled", "continuous")

_PRESET_TO_MODE = {
    "off": "off",
    "on-demand": "manual",
    "scheduled": "scheduled",
    "continuous": "continuous",
}
_MODE_TO_PRESET = {
    "off": "off",
    "manual": "on-demand",
    "scheduled": "scheduled",
    "continuous": "continuous",
}


def preset_to_mode(preset: str | None) -> str:
    """Back-compat: the old `autonomy.mode` value a preset shadows."""
    return _PRESET_TO_MODE.get(preset or "off", "off")


def mode_to_preset(mode: str | None) -> str:
    """Back-compat: the preset an existing `autonomy.mode` maps to."""
    return _MODE_TO_PRESET.get(mode or "off", "off")


def preset_to_triggers(preset: str | None, *, cron: str | None = None,
                       limits: dict | None = None) -> list[dict]:
    """Expand an autonomy preset into its concrete trigger list + budget bundle.

    `cron` supplies the expression for `scheduled`. `limits` (a dict with any of
    `max_iterations`, `budget`, `completion_gate`) tunes the `continuous` loop's
    bound; absent values fall back to the loop defaults. Unknown presets are
    treated as `off`."""
    preset = preset or "off"
    lim = limits or {}
    if preset == "continuous":
        return [make_trigger(
            "loop",
            max_iterations=lim.get("max_iterations"),
            budget=lim.get("budget"),
            completion_gate=lim.get("completion_gate"),
        )]
    if preset == "scheduled":
        return [make_trigger("cron", cron_expression=cron or "")]
    if preset == "on-demand":
        return [make_trigger("manual")]
    # off (and any unknown value): no automatic trigger; never background-swept.
    return []


def triggers_to_preset(triggers: list[dict] | None) -> str:
    """Best-effort inverse of `preset_to_triggers`: classify an existing trigger
    list back to a preset so stored configs display correctly. A loop trigger is
    `continuous`; a cron trigger is `scheduled`; a manual-only list is
    `on-demand`; an empty list is `off`."""
    kinds = {(t or {}).get("kind") for t in (triggers or [])}
    if "loop" in kinds:
        return "continuous"
    if "cron" in kinds:
        return "scheduled"
    if "manual" in kinds:
        return "on-demand"
    return "off"


# ---------------------------------------------------------------------------
# Agent adapter — Letta metadata (Phase 1) -> triggers
# ---------------------------------------------------------------------------
# Phase 2 will call this same function with definition frontmatter instead of
# Letta metadata; the accepted keys (autonomy_mode / cron_expression /
# event_sources) are intentionally identical so the seam is a no-op swap.

def agent_triggers(meta: dict | None) -> list[dict]:
    """Present an agent's autonomy metadata as a trigger list. Reads
    `autonomy_mode` (off|scheduled|event-driven|continuous), `cron_expression`,
    and `event_sources`.

    Phase 1 read path, kept for agents not yet migrated to a definition file
    (their triggers still live on Letta metadata). Phase 2's source of record is
    `definition_triggers` below; the two produce the same normalized shape."""
    meta = meta or {}
    mode = meta.get("autonomy_mode") or meta.get("autonomy") or "off"
    out: list[dict] = []
    if mode == "scheduled":
        out.append(make_trigger("cron", cron_expression=meta.get("cron_expression") or ""))
    elif mode == "event-driven":
        out.append(make_trigger("event", event_sources=list(meta.get("event_sources") or [])))
    elif mode == "continuous":
        out.append(make_trigger("loop"))
    # off -> no automatic trigger; the agent still runs on manual invocation.
    return out


# ---------------------------------------------------------------------------
# Agent adapter — definition frontmatter (Phase 2 backing store) -> triggers
# ---------------------------------------------------------------------------
# Phase 2 swaps the agent-side backing store from Letta metadata to markdown
# definition frontmatter. The frontmatter already stores triggers in normalized
# shape under a `triggers:` list, so the adapter is a normalize pass — the same
# output shape as `agent_triggers`, so nothing downstream of the adapter changes.

def definition_triggers(defn: dict | None) -> list[dict]:
    """Read the `triggers` list from an agent definition's frontmatter (or a
    registry record) and return it normalized."""
    raw = (defn or {}).get("triggers") or []
    return [normalize(t) for t in raw if isinstance(t, dict)]


# ---------------------------------------------------------------------------
# Effective autonomy: the per-agent mode with the workshop override overlaid
# ---------------------------------------------------------------------------
# The autonomy-model doc (§4) folds the workshop-level override on top of the
# per-agent field with a single two-step read: if the override says engaged,
# every scope answers `reactive`; otherwise the agent's own field wins.
#
# We deliberately do NOT mutate the agent's stored `autonomy` when the override
# engages — the field is authoritative, the override is an overlay. Clearing
# the override restores each agent's declared behavior without a restore log.

def effective_autonomy(agent_slug: str, *, registry=None, override=None) -> str:
    """Return one of AUTONOMY_MODES for this agent, honoring the workshop
    override.

    `registry` and `override` are injection seams for tests; production code
    passes neither and the module resolves both. Any exception during resolution
    returns `reactive` (fail-closed matches autonomy-model §6 "override file is
    corrupt")."""
    try:
        if override is None:
            from services import autonomy_override as _ov
            state = _ov.read()
        else:
            state = override
        if isinstance(state, dict) and state.get("engaged"):
            return "reactive"
    except Exception:
        # An unreadable overlay is treated as engaged per autonomy-model §6.
        return "reactive"

    try:
        if registry is None:
            from services import agents_registry as _reg
            rec = _reg.get(agent_slug)
        else:
            rec = registry.get(agent_slug)
        if rec is None:
            return "reactive"
        mode = rec.get("autonomy") or "reactive"
        if mode in AUTONOMY_MODES:
            return mode
    except Exception:
        pass
    return "reactive"


AUTONOMY_MODES = ("reactive", "supervised", "autonomous")


# ---------------------------------------------------------------------------
# Dispatch — a firing trigger creates a Job
# ---------------------------------------------------------------------------

def dispatch(
    trigger: dict,
    *,
    agent_id: str | None = None,
    project_slug: str | None = None,
    chat_id: str | None = None,
) -> dict:
    """A trigger fired: open a Job for the execution it kicks off. Returns the
    Job record. The caller runs the actual work and calls `jobs.finish(id, ...)`
    when it completes."""
    kind = (trigger or {}).get("kind", "manual")
    budget = trigger.get("budget") if kind == "loop" else None
    return jobs.start(
        agent_id=agent_id,
        project_slug=project_slug,
        chat_id=chat_id,
        trigger=kind,
        status="running",
        budget=budget,
    )
