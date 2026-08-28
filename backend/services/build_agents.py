"""Build agents — Claude Code coding sessions surfaced as Agents.

Agents are typed by `role` (maker/reviewer/triager/keeper/broker). This
module is the maker side: a Claude Code coding session. ADR-008 originally
paired it with Letta-backed data agents; Letta was decommissioned 2026-08-16
and `services/letta_*` no longer exists.

A Build agent is a persistent (in concept) Claude Code session scoped to a
cwd. The cwd is either a Project directory (link by project_slug) or a
freestanding scratch dir under ~/workshop/build-agents/<slug>/ when no
project is named.

Per-turn vs persistent: today the chat path subprocesses `claude -p` per
turn with cwd pinned. The build agent's identity (name, slug, cwd) is what's
persistent; the model conversation lives in chat_store. A future Phase B
upgrade swaps in a real `--session-id` + `--resume` lifecycle without
changing the agent identity.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from wsplatform import paths

# Per-instance operator state — lives in .runtime/ (gitignored), never in the
# tracked tree. See routes/projects.py for the matching rationale.
# Functions rather than constants so tests and a relocated root are honoured at
# CALL time. NOT per-owner: a build agent is owned by its registry record's
# `owner_id`, not by which directory it sits in, because `agent:<slug>` is a
# global scope key — see paths.projects_root for the full reasoning.
def _registry() -> Path:
    return paths.env_path("WORKSHOP_BUILD_AGENTS_JSON", "build_agents.json")


def _scratch_root() -> Path:
    return paths.env_path("WORKSHOP_BUILD_AGENTS_SCRATCH", "build-agents", base="root")


def _projects_registry() -> Path:
    return paths.projects_registry()


def _scope_owner():
    # Local import: scope_owner reads this module's own registry.
    from services import scope_owner
    return scope_owner

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

BuildAgentState = Literal["idle", "running", "parked", "retired"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if not _registry().exists():
        return {"build_agents": {}}
    try:
        return json.loads(_registry().read_text())
    except Exception:
        return {"build_agents": {}}


def _save(data: dict) -> None:
    _registry().parent.mkdir(parents=True, exist_ok=True)
    _registry().write_text(json.dumps(data, indent=2) + "\n")


def _project_dir(slug: str) -> Path | None:
    if not _projects_registry().exists():
        return None
    try:
        reg = json.loads(_projects_registry().read_text())
    except Exception:
        return None
    p = (reg.get("projects") or {}).get(slug)
    return Path(p["dir"]) if p and p.get("dir") else None


def list_agents() -> list[dict]:
    data = _load()
    items = []
    for slug, raw in (data.get("build_agents") or {}).items():
        items.append({"slug": slug, **raw})
    return sorted(items, key=lambda a: a.get("last_active") or a.get("created", ""), reverse=True)


def get_agent(slug: str) -> dict:
    data = _load()
    a = (data.get("build_agents") or {}).get(slug)
    if not a:
        raise KeyError(slug)
    return {"slug": slug, **a}


def _branch_dir(project_slug: str, branch_slug: str):
    """This project's tree for one branch, asked of the branches layer.

    ONE FUNCTION-LOCAL IMPORT INSTEAD OF ONE PER CALLER: `services/worktrees.py`
    pulls in the app runner and its yaml dependency, which the system python3
    some callers run under does not have, so it stays deferred -- but it is
    deferred in one place now that both `create_agent` and `set_branch` resolve
    a cwd the same way.
    """
    from services import worktrees
    return worktrees.branch_dir(project_slug, branch_slug)


def create_agent(
    *,
    slug: str,
    name: str,
    purpose: str = "",
    project_slug: str | None = None,
    branch_slug: str | None = None,
) -> dict:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("slug must be kebab-case alphanumeric, start with a letter, max 48 chars")
    data = _load()
    if slug in (data.get("build_agents") or {}):
        raise ValueError(f"build agent already exists: {slug}")

    # cwd resolution: a branch pin wins over the project main worktree, which
    # wins over the freestanding scratch dir.
    if project_slug and branch_slug:
        try:
            cwd = _branch_dir(project_slug, branch_slug)
        except ValueError as e:
            raise ValueError(str(e))
        if not cwd.exists():
            raise ValueError(f"branch worktree not found: {cwd}")
    elif project_slug:
        pd = _project_dir(project_slug)
        if not pd:
            raise ValueError(f"project not found: {project_slug}")
        cwd = pd
    else:
        _scratch_root().mkdir(parents=True, exist_ok=True)
        cwd = _scratch_root() / slug
        cwd.mkdir(parents=True, exist_ok=False)
        # Drop the same agent rules + brief scaffold a Project would get.
        (cwd / "PROJECT.md").write_text(
            f"# {name}\n\n> {purpose}\n\n## Goal\n\n(fill in)\n\n## Constraints\n\n- (fill in)\n"
        )
        (cwd / "TODO.md").write_text("# TODO\n\n- [ ] First task\n")
        (cwd / "AGENTS.md").write_text(
            "# Build agent rules\n\n"
            "- Read `PROJECT.md` first every turn.\n"
            "- Update `TODO.md` as work progresses.\n"
            "- Edit only within this directory.\n"
        )
        (cwd / ".gitignore").write_text("INTERVENTIONS.md\n.DS_Store\nnode_modules/\ndist/\n")
        # Auto-git so the operator can review diffs, revert, and commit
        # snapshots from the UI Diff tab. Neutral committer matches Projects.
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Workshop Projects",
            "GIT_AUTHOR_EMAIL": "projects@workshop.local",
            "GIT_COMMITTER_NAME": "Workshop Projects",
            "GIT_COMMITTER_EMAIL": "projects@workshop.local",
        }
        try:
            subprocess.run(["git", "init", "-q"], cwd=cwd, env=env, check=True, timeout=10)
            subprocess.run(["git", "add", "."], cwd=cwd, env=env, check=True, timeout=10)
            subprocess.run(
                ["git", "commit", "-q", "-m", f"Scaffold build agent {slug}"],
                cwd=cwd, env=env, check=True, timeout=10,
            )
        except Exception:
            # If git fails, the agent is still usable; diff tab just shows
            # "no git repo". Don't block creation on it.
            pass

    now = _now()
    record = {
        "name": name,
        "purpose": purpose,
        "kind": "build",
        "cwd": str(cwd),
        "project_slug": project_slug,
        "branch_slug": branch_slug,
        "state": "idle",
        "created": now,
        "last_active": now,
        "claude_session": None,
        # Whose build agent this is — see services/scope_owner.py.
        **_scope_owner().stamp(),
    }
    data.setdefault("build_agents", {})[slug] = record
    _save(data)
    return {"slug": slug, **record}


def rename(slug: str, *, name: str | None = None, purpose: str | None = None) -> dict:
    """Update a build agent's display name and/or purpose. The slug (and thus its
    cwd + session) is immutable — only the human-facing labels change, which is
    what "rename once decided" means for a blank project."""
    data = _load()
    rec = (data.get("build_agents") or {}).get(slug)
    if not rec:
        raise KeyError(slug)
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("name cannot be empty")
        rec["name"] = name[:64]
    if purpose is not None:
        rec["purpose"] = purpose.strip()[:280]
    rec["last_active"] = _now()
    _save(data)
    return {"slug": slug, **rec}


def set_branch(slug: str, branch_slug: str | None) -> dict:
    """Re-pin this agent to a branch of its project, or back to the main tree.

    THE CONTROL THAT WAS ON SCREEN AND INERT. `AgentBuild.tsx` has had a Branch
    picker beside the status pills since it was built, populated from
    `GET /worktrees/{project}` -- and nothing read its value, so choosing a
    branch changed nothing about where the agent's turns ran. `create_agent`
    accepted a `branch_slug` and resolved a cwd from it, which made the one
    working pin reachable only from the API.

    THE CWD IS RE-DERIVED, NEVER PATCHED INDEPENDENTLY, and it is derived by the
    same call `create_agent` uses (`worktrees.branch_dir`), so a pinned agent's
    directory cannot be one thing at create time and another at re-pin time.

    Takes effect from the NEXT turn: a turn in flight had its cwd fixed when its
    process started. The Claude Code session is deliberately NOT reset -- the
    conversation is about the work, not about the directory, and dropping it
    would make changing branch cost the agent its memory of what it was doing.
    """
    data = _load()
    record = (data.get("build_agents") or {}).get(slug)
    if not record:
        raise KeyError(slug)
    project_slug = record.get("project_slug")
    if not project_slug:
        raise ValueError("this agent has no project, so it has no branches")
    branch_slug = (branch_slug or "").strip() or None
    if branch_slug:
        cwd = _branch_dir(project_slug, branch_slug)
        if not cwd.exists():
            raise ValueError(f"branch worktree not found: {cwd}")
    else:
        pd = _project_dir(project_slug)
        if not pd:
            raise ValueError(f"project not found: {project_slug}")
        cwd = pd
    record["branch_slug"] = branch_slug
    record["cwd"] = str(cwd)
    record["last_active"] = _now()
    _save(data)
    return {"slug": slug, **record}


def touch(slug: str) -> None:
    data = _load()
    if slug in (data.get("build_agents") or {}):
        data["build_agents"][slug]["last_active"] = _now()
        _save(data)


def retire(slug: str) -> None:
    data = _load()
    if slug in (data.get("build_agents") or {}):
        data["build_agents"][slug]["state"] = "retired"
        _save(data)


def mark_idle(slug: str) -> None:
    """Return an agent to idle when its turn finishes. Roster liveness is now
    computed on read from live turn journals (agents_registry), but the persisted
    `state` used to ratchet to "running" forever because nothing reset it; keep it
    honest so any direct reader of build_agents.json is not misled."""
    data = _load()
    record = (data.get("build_agents") or {}).get(slug)
    if record and record.get("state") == "running":
        record["state"] = "idle"
        _save(data)


def slugs(include_retired: bool = False) -> list[str]:
    """Registered agent slugs. Retired agents are excluded by default: `retire()`
    was supposed to take an agent out of circulation, but every caller listed all
    slugs regardless, so a retired agent went on cluttering the chat picker
    forever and retiring achieved nothing visible."""
    agents = _load().get("build_agents") or {}
    if include_retired:
        return list(agents.keys())
    return [s for s, a in agents.items() if a.get("state") != "retired"]


def delete_agent(slug: str) -> bool:
    """Drop an agent from the registry entirely. Distinct from `retire()`, which
    keeps the record: this is for agents that should leave no trace (test and
    probe stubs). Returns False if it was not registered.

    Does NOT touch the agent's working tree or its claude session on disk; those
    are owned by the filesystem and pruned separately."""
    data = _load()
    agents = data.get("build_agents") or {}
    if slug not in agents:
        return False
    del agents[slug]
    _save(data)
    return True


def ensure_session_id(slug: str) -> tuple[str, bool]:
    """Return (session_id, is_new) for the agent's persistent claude session.
    If the agent has no session_id yet, mint a fresh uuid and persist. The
    is_new flag tells the caller whether this turn should use --session-id
    (first run) or --resume (continuing).
    """
    data = _load()
    record = (data.get("build_agents") or {}).get(slug)
    if not record:
        raise KeyError(slug)
    existing = record.get("claude_session")
    if existing:
        return existing, False
    sid = str(uuid.uuid4())
    record["claude_session"] = sid
    record["state"] = "running"
    record["last_active"] = _now()
    _save(data)
    return sid, True


def reset_session(slug: str) -> None:
    """Clear the agent's session_id so the next turn starts a fresh session.
    Used by the UI 'reset session' button when the conversation gets confused.
    Note: this does NOT delete Claude Code's checkpoint on disk; it just
    detaches this agent record from the old session and forces a new one
    next time."""
    data = _load()
    record = (data.get("build_agents") or {}).get(slug)
    if record:
        record["claude_session"] = None
        record["state"] = "idle"
        _save(data)
