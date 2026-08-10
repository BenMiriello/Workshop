"""LLM decision layer.

Hybrid model per operator:
    - Programmatic poller + scoring runs unsupervised (free).
    - LLM is invoked at pre-commute (1h, 30min before depart) and during-commute
      checkpoints (every 10min while a schedule is active) to interpret the
      brief and decide whether the operator should be interrupted.

Routing:
    - Forced to `/relay/local` (Ollama qwen2.5:32b). The "should I alert about
      this commute?" decision class is boilerplate — qwen handles it fine and
      avoids the cloud-deferred silent-fallback degradation.
    - This module only WRITES the decision + suggested text. The alerter
      module owns delivery + cooldown.

The decision schema is small + deterministic so the local LLM's JSON output
parses cleanly:
    {"interject": bool, "severity": "info|caution|urgent", "text": str, "reasoning": str}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

def _operator() -> str:
    """Who the agent is assisting. One name in source would address every
    other deployment's operator by the author's name."""
    from services.chat_context import operator_name

    return operator_name()


from . import config


RELAY_URL = os.environ.get("WORKSHOP_RELAY_URL", "http://127.0.0.1:8000/relay/auto")
RELAY_LOCAL_URL = os.environ.get("WORKSHOP_RELAY_LOCAL_URL", "http://127.0.0.1:8000/relay/local")
RELAY_HTTP_TIMEOUT = 120.0


def _build_prompt(brief: dict[str, Any], checkpoint: str) -> str:
    """Compose the operator-context prompt for the LLM.

    `checkpoint` is one of: "pre-1h", "pre-30m", "during", "post".
    """
    base = (
        f"You are a commute-assist agent for {_operator()}. Decide whether to interject "
        "in his Discord. Hard rule: ONLY alert if he's at risk of being LATE past his "
        "flex window. NEVER alert when running early or on-time. Default to NOT "
        "interjecting unless there is a clear, actionable late-risk signal.\n\n"
        "Output strict JSON ONLY, no prose around it. Schema:\n"
        '  {"interject": bool, "severity": "info"|"caution"|"urgent", "text": "short message to send", "reasoning": "1-2 sentence justification"}\n\n'
    )
    base += f"Checkpoint: {checkpoint}\n"
    base += "Current brief:\n"
    base += json.dumps({k: v for k, v in brief.items() if k not in ("legs",)}, indent=2)
    base += "\n\nDecide now. JSON only."
    return base


def _parse_decision(raw: str) -> dict[str, Any]:
    """Extract the first JSON object from the LLM's response. Models sometimes
    pad with prose; we strip it."""
    # Find first { ... } block
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"interject": False, "severity": "info", "text": "", "reasoning": "no json in llm output"}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"interject": False, "severity": "info", "text": "", "reasoning": "json parse failed"}
    return {
        "interject": bool(d.get("interject", False)),
        "severity": d.get("severity", "info"),
        "text": str(d.get("text", ""))[:1500],
        "reasoning": str(d.get("reasoning", ""))[:500],
    }


async def decide_interject(brief: dict[str, Any], checkpoint: str = "during") -> dict[str, Any]:
    """Ask the relay (auto-routed local→cloud) to make the interject decision.

    Returns the parsed decision + meta about which route was taken. Best-effort
    fallback to a programmatic decision if the relay is unreachable.
    """
    if not brief.get("active"):
        return {"interject": False, "severity": "info", "text": "", "reasoning": "no schedule active", "route": "skipped"}
    prompt = _build_prompt(brief, checkpoint)
    started = datetime.now(timezone.utc)
    decision: dict[str, Any] = {}
    route = "unknown"
    try:
        async with httpx.AsyncClient(timeout=RELAY_HTTP_TIMEOUT) as c:
            r = await c.post(
                RELAY_LOCAL_URL,
                json={"task": prompt, "max_tokens": 1024},
            )
            if r.status_code != 200:
                raise RuntimeError(f"relay http {r.status_code}: {r.text[:200]}")
            data = r.json()
        if "response" in data:
            route = "local"
            decision = _parse_decision(data["response"])
        else:
            route = "unknown"
            decision = _programmatic_fallback(brief)
    except Exception as e:  # noqa: BLE001
        route = "error"
        decision = _programmatic_fallback(brief)
        decision["reasoning"] = f"relay error: {str(e)[:150]} (fellback to programmatic)"
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    out = {
        **decision,
        "route": route,
        "checkpoint": checkpoint,
        "ts": started.isoformat(),
        "elapsed_s": round(elapsed, 2),
    }
    _log_decision(out, brief)
    return out


def _programmatic_fallback(brief: dict[str, Any]) -> dict[str, Any]:
    """If the relay is down or routed cloud, fall back to deterministic logic.
    This matches the alerter's policy: alert iff late_risk True."""
    if not brief.get("late_risk"):
        return {
            "interject": False,
            "severity": "info",
            "text": "",
            "reasoning": "programmatic fallback: no late-risk",
        }
    return {
        "interject": True,
        "severity": "caution",
        "text": (
            f"Late-risk: projected {brief['projected_arrive_local']}, "
            f"{int(brief.get('late_by_min', 0))} min past target."
        ),
        "reasoning": "programmatic fallback: projected arrival exceeds flex deadline",
    }


def _log_decision(decision: dict[str, Any], brief: dict[str, Any]) -> None:
    """Append every LLM decision to .runtime/commute/llm-decisions.jsonl
    so future tuning can see what the LLM said vs what actually happened."""
    p = config.runtime_path("llm-decisions.jsonl")
    rec = {
        "ts": decision.get("ts"),
        "checkpoint": decision.get("checkpoint"),
        "route": decision.get("route"),
        "interject": decision.get("interject"),
        "severity": decision.get("severity"),
        "reasoning": decision.get("reasoning"),
        "schedule_id": brief.get("schedule_id"),
        "late_risk": brief.get("late_risk"),
        "late_by_min": brief.get("late_by_min"),
    }
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def recent_decisions(limit: int = 20) -> list[dict[str, Any]]:
    p = config.runtime_path("llm-decisions.jsonl")
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
