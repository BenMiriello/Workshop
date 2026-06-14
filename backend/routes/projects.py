"""Projects hub backend.

Manages the projects.json registry, scaffolds new projects from templates,
and serves project metadata + working-brain (PROJECT.md) + TODO + interventions
to the UI. The per-project Claude Code persistent session lives behind a
separate service (services/project_sessions.py); this router only exposes
project-level metadata + file IO.

Replaces the old /projects/ git-list, which moved to routes/base_apps.py.
See ADR-008 and docs/projects-hub.md (in clawd-house/docs).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# Project tree lives inside ~/workshop so the design-system gate + watchdog
# already apply. Configurable for tests.
PROJECTS_ROOT = Path(os.environ.get(
    "WORKSHOP_PROJECTS_ROOT_NEW",
    str(Path.home() / "workshop" / "projects"),
))
REGISTRY = PROJECTS_ROOT.parent / "projects.json"
TEMPLATES_ROOT = PROJECTS_ROOT / "_templates"

# Slug discipline: kebab-case, alphanumeric + dashes, max 48 chars.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

State = Literal["sketch", "wip", "mounted", "shared", "published", "parked", "retired"]
Sensitivity = Literal["private", "shareable", "public"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_registry() -> dict:
    if not REGISTRY.exists():
        return {"projects": {}}
    return json.loads(REGISTRY.read_text())


def _save_registry(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2) + "\n")


def _ensure_project(slug: str) -> dict:
    data = _load_registry()
    p = data.get("projects", {}).get(slug)
    if not p:
        raise HTTPException(404, f"project not found: {slug}")
    return p


def _read_text(path: Path, default: str = "") -> str:
    return path.read_text() if path.exists() else default


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---- Models -----------------------------------------------------------------

class Surfaces(BaseModel):
    integrated: str | None = None
    standalone: dict | None = None
    public: str | None = None


class Project(BaseModel):
    slug: str
    title: str
    purpose: str
    dir: str
    sensitivity: Sensitivity = "shareable"
    state: State = "sketch"
    surfaces: Surfaces = Surfaces()
    needs: list[str] = []
    provides: list[str] = []
    created: str
    last_touched: str
    claude_session: str | None = None


class CreateProjectIn(BaseModel):
    slug: str
    title: str
    purpose: str = ""
    template: str = "blank"
    sensitivity: Sensitivity = "shareable"
    brief: str = ""


class ImportProjectIn(BaseModel):
    url: str
    slug: str | None = None
    title: str | None = None
    sensitivity: Sensitivity = "shareable"


class FileWriteIn(BaseModel):
    content: str


# ---- Templates --------------------------------------------------------------

@router.get("/templates")
async def list_templates() -> list[dict]:
    """List available templates. 'blank' is always available (programmatic
    scaffold); the rest are detected from `_templates/<name>/` directories."""
    items = [{"slug": "blank", "label": "Blank", "hint": "Empty scaffold with the standard files"}]
    if TEMPLATES_ROOT.exists():
        for entry in sorted(TEMPLATES_ROOT.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                items.append({"slug": entry.name, "label": entry.name, "hint": ""})
    return items


# ---- List / get -------------------------------------------------------------

@router.get("/")
async def list_projects() -> list[Project]:
    data = _load_registry()
    items = []
    for slug, raw in (data.get("projects") or {}).items():
        items.append(Project(slug=slug, **raw))
    return sorted(items, key=lambda p: p.last_touched, reverse=True)


@router.get("/{slug}")
async def get_project(slug: str) -> Project:
    raw = _ensure_project(slug)
    return Project(slug=slug, **raw)


# ---- Create -----------------------------------------------------------------

def _scaffold_blank(target: Path, slug: str, title: str, purpose: str, brief: str) -> None:
    target.mkdir(parents=True, exist_ok=False)
    (target / "src").mkdir()
    _write_text(target / ".gitignore", "node_modules/\ndist/\nINTERVENTIONS.md\n.DS_Store\n")
    _write_text(
        target / "PROJECT.md",
        f"# {title}\n\n> {purpose}\n\n## Goal\n\n{brief}\n\n## Constraints\n\n- (fill in)\n\n## Open questions\n\n- (fill in)\n",
    )
    _write_text(target / "TODO.md", "# TODO\n\n- [ ] First task\n")
    _write_text(target / "AGENTS.md",
        "# Agent rules for this project\n\n"
        "- Read `PROJECT.md` first every turn for current goals and constraints.\n"
        "- Append to `TODO.md` for new tasks; check items off when done.\n"
        "- Edit only within this project directory. If you need to touch files\n"
        "  outside it, surface to the operator and stop.\n"
        "- Follow the workshop design system (see `.claude/CLAUDE.md`).\n"
    )
    rules_dir = target / ".claude"
    rules_dir.mkdir()
    _write_text(rules_dir / "CLAUDE.md",
        f"# {title} — house rules\n\n"
        "This project lives inside the workshop. The workshop design system\n"
        "applies: build from `frontend/src/ui` primitives + `src/styles/tokens.css`;\n"
        "no inline hex; lucide icons only; five status kinds; Edit-mode for\n"
        "config/money writes; `ConfirmDialog` for destructive actions; all four\n"
        "list states (loading/empty/error/overflow). Dark theme is the default.\n\n"
        "Before claiming a UI task done: `bash scripts/check_design.sh` and\n"
        "`cd frontend && npx tsc --noEmit` from the workshop root.\n"
    )


def _scaffold_from_template(target: Path, template: str, slug: str, title: str, purpose: str, brief: str) -> None:
    tpl_dir = TEMPLATES_ROOT / template
    if template == "blank" or not tpl_dir.exists():
        _scaffold_blank(target, slug, title, purpose, brief)
        return
    shutil.copytree(tpl_dir, target)
    # Token substitution in text files.
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        new = (text
               .replace("{{slug}}", slug)
               .replace("{{title}}", title)
               .replace("{{purpose}}", purpose))
        if new != text:
            path.write_text(new)
    # PROJECT.md gets the operator-supplied brief overlaid.
    brief_path = target / "PROJECT.md"
    if brief.strip():
        brief_path.write_text(f"# {title}\n\n> {purpose}\n\n## Goal\n\n{brief}\n")


def _git_init(target: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Projects",
        "GIT_AUTHOR_EMAIL": "projects@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Projects",
        "GIT_COMMITTER_EMAIL": "projects@workshop.local",
    }
    subprocess.run(["git", "init", "-q"], cwd=target, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=target, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"Scaffold {target.name}"],
        cwd=target, env=env, check=True,
    )


@router.post("/")
async def create_project(body: CreateProjectIn) -> Project:
    slug = body.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "slug must be kebab-case alphanumeric, start with a letter, max 48 chars")
    target = PROJECTS_ROOT / slug
    if target.exists():
        raise HTTPException(409, f"project dir already exists: {target}")
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        raise HTTPException(409, f"slug already in registry: {slug}")

    title = body.title.strip() or slug
    purpose = body.purpose.strip()
    brief = body.brief.strip()

    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    _scaffold_from_template(target, body.template, slug, title, purpose, brief)
    _git_init(target)

    now = _now()
    record = {
        "title": title,
        "purpose": purpose,
        "dir": str(target),
        "sensitivity": body.sensitivity,
        "state": "sketch",
        "surfaces": {"integrated": None, "standalone": None, "public": None},
        "needs": [],
        "provides": [],
        "created": now,
        "last_touched": now,
        "claude_session": None,
    }
    data.setdefault("projects", {})[slug] = record
    _save_registry(data)
    return Project(slug=slug, **record)


# ---- File access (Brief / TODO / Interventions) -----------------------------

def _project_dir(slug: str) -> Path:
    raw = _ensure_project(slug)
    return Path(raw["dir"])


@router.get("/{slug}/brief")
async def get_brief(slug: str) -> dict:
    return {"content": _read_text(_project_dir(slug) / "PROJECT.md")}


@router.put("/{slug}/brief")
async def put_brief(slug: str, body: FileWriteIn) -> dict:
    _write_text(_project_dir(slug) / "PROJECT.md", body.content)
    _touch(slug)
    return {"ok": True}


@router.get("/{slug}/todo")
async def get_todo(slug: str) -> dict:
    return {"content": _read_text(_project_dir(slug) / "TODO.md")}


@router.put("/{slug}/todo")
async def put_todo(slug: str, body: FileWriteIn) -> dict:
    _write_text(_project_dir(slug) / "TODO.md", body.content)
    _touch(slug)
    return {"ok": True}


@router.get("/{slug}/interventions")
async def get_interventions(slug: str) -> dict:
    return {"content": _read_text(_project_dir(slug) / "INTERVENTIONS.md")}


def _touch(slug: str) -> None:
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        data["projects"][slug]["last_touched"] = _now()
        _save_registry(data)


# ---- File tree --------------------------------------------------------------

@router.get("/{slug}/tree")
async def get_tree(slug: str) -> dict:
    root = _project_dir(slug)
    SKIP = {".git", "node_modules", "dist", ".next", "__pycache__"}

    def walk(p: Path) -> list[dict]:
        items = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
            if child.name in SKIP or child.name.startswith("."):
                continue
            item = {"name": child.name, "path": str(child.relative_to(root))}
            if child.is_dir():
                item["children"] = walk(child)
                item["kind"] = "dir"
            else:
                item["kind"] = "file"
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    item["size"] = None
            items.append(item)
        return items

    return {"root": str(root), "tree": walk(root)}


@router.get("/{slug}/file")
async def get_file(slug: str, path: str) -> dict:
    root = _project_dir(slug)
    target = (root / path).resolve()
    # Path-traversal guard: target must be under the project root.
    if root.resolve() not in target.parents and target != root.resolve():
        raise HTTPException(400, "path escapes project root")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        return {"path": path, "content": target.read_text()}
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file")


@router.put("/{slug}/file")
async def put_file(slug: str, path: str, body: FileWriteIn) -> dict:
    root = _project_dir(slug)
    target = (root / path).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise HTTPException(400, "path escapes project root")
    _write_text(target, body.content)
    _touch(slug)
    return {"ok": True}


# ---- Import (clone an external repo) ----------------------------------------

def _derive_slug(url: str) -> str:
    """Derive a kebab-case slug from a git URL or local path.

    Strips a trailing `.git`, takes the basename, lowercases, and replaces
    any non-alnum chars with dashes. The result still has to satisfy
    SLUG_RE — the caller validates.
    """
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    base = base.lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base


def _valid_import_url(url: str) -> bool:
    """Accept https URLs, ssh-style `git@host:path`, or absolute local paths.

    Anything else (file://, http:// without TLS, relative paths) is rejected
    up front so we don't fork a subprocess on obviously bad input.
    """
    if url.startswith("https://"):
        return True
    if url.startswith("git@"):
        return True
    if url.startswith("/") and Path(url).exists():
        return True
    return False


@router.post("/import")
async def import_project(body: ImportProjectIn) -> Project:
    """Clone an external git repo into PROJECTS_ROOT and register it.

    Steps: validate URL, derive/validate slug, reject collisions, git clone
    (timeout 180s, neutral committer env), stub PROJECT.md only if missing,
    register with state=mounted. On clone failure the subprocess exit code
    + first 200 chars of stderr surface in the HTTPException detail so the
    operator can see what happened (private repo, network blocked, etc).
    """
    url = (body.url or "").strip()
    if not _valid_import_url(url):
        raise HTTPException(400, "url must be https://, git@host:path, or an absolute local path")

    slug = (body.slug or "").strip().lower() or _derive_slug(url)
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "slug must be kebab-case alphanumeric, start with a letter, max 48 chars")

    target = PROJECTS_ROOT / slug
    if target.exists():
        raise HTTPException(409, f"project dir already exists: {target}")
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        raise HTTPException(409, f"slug already in registry: {slug}")

    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Projects",
        "GIT_AUTHOR_EMAIL": "projects@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Projects",
        "GIT_COMMITTER_EMAIL": "projects@workshop.local",
        # Refuse to prompt for credentials — surface the failure to the
        # operator instead of hanging the request.
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            ["git", "clone", url, str(target)],
            env=env, capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        # Half-cloned dir is useless; clean it up so the slug is reusable.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(504, f"git clone timed out after 180s for {url}")
    if result.returncode != 0:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        stderr = (result.stderr or "").strip()[:200]
        raise HTTPException(502, f"git clone failed (exit {result.returncode}): {stderr}")

    title = (body.title or "").strip() or slug
    purpose = (body.title or "").strip() or f"Imported from {url}"

    project_md = target / "PROJECT.md"
    if not project_md.exists():
        project_md.write_text(
            f"# {title}\n\n> {purpose}\n\n## Goal\n\n(imported repo — fill in)\n\n## Constraints\n\n- (fill in)\n"
        )

    now = _now()
    record = {
        "title": title,
        "purpose": purpose,
        "dir": str(target),
        "sensitivity": body.sensitivity,
        "state": "mounted",
        "surfaces": {"integrated": None, "standalone": None, "public": None},
        "needs": [],
        "provides": [],
        "created": now,
        "last_touched": now,
        "claude_session": None,
    }
    data.setdefault("projects", {})[slug] = record
    _save_registry(data)
    return Project(slug=slug, **record)


# ---- Delete (retire) --------------------------------------------------------

@router.post("/{slug}/retire")
async def retire_project(slug: str) -> dict:
    data = _load_registry()
    if slug not in (data.get("projects") or {}):
        raise HTTPException(404, f"project not found: {slug}")
    data["projects"][slug]["state"] = "retired"
    _save_registry(data)
    return {"ok": True}
