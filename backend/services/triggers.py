"""Unified trigger vocabulary.

One vocabulary describes when any Agent or Project runs. A trigger has a `kind`:

    manual  — only runs when a person presses "run now"
    cron    — runs on a cron schedule (carries `cron_expression`)
    event   — runs when a subscribed source fires (carries `event_sources`)
    loop    — an unattended self-refiring loop; carries `max_iterations`,
              `budget`, and a `completion_gate` reference (the reviewer that
              decides "done") so the loop is bounded and cannot run forever.

Normalized trigger shape (a plain dict, JSON-friendly):

    {
      "kind": "manual" | "cron" | "event" | "loop",
      "cron_expression": "<expr>",   # kind == cron
      "event_sources": ["plaid"],    # kind == event
      "max_iterations": 20,          # kind == loop
      "budget": {"max_wall_s": 3000},# kind == loop
      "completion_gate": "reviewer"  # kind == loop
    }

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

from services import jobs

KINDS = ("manual", "cron", "event", "loop")

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
    max_iterations: int | None = None,
    budget: dict | None = None,
    completion_gate: str | None = None,
) -> dict:
    """Build a normalized trigger dict, filling loop defaults."""
    if kind not in KINDS:
        raise ValueError(f"unknown trigger kind: {kind!r}")
    t: dict[str, Any] = {"kind": kind}
    if kind == "cron":
        t["cron_expression"] = cron_expression or ""
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
        cron_expression=raw.get("cron_expression"),
        event_sources=raw.get("event_sources"),
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
