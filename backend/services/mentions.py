"""@-mention parsing and routing for chat turns.

An `@slug` in a chat message addresses ONE agent. That agent answers in the
chat, attributed to it; the ordinary assistant does not answer a message that
was addressed to someone else.

Two rules the operator set explicitly, both enforced here rather than in the UI
so a message sent from any surface behaves the same way:

  * **First tag wins.** Only the first `@slug` in a message is parsed. Later
    tags stay literal text. Addressing two agents in one breath has no coherent
    meaning (whose turn is it? do they see each other?), so instead of inventing
    a turn-taking policy the second tag simply is not a mention.

  * **Mentionable is opt-in.** `@` only resolves against agents whose definition
    sets `mentionable: true`. Most agents are triggered workers that report
    through the inbox and have no business in a chat; surfacing them in the
    picker would only make it harder to find the ones that do.

Scope bounds delivery: an agent is addressable from a chat whose scope is the
agent's own scope or an ancestor of it. A project agent can be reached from the
workshop chat and from its project's chat, not from an unrelated project's.
"""
from __future__ import annotations

import re
from typing import Any

# A mention is `@` + a slug, anchored so it cannot fire inside an email address
# or a path. The slug grammar matches SLUG_RE in agents_registry.
_MENTION_RE = re.compile(r"(?:(?<=\s)|^)@([a-z][a-z0-9-]{0,63})\b")


def find_first(content: str) -> tuple[str | None, int, int]:
    """The first `@slug` in `content` as (slug, start, end), or (None, -1, -1).

    Position is returned so a caller can strip or re-render the token without
    re-scanning, and so "first" is verifiable rather than implied."""
    m = _MENTION_RE.search(content or "")
    if not m:
        return None, -1, -1
    return m.group(1), m.start(), m.end()


def scope_allows(chat_scope: str, agent_scope: str) -> bool:
    """May a chat at `chat_scope` address an agent whose home is `agent_scope`?

    True when they are the same scope, or when the chat sits ABOVE the agent —
    `workshop` reaches everything, `project:x` reaches `project:x` and anything
    nested under it. The inverse is deliberately false: an agent's own private
    chat is not a place to summon the workshop's agents from.

    A chat with no resolvable scope is treated as `workshop`, which is where an
    unscoped conversation already files its work.
    """
    chat = (chat_scope or "workshop").strip()
    home = (agent_scope or "workshop").strip()
    if chat == home:
        return True
    if chat == "workshop":
        return True
    return home.startswith(chat + ":") or home.startswith(chat + "/")


def mentionable_agents(chat_scope: str) -> list[dict]:
    """Agents this chat may address, in picker order.

    Ordering is by how likely the operator means them: agents whose home scope IS
    this chat's scope come first (the local ones), then the wider ones, and
    alphabetically within each band so the list is stable between renders.
    """
    from services import agents_registry

    try:
        roster = agents_registry.list_agents()
    except Exception:
        return []
    chat = (chat_scope or "workshop").strip()
    out: list[dict] = []
    for a in roster:
        if not a.get("mentionable") or a.get("retired"):
            continue
        home = _home_scope(a)
        if not scope_allows(chat, home):
            continue
        out.append({
            "slug": a.get("slug"),
            "name": a.get("name") or a.get("slug"),
            "role": a.get("role"),
            "purpose": a.get("purpose") or "",
            "scope": home,
            "local": home == chat,
        })
    out.sort(key=lambda x: (not x["local"], x["slug"]))
    return out


def resolve(content: str, chat_scope: str) -> dict | None:
    """The agent addressed by `content`, or None.

    None covers every "not a mention" case uniformly: no `@` at all, an `@` that
    names nothing, an agent that is not mentionable, and an agent out of scope.
    The caller then runs an ordinary turn — an unresolvable `@word` is just text,
    never an error, because the operator typing an email address or a decorator
    must not fail their message.
    """
    slug, _, _ = find_first(content)
    if not slug:
        return None
    for a in mentionable_agents(chat_scope):
        if a["slug"] == slug:
            return a
    return None


def _home_scope(agent: dict[str, Any]) -> str:
    """The scope address an agent lives in, from its registry record."""
    if agent.get("project_slug"):
        return f"project:{agent['project_slug']}"
    scope = (agent.get("scope") or "").strip()
    return scope if scope.startswith(("project:", "app:", "agent:")) else "workshop"
