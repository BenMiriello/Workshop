"""Jobs API — one feed for every bounded execution.

`GET /jobs`      list/filter by agent_id, project_slug, status, limit
`GET /jobs/{id}` single Job detail

Backed by services.jobs (append-only `.runtime/jobs.jsonl`). This is the new
top-level activity surface; `/agents/runs` (routes/agent_runs.py) is a thin
backward-compatible alias over the same data until Phase 5.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import logging

from services import jobs, tasks, task_scope, agents_registry, usage_ledger

log = logging.getLogger("workshop.routes.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])

# --- Ticket-shaping: enrich raw Job records into readable tickets -----------
# A stored Job is machine-shaped: agent_id is a raw Letta UUID, trigger is
# jargon ("loop"), evidence is a freeform dump, and failures hide inside it with
# no exit_code. We compute display fields at read time so every job reads like a
# ticket (a name, a title, an operator-language trigger, a real error) without
# migrating the append-only log.

_TRIGGER_LABELS = {
    "loop": "Autonomous",
    "cron": "Scheduled",
    "event": "Event",
    "manual": "Run now",
}


def _name_map() -> dict[str, str]:
    """agent-id/slug/letta-id -> human name, built once per request."""
    m: dict[str, str] = {}
    try:
        for a in agents_registry.list_agents(include_retired=True):
            name = a.get("name") or a.get("slug")
            if not name:
                continue
            for key in (a.get("slug"), a.get("id"), a.get("letta_agent_id")):
                if key:
                    m[key] = name
    except Exception:
        pass
    return m


def _display_agent(agent_id: str | None, names: dict[str, str]) -> str | None:
    """Resolve a job's agent_id to a human name; degrade gracefully so the
    operator never sees a bare UUID."""
    if not agent_id:
        return None
    if agent_id in names:
        return names[agent_id]
    if agent_id.startswith("agent:"):
        slug = agent_id.split(":", 1)[1]
        return names.get(slug, slug)
    if agent_id.startswith("agent-"):
        # Raw Letta UUID with no registry definition (legacy heartbeat agent).
        return f"Letta {agent_id[6:14]}"
    return agent_id


def _friendly_error(err: str | None) -> str:
    if not err or not err.strip():
        return "Failed (no detail recorded)."
    s = err.strip()
    if s.startswith("ollama_failed:"):
        detail = s[len("ollama_failed:"):].strip()
        base = "Local model server (Ollama) was unreachable."
        return f"{base} {detail}" if detail else base
    return s


def _clip(s: str | None, n: int = 100) -> str:
    s = (s or "").split("\n", 1)[0].strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _scope_root_for_job(job: dict):
    """The task-store root this job worked in, from its project_slug or agent_id,
    so we can join the Task it ran. Best-effort -> None (legacy/unresolvable).

    `workshop` is accepted alongside `development` because every sweep against
    the platform repo before ADR 0014 stamped its job with that slug. The
    tickets those jobs worked did not move — the queue was renamed, not
    relocated — so resolving the old slug to `development` is what keeps a
    year of run history joined to its tasks."""
    slug = job.get("project_slug")
    agent = job.get("agent_id") or ""
    try:
        if slug in ("workshop", "development"):
            return task_scope.root_for_context("development")
        if slug:
            return task_scope.root_for_context(f"project:{slug}")
        if agent.startswith("agent:"):
            return task_scope.root_for_context(agent)
    except Exception:
        return None
    return None


def _task_context(job: dict, cache: dict) -> dict:
    """Join the Task a child Job worked: its TEXT (what the job was actually for),
    its current status, and the chat that spawned it (source.ref -> thread title).
    Retroactive over all history, no writer change / log migration needed. The
    per-request `cache` loads each scope's task list once. Best-effort."""
    tid = job.get("task_id")
    if not tid:
        return {}
    root = _scope_root_for_job(job)
    if root is None:
        return {}
    key = str(root)
    if key not in cache:
        try:
            cache[key] = {t["id"]: t for t in tasks.list_tasks(root, include_done=True)}
        except Exception:
            cache[key] = {}
    t = cache[key].get(tid)
    if not t:
        return {}
    out: dict = {}
    text = (t.get("text") or "").strip()
    if text:
        out["task_text"] = text
    if t.get("status"):
        out["task_status"] = t.get("status")
    src = t.get("source") or {}
    if src.get("kind") == "chat" and src.get("ref"):
        out.update(_chat_display(src["ref"]))
    return out


