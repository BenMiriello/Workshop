"""Canonical commute MESSAGE contract + store.

One object shape used by BOTH Discord and the app — predictable shape, adaptive
content. The reasoner fills it; the alerter renders it to Discord; the app feed
renders it as cards. Append-only to .runtime/commute/messages.jsonl with
offset/limit pagination for the in-app history.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import config

KINDS = ("brief", "alert", "insight", "nudge")
SEVERITIES = ("ok", "info", "caution", "urgent")
CONFIDENCES = ("high", "medium", "low")


def make_message(*, kind: str, severity: str, headline: str,
                 detail: str = "", reasoning: str = "", confidence: str = "medium",
                 action: str | None = None, evidence: dict[str, Any] | None = None,
                 journey_ref: str | None = None, ts: str | None = None,
                 source: str = "reasoner") -> dict[str, Any]:
    """Build + validate a message. Coerces bad enums to safe defaults and clamps
    string lengths so a misbehaving reasoner can't emit a wall of text."""
    kind = kind if kind in KINDS else "brief"
    severity = severity if severity in SEVERITIES else "info"
    confidence = confidence if confidence in CONFIDENCES else "medium"
    ts = ts or datetime.now(timezone.utc).isoformat()
    headline = (headline or "").strip()[:160] or "Commute update"
    detail = (detail or "").strip()[:400]
    reasoning = (reasoning or "").strip()[:600]
    action = (action or "").strip()[:160] or None
    mid = hashlib.sha1(f"{ts}|{kind}|{headline}".encode()).hexdigest()[:12]
    return {
        "id": mid,
        "ts": ts,
        "kind": kind,
        "severity": severity,
        "headline": headline,
        "detail": detail,
        "reasoning": reasoning,
        "confidence": confidence,
        "action": action,
        "evidence": evidence or {},
        "journey_ref": journey_ref,
        "source": source,
    }


def validate(d: dict[str, Any]) -> dict[str, Any]:
    """Normalise an arbitrary dict (e.g. parsed LLM JSON) into a message."""
    return make_message(
        kind=str(d.get("kind", "brief")),
        severity=str(d.get("severity", "info")),
        headline=str(d.get("headline", "")),
        detail=str(d.get("detail", "")),
        reasoning=str(d.get("reasoning", "")),
        confidence=str(d.get("confidence", "medium")),
        action=(str(d["action"]) if d.get("action") else None),
        evidence=d.get("evidence") if isinstance(d.get("evidence"), dict) else None,
        journey_ref=(str(d["journey_ref"]) if d.get("journey_ref") else None),
        source=str(d.get("source", "reasoner")),
    )


def _path():
    return config.runtime_path("messages.jsonl")


def append(msg: dict[str, Any]) -> dict[str, Any]:
    p = _path()
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg, separators=(",", ":")) + "\n")
    return msg


def _all() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def recent(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Newest-first page. Returns {messages, total, has_more}."""
    items = list(reversed(_all()))
    total = len(items)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    page = items[offset:offset + limit]
    return {"messages": page, "total": total, "has_more": (offset + limit) < total}


def latest() -> dict[str, Any] | None:
    items = _all()
    return items[-1] if items else None


def is_recent_duplicate(msg: dict[str, Any], within_min: float = 25.0) -> bool:
    """True if the most recent stored message has the same headline+severity and
    is younger than within_min — used to keep the feed/Discord from repeating an
    unchanged 'on track' every 5-minute tick."""
    last = latest()
    if not last:
        return False
    if last.get("headline") != msg.get("headline") or last.get("severity") != msg.get("severity"):
        return False
    from datetime import datetime as _dt, timezone as _tz
    try:
        prev = _dt.fromisoformat(str(last["ts"]).replace("Z", "+00:00"))
    except Exception:
        return False
    age_min = (_dt.now(_tz.utc) - prev).total_seconds() / 60.0
    return age_min < within_min


def append_deduped(msg: dict[str, Any], within_min: float = 25.0) -> tuple[dict[str, Any], bool]:
    """Append unless it's a recent duplicate. Returns (message, stored)."""
    if is_recent_duplicate(msg, within_min):
        return msg, False
    append(msg)
    return msg, True
