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
    "properties": {
        "topic": {
            "type": "string",
            "description": "Optional free-text focus; currently informational only.",
        }
    },
    "additionalProperties": False,
}


def _pin_owner() -> None:
    """Best-effort: pin the operator owner so owner-scoped stores (jobs, knowledge)
    resolve. Single-operator system."""
    try:
        from services import auth_store, ownership
        oid = auth_store.operator_id()
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
            "result": {"tools": [{
                "name": TOOL_NAME,
                "description": TOOL_DESC,
                "inputSchema": TOOL_SCHEMA,
            }]},
        }
    if method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        if name != TOOL_NAME:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
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
