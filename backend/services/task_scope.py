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
import os
import re
from pathlib import Path

from services import tasks

# ```task-patch\n{...}\n``` — same fence style as the budget plan-patch.
_PATCH_BLOCK = re.compile(r"```task-patch\s*\n(.*?)\n```", re.DOTALL)

# ```learning-note\n{...}\n``` the chat-side durable-preference capture (Phase
# 4). Applied in the SAME finalize path as the task-patch block: parsed, recorded
# into the learnings store at the named (or this chat's) scope, and stripped from
# the stored reply. Deterministic capture in ADDITION to compaction-time
# distillation, by design, so an explicit operator correction lands immediately.
_LEARNING_BLOCK = re.compile(r"```learning-note\s*\n(.*?)\n```", re.DOTALL)

# The workshop-root task store (the top-level `.tasks.json`). This is the scope
# for tickets parked at the root, distinct from the `workshop` PROJECT under
# projects/. Overridable for tests / alternate checkouts; the default is this
# checkout's repo root (backend/services -> parents[2]).
WORKSHOP_ROOT = Path(os.environ.get(
    "WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))


def root_for_context(context_key: str | None) -> Path | None:
    """The task-store directory for a chat's context binding, or None if the
    binding has no task scope (general / app chats)."""
    if not context_key:
        return None
    if context_key in ("workshop", "general"):
        # The general chat is the orchestrator (Phase 4): it owns the workshop
        # root queue, so a task-patch / task-proposal default resolves to the same
        # root store the `workshop` binding uses. This is what turns "a chat that
        # talks about work" into "a chat that owns a queue".
        return WORKSHOP_ROOT
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


def root_for_job(job: dict | None) -> Path | None:
    """The task-store directory a Job worked, from its binding. A project sweep
    carries project_slug; an agent sweep carries agent_id ('agent:<slug>'). Lets
    the Jobs API act on an awaiting job's bound tasks without the caller knowing
    project-vs-agent routing."""
    if not job:
        return None
    slug = job.get("project_slug")
    if slug:
        return root_for_context(f"project:{slug}")
    agent_id = job.get("agent_id") or ""
    if agent_id.startswith("agent:"):
        return root_for_context(agent_id)
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

    from services import task_dedup

    added = updated = noted = 0
    merged = related = 0
    for op in patch.get("add", []) or []:
        if not isinstance(op, dict) or not (op.get("text") or "").strip():
            continue
        try:
            # Cross-chat dedup (Stage 7 / 2e): fold a duplicate into the existing
            # scope task, link a near-match as related, or create fresh. This is
            # the path that previously passed no provenance at all.
            res = task_dedup.dedup_add(
                root, text=op["text"], priority=int(op.get("priority") or 0),
                depends_on=[str(d) for d in (op.get("depends_on") or [])],
                acceptance=op.get("acceptance") if isinstance(op.get("acceptance"), dict) else None,
                touches=[str(t) for t in (op.get("touches") or [])],
                source={"kind": "chat", "ref": context_key},
            )
            decision = res.get("decision")
            if decision in ("exact", "merge"):
                merged += 1
            elif decision == "related":
                related += 1
                added += 1
            else:
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
                priority=op.get("priority"),
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
    if related:
        bits.append(f"{related} linked as related to a near-match")
    if merged:
        bits.append(f"merged {merged} duplicate{'s' if merged != 1 else ''} into existing task(s)")
    if updated:
        bits.append(f"updated {updated}")
    if noted:
        bits.append(f"noted {noted}")
    notes = ["Tasks: " + ", ".join(bits) + "."] if bits else []
    return cleaned, notes


def apply_learning_note(context_key: str | None, reply: str) -> tuple[str, list[str]]:
    """Find a ``learning-note`` block, record its note as durable operator feedback
    in the learnings store, strip the block, and return (cleaned_reply, notes).
    No-op (the reply unchanged, empty notes) when there is no block. The note is
    recorded at the block's explicit `scope` or, absent one, this chat's own
    knowledge scope (general -> workshop). Mirrors apply_task_patch: server-side
    application, fence stripped, a human-readable outcome for meta.notes."""
    m = _LEARNING_BLOCK.search(reply)
    if not m:
        return reply, []
    cleaned = _LEARNING_BLOCK.sub("", reply).strip()
    try:
        block = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return cleaned, ["(could not parse a learning note, nothing was captured)"]
    if not isinstance(block, dict):
        return cleaned, []
    note = (block.get("note") or "").strip()
    if not note:
        return cleaned, []

    # Default the scope to this chat's own knowledge scope (which the learnings
    # store shares its scope keys with); an explicit block scope wins.
    scope = (block.get("scope") or "").strip()
    if not scope:
        from services import knowledge
        try:
            scope = knowledge.scope_for_context(context_key or "workshop")
        except Exception:
            scope = "workshop"

    from services import learnings
    try:
        learnings.record_feedback(note, source="chat", scope=scope)
    except ValueError:
        return cleaned, [f"(learning note skipped: {scope!r} is not a valid scope)"]
    return cleaned, [f"Captured a preference in learnings ({scope})."]
