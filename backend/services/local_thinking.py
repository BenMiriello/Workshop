"""What a local model's chat request may think, set per request from its effort.

Effort on a local model means the thinking level. The CLI sends the picked level
in `output_config.effort`, which a local server ignores, and has no setting that
sends a thinking budget for a model it does not know. So the request is
rewritten on its way (`routes/local_llm`): two knobs reach the server, and they
cost very differently.

- `thinking.budget_tokens` caps the thinking the sampler allows. It changes
  nothing in the prompt, so it is set per request: a tool-result continuation
  needs little, an operator's message what the effort says, and compaction or a
  side request (titles, summaries) none at all.
- `chat_template_kwargs.reasoning_effort` is written by the chat template as the
  first line of the system prompt. Changing it makes the server read the whole
  context again, so it follows the CHAT's effort, is the same on every request
  of the chat, and changes only when the operator changes the effort.

The request class arrives in the CLI's `x-claude-code-request-class` header
(absent is `main`; the CLI sends it when `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`),
and the chat is named by the CLI's session id.
"""
from __future__ import annotations

import json
from collections import OrderedDict

CLASS_HEADER = "x-claude-code-request-class"
SESSION_HEADER = "x-claude-code-session-id"

LEVELS = ("low", "medium", "high")

# Request classes that do not think: summarising the conversation, and the
# CLI's side requests.
NO_THINKING = ("compaction", "auxiliary")
CONTINUATION = 512
BY_EFFORT = {"low": 1024, "medium": 2048, "high": 4096}
# The server's own budget, for a request that names no effort.
DEFAULT = 2048

# The server's chat template has no `high`; its `xhigh` is the rung above
# `medium`. One table because this is the template's spelling, not ours.
TEMPLATE_EFFORT = {"low": "low", "medium": "medium", "high": "xhigh"}

_KEEP = 512


def new_memory() -> OrderedDict[str, str]:
    """An empty record of each chat's effort. Held by the caller (the app), not
    in this module: it is state of one running backend."""
    return OrderedDict()


def budget(request_class: str | None, effort: str | None,
           continuation: bool) -> int:
    """`thinking.budget_tokens` for one request."""
    if (request_class or "main") in NO_THINKING:
        return 0
    if continuation:
        return CONTINUATION
    return BY_EFFORT.get(effort or "", DEFAULT)


def is_continuation(messages) -> bool:
    """Whether the request answers tool results: its last user message carries
    them, rather than something the operator wrote."""
    for msg in reversed(messages or ()):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            return isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content)
    return False


def chat_effort(memory: OrderedDict[str, str], chat: str,
                request_class: str | None, effort: str | None) -> str | None:
    """The chat's effort: learnt from its main requests, the ones the operator's
    choice is passed on, and kept in `memory` for every other request of it."""
    if (request_class or "main") == "main" and effort in BY_EFFORT:
        memory[chat] = effort
        memory.move_to_end(chat)
        while len(memory) > _KEEP:
            memory.popitem(last=False)
    return memory.get(chat)


def declines_thinking(payload: dict) -> bool:
    """Whether the caller turned thinking off itself, as a title request does:
    its choice stands, budget and template kwargs untouched."""
    thinking = payload.get("thinking")
    kwargs = payload.get("chat_template_kwargs")
    return ((isinstance(thinking, dict) and thinking.get("type") == "disabled")
            or (isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False))


def rewrite(body: bytes, headers, memory: OrderedDict[str, str]) -> bytes:
    """A Messages request body with its thinking budget set for its class and
    the chat's effort, and the chat's effort in the template's kwargs. Nothing
    else in it changes; a body that is not a JSON object passes unchanged."""
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return body
    if not isinstance(payload, dict) or declines_thinking(payload):
        return body
    get = {k.lower(): v for k, v in dict(headers).items()}.get
    klass = get(CLASS_HEADER) or "main"
    chat = get(SESSION_HEADER) or ""
    asked = (payload.get("output_config") or {}).get("effort")
    effort = chat_effort(memory, chat, klass, asked) if chat else (
        asked if asked in BY_EFFORT else None)
    thinking = payload.get("thinking")
    payload["thinking"] = {**(thinking if isinstance(thinking, dict) else {}),
                           "type": "enabled",
                           "budget_tokens": budget(
                               klass, effort,
                               is_continuation(payload.get("messages")))}
    if effort:
        kwargs = payload.get("chat_template_kwargs")
        payload["chat_template_kwargs"] = {
            **(kwargs if isinstance(kwargs, dict) else {}),
            "reasoning_effort": TEMPLATE_EFFORT[effort]}
    return json.dumps(payload, ensure_ascii=False).encode()
