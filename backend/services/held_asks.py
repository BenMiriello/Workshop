"""Calls a card asked about and nobody answered, which the operator's next
message approves.

A card that waits out `build_streaming.APPROVAL_WAIT_S` refuses its call, and
the turn says what it could not do. The operator's yes then arrives as an
ordinary message, which no card hears. So the refused call is held on the chat,
and the operator's next message approves every call held before it: the same
call asked again (the same tool with the same input) is answered yes with no
card. That message is the whole approval. A call it approved and nothing used
is dropped by the message after it, and one used is gone. A message is known by
its place in the log (`seq`), so a retried send that appends nothing approves
nothing twice.

A message counts as the operator's when it carries no `sender`, the same rule
the transcript uses (routes/chat `_record_user_turn`).

Only the rules live here, over the list a chat keeps; `chat_store` keeps the
list in the chat's meta (`hold_ask`, `take_approved_ask`, `_operator_wrote`).
"""
from __future__ import annotations

import json


def key(tool: str, tool_input: dict | None) -> str:
    return json.dumps([tool, tool_input or {}], sort_keys=True, default=str)


def valid(raw: object) -> list[dict]:
    return [h for h in raw if isinstance(h, dict) and h.get("key")] if isinstance(raw, list) else []


def held(entries: list[dict], call: str) -> list[dict]:
    """`entries` with `call` held, waiting for the operator's next message."""
    return [h for h in entries if h["key"] != call] + [{"key": call, "approved_by": None}]


def after_message(entries: list[dict], seq: int) -> list[dict]:
    """`entries` once the operator wrote message `seq`: what was held is approved
    by it, what an earlier message approved and nothing used is dropped."""
    return [{**h, "approved_by": h.get("approved_by") or seq} for h in entries
            if h.get("approved_by") in (None, seq)]


def approved(entries: list[dict], call: str) -> bool:
    return any(h["key"] == call and h.get("approved_by") is not None for h in entries)


def spent(entries: list[dict], call: str) -> list[dict]:
    return [h for h in entries if h["key"] != call]
