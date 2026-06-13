"""The reasoning step — turns a scored brief + learned patterns into ONE
canonical message (predictable shape, adaptive content).

Ladder: Claude (subscription, via the `claude` CLI + CLAUDE_CODE_OAUTH_TOKEN —
NEVER ANTHROPIC_API_KEY) → local qwen (relay) → deterministic. Each rung returns
the same message contract (messages.make_message), so callers never branch on
which model answered.

Design intent the prompt enforces: be concise; reason WITH the data-quality
caveats (say "signal unclear" rather than inventing a conclusion from thin
data); only surface what's worth surfacing; prefer one concrete action.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

import httpx

from . import config, messages, patterns

# Operator chose Claude for the reasoning step.
REASONER = os.environ.get("COMMUTE_REASONER", "claude")          # claude | qwen | deterministic
CLAUDE_MODEL = os.environ.get("COMMUTE_REASONER_MODEL", "claude-sonnet-4-6")
CLAUDE_TIMEOUT_S = float(os.environ.get("COMMUTE_REASONER_TIMEOUT", "75"))
AUTH_ENV = os.path.expanduser("~/.config/clawdbot/auth.env")
RELAY_LOCAL_URL = os.environ.get("WORKSHOP_RELAY_LOCAL_URL", "http://127.0.0.1:8000/relay/local")

SYSTEM = (
    "You are benmiriello's commute assistant. You receive a JSON 'brief' (the live "
    "scored commute) plus 'patterns' (what his PAST commutes actually looked like) "
    "and 'data_quality'. Produce ONE message as STRICT JSON, no prose around it:\n"
    '{"kind":"brief|alert|insight|nudge","severity":"ok|info|caution|urgent",'
    '"headline":"<=1 line, the single most important thing","detail":"1-2 lines: '
    'concrete times/route","reasoning":"why — cite the signal you used","confidence":'
    '"high|medium|low","action":"optional imperative or null"}\n\n'
    "Rules:\n"
    "- Be concise and concrete. The headline is the one thing he should know.\n"
    "- Confidence MUST reflect data_quality: thin/stale/low-coverage data => low, and "
    "say so in reasoning rather than inventing a conclusion.\n"
    "- Reason adaptively from patterns: if his real leg times / transfer waits / "
    "punctuality suggest a risk (e.g. he routinely leaves a minute late and just "
    "misses a connection), name it. Do NOT use a fixed checklist.\n"
    "- Only escalate severity for genuine LATE risk; never nag when he's on-time/early.\n"
    "- kind 'insight' is for a post-commute observation; 'brief' for a pre/during "
    "status; 'alert' for an active late-risk; 'nudge' for a small actionable tip.\n"
    "Output JSON only."
)


def _evidence(brief: dict[str, Any]) -> dict[str, Any]:
    direction = brief.get("direction")
    dq = {
        "realtime": brief.get("realtime_status", {}),
        "location": brief.get("location", {}),
    }
    return {
        "brief": {k: v for k, v in brief.items() if k not in ("legs", "timeline", "alerts")},
        "timeline": [
            {"mode": l.get("mode"), "from": l.get("from"), "to": l.get("to"),
             "depart_local": l.get("depart_local"), "arrive_local": l.get("arrive_local"),
             "realtime": l.get("realtime", {}).get("delay_min") if l.get("realtime", {}).get("available") else None,
             "status": l.get("status")}
            for l in brief.get("timeline", [])
        ],
        "alerts": [a.get("header") for a in brief.get("alerts", [])][:3],
        "patterns": patterns.summarize_for_reasoner(direction),
        "data_quality": dq,
    }


# --- Claude (subscription CLI) ----------------------------------------------
def _oauth_token() -> str | None:
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok
    try:
        for ln in open(AUTH_ENV):
            ln = ln.strip()
            if ln.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def _claude_bin() -> str | None:
    return (os.environ.get("COMMUTE_CLAUDE_BIN")
            or shutil.which("claude")
            or next((p for p in [
                os.path.expanduser("~/.nvm/versions/node/v22.22.3/bin/claude"),
                "/usr/local/bin/claude",
            ] if os.path.exists(p)), None))


def _call_claude(prompt: str) -> dict[str, Any] | None:
    token = _oauth_token()
    binary = _claude_bin()
    if not token or not binary:
        return None
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token}
    env.pop("ANTHROPIC_API_KEY", None)  # subscription only — never the API key
    try:
        proc = subprocess.run(
            [binary, "-p", "--output-format", "json", "--model", CLAUDE_MODEL,
             "--max-turns", "1", "--append-system-prompt", SYSTEM],
            input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S,
            env=env, cwd="/tmp",
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if envelope.get("is_error"):
        return None
    return _extract_json(envelope.get("result", ""))


def _extract_json(text: str) -> dict[str, Any] | None:
    import re
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# --- local qwen fallback -----------------------------------------------------
def _call_qwen(prompt: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=90.0) as c:
            r = c.post(RELAY_LOCAL_URL, json={"task": SYSTEM + "\n\n" + prompt, "max_tokens": 700})
        if r.status_code != 200:
            return None
        return _extract_json(r.json().get("response", ""))
    except (httpx.HTTPError, json.JSONDecodeError):
        return None


# --- deterministic fallback --------------------------------------------------
def _deterministic(brief: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    if not brief.get("active"):
        nxt = brief.get("next_schedule")
        if nxt:
            return {"kind": "brief", "severity": "ok", "confidence": "high",
                    "headline": f"Next commute {nxt.get('date_local')} — leave by {nxt.get('leave_by_local')}",
                    "detail": f"Target arrive {nxt.get('target_arrive_local')}.",
                    "reasoning": "No commute active now; showing the next scheduled one."}
        return {"kind": "brief", "severity": "ok", "confidence": "high",
                "headline": "No commute active", "detail": "", "reasoning": brief.get("reason", "")}
    rt = brief.get("realtime_status", {})
    conf = "high" if rt.get("available") else "low"
    dirn = "work" if brief.get("direction") == "to-work" else "home"
    if brief.get("late_risk"):
        return {"kind": "alert", "severity": "caution" if brief.get("late_by_min", 0) < 20 else "urgent",
                "confidence": conf,
                "headline": f"Late risk → {dirn}: ~{int(brief.get('late_by_min', 0))}m past target",
                "detail": f"Target {brief.get('target_arrive_local')}, projected {brief.get('projected_arrive_local')}.",
                "reasoning": ("Projected arrival exceeds the flex deadline."
                              + ("" if rt.get("available") else " (Times are static schedule — realtime unavailable.)")),
                "action": "Consider an earlier train or trimming the first leg."}
    return {"kind": "brief", "severity": "ok", "confidence": conf,
            "headline": f"On track → {dirn} — leave by {brief.get('leave_by_local')}",
            "detail": f"Target {brief.get('target_arrive_local')}, projected {brief.get('projected_arrive_local')}.",
            "reasoning": "Projected arrival is within the flex window."
                         + ("" if rt.get("available") else " Times are static schedule (no live transit data).")}


# --- public ------------------------------------------------------------------
def compose(brief: dict[str, Any], checkpoint: str = "during",
            allow_llm: bool = True) -> dict[str, Any]:
    """Build a message via the ladder. Always returns a valid message dict.

    `allow_llm=False` forces the cheap deterministic path — used for routine
    on-track 'during' ticks so the expensive Claude call is reserved for the
    moments that matter (checkpoints, late-risk, explicit app request)."""
    evidence = _evidence(brief)
    prompt = (f"checkpoint: {checkpoint}\n\nEVIDENCE:\n"
              + json.dumps(evidence, indent=2, default=str)
              + "\n\nProduce the message JSON now.")
    raw = None
    route = "deterministic"
    if allow_llm and REASONER == "claude":
        raw = _call_claude(prompt)
        route = "claude" if raw else None
        if raw is None:
            raw = _call_qwen(prompt)
            route = "qwen" if raw else "deterministic"
    elif allow_llm and REASONER == "qwen":
        raw = _call_qwen(prompt)
        route = "qwen" if raw else "deterministic"
    if raw is None:
        raw = _deterministic(brief, evidence)
        route = "deterministic"
    msg = messages.validate(raw)
    msg["source"] = f"reasoner:{route}"
    # attach the evidence we reasoned over (for the in-app "why" expander)
    msg["evidence"] = {**(msg.get("evidence") or {}), **{
        "checkpoint": checkpoint,
        "late_risk": brief.get("late_risk"),
        "late_by_min": brief.get("late_by_min"),
        "realtime_available": brief.get("realtime_status", {}).get("available"),
        "location": brief.get("location", {}),
        "patterns": evidence["patterns"].get("stats"),
    }}
    return msg


def compose_and_store(brief: dict[str, Any], checkpoint: str = "during",
                      allow_llm: bool = True) -> dict[str, Any]:
    """Compose + persist (deduped). Returns {message, stored, duplicate}."""
    msg = compose(brief, checkpoint, allow_llm=allow_llm)
    _, stored = messages.append_deduped(msg)
    return {"message": msg, "stored": stored, "duplicate": not stored}
