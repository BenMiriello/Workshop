"""Structured task store (canonical Task model, orchestration-and-autonomy O1).

The authoritative store is a JSON file per working tree at ``<root>/.tasks.json``;
``TODO.md`` is a regenerated EXPORT plus a healed inbox. One write path: nothing
edits TODO.md as the way to change state; a stray hand edit is absorbed by
``reconcile`` on the next read, never destroyed.

Canonical Task (version 2)::

    {
      "id": str,                       # uuid4 hex, stable across reorders
      "text": str,
      "status": "todo"|"doing"|"in_review"|"blocked"|"done"|"cancelled",
      "assignee": None|"agent:<slug>"|"operator",   # the delegate (human owns)
      "parent": str | None,
      "priority": int,                 # higher first; default 0
      "depends_on": [task_id],         # gates readiness
      "acceptance": {"kind": "reviewer"|"command"|"operator", "spec": str|None}|None,
      "max_iterations": int | None,    # per-task override of the ralph cap
      "blocked_reason": str | None,
      "awaiting": {"question": str, "since": iso}|None,  # operator input this task waits on
      "attempts": int,                 # worker runs spent on this task (cap enforcement)
      "job_ids": [str],                # executions that worked this task
      "evidence": [{"kind": str, "ref": str, "ts": iso}],
      "source": {"kind": "operator"|"chat"|"agent"|"autogen", "ref": str|None},
      "provenance": str | None,        # stable dedupe key
      "touches": [str],                # path globs this task writes (fan-out safety)
      "discovered_from": str | None,   # task id that spawned this follow-up
      "effort": str | None,            # "s"|"m"|"l" hint
      "created": iso, "updated": iso
    }

Backward compatibility: records carry an ``owner`` field mirroring ``assignee``
so existing readers (frontend task panel, per-agent endpoints) keep working while
callers migrate. Legacy ``owner`` on read maps to ``assignee`` (an agent slug
becomes ``agent:<slug>``; ``operator`` stays).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from services import todos

# in_review = worker finished, operator is the gate; cancelled = dropped.
STATUSES = {"todo", "doing", "in_review", "blocked", "done", "cancelled"}
TERMINAL = {"done", "cancelled"}
DEFAULT_OWNER = "operator"  # canonical assignee for the human

STORE_NAME = ".tasks.json"
EXPORT_NAME = "TODO.md"
LOCK_NAME = ".tasks.json.lock"
STORE_VERSION = 2

# Roots this store must NOT manage. Empty by default since the 2026-07-15 root
# migration (Stage 3 / O7): the workshop root's legacy prose TODO.md was archived
# to docs/archive/root-todo-legacy-2026-07-15.md and the structured store became
# canonical at root, so nothing is protected anymore. Still env-overridable
# (`WORKSHOP_TASKS_PROTECTED_ROOTS`, colon-separated) as an escape hatch.
_PROTECTED_ROOTS = {
    Path(p).resolve()
    for p in os.environ.get("WORKSHOP_TASKS_PROTECTED_ROOTS", "").split(":")
    if p.strip()
}


def _is_protected(root: Path) -> bool:
    try:
        return root.resolve() in _PROTECTED_ROOTS
    except OSError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _store_path(root: Path) -> Path:
    return root / STORE_NAME


def _export_path(root: Path) -> Path:
    return root / EXPORT_NAME


def _lock_path(root: Path) -> Path:
    return root / LOCK_NAME


# ---- IO --------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@contextmanager
def _locked(root: Path):
    """flock around a load-modify-persist so the backend API and the shell CLI
    (project_tasks.py) can genuinely interleave writes to the same store during
    a sweep without clobbering each other. One lock file, one module, every
    writer goes through here."""
    root.mkdir(parents=True, exist_ok=True)
    lp = _lock_path(root)
    f = open(lp, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _read_payload(root: Path) -> tuple[list[dict] | None, str | None]:
    """Return (raw task list, export_sha) from .tasks.json, or (None, None)."""
    p = _store_path(root)
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    tasks = data.get("tasks")
    export_sha = data.get("export_sha")
    return (tasks if isinstance(tasks, list) else None,
            export_sha if isinstance(export_sha, str) else None)


def _read_store(root: Path) -> list[dict] | None:
    tasks, _ = _read_payload(root)
    return tasks


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- Normalization ---------------------------------------------------------

def _assignee_from_legacy(task: dict) -> str | None:
    """Map a record's assignee/owner to the canonical assignee. Legacy `owner`:
    'operator' stays; a bare agent slug becomes 'agent:<slug>'; blank -> operator."""
    a = task.get("assignee")
    if a is not None:
        return a or None
    owner = task.get("owner")
    if owner is None or owner == "" or owner == DEFAULT_OWNER:
        return DEFAULT_OWNER
    return owner if str(owner).startswith("agent:") else f"agent:{owner}"


def _normalize(task: dict) -> dict:
    """Coerce a stored record into a well-formed canonical Task (defensive
    against hand edits and forward-migrating v1 records losslessly)."""
    status = task.get("status")
    if status not in STATUSES:
        status = "done" if task.get("done") else "todo"
    now = _now()
    assignee = _assignee_from_legacy(task)
    acceptance = task.get("acceptance")
    if not isinstance(acceptance, dict):
        acceptance = None
    src = task.get("source")
    if not isinstance(src, dict):
        src = {"kind": "operator", "ref": None}
    rec = {
        "id": str(task.get("id") or _new_id()),
        "text": str(task.get("text") or "").strip(),
        "status": status,
        "assignee": assignee,
        "parent": task.get("parent") or None,
        "priority": _as_int(task.get("priority"), 0),
        "depends_on": [str(d) for d in (task.get("depends_on") or []) if d],
        "acceptance": acceptance,
        "max_iterations": _as_int_or_none(task.get("max_iterations")),
        "blocked_reason": task.get("blocked_reason") or None,
        "awaiting": task.get("awaiting") if isinstance(task.get("awaiting"), dict) else None,
        "attempts": _as_int(task.get("attempts"), 0),
        "job_ids": [str(j) for j in (task.get("job_ids") or []) if j],
        "evidence": [e for e in (task.get("evidence") or []) if isinstance(e, dict)],
        "source": {"kind": str(src.get("kind") or "operator"), "ref": src.get("ref")},
        "provenance": task.get("provenance") or None,
        "touches": [str(t) for t in (task.get("touches") or []) if t],
        "discovered_from": task.get("discovered_from") or None,
        "effort": task.get("effort") or None,
        "created": str(task.get("created") or now),
        "updated": str(task.get("updated") or now),
    }
    # `owner` mirror keeps legacy readers (frontend panel, per-agent endpoints)
    # working until they migrate to `assignee`.
    rec["owner"] = rec["assignee"] or DEFAULT_OWNER
    return rec


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _migrate_from_todo(root: Path) -> list[dict]:
    """Parse an existing TODO.md into operator-assigned tasks."""
    export = _export_path(root)
    text = export.read_text() if export.exists() else ""
    now = _now()
    tasks: list[dict] = []
    for item in todos.parse(text):
        tasks.append(_normalize({
            "id": _new_id(),
            "text": item.get("text", ""),
            "status": "done" if item.get("done") else "todo",
            "assignee": DEFAULT_OWNER,
            "created": now, "updated": now,
            "source": {"kind": "operator", "ref": "todo-migrate"},
        }))
    return tasks


def _load(root: Path) -> list[dict]:
    """Load the authoritative task list, migrating from TODO.md on first access.
    Does NOT reconcile (callers that want the healed view call reconcile first)."""
    raw = _read_store(root)
    if raw is None:
        # Protected root with no store yet: return empty WITHOUT migrating (which
        # would _persist and wipe its prose TODO.md). Inert until O7.
        if _is_protected(root):
            return []
        tasks = _migrate_from_todo(root)
        _persist(root, tasks)
        return tasks
    return [_normalize(t) for t in raw if isinstance(t, dict)]


def _persist(root: Path, tasks: list[dict]) -> None:
    """Atomically write .tasks.json (version 2, with export_sha) AND regenerate
    the TODO.md export. export_sha lets reconcile detect out-of-band edits."""
    if _is_protected(root):
        # Never regenerate a protected root's TODO.md (would wipe its prose
        # sections). Inert until O7 migrates it properly.
        return
    rendered = render_markdown(tasks)
    payload = {"version": STORE_VERSION, "tasks": tasks, "export_sha": _sha(rendered)}
    _atomic_write(_store_path(root), json.dumps(payload, indent=2) + "\n")
    _atomic_write(_export_path(root), rendered)


# ---- Reconcile (heal out-of-band TODO.md edits) ----------------------------

_BOX_TO_STATUS = {" ": "todo", "x": "done", "X": "done", "~": "doing",
                  "!": "blocked", "?": "in_review", "-": "cancelled"}
_LINE_RE = re.compile(r"^(\s*)[-*]\s*\[([ xX~!?\-])\]\s*(.*?)\s*$")


def _norm_text(s: str) -> str:
    """Normalize a task's FIRST line for matching (strip trailing status tag,
    collapse whitespace, lowercase). Multi-line bodies match on line one."""
    first = (s or "").split("\n", 1)[0]
    first = re.sub(r"\s*\((?:doing|blocked|in_review|in review|cancelled)\)\s*$", "", first, flags=re.I)
    return re.sub(r"\s+", " ", first).strip().lower()


def _parse_export_blocks(text: str) -> list[tuple[str, str]]:
    """Return [(status, full_text)] for every checklist item in a TODO.md body.
    A task's full_text is its checklist line PLUS any following continuation
    lines indented deeper than the marker (the detail block), dedented and joined
    with newlines, so multi-line task bodies round-trip losslessly. Matches all
    six status boxes plus a bare `[ ]`. Nested checklist items are their own
    tasks (handled by the store's parent field, not folded in here)."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = _LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        marker_indent = len(m.group(1))
        box, body = m.group(2), m.group(3)
        i += 1
        cont: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                # blank line: include only if a deeper continuation follows
                if (i + 1 < len(lines) and lines[i + 1].strip()
                        and not _LINE_RE.match(lines[i + 1])
                        and len(lines[i + 1]) - len(lines[i + 1].lstrip()) > marker_indent):
                    cont.append("")
                    i += 1
                    continue
                break
            if _LINE_RE.match(nxt):
                break  # next checklist item / nested task
            indent = len(nxt) - len(nxt.lstrip())
            if indent <= marker_indent:
                break  # dedented back out: not part of this item
            cont.append(nxt.strip())
            i += 1
        full = body.strip()
        if cont:
            full = full + "\n" + "\n".join(cont)
        if full.strip():
            out.append((_BOX_TO_STATUS.get(box, "todo"), full))
    return out


def reconcile(root: Path) -> bool:
    """Heal an out-of-band TODO.md edit back into the store. Cheap: short-circuits
    on an unchanged hash. Checkbox flips apply to the matching store task
    (normalized-text match); unmatched new checklist lines import as new tasks;
    lines missing from the file are ignored (the store wins on deletion). Returns
    True if anything changed. The store remains the single authority."""
    if _is_protected(root):
        return False  # never heal/persist a protected root's mixed TODO.md
    export = _export_path(root)
    if not export.exists():
        return False
    with _locked(root):
        tasks_raw, export_sha = _read_payload(root)
        if tasks_raw is None:
            return False
        current = export.read_text()
        if export_sha is not None and _sha(render_markdown([_normalize(t) for t in tasks_raw])) == export_sha \
                and _sha(current) == export_sha:
            return False
        if _sha(current) == export_sha:
            return False  # export matches what we last wrote; nothing to heal
        tasks = [_normalize(t) for t in tasks_raw]
        by_text: dict[str, dict] = {}
        for t in tasks:
            by_text.setdefault(_norm_text(t["text"]), t)
        changed = False
        now = _now()
        for status, text in _parse_export_blocks(current):
            key = _norm_text(text)
            match = by_text.get(key)
            if match is not None:
                # Heal a checkbox flip; also absorb an edited/extended body so a
                # hand-edited detail block is not lost on the next re-render.
                item_changed = False
                if match["status"] != status:
                    match["status"] = status
                    item_changed = True
                if text.strip() != (match["text"] or "").strip():
                    match["text"] = text.strip()
                    item_changed = True
                if item_changed:
                    match["updated"] = now
                    changed = True
            else:
                # Unmatched new line -> import (non-destructive).
                new = _normalize({
                    "id": _new_id(), "text": text, "status": status,
                    "assignee": DEFAULT_OWNER, "created": now, "updated": now,
                    "source": {"kind": "operator", "ref": "todo-reconcile"},
                })
                tasks.append(new)
                by_text.setdefault(key, new)
                changed = True
        # Always re-persist so export_sha tracks the current file even when the
        # only diff was cosmetic (whitespace), preventing a reconcile loop.
        _persist(root, tasks)
        return changed


# ---- Markdown export -------------------------------------------------------

_STATUS_BOX = {"todo": " ", "doing": "~", "in_review": "?", "blocked": "!",
               "done": "x", "cancelled": "-"}
_STATUS_LABEL = {"doing": "doing", "blocked": "blocked", "in_review": "in_review",
                 "cancelled": "cancelled"}


def render_markdown(tasks: list[dict], *, header: str = "# TODO\n\n") -> str:
    """Render the tasks as a GitHub-style checklist grouped by assignee (operator
    first). .tasks.json is the source of truth; this is the export."""
    groups = group_by_owner(tasks)
    owners = sorted(groups.keys(), key=lambda o: (o != DEFAULT_OWNER, o))
    lines: list[str] = []
    for owner in owners:
        if not groups[owner]:
            continue
        roots, children = _tree_for(groups[owner])
        lines.append(f"## {owner}")
        lines.append("")
        for t in roots:
            _emit(t, children, lines, depth=0)
        lines.append("")
    body = "\n".join(lines).rstrip("\n")
    if body:
        body += "\n"
    return f"{header}{body}"


def _emit(task: dict, children: dict[str, list[dict]], out: list[str], *, depth: int) -> None:
    indent = "  " * depth
    box = _STATUS_BOX.get(task["status"], " ")
    tag = _STATUS_LABEL.get(task["status"])
    suffix = f"  ({tag})" if tag else ""
    # Multi-line task bodies (detail lines under the checklist item) round-trip
    # losslessly: first line carries the box + tag, continuation lines align
    # under the text so the parser re-gathers them.
    lines = (task["text"] or "").split("\n")
    first = lines[0] if lines else ""
    out.append(f"{indent}- [{box}] {first}{suffix}")
    cont_indent = indent + "      "  # under "- [x] "
    for extra in lines[1:]:
        out.append(f"{cont_indent}{extra}" if extra.strip() else "")
    for child in children.get(task["id"], []):
        _emit(child, children, out, depth=depth + 1)


# ---- Tree / grouping helpers ----------------------------------------------

def group_by_owner(tasks: list[dict]) -> dict[str, list[dict]]:
    """Group tasks by assignee (the `owner` mirror). Name kept for compat."""
    groups: dict[str, list[dict]] = {}
    for t in tasks:
        groups.setdefault(t.get("owner") or t.get("assignee") or DEFAULT_OWNER, []).append(t)
    return groups


def _tree_for(tasks: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    ids = {t["id"] for t in tasks}
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for t in tasks:
        parent = t.get("parent")
        if parent and parent in ids:
            children.setdefault(parent, []).append(t)
        else:
            roots.append(t)
    return roots, children


def _match_assignee(task: dict, owner: str | None) -> bool:
    """Assignee-scoped match, tolerant of the legacy owner form (bare slug)."""
    if owner is None:
        return True
    want = owner if (owner == DEFAULT_OWNER or owner.startswith("agent:")) else f"agent:{owner}"
    return (task.get("assignee") or DEFAULT_OWNER) == want or task.get("owner") == owner


def tree(root: Path, *, include_done: bool = True, owner: str | None = None) -> dict:
    reconcile(root)
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if _match_assignee(t, owner)]
    if not include_done:
        tasks = [t for t in tasks if t["status"] not in TERMINAL]
    groups = group_by_owner(tasks)
    owners = sorted(groups.keys(), key=lambda o: (o != DEFAULT_OWNER, o))

    def nest(task: dict, children: dict[str, list[dict]]) -> dict:
        node = dict(task)
        node["children"] = [nest(c, children) for c in children.get(task["id"], [])]
        return node

    out_owners = []
    for o in owners:
        roots, children = _tree_for(groups[o])
        out_owners.append({"owner": o, "tasks": [nest(r, children) for r in roots]})
    return {"owners": out_owners}


# ---- Queries ---------------------------------------------------------------

def list_tasks(root: Path, *, include_done: bool = True, owner: str | None = None) -> list[dict]:
    reconcile(root)
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if _match_assignee(t, owner)]
    if not include_done:
        tasks = [t for t in tasks if t["status"] not in TERMINAL]
    return tasks