def _chat_display(chat_id: str) -> dict:
    """A chat id plus what a link to it needs: its title, and the context key the
    route is built from. Never raises; a chat that has been deleted still leaves
    the id, which is better than dropping the provenance entirely."""
    out: dict = {"chat_id": chat_id}
    try:
        from services import chat_store
        th = chat_store.get_thread(chat_id)
        if th:
            out["chat_title"] = th.get("title") or "Chat"
            if th.get("context_key"):
                out["chat_context_key"] = th.get("context_key")
    except Exception:
        pass
    return out


def _title(job: dict, agent_disp: str | None, tctx: dict) -> str:
    # A child Job's title IS what it was for: the task text, clipped to a
    # name-length line (the full text lives on the task/detail view).
    if tctx.get("task_text"):
        return _clip(tctx["task_text"], 72)
    trig = job.get("trigger")
    label = _TRIGGER_LABELS.get(trig, (trig or "Run").capitalize())
    binding = job.get("project_slug") or agent_disp or "workspace"
    # A coordinator sweep (no task_id): name what it swept + the outcome.
    if not job.get("task_id"):
        done, failed = job.get("tasks_done"), job.get("tasks_failed")
        counts = []
        if done:
            counts.append(f"{done} done")
        if failed:
            counts.append(f"{failed} failed")
        tail = f" ({', '.join(counts)})" if counts else ""
        return f"Sweep · {binding}{tail}"
    return f"{label} · {binding}"


def _enrich(job: dict, names: dict[str, str], cache: dict | None = None) -> dict:
    j = dict(job)
    cache = cache if cache is not None else {}
    trig = job.get("trigger")
    j["trigger_label"] = _TRIGGER_LABELS.get(trig, (trig or "Run").capitalize())
    agent_disp = _display_agent(job.get("agent_id"), names)
    if agent_disp:
        j["agent_name"] = agent_disp
    tctx = _task_context(job, cache)
    # The TICKET's originating chat must not overwrite the RUN's own. They are
    # different facts: the ticket's source says who FILED it, `job.chat_id` says
    # who LAUNCHED this run, and when they differ the launcher is the one the
    # run page's "open the chat" action means. The task-derived value remains the
    # fallback for every run recorded before that field was ever written (which,
    # measured 2026-08-12, is all 2753 of them).
    own_chat = job.get("chat_id")
    if own_chat and tctx.get("chat_id") and tctx["chat_id"] != own_chat:
        tctx.update(_chat_display(own_chat))
    j.update(tctx)  # task_text, task_status, chat_id, chat_title
    ev = job.get("evidence")
    receipt = ev if isinstance(ev, dict) and ev.get("kind") == "receipt" else None
    if receipt and (rs := (receipt.get("summary") or "").strip()):
        # The worker's own account of what it did doubles as the job's summary.
        j["summary"] = rs[:240]
    if job.get("status") == "failed":
        if isinstance(ev, str):
            j["error"] = _friendly_error(ev)
        elif receipt:
            j["error"] = (receipt.get("summary") or "").strip()[:240] or "Failed (see receipt)."
        else:
            j["error"] = _friendly_error(None)
    elif isinstance(ev, str) and ev.strip():
        # A short freeform evidence string doubles as the job's one-line summary.
        j["summary"] = ev.strip()[:240]
    j["title"] = _title(job, agent_disp, tctx)
    # A compact "where / who / from" subtitle for the feed row.
    bits: list[str] = []
    if job.get("project_slug"):
        bits.append(job["project_slug"])
    elif agent_disp:
        bits.append(agent_disp)
    if j.get("chat_title"):
        bits.append(f"from chat: {j['chat_title']}")
    if bits:
        j["context_label"] = " · ".join(bits)
    return j

# Operator responses to an awaiting_input job, expressed as a task transition.
# Acting changes the scope store's mtime, so the dispatcher's skip-unchanged
# guard releases the scope on the next tick and the sweep resumes on its own.
_ACTIONS = {
    "approve": ("done", "in_review"),    # accept the worker's completion
    "reject": ("todo", "in_review"),     # send an in_review task back for rework
    "unblock": ("todo", "blocked"),      # blocker resolved: return to the queue
    "cancel": ("cancelled", None),       # drop the task (any state)
}


