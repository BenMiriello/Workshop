#!/usr/bin/env python3
"""PreToolUse hook for a turn on a local model: end loops and bound the turn.

A local model with a small window repeats itself. It runs the same failing
command again, reads the same file again, and spends the whole turn on tool
calls without ever writing an answer (2026-10-11: 150 requests over two hours,
six compactions, nothing delivered). A model cannot be relied on to stop that
itself, so this does it from outside, with no model call:

- the same tool with the same input a third time inside the last twelve calls is
  refused when its last two answers were the same, with a message that says to
  change approach or report. The answers are recorded by this same script run
  as the PostToolUse hook, so reading a page again after it changed is not a
  loop; with no answers recorded, the third call is refused regardless. A call
  of another kind that succeeds with an answer it has not given before (the
  PostToolUse hook fires only on success) clears the count of every other call,
  so a call retried after its cause was fixed elsewhere is not a loop, while
  alternating with the same answer again is not progress;
- once a turn has made `WORKSHOP_GUARD_MAX_CALLS` tool calls every further one
  is refused, with a message that says to write the report now. The operator's
  next message starts the next stretch from the notes the report leaves.

Exit 2 refuses the call and hands stderr to the model. Any other failure here
lets the call through: a broken guard must not stop a working turn.

State is one small JSON file per turn in the temp directory, named by
`WORKSHOP_GUARD_TURN`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

MAX_CALLS = int(os.environ.get("WORKSHOP_GUARD_MAX_CALLS") or 150)
REPEAT_LIMIT = 3
WINDOW = 12

REPEATED = ("You have made this exact {tool} call {n} times. It is not going to "
            "give a different answer. Say what you learned from it, then take a "
            "different approach or stop and report what is blocked.")
BUDGET = ("This turn has used its {limit} tool calls. Write your report now: what "
          "you did, what you found, what is blocked, and the files you saved. Do "
          "not call another tool.")


def _path(turn: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", turn)[:80] or "none"
    return os.path.join(tempfile.gettempdir(), f"workshop-guard-{safe}.json")


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def record(event: dict, state: dict) -> dict:
    """After a successful call: keep the hash of its last two answers, under its
    key, and when the answer is new, start every other call's count again."""
    key = _hash([event.get("tool_name"), event.get("tool_input")])
    answers = dict(state.get("answers") or {})
    before = list(answers.get(key) or [])
    answer = _hash(event.get("tool_response"))
    answers[key] = (before + [answer])[-2:]
    if len(answers) > WINDOW * 2:
        answers = dict(list(answers.items())[-WINDOW * 2:])
    recent = list(state.get("recent") or [])
    if not before or before[-1] != answer:
        recent = [k for k in recent if k == key]
    return {**state, "answers": answers, "recent": recent}


def decide(event: dict, state: dict) -> tuple[str | None, dict]:
    """(refusal or None, next state)."""
    calls = int(state.get("calls") or 0) + 1
    recent = list(state.get("recent") or [])
    key = _hash([event.get("tool_name"), event.get("tool_input")])
    seen = recent[-(WINDOW - 1):].count(key) + 1
    nxt = {**state, "calls": calls, "recent": (recent + [key])[-WINDOW:]}
    if calls > MAX_CALLS:
        return BUDGET.format(limit=MAX_CALLS), nxt
    answers = (state.get("answers") or {}).get(key) or []
    same = len(answers) < 2 or answers[-1] == answers[-2]
    if seen >= REPEAT_LIMIT and same:
        return REPEATED.format(tool=event.get("tool_name") or "tool", n=seen), nxt
    return None, nxt


def main() -> int:
    try:
        event = json.load(sys.stdin)
        turn = os.environ.get("WORKSHOP_GUARD_TURN") or str(event.get("session_id") or "")
        path = _path(turn)
        if event.get("hook_event_name") == "PostToolUse":
            refusal, nxt = None, record(event, _load(path))
        else:
            refusal, nxt = decide(event, _load(path))
        with open(path, "w") as f:
            json.dump(nxt, f)
    except Exception:
        return 0
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