def get_task(root: Path, task_id: str, *, owner: str | None = None) -> dict | None:
    for t in _load(root):
        if t["id"] == task_id and _match_assignee(t, owner):
            return t
    return None


def next_ready(root: Path, *, assignee: str | None = None) -> dict | None:
    """The next task ready to run: status==todo AND every depends_on is
    done|cancelled, ordered by priority desc then created asc. Optionally scoped
    to an assignee (an agent claims only its own or unassigned tasks). Replaces
    'first todo'. Reconciles first so a task added by any route is seen."""
    reconcile(root)
    tasks = _load(root)
    by_id = {t["id"]: t for t in tasks}

    def ready(t: dict) -> bool:
        if t["status"] != "todo":
            return False
        for dep in t.get("depends_on") or []:
            d = by_id.get(dep)
            if d is not None and d["status"] not in TERMINAL:
                return False
        return True

    def in_scope(t: dict) -> bool:
        if assignee is None:
            return True
        a = t.get("assignee") or DEFAULT_OWNER
        return a == assignee or a is None or t.get("assignee") is None

    cands = [t for t in tasks if ready(t) and in_scope(t)]
    cands.sort(key=lambda t: (-_as_int(t.get("priority"), 0), t.get("created") or ""))
    return cands[0] if cands else None


