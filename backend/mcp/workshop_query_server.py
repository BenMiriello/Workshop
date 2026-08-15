#!/usr/bin/env python3
"""Read-only workshop-query MCP server (Stage 7 / design 2e).

A scoped, read-only tool for the general chat to answer "what is going on / what
tasks and notes exist" from the task + knowledge digest ONLY. It is deliberately
NOT a filesystem Read grant: it exposes a bounded, computed summary (open tasks,
running/recent jobs, knowledge note titles) and never reads arbitrary files, so
it cannot leak `.runtime` secrets through the model's egress.

Transport: MCP stdio (newline-delimited JSON-RPC 2.0), implemented by hand so it
needs no MCP SDK (none is installed in the backend venv). The claude CLI spawns
this over stdio; it answers initialize / tools/list / tools/call / ping.

Scope comes from WORKSHOP_QUERY_SCOPE (the chat's binding, default "workshop" =
workspace-wide). Runs under the backend venv with PYTHONPATH=backend so it can
reuse services.workspace_digest + services.knowledge (single source of truth for
the digest, shared with the ambient context injection).
"""
from __future__ import annotations

import json
import os
import sys

SERVER_NAME = "workshop"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2025-06-18"

TOOL_NAME = "query"
TOOL_DESC = (
    "Read-only summary of ongoing work in the Workshop: open tasks, running and "
    "recent jobs, and knowledge note titles for the current scope. Use it to "
    "answer 'what is going on', 'what tasks exist', or 'what notes do we have'. "
    "It returns a computed digest only; it cannot read files or secrets."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# The `topic` parameter is gone rather than implemented. It was declared, was
# accepted, and was then discarded: `_build_digest()` takes no arguments and
# never read it. A tool that advertises a filter it ignores is worse than one
# that advertises nothing, because the model spends a turn phrasing a query and
# reasons over an answer it believes was narrowed. Searching is now a real tool,
# below, and `query` is honestly what it always was: a fixed digest.

SEARCH_TOOL_NAME = "search"
SEARCH_TOOL_DESC = (
    "Search the workshop's own writing by meaning: knowledge notes, everything "
    "under docs/, and the full text of past chats. Returns ranked passages with "
    "the text itself and where it came from, so you can read what you find. Use "
    "it for 'have we decided X before', 'why is Y built this way', 'what did we "
    "say about Z', and before designing anything that may already exist. "
    "Returns nothing when nothing is genuinely relevant, which means an empty "
    "result is an answer: there is no prior art in the workshop for that."
)
SEARCH_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What you want to know, phrased as a question or a topic.",
        },
        "kinds": {
            "type": "array",
            "items": {"type": "string", "enum": ["note", "doc", "chat", "vault"]},
            "description": (
                "Restrict to some sources. Default: note, doc and chat. "
                "'vault' is the operator's personal Obsidian notes and is only "
                "searched when you ask for it by name."
            ),
        },
        "scope": {
            "type": "string",
            "description": (
                "Narrow to one scope and everything below it in the tree "
                "(e.g. 'project:birds', 'app:budget'). Default: everything "
                "this chat's own scope can reach, which already excludes "
                "sibling projects and, unless this chat IS a development "
                "chat, the development scope."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "How many passages to return. Default 6, max 20.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _pin_owner() -> None:
    """Pin the data owner so owner-scoped stores (jobs, knowledge) resolve.

    This runs as a SEPARATE stdio subprocess of the `claude` CLI, spawned fresh
    per turn — it has no access to the backend request's `ownership`
    contextvar, only whatever the parent process put in its environment.
    `WORKSHOP_QUERY_OWNER` (app_registry._platform_mcp_servers) is the real
    caller, set at spawn time from the actual request. Falling back to the
    operator when it's absent is deliberate, not a workaround: a background
    automation pass has no per-request owner to hand down either, and the
    operator's id IS the correct one there (ownership.py's own rule for
    background loops). What changed is that an ordinary chat turn now passes
    its real caller instead of this always defaulting to the operator
    regardless of who was actually asking — see
    docs/security-audit-2026-08-11-scope-separation.md section 3.
    """
    try:
        from services import auth_store, ownership
        raw = os.environ.get("WORKSHOP_QUERY_OWNER", "").strip()
        oid = int(raw) if raw else auth_store.operator_id()
        if oid is not None:
            ownership.set_current_owner(oid)
    except Exception:
        pass


def _build_digest() -> str:
    scope = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    _pin_owner()
    parts: list[str] = []
    # Task + job digest (workspace-wide for general; own scope otherwise).
    try:
        from services import workspace_digest
        ctx = None if scope in ("workshop", "general", "global") else scope
        body = workspace_digest.digest_for_context(ctx)
        if body:
            parts.append(body)
    except Exception as e:  # noqa: BLE001
        parts.append(f"(work digest unavailable: {e})")
    # Knowledge note titles for the scope (index only, never note bodies here).
    try:
        from services import knowledge
        kscope = "workshop" if scope in ("general", "global") else scope
        idx = knowledge.index_block(kscope)
        if idx:
            parts.append(f"Knowledge notes (scope: {kscope}):\n{idx}")
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "No open tasks, jobs, or notes in scope."


def _caller_scope_key(raw: str) -> str:
    """`WORKSHOP_QUERY_SCOPE` is the chat's `context_key` ("general",
    "workshop", "budget", a project/app/agent key...) -- the same value
    `_build_digest` reads. `reach()` operates on `scope_tree`'s scope_key
    namespace, so this is the same translation `_build_digest` already
    applies inline, pulled out because the search path needs it too."""
    from services import knowledge
    ck = "workshop" if raw in ("general", "global") else raw
    try:
        return knowledge.scope_for_context(ck)
    except Exception:
        return "workshop"


def _reach_scopes(scope_key: str) -> set[str] | None:
    """`reach(scope_key)` as a set, or None when the tree does not recognise
    the key. `reach` is curation, not a security boundary (scope_tree module
    docstring, SEC-A2), so an unrecognised scope degrades to "no filter"
    rather than an error."""
    from services import scope_tree
    try:
        return set(scope_tree.reach(scope_key))
    except ValueError:
        return None


def _run_search(args: dict) -> str:
    """Ranked passages, rendered for a model to read.

    Each hit leads with its source so the model can cite it or go read the whole
    thing, and carries the passage itself. Returning a title alone was the old
    tool's defect and is the one thing this must not repeat."""
    _pin_owner()
    from services import semantic_index

    query = (args.get("query") or "").strip()
    if not query:
        return "error: a query is required"
    kinds = args.get("kinds") or list(semantic_index.DEFAULT_KINDS)
    limit = max(1, min(int(args.get("limit") or 6), 20))

    # The default candidate set is what THIS chat's scope can reach, not
    # everything: a workshop chat searches every project and app but not
    # development, a project chat cannot reach a sibling project (memory-and-
    # documentation-model-2026-08-14.md sec 4.2). An explicit `scope` argument
    # narrows further and is itself bounded by the caller's own reach, so a
    # workshop chat cannot ask its way into development just by naming it.
    caller_raw = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    my_reach = _reach_scopes(_caller_scope_key(caller_raw))
    requested = (args.get("scope") or "").strip()
    scopes = my_reach
    if requested:
        req_reach = _reach_scopes(requested)
        if req_reach is not None:
            scopes = req_reach if my_reach is None else (my_reach & req_reach)

    result = semantic_index.search(query, limit=limit, kinds=tuple(kinds), scope=scopes)
    hits, mode, rebuilding = result["hits"], result["mode"], result["rebuilding"]

    rebuild_note = ("\n(the index is rebuilding in the background; these results "
                     "are from what is on disk right now)" if rebuilding else "")

    if not hits:
        st = semantic_index.status()
        if not st["chunks"]:
            why = st["embedder_error"] or "it has not been built yet"
            return f"The search index is empty: {why}.{rebuild_note}"
        # An empty result is a real answer, and saying so plainly stops the model
        # from reading it as a malfunction and retrying the same query.
        return (f"No passages in the workshop are relevant to that "
                f"({st['chunks']} indexed, mode: {mode}). Nothing has been "
                f"written about it.{rebuild_note}")

    # The mode is surfaced up front, not buried in a footer: a model that does
    # not know it got a degraded (lexical) result over-trusts it.
    out = [f"{len(hits)} passage(s) for: {query}  [mode: {mode}]{rebuild_note}", ""]
    for h in hits:
        where = h["ref"]
        if h["kind"] == "chat":
            where = f"chat {h['ref']}"
        stamp = (h["ts"] or "")[:10]
        scope = f" · scope {h['scope']}" if h.get("scope") else ""
        out.append(f"--- [{h['score']:.2f}] {h['kind']}: {h['title']} ({where}{scope}"
                   + (f" · {stamp}" if stamp else "") + ")")
        out.append(h["text"])
        out.append("")
    return "\n".join(out)


def _handle(msg: dict) -> dict | None:
    """Return a JSON-RPC response dict, or None for a notification (no reply)."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or DEFAULT_PROTOCOL
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"tools": [
                {"name": TOOL_NAME, "description": TOOL_DESC,
                 "inputSchema": TOOL_SCHEMA},
                {"name": SEARCH_TOOL_NAME, "description": SEARCH_TOOL_DESC,
                 "inputSchema": SEARCH_TOOL_SCHEMA},
            ]},
        }
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in (TOOL_NAME, SEARCH_TOOL_NAME):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            text = (_run_search(params.get("arguments") or {})
                    if name == SEARCH_TOOL_NAME else _build_digest())
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"error: {e}"}],
                               "isError": True}}
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": False}}
    # Unknown method with an id -> method-not-found; notification -> ignore.
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> None:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = _handle(msg)
        except Exception as e:  # noqa: BLE001 - never die on one bad message
            resp = ({"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": str(e)}}
                    if msg.get("id") is not None else None)
        if resp is not None:
            out.write(json.dumps(resp) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
