"""Ambient "ongoing work across the workspace" digest (Stage 7 / design 2e).

A compact, LOW-CHURN summary of the open tasks in a chat's scope, injected into
chat context so a chat stops losing track of work happening in other chats.
Scope-correct (single-operator, so scope is correctness not a permission wall):

  - a project / build / agent chat -> ITS OWN task scope only.
  - a general chat -> a workspace-wide roll-up (the workshop inbox + every project).

Two things this block must NOT do (review F6, research 2026-07-15 #1/#4):
  - bust the prompt cache. It sits in the cache-stable prefix, so its BYTES must
    change as rarely as possible. We therefore select the top tasks by priority
    but RENDER them ordered by creation (append-stable), and we drop the per-task
    status tag (status flips todo/doing/todo every sweep tick and would reshuffle
    the block constantly). Adding/closing/reprioritizing a task still changes it;
    a status flip no longer does.
  - inject distractors. A long list of topically-related tasks measurably
    degrades the model (Chroma context-rot), so the block is capped small with a
    "+N more" remainder count rather than dumping everything.

Live JOB status is deliberately NOT here: it is the most volatile state in the
system (flips every tick) and belongs in the Jobs UI, not in every chat's cached
prefix. The digest answers "what work exists", not "what is running right now".

Best-effort and self-contained: any failure yields "" rather than breaking the
chat path, mirroring chat_context.learnings_context.
"""
from __future__ import annotations

import json
from pathlib import Path

# Small cap keeps the block cache-friendly and distractor-light (research: ~5-7).
CAP_TASKS = 6
_TEXT_CLIP = 90


def _clip(s: str, n: int = _TEXT_CLIP) -> str:
    s = (s or "").split("\n", 1)[0].strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


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


def _open_task_lines(root: Path, cap: int, *, label: str | None = None) -> tuple[list[str], int]:
    """Return (lines, total_open) for a store. SELECT the top `cap` open tasks by
    priority (relevance), but RENDER them ordered by creation so the byte prefix
    is append-stable across sweeps (a status change does not reshuffle it). No
    status tag, for the same cache-stability reason. `total_open` lets the caller
    add a "+N more" remainder count."""
    try:
        from services import tasks
        items = [t for t in tasks.list_tasks(root, include_done=False)
                 if t.get("status") not in tasks.TERMINAL]
    except Exception:
        return [], 0
    total = len(items)
    # Selection: most important first (stable tie-break on creation, not updated).
    def _created(t: dict) -> str:
        return t.get("created") or t.get("id") or ""
    top = sorted(items, key=lambda t: (-int(t.get("priority") or 0), _created(t)))[:cap]
    # Render: creation order, so appends extend the tail and leave earlier bytes
    # stable for the prompt cache.
    top.sort(key=_created)
    prefix = f"[{label}] " if label else ""
    lines = [f"- {prefix}{_clip(t.get('text') or '')}" for t in top]
    return lines, total


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
) -> str:
    """The ambient digest string for a chat's context binding, or "" when there
    is nothing to show. A project/build/agent chat gets its own scope; a general
    chat gets the workspace-wide roll-up; an app chat gets "" (it must not carry
    unrelated project tasks). Capped small with a "+N more" remainder so the block
    stays cache-friendly and distractor-light."""
    scoped = _scoped_root(context_key)

    if scoped is not None:
        # Own-scope digest for a builder/project/agent chat.
        lines, total = _open_task_lines(scoped, cap_tasks)
        if not lines:
            return ""
        body = "Open tasks in this scope:\n" + "\n".join(lines)
        if total > len(lines):
            body += f"\n- (+{total - len(lines)} more; ask to list)"
        return body

    # scoped is None: either a general chat (workspace roll-up) or an APP chat. An
    # app chat (budget/finance/commute/... any non-general bare key) must not carry
    # unrelated project task text every turn (token cost with no benefit), so
    # return "" for it. Only a genuine general chat (no key / general / new-project)
    # gets the workspace-wide roll-up. (review L4)
    ck = (context_key or "").strip()
    if ck and ck not in ("general", "new-project"):
        return ""

    # Workspace-wide roll-up for a general chat. Aggregate across the workshop
    # inbox and every project, still capped, with a remainder count.
    #
    # The inbox is asked for by scope, not by path. It used to read WORKSHOP_ROOT
    # directly, which since ADR 0014 is the `development` store — so with
    # developer mode on the same tickets were listed twice, once as "workshop"
    # and again as "development" via the self registry row below.
    from services import task_scope
    agg: list[str] = []
    total_all = 0
    ws_root = task_scope.root_for_context("workshop")
    wl, wt = _open_task_lines(ws_root, cap_tasks, label="workshop") if ws_root else ([], 0)
    agg += wl
    total_all += wt
    for slug, root in _project_roots():
        remaining = max(0, cap_tasks - len(agg))
        pl, pt = _open_task_lines(root, remaining if remaining else 1, label=slug)
        total_all += pt
        if remaining:
            agg += pl[:remaining]
    if not agg:
        return ""
    body = "Open tasks across the workspace:\n" + "\n".join(agg[:cap_tasks])
    if total_all > len(agg[:cap_tasks]):
        body += f"\n- (+{total_all - len(agg[:cap_tasks])} more; ask to list)"
    return body


def digest_for_provider(provider) -> str:
    """Convenience wrapper used by chat_context: resolve the provider's context
    key and build its ambient digest. Best-effort."""
    try:
        return digest_for_context(getattr(provider, "key", None))
    except Exception:
        return ""