# ---- Mutations -------------------------------------------------------------

def add(
    root: Path,
    *,
    text: str,
    owner: str = DEFAULT_OWNER,
    assignee: str | None = None,
    parent: str | None = None,
    status: str = "todo",
    priority: int = 0,
    depends_on: list[str] | None = None,
    acceptance: dict | None = None,
    max_iterations: int | None = None,
    touches: list[str] | None = None,
    discovered_from: str | None = None,
    effort: str | None = None,
    source: dict | None = None,
    provenance: str | None = None,
) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    if status not in STATUSES:
        status = "todo"
    with _locked(root):
        tasks = _load(root)
        if parent is not None and not any(t["id"] == parent for t in tasks):
            raise ValueError(f"parent task not found: {parent}")
        if provenance:  # idempotent re-add (work_generator dedupe key)
            for t in tasks:
                if t.get("provenance") == provenance:
                    return t
        now = _now()
        task = _normalize({
            "id": _new_id(), "text": text, "status": status,
            "assignee": assignee if assignee is not None else owner,
            "parent": parent or None, "priority": priority,
            "depends_on": depends_on or [], "acceptance": acceptance,
            "max_iterations": max_iterations, "touches": touches or [],
            "discovered_from": discovered_from, "effort": effort,
            "source": source or {"kind": "operator", "ref": None},
            "provenance": provenance, "created": now, "updated": now,
        })
        tasks.append(task)
        _persist(root, tasks)
        return task


