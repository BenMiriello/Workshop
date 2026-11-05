"""What a turn has done so far, read back from its own CLI journal.

A compaction inside a long single-request turn replaces everything the turn
found with the model's own summary, and on a small local model that summary
keeps none of the ids it looked up. The chat's transcript does not help there:
in such a turn it is only the request. The turn's journal (the CLI's own event
stream, on disk) still holds every tool call with its arguments and its result,
so this module turns that into a short block the turn is handed back.

Generic over tools: arguments are shown as written, and a result is reduced to
its shape (scalar fields, list lengths, the ids of the first few items) rather
than read by anyone's schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

__all__ = ["PROGRESS_CHARS", "progress_block", "tool_calls"]

# About a thousand tokens: room for the last few dozen calls on one line each.
PROGRESS_CHARS = 4000
_ARGS_CHARS = 160
_RESULT_CHARS = 220
_IDS_SHOWN = 5


def tool_calls(lines: Iterable[str]) -> list[dict]:
    """The turn's own tool calls, oldest first: name, input, result text (None
    while it has not come back) and whether it failed. A subagent's inner calls
    are left out; they are not the turn's own steps."""
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    for line in lines:
        if '"tool_use"' not in line and '"tool_result"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("parent_tool_use_id"):
            continue
        kind = ev.get("type")
        content = (ev.get("message") or {}).get("content")
        if kind not in ("assistant", "user") or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                _fold(block, kind, calls, by_id)
    return calls


def _fold(block: dict, kind: str, calls: list[dict], by_id: dict[str, dict]) -> None:
    tid = block.get("id") or block.get("tool_use_id") or ""
    if kind == "assistant" and block.get("type") == "tool_use" and tid not in by_id:
        call = {"name": block.get("name") or "?", "input": block.get("input") or {},
                "result": None, "error": False}
        calls.append(call)
        by_id[tid] = call
    elif kind == "user" and block.get("type") == "tool_result" and tid in by_id:
        by_id[tid]["result"] = _flatten(block.get("content"))
        by_id[tid]["error"] = bool(block.get("is_error"))


def progress_block(journal: Path, max_chars: int = PROGRESS_CHARS) -> str:
    """The turn's tool calls, newest first, one line each, within `max_chars`.
    "" when the journal is unreadable or holds no call."""
    try:
        with open(journal, encoding="utf-8", errors="replace") as f:
            calls = tool_calls(f)
    except OSError:
        return ""
    if not calls:
        return ""
    head = (
        "=== THIS TURN'S PROGRESS (restored after compaction) ===\n"
        "The tool calls this turn made before the compaction, newest first: what "
        "each was called with and the short form of what came back. These ids "
        "and counts are real; use them rather than finding the same things "
        "again.\n"
    )
    runs = _runs([_line(c) for c in reversed(calls)])
    kept: list[str] = []
    used, shown = len(head), 0
    for line, n in runs:
        text = line if n == 1 else f"{line} (x{n}, the same call repeated)"
        if used + len(text) > max_chars and kept:
            break
        kept.append(text)
        used += len(text) + 1
        shown += n
    if shown < len(calls):
        kept.append(f"- ... and {len(calls) - shown} earlier call(s) not shown")
    return head + "\n".join(kept)


def _runs(lines: list[str]) -> list[tuple[str, int]]:
    """Consecutive identical lines folded into one with a count."""
    out: list[tuple[str, int]] = []
    for line in lines:
        if out and out[-1][0] == line:
            out[-1] = (line, out[-1][1] + 1)
        else:
            out.append((line, 1))
    return out


def _line(call: dict) -> str:
    return f"- {call['name']}({_args(call['input'])}) -> {_result(call)}"


def _args(inp: object) -> str:
    if not isinstance(inp, dict):
        return _clip(_squash(str(inp)), _ARGS_CHARS)
    return _clip(", ".join(f"{k}={_scalar(v, 60)}" for k, v in inp.items()),
                 _ARGS_CHARS)


def _result(call: dict) -> str:
    text = call["result"]
    if text is None:
        return "no result"
    if call["error"]:
        return "ERROR " + _clip(_squash(text), _RESULT_CHARS)
    try:
        data = json.loads(text)
    except ValueError:
        return _clip(_squash(text), _RESULT_CHARS) or "(empty)"
    return _clip(_shape(data), _RESULT_CHARS)


def _shape(data: object, depth: int = 0) -> str:
    if isinstance(data, list):
        return _list_shape(data)
    if not isinstance(data, dict):
        return _scalar(data, 80)
    parts = []
    for k, v in data.items():
        if isinstance(v, list):
            parts.append(f"{k}={_list_shape(v)}")
        elif isinstance(v, dict):
            parts.append(f"{k}={{{_shape(v, 1)}}}" if depth == 0 else f"{k}={{...}}")
        else:
            parts.append(f"{k}={_scalar(v, 40)}")
    return ", ".join(parts)


def _list_shape(items: list) -> str:
    ids = [i for i in (_item_id(x) for x in items[:_IDS_SHOWN]) if i]
    more = ", ..." if ids and len(items) > _IDS_SHOWN else ""
    return f"[{len(items)}" + (": " + ", ".join(ids) + more if ids else "") + "]"


def _item_id(item: object) -> str:
    """An item's id and, where it has one, its name or title."""
    if not isinstance(item, dict):
        return ""
    key = next((k for k in item if k == "id" or k.endswith("_id")), None)
    if key is None:
        return ""
    label = item.get("title") or item.get("name")
    return str(item[key]) + (f" {_scalar(label, 24)}" if isinstance(label, str) else "")


def _flatten(content: object) -> str:
    if isinstance(content, list):
        content = "\n".join(str(b.get("text") or "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    return str(content or "")


def _scalar(v: object, cap: int) -> str:
    if isinstance(v, str):
        return json.dumps(_clip(_squash(v), cap), ensure_ascii=False)
    if isinstance(v, (list, dict)):
        return _clip(json.dumps(v, ensure_ascii=False, separators=(",", ":")), cap)
    return str(v)


def _squash(s: str) -> str:
    return " ".join(s.split())


def _clip(s: str, cap: int) -> str:
    return s if len(s) <= cap else s[:cap - 3] + "..."
