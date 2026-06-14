"""Structured task store (ADR / docs/workshop-v1 02 §3, plan item 14).

Replaces raw TODO.md editing. The authoritative store is a JSON file per
working tree at ``<root>/.tasks.json``; TODO.md is regenerated from the store
on every write so it stays a git-friendly export AND so chat_context (which
reads TODO.md as live turn context) always sees current state.

A Task::

    {
      "id":      str,          # uuid4 hex, stable across reorders
      "text":    str,
      "status":  "todo"|"doing"|"done"|"blocked",
      "owner":   str,          # a build-agent slug or "operator"
      "parent":  str | None,   # parent task id for the tree
      "created": iso8601,
      "updated": iso8601,
    }

Ownership: a project's roll-up holds tasks for every owner (operator + each
agent). A build-agent's view is the subset of tasks owned by that agent slug,
stored in the agent's linked project (if any) else the agent's own cwd.

Migration: on first access, if ``.tasks.json`` is absent but ``TODO.md``
exists, the markdown checklist is parsed via :mod:`services.todos` into tasks
owned by ``"operator"`` (done flag → status ``done``/``todo``).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import todos

STATUSES = {"todo", "doing", "done", "blocked"}
DEFAULT_OWNER = "operator"

STORE_NAME = ".tasks.json"
EXPORT_NAME = "TODO.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _store_path(root: Path) -> Path:
    return root / STORE_NAME


def _export_path(root: Path) -> Path:
    return root / EXPORT_NAME


# ---- IO --------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write via a temp file in the same dir + os.replace for atomicity."""
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