def update(
    root: Path,
    task_id: str,
    *,
    status: str | None = None,
    text: str | None = None,
    owner: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    depends_on: list[str] | None = None,
    blocked_reason: str | None = None,
    acceptance: dict | None = None,
    max_iterations: int | None = None,
    job_id: str | None = None,
    evidence: dict | None = None,
    awaiting: dict | None = None,
    bump_attempt: bool = False,
) -> dict | None:
    """Update a task. ``owner`` (or ``assignee``) scopes the match so an agent
    can't mutate another delegate's task. Additive fields are patched when
    provided. Returns the updated task or None."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if text is not None:
        text = text.strip()
        if not text:
            raise ValueError("text required")
    scope = assignee if assignee is not None else owner
    with _locked(root):
        tasks = _load(root)
        found = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, scope):
                found = t
                break
        if found is None:
            return None
        if status is not None:
            found["status"] = status
            # Leaving a waiting state clears the operator-input payload; nothing
            # is waiting on a task that is now todo/doing/done/cancelled.
            if status not in ("blocked", "in_review"):
                found["awaiting"] = None
        if text is not None:
            found["text"] = text
        if assignee is not None:
            found["assignee"] = assignee
            found["owner"] = assignee
        if priority is not None:
            found["priority"] = int(priority)
        if depends_on is not None:
            found["depends_on"] = [str(d) for d in depends_on if d]
        if blocked_reason is not None:
            found["blocked_reason"] = blocked_reason or None
        if acceptance is not None:
            found["acceptance"] = acceptance or None
        if max_iterations is not None:
            found["max_iterations"] = _as_int_or_none(max_iterations)
        if awaiting is not None:
            found["awaiting"] = ({"question": str(awaiting.get("question") or "").strip(),
                                  "since": awaiting.get("since") or _now()}
                                 if awaiting.get("question") else None)
        if bump_attempt:
            found["attempts"] = _as_int(found.get("attempts"), 0) + 1
        if job_id:
            if job_id not in found["job_ids"]:
                found["job_ids"].append(job_id)
        if evidence:
            ev = dict(evidence)
            ev.setdefault("ts", _now())
            found["evidence"].append(ev)
        found["updated"] = _now()
        _persist(root, tasks)
        return found


def claim(root: Path, task_id: str, job_id: str, *, assignee: str | None = None) -> dict | None:
    """Atomically claim a task: todo -> doing under flock, recording the job id.
    Returns the claimed task, or None if it was already claimed / not todo (a
    concurrent sweep + CLI can race for the same task; the loser gets None)."""
    with _locked(root):
        tasks = _load(root)
        found = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, assignee):
                found = t
                break
        if found is None or found["status"] != "todo":
            return None
        found["status"] = "doing"
        found["attempts"] = _as_int(found.get("attempts"), 0) + 1
        if job_id and job_id not in found["job_ids"]:
            found["job_ids"].append(job_id)
        found["updated"] = _now()
        _persist(root, tasks)
        return found


def remove(root: Path, task_id: str, *, owner: str | None = None) -> bool:
    with _locked(root):
        tasks = _load(root)
        target = None
        for t in tasks:
            if t["id"] == task_id and _match_assignee(t, owner):
                target = t
                break
        if target is None:
            return False
        new_parent = target.get("parent")
        keep = [t for t in tasks if t["id"] != task_id]
        for t in keep:
            if t.get("parent") == task_id:
                t["parent"] = new_parent
        _persist(root, keep)
        return True


# ---- Legacy TODO.md bridge -------------------------------------------------

def export_markdown(root: Path) -> str:
    reconcile(root)
    tasks = _load(root)
    _persist(root, tasks)
    return _export_path(root).read_text()


def import_markdown(root: Path, content: str) -> None:
    """Legacy PUT /todo: write the body then reconcile it into the store (the
    store stays authoritative). Non-destructive now, unlike the old re-key."""
    with _locked(root):
        _atomic_write(
            _export_path(root),
            content if (content.endswith("\n") or not content) else content + "\n",
        )
        # Force reconcile to see this as an out-of-band edit by clearing the sha.
        tasks_raw, _ = _read_payload(root)
        if tasks_raw is None:
            tasks = _migrate_from_todo(root)
            _persist(root, tasks)
    reconcile(root)
