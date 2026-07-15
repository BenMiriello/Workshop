"""Ambient "ongoing work across the workspace" digest (Stage 7 / design 2e).

A compact, cache-stable summary of open tasks + in-flight/recent jobs, injected
into chat context so a chat stops losing track of work happening in other chats.
Scope-correct (single-operator, so scope is correctness not a permission wall):

  - a project / build / agent chat -> ITS OWN task scope only.
  - a general chat -> a workspace-wide roll-up (workshop root + every project),
    plus jobs currently running or recently finished.

Kept short (entries are capped) so it never blows the context window. Best-effort
and self-contained: any failure yields "" rather than breaking the chat path,
mirroring chat_context.learnings_context.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Caps keep the block small + cache-stable.
CAP_TASKS = 8
CAP_JOBS = 5
_TEXT_CLIP = 90


def _clip(s: str, n: int = _TEXT_CLIP) -> str:
    s = (s or "").split("\n", 1)[0].strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _workshop_root() -> Path:
    return Path(os.environ.get("WORKSHOP_ROOT", str(Path.home() / "workshop")))


def _project_roots() -> list[tuple[str, Path]]:
    """(label, task-root) for every registered project. Best-effort."""
    out: list[tuple[str, Path]] = []
    try:
        from routes.projects import REGISTRY
        if not REGISTRY.exists():
            return out
        data = json.loads(REGISTRY.read_text())
    except Exception:
        return out
    from services import task_scope
    for slug in sorted((data.get("projects") or {}).keys()):
        try:
            root = task_scope.root_for_context(f"project:{slug}")
        except Exception:
            root = None
        if root is not None:
            out.append((slug, root))
    return out


def _open_task_lines(root: Path, cap: int, *, label: str | None = None) -> list[str]:
    """Up to `cap` open tasks from a store, highest priority + most recent first."""
    try:
        from services import tasks
        items = [t for t in tasks.list_tasks(root, include_done=False)
                 if t.get("status") not in tasks.TERMINAL]
    except Exception:
        return []
    items.sort(key=lambda t: (int(t.get("priority") or 0), t.get("updated") or ""), reverse=True)
    lines: list[str] = []
    for t in items[:cap]:
        prefix = f"[{label}] " if label else ""
        status = t.get("status") or "todo"
        tag = "" if status == "todo" else f" ({status})"
        lines.append(f"- {prefix}{_clip(t.get('text') or '')}{tag}")
    return lines


def _job_lines(cap: int) -> list[str]:
    """Running jobs first, then recently finished, deduped. Workspace-wide."""
    try:
        from services import jobs
    except Exception:
        return []
    seen: set[str] = set()
    picked: list[dict] = []
    try:
        for r in jobs.read(status="running", limit=cap):
            if r.get("id") not in seen:
                seen.add(r.get("id"))
                picked.append(r)
    except Exception:
        pass
    try:
        for r in jobs.read(limit=cap * 2):
            if len(picked) >= cap:
                break
            if r.get("id") not in seen:
                seen.add(r.get("id"))
                picked.append(r)
    except Exception:
        pass
    lines: list[str] = []
    for r in picked[:cap]:
        title = _clip(r.get("title") or r.get("summary") or r.get("kind") or r.get("id") or "job")
        status = r.get("status") or "?"
        lines.append(f"- {title} ({status})")
    return lines


def _scoped_root(context_key: str | None) -> Path | None:
    """The single task root for a project/build/agent chat, or None for a general
    or app chat (which gets the workspace roll-up)."""
    if not context_key:
        return None
    try:
        from services import task_scope
        return task_scope.root_for_context(context_key)
    except Exception:
        return None


def digest_for_context(
    context_key: str | None,
    *,
    cap_tasks: int = CAP_TASKS,
    cap_jobs: int = CAP_JOBS,
) -> str:
    """The ambient digest string for a chat's context binding, or "" when there
    is nothing to show. A project/build/agent chat gets its own scope; anything
    else (general / app) gets the workspace-wide roll-up."""
    scoped = _scoped_root(context_key)
    parts: list[str] = []

    if scoped is not None:
        # Own-scope digest for a builder/project/agent chat.
        task_lines = _open_task_lines(scoped, cap_tasks)
        if task_lines:
            parts.append("Open tasks in this scope:\n" + "\n".join(task_lines))
    else:
        # Workspace-wide roll-up for a general chat.
        agg: list[str] = []
        agg += _open_task_lines(_workshop_root(), cap_tasks, label="workshop")
        remaining = max(0, cap_tasks - len(agg))
        if remaining:
            for slug, root in _project_roots():
                if remaining <= 0:
                    break
                pl = _open_task_lines(root, remaining, label=slug)
                agg += pl
                remaining -= len(pl)
        if agg:
            parts.append("Open tasks across the workspace:\n" + "\n".join(agg[:cap_tasks]))

    job_lines = _job_lines(cap_jobs)
    if job_lines:
        parts.append("Jobs (running / recent):\n" + "\n".join(job_lines))

    if not parts:
        return ""
    return "\n\n".join(parts)


def digest_for_provider(provider) -> str:
    """Convenience wrapper used by chat_context: resolve the provider's context
    key and build its ambient digest. Best-effort."""
    try:
        return digest_for_context(getattr(provider, "key", None))
    except Exception:
        return ""
