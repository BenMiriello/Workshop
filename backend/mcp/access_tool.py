"""`chat_access` on the workshop tool server: what this chat may reach, read and
changed by the chat itself (services/page_link, services/chat_apps).

The operator sets a chat's access in its Access menu, and may set it wrong for
the task. Rather than stopping to ask for a switch the operator would flip
anyway, the chat changes it and says what it changed. Offered to any turn with
a chat behind it (WORKSHOP_QUERY_THREAD) and a backend to reach
(WORKSHOP_ACCESS_URL, set at turn start).

The tool texts are PLATFORM prompt text: generic, about any chat and any app.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp import turn_context

NAME = "chat_access"

DESC = (
    "See and change what this chat can reach: how much of the page beside it "
    "(level: send = only what the operator attaches, see = read the page, use = "
    "read and act on it, drive = in control of it) and which apps' tools it has, "
    "with each app's own options (for example an app's history). Call with no "
    "arguments to see the current settings and every app and option on offer. "
    "When a task needs access this chat does not have, turn it on here rather "
    "than asking, and tell the operator in one line what you changed. A level "
    "change takes effect on the next page call, and an app's option on the "
    "app's next call; a newly granted app's tools arrive on the next turn."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["send", "see", "drive"],
                  "description": "send: only what the operator sends; see: reads the page; drive: full autonomy, the operator can step in anytime"},
        "grant": {
            "type": "object",
            "description": "App slug to the option ids to have on, e.g. {\"some-app\": [\"history\"]}. "
                           "Replaces that app's options; an empty list grants the app with none.",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "revoke": {"type": "array", "items": {"type": "string"},
                   "description": "App slugs to take away from this chat."},
    },
    "additionalProperties": False,
}


def offered() -> bool:
    return bool(turn_context.get("WORKSHOP_ACCESS_URL"))


def tool_list() -> list[dict]:
    return [{"name": NAME, "description": DESC, "inputSchema": SCHEMA}] if offered() else []


def run(args: dict) -> tuple[str, bool]:
    req = urllib.request.Request(
        turn_context.get("WORKSHOP_ACCESS_URL"),
        data=json.dumps(args or {}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Page-Token": turn_context.get("WORKSHOP_ACCESS_TOKEN", "")},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001 - the status is enough to report
            detail = None
        return f"error: {detail or f'the backend refused ({e.code})'}", True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"error: the backend could not be reached: {e}", True
    return body.get("text") or json.dumps(body, ensure_ascii=False), False
