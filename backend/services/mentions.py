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

Scope bounds who is reachable, and mentioning and delivery use DIFFERENT rules:

  * **Mentioning** (`scope_allows`) works in either direction along one branch.
    The operator typed the name, so there is nothing to protect them from; a
    workshop-scope agent is addressable from every chat under it. Only a
    genuinely unrelated branch is excluded (`project:a` cannot summon
    `project:b`'s agent).

  * **Delivery** (`may_deliver_to`) is unsolicited, so it only flows UP: an
    agent surfaces into its own chat or a broader one, never sideways.
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


ROOT_SCOPE = "workshop"


def is_ancestor(outer: str, inner: str) -> bool:
    """Does `outer` contain `inner` in the scope tree (strictly)?

    `workshop` is the root and contains everything, but it is NOT a string
    prefix of `project:x` or `agent:y` — the address grammar writes the root as
    a bare word and its children as `kind:name`, so they look like siblings to a
    prefix test. That mismatch is worth stating rather than rediscovering: a
    plain `startswith` says workshop contains nothing, which silently empties
    every scoped picker.
    """
    if outer == inner:
        return False
    if outer == ROOT_SCOPE:
        return True
    return inner.startswith(outer + ":")


def _on_same_branch(a: str, b: str) -> bool:
    """Do these two scope addresses lie on one path through the scope tree?"""
    return a == b or is_ancestor(a, b) or is_ancestor(b, a)


def scope_allows(chat_scope: str, agent_scope: str) -> bool:
    """May a chat at `chat_scope` MENTION an agent whose home is `agent_scope`?

    Either direction along one branch. `workshop` reaches everything below it,
    and — the part that matters — everything below reaches back up: a
    workshop-scope agent is addressable from inside a project or agent chat.

    The first cut of this only allowed reaching DOWNWARD, by analogy with the
    delivery rule below. That was wrong, and wrong in a way that made the
    feature look broken: every project and agent chat got an empty picker,
    because the general-purpose agents all live at workshop scope. The two
    rules are not analogous. Delivery is unsolicited and its constraint exists
    to stop noise flowing uphill; a mention is the operator typing a name, so
    there is no noise to prevent and no reason to hide an agent they can see on
    the Agents page. What stays excluded is a genuinely unrelated branch —
    `project:a` cannot summon `project:b`'s agent — because that is a scope
    boundary rather than a volume control.

    A chat with no resolvable scope is treated as `workshop`, which is where an
    unscoped conversation already files its work.
    """
    return _on_same_branch((chat_scope or "workshop").strip(),
                           (agent_scope or "workshop").strip())


def may_deliver_to(chat_scope: str, agent_scope: str) -> bool:
    """May an agent at `agent_scope` push an UNSOLICITED message to this chat?

    Stricter than mentioning, and deliberately one-directional: the chat must be
    the agent's scope or an ancestor of it. This is the operator's rule verbatim
    — "you don't want just any old chat up at the top level getting pings from
    all agents underneath it" — so an agent surfaces upward toward the operator's
    broader chats, never sideways into an unrelated one.
    """
    chat = (chat_scope or ROOT_SCOPE).strip()
    home = (agent_scope or ROOT_SCOPE).strip()
    return chat == home or is_ancestor(chat, home)


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
        home = home_scope(a)
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


def home_scope(agent: dict[str, Any]) -> str:
    """The scope address an agent lives in, from its registry record.

    A project link wins: `agents_registry` writes `project_slug` for every agent
    whose definition file sits in a project tree, and that binding is what
    `scope_tree._default_parent` already reads.

    Otherwise the record's own `scope` goes through the ONE context_key -> scope
    mapping. This used to be a three-prefix allowlist that fell to `workshop`
    for everything else, so an agent declaring `development` was homed in
    `workshop` -- mentionable from every chat in the install and never `local`
    to the one chat it belongs to -- and one declaring a bare app id (`budget`)
    landed in `workshop` rather than `app:budget`. Both answers now match what
    the chat on the other side of `scope_allows` is being compared against,
    which is `scope_for_context(thread.context_key)`.
    """
    if agent.get("project_slug"):
        return f"project:{agent['project_slug']}"
    from services import scope_tree
    return scope_tree.scope_for_context(agent.get("scope"))
