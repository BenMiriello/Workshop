"""Build agents — Claude Code coding sessions surfaced as Agents.

Two kinds of Agents per ADR-008:
  - Data agents (Letta-backed)     → existing services/letta_*
  - Build agents (Claude Code)     → THIS module

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

REGISTRY = Path(os.environ.get(
    "WORKSHOP_BUILD_AGENTS_JSON",
    str(Path.home() / "workshop" / "build_agents.json"),
))
SCRATCH_ROOT = Path(os.environ.get(
    "WORKSHOP_BUILD_AGENTS_SCRATCH",
    str(Path.home() / "workshop" / "build-agents"),
))
PROJECTS_ROOT = Path(os.environ.get(
    "WORKSHOP_PROJECTS_ROOT_NEW",
    str(Path.home() / "workshop" / "projects"),
))
PROJECTS_REGISTRY = PROJECTS_ROOT.parent / "projects.json"

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

BuildAgentState = Literal["idle", "running", "parked", "retired"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if not REGISTRY.exists():
        return {"build_agents": {}}
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {"build_agents": {}}


def _save(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2) + "\n")


def _project_dir(slug: str) -> Path | None:
    if not PROJECTS_REGISTRY.exists():
        return None
    try:
        reg = json.loads(PROJECTS_REGISTRY.read_text())
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
        from services import worktrees as _worktrees
        try:
            cwd = _worktrees.branch_dir(project_slug, branch_slug)
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
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cwd = SCRATCH_ROOT / slug
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
    }
    data.setdefault("build_agents", {})[slug] = record
    _save(data)
    return {"slug": slug, **record}


def create_blank() -> dict:
    """Mint a fresh, freestanding "Blank project N" build agent.

    This is the "build with chat" entry point: instead of a text-only planning
    chat, the operator gets a REAL builder — full Claude Code toolset scoped to a
    scaffolded working tree — that can research the web, write files, and stand up
    a starter environment from the first turn. It shows up in the Agents list like
    any other builder; the operator renames it (see `rename`) once the project
    takes shape. N increments from the highest existing blank-project-N so a new
    one never collides with a retired one still in the registry.
    """
    data = _load()
    existing = data.get("build_agents") or {}
    n = 1
    pat = re.compile(r"^blank-project-(\d+)$")
    for s in existing:
        m = pat.match(s)
        if m:
            n = max(n, int(m.group(1)) + 1)
    slug = f"blank-project-{n}"
    return create_agent(
        slug=slug,
        name=f"Blank project {n}",
        purpose="Freestanding exploration — rename once it takes shape.",
    )


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


def slugs() -> list[str]:
    return list((_load().get("build_agents") or {}).keys())


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