@router.get("")
async def list_jobs(
    agent_id: str | None = None,
    project_slug: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    parent_id: str | None = None,
    limit: int = 50,
) -> dict:
    records = jobs.read(
        agent_id=agent_id,
        project_slug=project_slug,
        status=status,
        task_id=task_id,
        parent_id=parent_id,
        limit=limit,
    )
    names = _name_map()
    cache: dict = {}
    enriched = [_enrich(r, names, cache) for r in records]
    return {"jobs": enriched, "count": len(enriched)}


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    rec = jobs.get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    out = _enrich(rec, _name_map())
    # What this run cost. The ledger has carried `job_id` on every row since it
    # was written and nothing ever read them back that way, so answering it
    # meant grepping usage.jsonl. It is the number a watched run is judged on
    # (autonomy-turn-on-2026-08-12 phase 2), and a number nobody can see does
    # not get used.
    #
    # Includes the CHILDREN, because a coordinator sweep spends almost nothing
    # itself and its per-task children spend all of it. A total that excluded
    # them would read as "this sweep was free".
    try:
        kids = [c.get("id") for c in jobs.read(parent_id=job_id, limit=200)]
        totals = usage_ledger.usage_for_jobs([job_id, *kids])
        out["tokens"] = sum(totals.values()) or None
        out["tokens_own"] = totals.get(job_id) or None
    except Exception as e:  # noqa: BLE001 - a cost line is not worth a 500
        log.warning("usage lookup failed for job %s: %s", job_id, e)
    return out


class JobTaskAction(BaseModel):
    action: str  # approve | reject | unblock | cancel
    note: str | None = None


@router.post("/{job_id}/tasks/{task_id}/action")
async def act_on_job_task(job_id: str, task_id: str, body: JobTaskAction) -> dict:
    """Resolve one awaiting_input job's bound task: approve / reject / unblock /
    cancel. Applies the status transition on the job's scope store (project or
    agent), records the operator note as evidence, and clears the awaiting
    payload. The dispatcher re-runs the scope next tick because the store moved."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    if body.action not in _ACTIONS:
        raise HTTPException(400, f"unknown action: {body.action}")
    root = task_scope.root_for_job(job)
    if root is None:
        raise HTTPException(400, "this job has no resolvable task scope")
    new_status, require_from = _ACTIONS[body.action]
    task = tasks.get_task(root, task_id)
    if task is None:
        raise HTTPException(404, f"task not found in scope: {task_id}")
    # Drift-tolerant transitions: a job.awaiting_input can outlive its task, since
    # the task's status can be changed elsewhere (a /review verb, a direct
    # tasks.update, another chat) without knowing which jobs are waiting on it. If
    # the task landed in a TERMINAL state (done or cancelled) before the operator
    # got here, the intended verb is already satisfied (or moot). Treat it as an
    # idempotent no-op and fall through to the job-clear pass below, so Home's
    # attention strip drops the ghost card instead of 409-ing forever.
    current_status = task.get("status")
    terminal = {"done", "cancelled"}
    updated = task
    if require_from and current_status != require_from:
        if current_status in terminal:
            # No transition needed; the operator's intent is already realized.
            pass
        else:
            raise HTTPException(
                409, f"task is {current_status}, not {require_from}; cannot {body.action}")
    else:
        # Status change auto-clears the awaiting payload (update() drops it on
        # exit from a waiting state). The operator note rides along as evidence.
        updated = tasks.update(
            root, task_id, status=new_status,
            evidence={"kind": "operator", "ref": body.note} if body.note else None)

    # If the operator has now answered every task this job was waiting on, the
    # job is no longer awaiting input: flip it to done so it drops off Home's
    # attention strip. (The next dispatcher tick re-runs the scope on its own,
    # since the store moved; a fresh sweep opens its own Job.)
    job_cleared = False
    if job.get("status") == "awaiting_input":
        ev = job.get("evidence")
        waited_ids = [e.get("id") for e in ev if isinstance(e, dict) and e.get("id")] if isinstance(ev, list) else []
        still_waiting = [
            tid for tid in waited_ids
            if (t := tasks.get_task(root, tid)) and t.get("status") in ("blocked", "in_review")
        ]
        if not still_waiting:
            jobs.finish(job_id, status="done")
            job_cleared = True
    return {"ok": True, "action": body.action, "task": updated, "job_cleared": job_cleared}


@router.post("/{job_id}/dismiss")
async def dismiss_job(job_id: str) -> dict:
    """The always-available escape hatch: clear a job off the attention feed
    regardless of its binding or task state. Needed because a malformed
    awaiting_input job (unbound sweep, phantom or question-less task) has no
    resolvable scope, so the per-task actions cannot act on it, and the operator
    must never be stuck with a permanent, unresolvable warning. Finishes the job
    `cancelled`; it does NOT touch any task store (there may be none to touch)."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    jobs.finish(job_id, status="cancelled")
    return {"ok": True, "dismissed": True}
