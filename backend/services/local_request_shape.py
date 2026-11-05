"""A local model's Messages request put in the shape the server reads right,
set on its way through the hop (`routes/local_llm`), after its thinking.

**Tool results first, in their own turn.** The CLI sends what it adds after a
tool's answer (its reminders, and the compaction instruction itself) in the
same user message as the `tool_result` blocks, after them, as the API wants.
llama-server's Anthropic conversion turns that message into a user message of
its text and THEN one `tool` message per result, so the model reads the text
before the answers it follows. For a compaction that order is the failure: on
2026-10-18 a local chat's summary instruction rendered ahead of the last tool
answer, the prompt ended on that answer, and the model carried on with the
work, which the CLI kept as the summary. Each such message is split where
results and other blocks meet, so each run is its own user message in the
order sent; consecutive user messages are one turn to the API, and a server
that converts them renders the results before the text.

**Room for a compaction's summary.** Every reply of a local turn is capped at
`LOCAL_MAX_OUTPUT_TOKENS`, which also sets when the CLI compacts, and the CLI
holds a compaction to that cap too. Its summary prompt asks for an analysis
and then nine sections: measured that day, two whole summaries took 6,721 and
7,234 tokens, and a third stopped at exactly 8,000 mid-summary. A compaction
(`x-claude-code-request-class`) may write up to `COMPACTION_OUT`, as far as
the model's window leaves room after its prompt.

**A compaction model, when one is set.** The operator may name one local
model that writes every compaction instead of the chat's own. It takes the
request only when its window holds the prompt and leaves the summary at least
the room the chat's model would have given it, so a switch never cuts a
summary short; otherwise the chat's model keeps it. The room is then sized
against the window of whichever model ends up named.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from services import compaction_brief

log = logging.getLogger("workshop.local_request_shape")

COMPACTION_OUT = 16000
# Kept free of the window beyond the prompt's estimate.
MARGIN = 1024
# A deliberately high estimate, so the room it leaves is never too much:
# JSON-heavy tool output runs near three characters a token, prose nearer four.
CHARS_PER_TOKEN = 3
# A picture's tokens, which its base64 length does not tell.
IMAGE_TOKENS = 4096


def _is_result(block) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def split_results(messages: list) -> list:
    """`messages` with every user message that mixes tool results and other
    blocks cut into consecutive user messages, one per run of either, in the
    order sent. Every other message is the same object."""
    out = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if (msg.get("role") if isinstance(msg, dict) else None) != "user" \
                or not isinstance(content, list) \
                or len({_is_result(b) for b in content}) < 2:
            out.append(msg)
            continue
        runs: list[list] = []
        for b in content:
            if runs and _is_result(runs[-1][0]) == _is_result(b):
                runs[-1].append(b)
            else:
                runs.append([b])
        out.extend({**msg, "content": run} for run in runs)
    return out


def _tokens(value) -> int:
    if isinstance(value, list):
        return sum(_tokens(v) for v in value)
    if isinstance(value, dict):
        if value.get("type") == "image":
            return IMAGE_TOKENS
        return sum(_tokens(v) for v in value.values())
    return len(str(value)) // CHARS_PER_TOKEN + 1


def request_tokens(payload: dict) -> int:
    """A request's estimated tokens: system, tools and messages."""
    return sum(_tokens(payload.get(k) or "") for k in ("system", "tools", "messages"))


def compaction_room(payload: dict, window: int,
                    tokens: int | None = None) -> int | None:
    """The `max_tokens` a compaction request may have in `window`: up to
    `COMPACTION_OUT`, never below what it asked, None to leave it. `tokens` is
    the request's estimate when the caller already has it."""
    asked = payload.get("max_tokens")
    if window <= 0 or not isinstance(asked, int):
        return None
    if tokens is None:
        tokens = request_tokens(payload)
    best = min(COMPACTION_OUT, window - tokens - MARGIN)
    return best if best > asked else None


def compaction_model(payload: dict, chosen: str | None,
                     window_of: Callable[[str], int]) -> str | None:
    """`chosen` when it should write this compaction instead of the model the
    request names, else None. It must have a known window that holds the
    prompt with at least the summary room the named model would give."""
    named = str(payload.get("model") or "")
    if not chosen or chosen == named:
        return None
    window = window_of(chosen)
    tokens = request_tokens(payload)
    asked = payload.get("max_tokens")
    need = max(compaction_room(payload, window_of(named), tokens) or 0,
               asked if isinstance(asked, int) else 0)
    if window <= 0 or window - tokens - MARGIN < need:
        log.info("compaction stays on %s: %s has window %d, the request needs "
                 "%d plus %d for the summary", named, chosen, window, tokens,
                 need)
        return None
    return chosen


def shape(body: bytes, request_class: str | None,
          window_of: Callable[[str], int],
          compaction_to: str | None = None) -> bytes:
    """`body` with its tool results in their own turns and, for a compaction,
    sent to `compaction_to` when that model fits (`compaction_model`) and with
    room for the summary in the window of the model it ends up naming
    (`window_of`, 0 for unknown). A body that needs none of this, or is not a
    JSON object, passes unchanged."""
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return body
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return body
    messages = split_results(payload["messages"])
    changed = len(messages) != len(payload["messages"])
    payload["messages"] = messages
    if request_class == "compaction":
        if compaction_brief.briefed(payload["messages"]):
            changed = True
        model = compaction_model(payload, compaction_to, window_of)
        if model is not None:
            payload["model"] = model
            changed = True
        room = compaction_room(payload, window_of(str(payload.get("model") or "")))
        if room is not None:
            payload["max_tokens"] = room
            changed = True
    return json.dumps(payload, ensure_ascii=False).encode() if changed else body
