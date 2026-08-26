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
        return (
            "This chat is the development scope: it works on the Workshop platform "
            "itself. `development` NAMES this work scope; it is not an environment "
            "tier (Workshop has no prod/staging/dev split, see below)."
        )
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
        "=== WHAT AND WHERE YOU ARE (orientation; reach by tool, don't grep) ===\n"
        "Workshop runs as ONE live instance with no environment tiers: there is no "
        "separate production, staging, or preview, and no deploy-to-promote step. "
        "\"Live\" means this one running instance, the one in use right now. Its "
        "stores hold the operator's real working data, so act deliberately, but "
        "nothing here is a walled-off \"production\" system and no scope name is an "
        "environment. Do not infer a deployment tier from a scope name or from the "
        "presence of hardening (roles, RLS, isolation): those are tenancy and "
        "security design, not a prod/dev boundary.\n"
        + _scope_line(scope_key) + "\n"
        "You do not need to search the filesystem to find the Workshop's own "
        "things, or to work out the STATE of one of its subsystems. Reach each kind "
        "by its tool:\n"
        "- The current state of a subsystem (database migrations, running services, "
        "queues, git) is whatever that subsystem's own command or tool reports, and "
        "that is authoritative. Read it from there; do not reconstruct it by "
        "hand-parsing files or logs. If a hand check disagrees with the tool, the "
        "tool wins.\n"
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
        "- `where_is` restates this map any time you need it."
    )
