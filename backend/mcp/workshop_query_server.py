#!/usr/bin/env python3
"""Workshop MCP server (Stage 7 / design 2e).

Two read-only tools for the chat to answer "what is going on / what tasks and
notes exist" from the task + knowledge digest and a semantic search. Neither is
a filesystem Read grant: they expose a bounded, computed summary and ranked
passages, never arbitrary files, so they cannot leak `.runtime` secrets through
the model's egress.

Plus one WRITE tool, `spawn_background`, offered ONLY to a real chat (when
WORKSHOP_QUERY_THREAD is bound): it launches a background agent for that chat and
returns an id, not file contents. The agent's own file access is a run's, not
this server's, and its result reaches the operator through the chat, not this
tool's return. See services/background_runs.

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
    "under docs/, tickets, and the full text of past chats. Returns ranked "
    "passages with the text itself and where it came from, so you can read what "
    "you find. Use it for 'have we decided X before', 'why is Y built this way', "
    "'what did we say about Z', 'is there already a ticket for this', and before "
    "designing anything that may already exist. Returns nothing when nothing is "
    "genuinely relevant, which means an empty result is an answer: there is no "
    "prior art in the workshop for that."
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
            "items": {"type": "string", "enum": ["note", "doc", "chat", "ticket", "vault"]},
            "description": (
                "Restrict to some sources. Default: note, doc, chat and ticket. "
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

# The one WRITE tool on this otherwise read-only server: spawn a background agent
# for THIS chat. It does not return work to the model; it launches a detached
# run that reports back into the chat later and lets the chat continue on its
# own (services/background_runs). The thread it belongs to comes from
# WORKSHOP_QUERY_THREAD, pinned at turn-start exactly like the owner and scope.
SPAWN_TOOL_NAME = "spawn_background"
SPAWN_TOOL_DESC = (
    "Start a BACKGROUND AGENT for a piece of work you want done out-of-band while "
    "you keep talking to the operator: a slow investigation, reading a large "
    "surface, a long search, drafting something sizeable. It runs as its own "
    "process, so the operator is not left waiting, and when it finishes its "
    "result is posted back into THIS chat and the chat continues on its own. Use "
    "this instead of a foreground subagent ONLY when the work is genuinely worth "
    "running in the background; for something quick, just do it inline. Give a "
    "clear, self-contained prompt: the agent does not see this conversation. "
    "Returns immediately with the agent's id; it does NOT return the result."
)
SPAWN_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": (
                "The full, self-contained instruction for the background agent. "
                "It cannot see this chat, so state everything it needs."
            ),
        },
        "label": {
            "type": "string",
            "description": "A short name for the agent, shown to the operator. Optional.",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


# --- Workshop toolkit (Phase L1/L2): reach things by tool, not by grep. -------

WHERE_IS_TOOL_NAME = "where_is"
WHERE_IS_TOOL_DESC = (
    "Return the orientation map for this chat: what its scope is and which tool "
    "reaches each kind of thing (uploads, notes, docs, tickets, runs). Call it "
    "when you are unsure where something lives, instead of searching the "
    "filesystem for it. Takes no arguments."
)
WHERE_IS_TOOL_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

LIST_ATTACHMENTS_TOOL_NAME = "list_attachments"
LIST_ATTACHMENTS_TOOL_DESC = (
    "List the files uploaded to THIS chat: their id, name, kind and size. Use it "
    "to see what the operator attached before reading one with get_file. Text "
    "attachments are already injected into the conversation; use this and "
    "get_file for binaries (zips, images, PDFs) or to re-read one on demand. "
    "Takes no arguments."
)
LIST_ATTACHMENTS_TOOL_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

GET_FILE_TOOL_NAME = "get_file"
GET_FILE_TOOL_DESC = (
    "Read one file uploaded to this chat, by its attachment id (from "
    "list_attachments). Returns the file's text when it is textual or has a text "
    "rendering; for a binary (a zip, an image) it returns the file's on-disk path "
    "so a tool-capable chat can open it directly. This is how you reach an upload "
    "without hunting for where it is stored."
)
GET_FILE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The attachment id, as shown by list_attachments."},
    },
    "required": ["id"],
    "additionalProperties": False,
}

_GET_FILE_MAX_CHARS = 100_000


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

    # `from_scope` is the caller's own scope, distinct from the reach SET above.
    # The `share` field (Phase 5) needs it: a `private` item is found only from
    # inside its own scope, and "which scope am I" is not recoverable from a set
    # of scopes I am allowed to see.
    result = semantic_index.search(query, limit=limit, kinds=tuple(kinds),
                                   scope=scopes,
                                   from_scope=_caller_scope_key(caller_raw))
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


def _run_where_is() -> str:
    """The orientation map for this chat's scope. Single source of truth is
    services.orientation, shared with the injected L1 manifest."""
    scope = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    from services import orientation
    return orientation.manifest_text(scope)


def _run_list_attachments() -> str:
    """Files uploaded to THIS chat: id, name, kind, size."""
    _pin_owner()
    from services import chat_attachments
    thread = _bound_thread()
    recs = chat_attachments.list_records()
    if thread:
        recs = [r for r in recs if (r.get("thread_id") or "") == thread]
    if not recs:
        return "No files have been uploaded to this chat."
    lines = [f"{len(recs)} file(s) in this chat:"]
    for r in recs:
        size = r.get("size")
        human = f"{size} bytes" if isinstance(size, int) else "unknown size"
        lines.append(f"- id {r.get('id')}: {r.get('name')}  ({r.get('kind')}, {human})")
    lines.append("Read one with get_file, by its id.")
    return "\n".join(lines)


def _run_get_file(args: dict) -> str:
    """One attachment's text, or its on-disk path for a binary."""
    _pin_owner()
    from services import chat_attachments
    att_id = (args.get("id") or "").strip()
    if not att_id:
        return "error: an attachment id is required (see list_attachments)."
    rec = chat_attachments.get(att_id)
    if rec is None:
        return f"No attachment with id {att_id} in this chat."
    name = rec.get("name") or att_id
    kind = rec.get("kind") or ""
    head = f"{name} (id {att_id}, {kind}, {rec.get('size')} bytes)"
    # A converted binary (pdf, docx) carries a text rendering: prefer it.
    derived = chat_attachments.derived_path(rec)
    if derived is not None:
        try:
            text = derived.read_text("utf-8", errors="replace")
            return _render_text(head, text)
        except Exception as e:  # noqa: BLE001
            return f"{head}\n(could not read the text rendering: {e})"
    blob = chat_attachments.blob_for(rec)
    if blob is None:
        return f"{head}\n(the file's bytes are missing from the store.)"
    if kind == "text":
        try:
            return _render_text(head, blob.read_text("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return f"{head}\n(could not read as text: {e})"
    # A binary with no text rendering (a zip, an image): hand back the real path
    # so a tool-capable chat can open it. This is a live tool result, not static
    # agent-facing copy, and there is no managed address for "operate on these
    # bytes with Bash".
    return (
        f"{head}\n"
        f"This is a binary file with no text rendering. Its path on disk is:\n"
        f"{blob}\n"
        "Open it with your own tools (for a zip, unzip it; for an image, view it)."
    )


def _render_text(head: str, text: str) -> str:
    if len(text) > _GET_FILE_MAX_CHARS:
        text = text[:_GET_FILE_MAX_CHARS] + "\n...(truncated)"
    return f"{head}\n\n{text}"


def _bound_thread() -> str | None:
    """The chat this server instance belongs to, if any. Pinned at turn-start as
    WORKSHOP_QUERY_THREAD (app_registry.set_current_query_thread). Absent for a
    background/CLI spawn with no chat behind it, which is why spawn_background is
    only offered when it is present."""
    t = (os.environ.get("WORKSHOP_QUERY_THREAD") or "").strip()
    return t or None


def _tool_list() -> list:
    tools = [
        {"name": TOOL_NAME, "description": TOOL_DESC, "inputSchema": TOOL_SCHEMA},
        {"name": SEARCH_TOOL_NAME, "description": SEARCH_TOOL_DESC,
         "inputSchema": SEARCH_TOOL_SCHEMA},
        {"name": WHERE_IS_TOOL_NAME, "description": WHERE_IS_TOOL_DESC,
         "inputSchema": WHERE_IS_TOOL_SCHEMA},
        {"name": LIST_ATTACHMENTS_TOOL_NAME, "description": LIST_ATTACHMENTS_TOOL_DESC,
         "inputSchema": LIST_ATTACHMENTS_TOOL_SCHEMA},
        {"name": GET_FILE_TOOL_NAME, "description": GET_FILE_TOOL_DESC,
         "inputSchema": GET_FILE_TOOL_SCHEMA},
    ]
    # Only a real chat can spawn a background agent for itself.
    if _bound_thread():
        tools.append({"name": SPAWN_TOOL_NAME, "description": SPAWN_TOOL_DESC,
                      "inputSchema": SPAWN_TOOL_SCHEMA})
    return tools


def _run_spawn(args: dict) -> str:
    """Launch a background agent for the bound chat. Returns a confirmation."""
    thread = _bound_thread()
    if not thread:
        return "Cannot spawn a background agent: this tool is not bound to a chat."
    owner_raw = (os.environ.get("WORKSHOP_QUERY_OWNER") or "").strip()
    try:
        owner_id = int(owner_raw)
    except ValueError:
        return "Cannot spawn a background agent: no owner is bound to this chat."
    scope = (os.environ.get("WORKSHOP_QUERY_SCOPE") or "workshop").strip() or "workshop"
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return "A background agent needs a prompt describing what to do."
    label = (args.get("label") or "").strip() or None
    from services import background_runs
    job_id = background_runs.launch(
        thread_id=thread, owner_id=owner_id, scope=scope, prompt=prompt, label=label)
    return (
        f"Background agent started (id {job_id}"
        + (f", '{label}'" if label else "")
        + "). It is running now; its result will appear in this chat when it "
        "finishes, and the chat will continue on its own. Tell the operator you "
        "have kicked it off, in one line, and carry on."
    )


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
            "result": {"tools": _tool_list()},
        }
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in (TOOL_NAME, SEARCH_TOOL_NAME, SPAWN_TOOL_NAME,
                        WHERE_IS_TOOL_NAME, LIST_ATTACHMENTS_TOOL_NAME,
                        GET_FILE_TOOL_NAME):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            if name == SEARCH_TOOL_NAME:
                text = _run_search(params.get("arguments") or {})
            elif name == SPAWN_TOOL_NAME:
                text = _run_spawn(params.get("arguments") or {})
            elif name == WHERE_IS_TOOL_NAME:
                text = _run_where_is()
            elif name == LIST_ATTACHMENTS_TOOL_NAME:
                text = _run_list_attachments()
            elif name == GET_FILE_TOOL_NAME:
                text = _run_get_file(params.get("arguments") or {})
            else:
                text = _build_digest()
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
