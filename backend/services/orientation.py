"""L1 orientation manifest.

The small, fixed "how to reach things" map every tool-capable chat and agent is
given, so nothing has to grep the tree to discover where uploads, notes, docs,
tickets or runs live. That rediscovery, an agent narrating "the uploads are in
the content-addressed blob store, extracting the zip" after finding it by hand,
is the exact failure this removes.

It is TOOL-CENTRIC on purpose: it names the verb that reaches each kind of
thing, never a raw filesystem path. Raw OS paths in agent-facing text are a
reviewer-gated violation (scripts/check_no_raw_paths.py), and pointing at a tool
is also what actually stops the grep. This module is the single source of truth
shared by the prompt injection (chat_context.orientation_manifest) and the
`where_is` MCP tool (backend/mcp/workshop_query_server.py).
"""
from __future__ import annotations


def _scope_line(scope_key: str | None) -> str:
    key = (scope_key or "workshop").strip() or "workshop"
    if key in ("workshop", "general", "global"):
        return (
            "This chat is at the workshop root: it can reach every app and "
            "project in this account, but not another account's data."
        )
    if key == "development":
        return "This chat is the development scope: it works on the Workshop platform itself."
    if key.startswith("project:") or key.startswith("app:"):
        return (
            f"This chat is scoped to {key}: its notes, tickets and runs are that "
            "one place's, and search stays inside it."
        )
    if key.startswith("agent:"):
        who = key.split(":", 1)[1]
        return f"This chat is agent {who}: it reads and writes that agent's own memory."
    if key.startswith("role:"):
        return f"This chat carries the {key} layer: procedural memory for that kind of work."
    return f"This chat's scope is {key}."


def manifest_text(scope_key: str | None = None) -> str:
    """The orientation block. `scope_key` is the chat's knowledge scope
    ("workshop", "development", "project:<slug>", "app:<name>", "agent:<id>")."""
    return (
        "=== WHERE THINGS ARE (orientation; reach by tool, don't grep) ===\n"
        + _scope_line(scope_key) + "\n"
        "You do not need to search the filesystem to find the Workshop's own "
        "things. Reach each kind by its tool:\n"
        "- Files uploaded to THIS chat: `list_attachments` lists them; `get_file` "
        "returns one file's text, or its on-disk path for a binary (a zip, an "
        "image) so you can open it directly. Don't hunt the filesystem for an "
        "upload, and don't assume where it lives.\n"
        "- Past decisions, notes, docs, and prior chats: `search` finds them by "
        "meaning. An empty result means there is genuinely no prior art, not that "
        "the search failed.\n"
        "- What is going on now (open tickets, running and recent runs, the note "
        "titles in scope): `query`.\n"
        "- When you cite a doc, note or ticket, write it as a managed link "
        "(`workshop:doc/<path>`, `workshop:note/<scope>/<id>`, "
        "`workshop:ticket/<id>`) so it renders as a chip, not raw text.\n"
        # THE ONE TOOL THAT ANSWERS "HOW DO I RUN SOMETHING IN THE BACKGROUND".
        # Added 2026-08-31. A chat's subagents run inline (a backgrounded one is
        # killed with the turn), and the guard that enforces that used to say
        # "backgrounding is not available here" without naming the tool that
        # provides it -- so a caller who wanted out-of-band work read a refusal
        # instead of a redirect. This manifest exists so nothing has to
        # rediscover where a capability lives; the capability was missing from it.
        "- Work that should outlive this turn and report back into the chat when "
        "it lands: `spawn_background`. A subagent (the Agent tool) runs INLINE in "
        "this chat, so it must be issued foreground; several issued in one "
        "message still run at the same time.\n"
        "- `where_is` restates this map any time you need it."
    )
