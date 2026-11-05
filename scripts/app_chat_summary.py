"""Summarize one chat turn from the CLI's own session transcript.

The chat CLI writes every session as JSONL under
`<home>/.claude/projects/<cwd-key>/<session>.jsonl`. One session spans many
turns (later turns `--resume` it), so a turn is the slice of records whose
timestamps fall inside the turn's own start and end, which the thread log
records. Pure functions over parsed records: no network, no backend imports, so
the parser is testable on a fixture.

Record shapes read here (CLI 2.1.x):
  assistant  one record PER CONTENT BLOCK; blocks of one model request share
             `message.id` and carry the same `message.usage`
  user       `message.content` holding `tool_result` blocks (`is_error` set on
             a failed call)
  system     subtype `compact_boundary`, `compactMetadata` with pre/post tokens
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ERROR_CLIP = 160


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_transcript(session_id: str, homes: list[str]) -> Path | None:
    """The session's JSONL under any of `homes`. The id is a uuid, so a glob
    across every cwd key cannot match another session."""
    for home in homes:
        pattern = os.path.join(home, ".claude", "projects", "*", f"{session_id}.jsonl")
        hits = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if hits:
            return Path(hits[0])
    return None


def load_records(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def in_window(records: list[dict], start: datetime | None,
              end: datetime | None, slack_s: float = 2.0) -> list[dict]:
    """Records stamped inside [start - slack, end + slack]. Unstamped records
    (queue and latch bookkeeping) are dropped; none of them is summarized."""
    lo = start - timedelta(seconds=slack_s) if start else None
    hi = end + timedelta(seconds=slack_s) if end else None
    kept = []
    for rec in records:
        ts = parse_ts(rec.get("timestamp"))
        if ts is None or (lo and ts < lo) or (hi and ts > hi):
            continue
        kept.append(rec)
    return kept


def _blocks(rec: dict) -> list[dict]:
    content = (rec.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return " ".join(str(content or "").split())[:ERROR_CLIP]


def summarize(records: list[dict]) -> dict:
    """Counts for one turn's slice of a transcript."""
    usage: dict[str, dict] = {}
    models: set[str] = set()
    tools: Counter = Counter()
    names_by_id: dict[str, str] = {}
    errors: list[dict] = []
    compactions: list[dict] = []
    thinking = text = 0
    last_text = ""
    stop_reason = None
    for rec in records:
        kind = rec.get("type")
        if kind == "system" and rec.get("subtype") == "compact_boundary":
            meta = rec.get("compactMetadata") or {}
            compactions.append({k: meta.get(k) for k in
                                ("trigger", "preTokens", "postTokens", "durationMs")})
        elif kind == "assistant" and not rec.get("isSidechain"):
            msg = rec.get("message") or {}
            mid = msg.get("id") or rec.get("uuid")
            usage[mid] = msg.get("usage") or usage.get(mid) or {}
            models.add(msg.get("model") or "?")
            stop_reason = msg.get("stop_reason") or stop_reason
            for b in _blocks(rec):
                if b.get("type") == "thinking":
                    thinking += len(b.get("thinking") or "")
                elif b.get("type") == "text":
                    text += len(b.get("text") or "")
                    last_text = b.get("text") or last_text
                elif b.get("type") == "tool_use":
                    tools[b.get("name") or "?"] += 1
                    names_by_id[b.get("id") or ""] = b.get("name") or "?"
        elif kind == "user":
            for b in _blocks(rec):
                if b.get("type") == "tool_result" and b.get("is_error"):
                    errors.append({"tool": names_by_id.get(b.get("tool_use_id") or "", "?"),
                                   "error": _result_text(b)})
    fresh = sum(int(u.get("input_tokens") or 0) for u in usage.values())
    cached = sum(int(u.get("cache_read_input_tokens") or 0)
                 + int(u.get("cache_creation_input_tokens") or 0) for u in usage.values())
    return {
        "requests": len(usage),
        "models": sorted(models),
        "input_tokens": fresh + cached,
        "input_tokens_cached": cached,
        "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage.values()),
        "tool_calls": dict(tools.most_common()),
        "tool_errors": errors,
        "compactions": compactions,
        "thinking_chars": thinking,
        "text_chars": text,
        "last_text": last_text,
        "stop_reason": stop_reason,
    }