def _read_store(root: Path) -> list[dict] | None:
    """Return the raw task list from .tasks.json, or None if absent/corrupt."""
    p = _store_path(root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return tasks if isinstance(tasks, list) else None


def _migrate_from_todo(root: Path) -> list[dict]:
    """Parse an existing TODO.md into operator-owned tasks. Returns the list
    (possibly empty if TODO.md is missing or has no checklist items)."""
    export = _export_path(root)
    text = export.read_text() if export.exists() else ""
    now = _now()
    tasks: list[dict] = []
    for item in todos.parse(text):
        tasks.append({
            "id": _new_id(),
            "text": item.get("text", ""),
            "status": "done" if item.get("done") else "todo",
            "owner": DEFAULT_OWNER,
            "parent": None,
            "created": now,
            "updated": now,
        })
    return tasks


def _normalize(task: dict) -> dict:
    """Coerce a stored record into a well-formed Task (defensive against hand
    edits to the JSON file)."""
    status = task.get("status")
    if status not in STATUSES:
        status = "done" if task.get("done") else "todo"
    now = _now()
    return {
        "id": str(task.get("id") or _new_id()),
        "text": str(task.get("text") or "").strip(),
        "status": status,
        "owner": str(task.get("owner") or DEFAULT_OWNER),
        "parent": task.get("parent") or None,
        "created": str(task.get("created") or now),
        "updated": str(task.get("updated") or now),
    }


def _load(root: Path) -> list[dict]:
    """Load the authoritative task list, migrating from TODO.md on first access.

    If the store is absent, migrate (and persist, regenerating TODO.md so the
    two are immediately consistent). If present, normalize in memory.
    """
    raw = _read_store(root)
    if raw is None:
        tasks = _migrate_from_todo(root)
        _persist(root, tasks)
        return tasks
    return [_normalize(t) for t in raw if isinstance(t, dict)]


def _persist(root: Path, tasks: list[dict]) -> None:
    """Atomically write .tasks.json AND regenerate the TODO.md export."""
    payload = {"version": 1, "tasks": tasks}
    _atomic_write(_store_path(root), json.dumps(payload, indent=2) + "\n")
    _atomic_write(_export_path(root), render_markdown(tasks))


# ---- Markdown export -------------------------------------------------------

_STATUS_BOX = {"todo": " ", "doing": "~", "done": "x", "blocked": "!"}
_STATUS_LABEL = {"doing": "doing", "blocked": "blocked"}


def render_markdown(tasks: list[dict], *, header: str = "# TODO\n\n") -> str:
    """Render the structured tasks as a GitHub-style checklist, grouped by
    owner with a heading per owner (operator first). Nested children are
    indented under their parent. Status maps to the checkbox + a tag for the
    non-binary states so the markdown stays lossy-but-readable; .tasks.json is
    the source of truth, this is the export.
    """
    groups = group_by_owner(tasks)
    # operator first, then the rest alphabetically.
    owners = sorted(groups.keys(), key=lambda o: (o != DEFAULT_OWNER, o))
    lines: list[str] = []
    for owner in owners:
        roots, children = _tree_for(groups[owner])
        if not groups[owner]:
            continue
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
    out.append(f"{indent}- [{box}] {task['text']}{suffix}")
    for child in children.get(task["id"], []):
        _emit(child, children, out, depth=depth + 1)


# ---- Tree / grouping helpers ----------------------------------------------

def group_by_owner(tasks: list[dict]) -> dict[str, list[dict]]:
    """Group tasks by owner. Returns {owner: [task, ...]} preserving order."""
    groups: dict[str, list[dict]] = {}
    for t in tasks:
        groups.setdefault(t.get("owner", DEFAULT_OWNER), []).append(t)
    return groups


def _tree_for(tasks: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Return (root tasks, {parent_id: [child, ...]}) for a flat list. A task
    whose parent is missing from this list is treated as a root so nothing is
    orphaned out of the rendering."""
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


def tree(root: Path, *, include_done: bool = True, owner: str | None = None) -> dict:
    """Build a grouped/nested view of the store for the frontend.

    Returns::

        {
          "owners": [
            {"owner": str, "tasks": [<task with "children">, ...]},
            ...
          ]
        }

    Operator group first, others alphabetical. When ``owner`` is given, only
    that owner's group is returned. ``include_done=False`` drops done tasks
    (and any of their subtrees that become empty are simply omitted).
    """
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if t["owner"] == owner]
    if not include_done:
        tasks = [t for t in tasks if t["status"] != "done"]

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


# ---- CRUD ------------------------------------------------------------------

def list_tasks(root: Path, *, include_done: bool = True, owner: str | None = None) -> list[dict]:
    """Flat list of tasks with parent/owner so the frontend can build the tree.

    ``owner`` filters to a single owner (used by the build-agent endpoints).
    ``include_done=False`` drops done tasks.
    """
    tasks = _load(root)
    if owner is not None:
        tasks = [t for t in tasks if t["owner"] == owner]
    if not include_done:
        tasks = [t for t in tasks if t["status"] != "done"]
    return tasks


def get_task(root: Path, task_id: str, *, owner: str | None = None) -> dict | None:
    for t in _load(root):
        if t["id"] == task_id and (owner is None or t["owner"] == owner):
            return t
    return None


def add(
    root: Path,
    *,
    text: str,
    owner: str = DEFAULT_OWNER,
    parent: str | None = None,
    status: str = "todo",
) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    if status not in STATUSES:
        status = "todo"
    tasks = _load(root)
    if parent is not None and not any(t["id"] == parent for t in tasks):
        raise ValueError(f"parent task not found: {parent}")
    now = _now()
    task = {
        "id": _new_id(),
        "text": text,
        "status": status,
        "owner": owner or DEFAULT_OWNER,
        "parent": parent or None,
        "created": now,
        "updated": now,
    }
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
) -> dict | None:
    """Update status and/or text of a task. ``owner`` (when given) scopes the
    match so an agent cannot mutate another owner's task. Returns the updated
    task or None if no matching task."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if text is not None:
        text = text.strip()
        if not text:
            raise ValueError("text required")
    tasks = _load(root)
    found = None
    for t in tasks:
        if t["id"] == task_id and (owner is None or t["owner"] == owner):
            found = t
            break
    if found is None:
        return None
    if status is not None:
        found["status"] = status
    if text is not None:
        found["text"] = text
    found["updated"] = _now()
    _persist(root, tasks)
    return found


def remove(root: Path, task_id: str, *, owner: str | None = None) -> bool:
    """Remove a task and re-parent its direct children to the removed task's
    parent (so subtrees are not silently dropped). ``owner`` scopes the match.
    Returns True if a task was removed."""
    tasks = _load(root)
    target = None
    for t in tasks:
        if t["id"] == task_id and (owner is None or t["owner"] == owner):
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
    """Return the current rendered TODO.md (regenerating from the store first
    so it is guaranteed current). Used by the legacy GET /todo endpoint so
    there is ONE source of truth."""
    tasks = _load(root)
    _persist(root, tasks)
    return _export_path(root).read_text()


def import_markdown(root: Path, content: str) -> None:
    """Accept a raw TODO.md body (legacy PUT /todo): write it, then re-derive
    the structured store from it so the store stays authoritative and the two
    representations converge. Operator-owned, since a hand-edited markdown
    blob has no per-owner attribution.

    Note: this re-keys task ids (markdown carries no stable ids), which is
    acceptable for the legacy textarea path — the structured endpoints are the
    primary surface and preserve ids.
    """
    _atomic_write(_export_path(root), content if content.endswith("\n") or not content else content + "\n")
    tasks = _migrate_from_todo(root)
    _persist(root, tasks)
