"""Task-scope resolution + the `task-patch` chat protocol (orchestration O2).

A chat bound to a task scope (project:/build:/agent:) can maintain its task list
mid-conversation. Builder chats use the shell CLI (scripts/project_tasks.py) via
Bash; data/assistant chats (no filesystem) emit a fenced ``task-patch`` block,
applied server-side in the turn finalize path exactly like the budget plan-patch:
parsed, applied against THIS scope's store, stripped from the stored reply, with
a human-readable outcome appended to meta.notes (which drives the panel refetch).

Scope is the thread's context binding; a provider only writes its own scope
(correctness, not a permission wall in this single-operator system).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from services import tasks

# ```task-patch\n{...}\n``` — same fence style as the budget plan-patch.
_PATCH_BLOCK = re.compile(r"```task-patch\s*\n(.*?)\n```", re.DOTALL)


def root_for_context(context_key: str | None) -> Path | None:
    """The task-store directory for a chat's context binding, or None if the
    binding has no task scope (general / app chats)."""
    if not context_key:
        return None
    if context_key.startswith("project:"):
        from services import build_agents
        return build_agents._project_dir(context_key.split(":", 1)[1])
    if context_key.startswith("build:"):
        return _build_agent_root(context_key.split(":", 1)[1])
    if context_key.startswith("agent:"):
        slug = context_key.split(":", 1)[1]
        from services import agents_registry
        rec = agents_registry.get(slug)
        if rec and rec.get("runtime") == "builder":
            return _build_agent_root(slug)
        return _agent_scope_dir(slug)  # assistant runtime: its own scope
    return None


def _build_agent_root(slug: str) -> Path | None:
    """A build agent's task store: its linked project dir, else its own cwd."""
    from services import build_agents
    try:
        a = build_agents.get_agent(slug)
    except KeyError:
        return None
    project_slug = a.get("project_slug")
    if project_slug:
        pd = build_agents._project_dir(project_slug)
        if pd is not None:
            return pd
    cwd = a.get("cwd")
    return Path(cwd) if cwd else None


def _agent_scope_dir(slug: str) -> Path:
    """A registry assistant's own durable task scope (.runtime/agent-scopes/<slug>)."""
    from services import ownership
    d = ownership._RUNTIME / "agent-scopes" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def apply_task_patch(context_key: str | None, reply: str) -> tuple[str, list[str]]:
    """Find a ``task-patch`` block, apply add/update/note against this chat's
    scope store, strip the block, and return (cleaned_reply, notes). No-op (the
    reply unchanged, empty notes) when there is no block or no task scope."""
    m = _PATCH_BLOCK.search(reply)
    if not m:
        return reply, []
    root = root_for_context(context_key)
    cleaned = _PATCH_BLOCK.sub("", reply).strip()
    if root is None:
        return cleaned, ["(no task scope for this chat, so the task edit was skipped)"]
    try:
        patch = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return cleaned, ["(could not parse a task edit, left the tasks unchanged)"]
    if not isinstance(patch, dict):
        return cleaned, []

    # A patch may name a scope, but it may only write ITS OWN binding.
    named = patch.get("scope")
    if named and named != context_key:
        return cleaned, [f"(task edit ignored: a {context_key} chat cannot write {named})"]

    added = updated = noted = 0
    for op in patch.get("add", []) or []:
        if not isinstance(op, dict) or not (op.get("text") or "").strip():
            continue
        try:
            tasks.add(
                root, text=op["text"], priority=int(op.get("priority") or 0),
                depends_on=[str(d) for d in (op.get("depends_on") or [])],
                acceptance=op.get("acceptance") if isinstance(op.get("acceptance"), dict) else None,
                touches=[str(t) for t in (op.get("touches") or [])],
                source={"kind": "chat", "ref": context_key},
            )
            added += 1
        except ValueError:
            pass
    for op in patch.get("update", []) or []:
        if not isinstance(op, dict) or not op.get("id"):
            continue
        try:
            if tasks.update(
                root, str(op["id"]), status=op.get("status"), text=op.get("text"),
                blocked_reason=op.get("blocked_reason"),
            ):
                updated += 1
        except ValueError:
            pass
    for op in patch.get("note", []) or []:
        if not isinstance(op, dict) or not op.get("id") or not (op.get("text") or "").strip():
            continue
        if tasks.update(root, str(op["id"]), evidence={"kind": "note", "ref": op["text"]}):
            noted += 1

    bits = []
    if added:
        bits.append(f"added {added} task{'s' if added != 1 else ''}")
    if updated:
        bits.append(f"updated {updated}")
    if noted:
        bits.append(f"noted {noted}")
    notes = ["Tasks: " + ", ".join(bits) + "."] if bits else []
    return cleaned, notes
