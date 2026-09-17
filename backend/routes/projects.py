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
import logging
import os
import re
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from routes.api_models import (AgentLimits, AgentTrigger, AwaitingRow, ContentOut, FileOut, Job, OkOut,
                               QueueCounts, TreeOut)
from services import apps as apps_svc
from services import tasks, jobs, triggers
from wsplatform import paths
from wsplatform.http import DEFAULT_PAGE, MAX_PAGE
from services import scope_owner
# MODULE SCOPE, not four function-local copies. `services.github` imports
# nothing from `routes` or `mcp`, so this is a real edge down the graph and
# not a cycle being deferred -- and a deferred import hides a cycle rather
# than breaking one. The file had three local copies and 799a7866 added a
# fourth, which is how this drifts one line at a time.
from services import github

log = logging.getLogger("workshop.projects")

router = APIRouter()

# Project tree lives inside ~/workshop so the design-system gate + watchdog
# already apply. Configurable for tests.
# Both are per-OWNER and therefore functions, not constants: a module-level
# constant is evaluated once at import, which would pin whichever owner happened
# to be current then (in practice the operator, forever, for everyone). The
# operator's own paths are unchanged — see paths.projects_root.
#
# Per-instance operator state lives in .runtime/ (gitignored), never in the
# tracked tree, so a fresh clone starts with an empty registry instead of
# inheriting whichever operator committed it last. Overridable for tests.
def _projects_root() -> Path:
    return paths.projects_root()


def _registry() -> Path:
    return paths.projects_registry()


def _templates_root() -> Path:
    """Shipped project templates. NOT per-owner: tracked source the instance
    provides, the same for everybody, forked into a real project on use."""
    return paths.env_path(
        "WORKSHOP_PROJECTS_ROOT_NEW", "projects", base="root") / "_templates"

# Slug discipline: kebab-case, alphanumeric + dashes, max 48 chars. A leading
# digit is fine ("3d-printer"): the frontend slugify produces it and it is valid
# as a directory name and a route segment.
#
# `\Z`, not `$`: Python's `$` also matches before a trailing newline, so with
# `.match()` (how all three call sites use this) the pattern accepted "foo\n"
# as the slug "foo" and handed the newline through to whatever built a path
# from it. Harmless in the cases that exist today and exactly the sort of
# almost-right a containment check should not be built on.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}\Z")

State = Literal["sketch", "wip", "mounted", "shared", "published", "parked", "retired"]
Sensitivity = Literal["private", "shareable", "public"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_registry() -> dict:
    if not _registry().exists():
        return {"projects": {}}
    return json.loads(_registry().read_text())


def _save_registry(data: dict) -> None:
    """Write the projects registry, then carry the change into the `app` table.

    THE CHOKEPOINT ON PURPOSE. Every write to this file goes through here (this
    module's own eight call sites plus `services/registry.py` and
    `routes/agents.py`), so this is the one place that can guarantee the app
    roster does not fall behind the file. A project created, renamed or retired
    without it left `app` holding the roster as of whenever somebody last ran
    `db/app_backfill --apply`, and nothing reported the gap because a stale
    roster looks exactly like a current one. See
    `services.apps.reconcile_from_registries`.

    Reconciliation never raises and never blocks the write: the file is the
    thing being written, the table follows it, and an instance with no database
    is still a working instance.
    """
    _registry().parent.mkdir(parents=True, exist_ok=True)
    _registry().write_text(json.dumps(data, indent=2) + "\n")
    result = apps_svc.reconcile_from_registries()
    if not result.get("ok"):
        log.info("projects registry written; app table not reconciled: %s",
                 result.get("reason"))


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

class StandaloneSurface(BaseModel):
    """Where a project's standalone deployment is running, reflected into the
    registry by `local_runner._reflect_surface` so the Projects hub can show a
    live URL. ONE writer now:

      app_runner                  runner="app", service, port, url, started_at

    There were two until 2026-09-04: `local_runner.start_deploy` also wrote
    `runner="local"` with a pid. It had no callers and went with the deploy
    subsystem (ticket `726d75a9`), so `runner` is `"app"` on every live record.
    `None` means nothing is running; the writer clears it that way on stop.

    NOTE ON THE WIRE. This router serialises full models (it returned
    constructed `Project` instances before it had a `response_model` at all, so
    `repo: null` and friends have always been on the wire here). Declaring these
    six therefore adds `pid: null` to an app-runner surface and `service: null`
    to a local one, matching how the sibling `integrated`/`public` keys already
    render. That is a deliberate, stated trade, not an oversight -- the
    alternative is `response_model_exclude_unset` on this router, which would
    REMOVE keys clients have always received."""

    model_config = ConfigDict(extra="allow")

    runner: str | None = None
    service: str | None = None
    pid: int | None = None
    port: int | None = None
    url: str | None = None
    started_at: str | None = None


class Surfaces(BaseModel):
    integrated: str | None = None
    standalone: StandaloneSurface | None = None
    public: str | None = None


class ProjectRepo(BaseModel):
    """A project's binding to a GitHub remote, written by `_repo_field` below,
    which is a closed six-key literal. `remote_url` and `default_branch` are the
    two anything reads (services/worktrees.py resolves the canonical branch from
    it); the rest is provenance.

    Absent means UNBOUND, which all six live projects are, and which is why the
    worktree completion path has a local-merge branch at all."""

    model_config = ConfigDict(extra="allow")

    provider: str | None = None
    owner: str | None = None
    name: str | None = None
    remote_url: str | None = None
    default_branch: str | None = None
    connected_at: str | None = None


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
    # Additive GitHub binding (Phase G1). Absent = a purely local project,
    # which is what all six live projects are. See ProjectRepo above.
    repo: ProjectRepo | None = None
    # Opt-in worktree-per-task concurrency (Phase G3). Default false keeps the
    # existing single-dir sweep behavior; the dispatcher consults this flag to
    # decide whether to allocate a per-task worktree. Absent reads as false.
    concurrent_worktrees: bool = False
    # The project's RESIDENT agent slugs. `[]` is the default for a new project
    # and is deliberate, not a placeholder (ask A4): the project loop is not an
    # agent, so a project is fully workable with nobody resident. Populated by
    # adopting a template group. Definition files stay the source of truth for
    # what an agent IS; this is the scope's roster of who lives here.
    agents: list[str] = []


class CreateProjectIn(BaseModel):
    slug: str
    title: str
    purpose: str = ""
    template: str = "blank"
    sensitivity: Sensitivity = "shareable"
    brief: str = ""
    # None = auto (on for real templates, off for blank/scratch). True/False
    # forces it. Provisions a private GitHub repo right after the local git init.
    create_repo: bool | None = None


class ConnectGithubIn(BaseModel):
    mode: Literal["create", "bind"]
    remote_url: str | None = None
    scratch: bool | None = None


class ProjectPatchIn(BaseModel):
    # Minimal setter for the opt-in worktree-per-task flag (Phase G3). Additive:
    # extend with more editable fields here as needed. None = leave unchanged.
    concurrent_worktrees: bool | None = None
    # The repo write (see `set_repo` below): binding or rebinding `origin`.
    # `default_branch` alone (no `remote_url`) is refused rather than silently
    # ignored -- there is no repo yet to attach it to and rebinding is cheap and
    # idempotent, so asking for both together is never a real burden.
    remote_url: str | None = None
    default_branch: str | None = None


class ImportProjectIn(BaseModel):
    url: str
    slug: str | None = None
    title: str | None = None
    sensitivity: Sensitivity = "shareable"


class FileWriteIn(BaseModel):
    content: str


# ---- Templates --------------------------------------------------------------

def load_templates() -> list[dict]:
    """The template catalog as `{slug, label, hint}`. Single source of truth for
    both the /templates route and chat_context's new-project persona.

    'blank' is always first (programmatic scaffold, no on-disk dir). Each on-disk
    `_templates/<name>/` reads its `template.json` for `label`/`hint`; a template
    with no (or a malformed) manifest falls back to the dir name + empty hint."""
    items = [{"slug": "blank", "label": "Blank", "hint": "Empty scaffold with the standard files"}]
    if _templates_root().exists():
        for entry in sorted(_templates_root().iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            label, hint = entry.name, ""
            manifest = entry / "template.json"
            if manifest.exists():
                try:
                    meta = json.loads(manifest.read_text())
                    label = str(meta.get("label") or entry.name)
                    hint = str(meta.get("hint") or "")
                except (json.JSONDecodeError, OSError):
                    pass
            items.append({"slug": entry.name, "label": label, "hint": hint})
    return items


class ProjectTemplate(BaseModel):
    """One entry in the scaffold catalog. `blank` is always first and is a
    programmatic scaffold with no on-disk directory."""

    slug: str
    label: str
    hint: str


@router.get("/templates", response_model=list[ProjectTemplate])
async def list_templates() -> list[dict]:
    """List available templates ({slug,label,hint}). See load_templates.

    A bare array, which ADR 0023 clause 1 bans in favour of the `items`/`page`
    envelope. Left as an array here for the reason the ADR's migration posture
    gives: this is a wire-shape change to a route the new-project dialog reads,
    and it is not complete until that reader changes in the same commit."""
    return load_templates()


# ---- List / get -------------------------------------------------------------

def _is_self_row(raw: dict) -> bool:
    """Is this registry row the platform repo (the Workshop's own source)?

    The same rule as `services.app_runner.is_self_project` — the `self` flag, or
    a dir that resolves to the repo root — asked of a row already in hand, so
    listing N projects does not re-read and re-parse the registry N times. The
    directory half delegates to `wsplatform.dev_mode.is_self_dir`, the one
    predicate ADR 0014 gates on, so a renamed row cannot slip back in as an
    ordinary project."""
    from wsplatform import dev_mode

    if raw.get("self"):
        return True
    d = raw.get("dir")
    return bool(d) and dev_mode.is_self_dir(d)


@router.get("/", response_model=list[Project])
async def list_projects() -> list[Project]:
    """Every registered project EXCEPT the platform repo.

    The `self: true` row is the Workshop's own source tree. It lives in this
    registry because that is where its directory and its autonomy config are
    stored, but it is not a project: §4 of
    `docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md` says
    exactly one name resolves to the platform repo and that name is the
    top-level `development` scope. Listing the row here put a project card for
    the platform on the Projects page and in Home's recents, and offered it in
    the agent-scope pickers, which is precisely the alias the redesign removes.

    Filtered in BOTH modes, not only when developer mode is off. The row is not
    an ordinary project on a developer's box either; there it is reached as the
    `development` scope, which is a different surface with a different name.

    `GET /api/projects/{slug}` for the self row deliberately KEEPS WORKING — see
    the note on `get_project`."""
    data = _load_registry()
    items = []
    for slug, raw in (data.get("projects") or {}).items():
        if _is_self_row(raw):
            continue
        items.append(Project(slug=slug, **raw))
    return sorted(items, key=lambda p: p.last_touched, reverse=True)


@router.get("/{slug}", response_model=Project)
async def get_project(slug: str) -> Project:
    """One project by slug, INCLUDING the self row. Deliberate, not an oversight.

    The choice was 404 the platform repo here too, or keep it addressable and
    gate only the listing. Kept addressable, for two reasons:

    1. ADR 0014 decided to GATE the self row's run path, not to remove it:
       `POST /{slug}/autonomy/run` calls `assert_run_target` and answers 403
       when developer mode is off. That is the sanctioned door, and it resolves
       through `_ensure_project` like every other per-project route. A 404 here
       would have to come from `_ensure_project` to be consistent, which would
       take the gated run path, the autonomy config and the queue readout down
       with it and replace a specific refusal with a misleading "not found".
    2. The gate is on the working tree, not on reads (ADR 0014: "a scope's store
       location and a run's working tree are different things, and only the
       second is gated"). Refusing to describe a row the API demonstrably has
       would be the kind of boundary-that-is-not-one that ADR unwinds.

    So the platform repo is no longer OFFERED as a project anywhere; a direct
    link to it still resolves."""
    raw = _ensure_project(slug)
    return Project(slug=slug, **raw)


class ProjectNotFound(ValueError):
    """No registered project has this slug. Its own type (rather than a bare
    HTTPException) because `set_repo` below has a second, non-HTTP caller --
    the MCP tool -- that needs to tell "not found" apart from "the write
    itself failed" without importing FastAPI."""


def set_repo(slug: str, remote_url: str, default_branch: str | None = None) -> dict:
    """Bind (or rebind) a project's GitHub remote through the SAME validated
    write `connect-github`'s `bind` mode uses (`github.bind_repo` +
    `_repo_field`), so owner policy and `reject_blocked` apply here exactly as
    they do there, and the registry record -- what worktrees and publish
    actually read -- never diverges from what a raw `git remote add/set-url`
    would leave behind. `default_branch`, when given, overrides what the
    remote's own HEAD resolves to (e.g. the operator wants a branch other than
    the detected default).

    Raises `ProjectNotFound`, `ValueError` (blank `remote_url`, missing
    directory) or one of `_GITHUB_ERRORS` (a `gh`/`git` failure) -- never
    `HTTPException`, so a non-HTTP caller (the MCP tool) can call this
    directly and render its own errors.
    """
    remote_url = (remote_url or "").strip()
    if not remote_url:
        raise ValueError("remote_url is required")
    data = _load_registry()
    raw = (data.get("projects") or {}).get(slug)
    if not raw:
        raise ProjectNotFound(slug)
    proj_dir = Path(raw["dir"])
    if not proj_dir.exists():
        raise ValueError(f"project directory missing: {proj_dir}")

    github.reject_blocked(remote_url)
    info = github.bind_repo(str(proj_dir), remote_url)
    raw["repo"] = _repo_field(info)
    branch = (default_branch or "").strip()
    if branch:
        raw["repo"]["default_branch"] = branch
    raw["last_touched"] = _now()
    data.setdefault("projects", {})[slug] = raw
    _save_registry(data)
    _assert_project_ignored(slug)
    return raw


@router.patch("/{slug}", response_model=Project)
async def patch_project(slug: str, body: ProjectPatchIn) -> Project:
    """Read-modify-write a small set of editable project fields: the
    `concurrent_worktrees` opt-in (Phase G3), and the repo binding (`set_repo`
    above) -- the same write the MCP `set_repo` tool calls, so a project chat
    and the UI's Repository panel go through one validated path. Absent fields
    are left unchanged."""
    data = _load_registry()
    raw = (data.get("projects") or {}).get(slug)
    if not raw:
        raise HTTPException(404, f"project not found: {slug}")
    if body.concurrent_worktrees is not None:
        raw["concurrent_worktrees"] = body.concurrent_worktrees
        raw["last_touched"] = _now()
        data.setdefault("projects", {})[slug] = raw
        _save_registry(data)
    if body.remote_url is not None:
        try:
            raw = set_repo(slug, body.remote_url, body.default_branch)
        except ProjectNotFound:
            raise HTTPException(404, f"project not found: {slug}")
        except _GITHUB_ERRORS as e:
            # Covers a plain ValueError too (blank remote_url, missing
            # directory, blocked owner, a remote bind_repo could not parse):
            # _GITHUB_ERRORS includes ValueError, and _github_http_error's own
            # fallback for anything it does not special-case is `400, str(e)`.
            raise _github_http_error(e)
    elif body.default_branch is not None:
        raise HTTPException(400, "default_branch requires remote_url")
    return Project(slug=slug, **raw)


# ---- Create -----------------------------------------------------------------

def _scaffold_blank(target: Path, slug: str, title: str, purpose: str, brief: str) -> None:
    target.mkdir(parents=True, exist_ok=False)
    (target / "src").mkdir()
    _write_text(target / ".gitignore", "node_modules/\ndist/\nINTERVENTIONS.md\n.DS_Store\n")
    # A supplied brief stands on its own (the operator or the new-project chat
    # already structures it). Only fall back to the Goal/Constraints/Open
    # questions skeleton when no brief was given, so we never duplicate headings.
    if brief.strip():
        project_md = f"# {title}\n\n> {purpose}\n\n{brief.strip()}\n"
    else:
        project_md = f"# {title}\n\n> {purpose}\n\n## Goal\n\n## Constraints\n\n- (fill in)\n\n## Open questions\n\n- (fill in)\n"
    _write_text(target / "PROJECT.md", project_md)
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
        f"# {title}: house rules\n\n"
        "This project lives inside the workshop. The workshop design system\n"
        "applies: build from the `@workshop/ui` kit (packages/ui). Import primitives\n"
        "from `@workshop/ui` and its tokens from `@workshop/ui/tokens.css`;\n"
        "no inline hex; lucide icons only; five status kinds; Edit-mode for\n"
        "config/money writes; `ConfirmDialog` for destructive actions; all four\n"
        "list states (loading/empty/error/overflow). Dark theme is the default.\n\n"
        "Before claiming a UI task done: `bash scripts/check_design.sh` and\n"
        "`cd frontend && npx tsc --noEmit` from the workshop root.\n"
    )


def _scaffold_from_template(target: Path, template: str, slug: str, title: str, purpose: str, brief: str) -> None:
    tpl_dir = _templates_root() / template
    if template == "blank" or not tpl_dir.exists():
        _scaffold_blank(target, slug, title, purpose, brief)
        return
    shutil.copytree(tpl_dir, target)
    # template.json is template metadata (label/hint for the picker), not project
    # content, so drop it from the scaffolded project rather than ship it as cruft.
    tpl_manifest = target / "template.json"
    if tpl_manifest.exists():
        tpl_manifest.unlink()
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
    # Merge the operator-supplied brief under a `## Brief` section rather than
    # overwriting the template's richer PROJECT.md (which carries the template's
    # own goal/constraints/open-questions scaffold). Append so the template's
    # structure is preserved and the brief is additive.
    brief_path = target / "PROJECT.md"
    if brief.strip():
        existing = brief_path.read_text() if brief_path.exists() else f"# {title}\n\n> {purpose}\n"
        if not existing.endswith("\n"):
            existing += "\n"
        brief_path.write_text(existing + f"\n## Brief\n\n{brief.strip()}\n")


def _git_init(target: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Workshop Projects",
        "GIT_AUTHOR_EMAIL": "projects@workshop.local",
        "GIT_COMMITTER_NAME": "Workshop Projects",
        "GIT_COMMITTER_EMAIL": "projects@workshop.local",
    }
    # -b main so the trunk is always `main` (git's default is often `master`,
    # which leaves the branches UI — which looks for `main` — empty). Existing
    # projects are NOT rewritten; this only affects fresh scaffolds.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=target, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"Scaffold {target.name}"],
        cwd=target, env=env, check=True,
    )


def _is_scratch_template(template: str) -> bool:
    """A blank/scratch scaffold is treated as scratch: repo provisioning defaults
    off and any created repo lands in the clean `workshop-` namespace."""
    return (template or "").strip().lower() in ("", "blank", "scratch")


def _assert_project_ignored(slug: str) -> None:
    """The workshop tree must never track a project working tree or its .git.
    `/projects/*` is gitignored; confirm it still holds after we touch the dir."""
    workshop_root = paths.workshop_root()
    r = subprocess.run(
        ["git", "-C", str(workshop_root), "check-ignore", f"projects/{slug}"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise HTTPException(
            500,
            f"boundary violation: projects/{slug} is not gitignored by the workshop tree",
        )


def _repo_field(info: dict) -> dict:
    return {
        "provider": "github",
        "owner": info["owner"],
        "name": info["name"],
        "remote_url": info["remote_url"],
        "default_branch": info["default_branch"],
        "connected_at": _now(),
    }


@router.post("/", response_model=Project)
async def create_project(body: CreateProjectIn) -> Project:
    slug = body.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "slug must be kebab-case alphanumeric, start with a letter, max 48 chars")
    target = _projects_root() / slug
    if target.exists():
        raise HTTPException(409, f"project dir already exists: {target}")
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        raise HTTPException(409, f"slug already in registry: {slug}")

    title = body.title.strip() or slug
    purpose = body.purpose.strip()
    brief = body.brief.strip()

    _projects_root().mkdir(parents=True, exist_ok=True)
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
        "concurrent_worktrees": False,
        # Whose project this is. Stamped at creation so the "an absent
        # owner_id reads as the operator's" assumption in scope_owner ages out
        # instead of compounding.
        **scope_owner.stamp(),
        # ZERO resident agents, explicitly (ask A4 / roles report 5.6). A new
        # project gets its autonomy preset and the project loop, and the project
        # loop IS NOT AN AGENT — it is project_ralph.sh sweeping the scope, which
        # needs no definition file. Three agents per new project, most of which
        # the operator never looks at and most of which would triage an empty
        # notes folder, is a machine for generating noise and cost.
        #
        # This is written as `[]` rather than left absent so the default is a
        # decision on the record, not an accident of a missing field. The upgrade
        # is one click: adopt the `tended` group when the project actually starts
        # accumulating notes (POST /api/agents/adopt-group).
        "agents": [],
    }

    # Provision a private GitHub repo unless this is a scratch/blank scaffold
    # (default off there). Owner is the configured account; the repo is always
    # private; a scratch project lands in the `workshop-` namespace.
    scratch = _is_scratch_template(body.template)
    want_repo = body.create_repo if body.create_repo is not None else (not scratch)
    if want_repo:
            # An install with no GitHub owner configured, or no `gh` login, used to
        # take the whole scaffold down with a 500 AFTER the directory had been
        # written. The local project is real either way, so a repo failure is
        # reported as a failure to connect one — not as a failure to create the
        # project the operator can already see on disk.
        try:
            github.reject_blocked(slug, title)
            base = github.repo_base_name(slug, scratch)
            name = github.unique_repo_name(base)
            info = github.create_repo(str(target), name)
        except _GITHUB_ERRORS as e:
            raise _github_http_error(e)
        record["repo"] = _repo_field(info)
        _assert_project_ignored(slug)

    data.setdefault("projects", {})[slug] = record
    _save_registry(data)
    return Project(slug=slug, **record)


def _github_http_error(e: Exception) -> HTTPException:
    """Turn a `gh`/`git` failure into a response that says what to do about it.

    Every one of these used to escape as an unhandled 500 with the traceback in
    the backend log and nothing in the UI. They are all ordinary operational
    states — not signed in, `gh` not installed, GitHub slow, no owner configured
    — so they are 400s that name the fix.

    `ValueError` is what `services.github` raises deliberately; the subprocess
    and OS errors are the ones nobody had claimed.
    """
    if isinstance(e, subprocess.TimeoutExpired):
        return HTTPException(400, f"GitHub did not respond in time (`{e.cmd[0]}` timed out).")
    if isinstance(e, FileNotFoundError):
        return HTTPException(400, "The `gh` CLI is not installed or not on PATH, "
                                  "so this project cannot reach GitHub.")
    if isinstance(e, subprocess.CalledProcessError):
        err = ((e.stderr or "") + (e.stdout or "")).strip()[:300] if e.stderr or e.stdout else ""
        return HTTPException(400, f"`gh` failed: {err or f'exit {e.returncode}'}. "
                                  "Settings → System shows whether the GitHub "
                                  "credential is still good.")
    return HTTPException(400, str(e))


# The failures `gh`/`git` can hand back. TimeoutExpired and CalledProcessError
# are both SubprocessError; FileNotFoundError is an OSError.
_GITHUB_ERRORS = (ValueError, subprocess.SubprocessError, OSError)


@router.post("/{slug}/connect-github", response_model=Project)
async def connect_github(slug: str, body: ConnectGithubIn) -> Project:

    raw = _ensure_project(slug)
    proj_dir = Path(raw["dir"])
    if not proj_dir.exists():
        raise HTTPException(404, f"project directory missing: {proj_dir}")

    try:
        if body.mode == "create":
            github.reject_blocked(slug, raw.get("title") or "")
            # Unknown scratch-ness defaults to scratch: safer, keeps a clean
            # `workshop-` namespace and never publishes a real repo by accident.
            scratch = body.scratch if body.scratch is not None else True
            base = github.repo_base_name(slug, scratch)
            name = github.unique_repo_name(base)
            info = github.create_repo(str(proj_dir), name)
        elif body.mode == "bind":
            if not (body.remote_url or "").strip():
                raise HTTPException(400, "bind requires remote_url")
            github.reject_blocked(body.remote_url)
            info = github.bind_repo(str(proj_dir), body.remote_url.strip())
        else:  # pragma: no cover - pydantic Literal blocks this
            raise HTTPException(400, f"unknown mode: {body.mode}")
    except _GITHUB_ERRORS as e:
        raise _github_http_error(e)

    raw["repo"] = _repo_field(info)
    raw["last_touched"] = _now()
    data = _load_registry()
    data.setdefault("projects", {})[slug] = raw
    _save_registry(data)

    # Enable git rerere so recurring conflict resolutions replay automatically
    # during rebase-before-publish (Phase G3). Best-effort: a failure here must
    # not fail the connect.
    try:
        github.enable_rerere(str(proj_dir))
    except Exception:
        pass

    # The connect flow operated inside the project dir only; confirm the
    # workshop tree still ignores it (no project .git leaked into the monorepo).
    _assert_project_ignored(slug)
    return Project(slug=slug, **raw)


# ---- File access (Brief / TODO / Interventions) -----------------------------

def _project_dir(slug: str) -> Path:
    raw = _ensure_project(slug)
    return Path(raw["dir"])


@router.get("/{slug}/brief", response_model=ContentOut)
async def get_brief(slug: str) -> dict:
    return {"content": _read_text(_project_dir(slug) / "PROJECT.md")}


@router.put("/{slug}/brief", response_model=OkOut)
async def put_brief(slug: str, body: FileWriteIn) -> dict:
    _write_text(_project_dir(slug) / "PROJECT.md", body.content)
    _touch(slug)
    return {"ok": True}


@router.get("/{slug}/todo", response_model=ContentOut)
async def get_todo(slug: str) -> dict:
    # Read through the structured store so the rendered markdown is always
    # current (the store is the single source of truth; TODO.md is its export).
    return {"content": tasks.export_markdown(_project_dir(slug))}


# `PUT /projects/{slug}/todo`: REMOVED (2026-08-17). It took raw markdown and
# re-derived the ticket store from it, which made a RENDERED EXPORT into store
# input. That is not a theoretical inversion: the `development` project's
# directory IS the repository root, so this route parsed a file the store had
# just written back into the store it came from. The read-back ran on 2026-08-17
# and minted four duplicate tickets, flipped a status, and wrote the export's
# rendered "(cancelled)" markers into 24 stored titles.
#
# It was guarded rather than removed at first, and the guard held; the route is
# gone because the direction should not exist for anything to reach. Tickets are
# edited through `/api/tasks` (or `PATCH /api/tasks/{id}`), which is the only
# writer. `GET /{slug}/todo` stays: reading the rendering is fine.
#
# ---- Structured tasks: REMOVED ---------------------------------------------
# `GET/POST /projects/{slug}/tasks` and `PATCH/DELETE
# /projects/{slug}/tasks/{id}` are deleted (2026-08-17). They were a SECOND CRUD
# surface over the same store as `/api/tasks?scope=project:<slug>`, and the two
# were not equivalent: this one filed straight through `tasks.add`, so it
# skipped the dedup pass, stored no `source` or `scoped_by`, and never touched
# `.runtime/queue-runner.wake`, so a ticket filed here sat until the next poll.
# Its PATCH accepted only `status` and `text`, so editing a tracker-linked
# ticket from the project page diverged it from the remote tracker in silence.
#
# The `/tasks` surface is the one that does all of that. ProjectDetail now calls
# it. The store is unchanged: projects/<slug>/.tasks.json, with TODO.md as the
# regenerated export.


@router.get("/{slug}/interventions", response_model=ContentOut)
async def get_interventions(slug: str) -> dict:
    return {"content": _read_text(_project_dir(slug) / "INTERVENTIONS.md")}


class InterventionIn(BaseModel):
    text: str
    who: str = "operator"


@router.post("/{slug}/interventions", response_model=ContentOut)
async def add_intervention(slug: str, body: InterventionIn) -> dict:
    """Append a timestamped note to INTERVENTIONS.md. Format matches the
    reader: `[<iso-utc>] <who>: <text>` (one line per entry, newest appended)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty note")
    d = _project_dir(slug)
    if not d.exists():
        raise HTTPException(404, f"project not found: {slug}")
    who = (body.who or "operator").strip() or "operator"
    line = f"[{_now()}] {who}: {text}\n"
    p = d / "INTERVENTIONS.md"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
    _touch(slug)
    return {"content": _read_text(p)}


def _touch(slug: str) -> None:
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        data["projects"][slug]["last_touched"] = _now()
        _save_registry(data)


# ---- Per-project autonomy (generalized Ralph) -------------------------------
# A project can run its OWN Ralph loop over its OWN TODO.md + git repo, so a
# PROJECT.md brief actually reaches an autonomous worker. As of the Phase 1
# platform redesign the run-state is a Job (services.jobs), not a bespoke
# ralph-run.json; the config surface is a unified trigger list (services.
# triggers) that translates to/from the registry's `autonomy.mode`:
#   off        — no automatic trigger (manual runs still available)
#   manual     — one `manual` trigger; operator-triggered "Run now" only
#   continuous — one `loop` trigger; also eligible for background sweeps
# The heavy lifting is scripts/project_ralph.sh; this router owns config + spawn.

SCRIPTS_ROOT = paths.workshop_root() / "scripts"

# A Job left in `running` past this bound (e.g. a hard-killed sweep whose
# finish() never ran) is reported as `crashed` rather than perpetually-running,
# so a dead sweep never wedges the "already running" guard.
RUNNING_STALE_S = 7200


class TriggersIn(BaseModel):
    # Preset shape (primary, O6/O7-config): an autonomy preset + budget limits,
    # expanded to triggers under the hood. The raw `triggers` list remains the
    # "advanced" escape hatch; when both are present, an explicit preset wins.
    #
    # `fanout` was accepted here and stored, and nothing ever read it back — the
    # sweep does not fan out to helper agents and never consulted the field. It
    # is gone rather than kept as a setting that does nothing
    # (docs/system-honesty-plan-2026-08-10.md, item 19). `depth` went with it for
    # the same reason: no sweep ever consulted it either, and the one readout
    # that reported it (GET /api/tasks/autonomy-scopes) has since been deleted
    # outright along with its unmounted component.
    preset: str | None = None
    cron: str | None = None
    limits: AgentLimits | None = None
    triggers: list[AgentTrigger] = Field(default_factory=list)


def _stored_triggers(autonomy: dict) -> list[dict]:
    """The scope's trigger list, which is the whole answer to "is this queue
    swept automatically?". A record with no `triggers` key has never been
    configured, and an unconfigured scope is `off` — no automatic sweep. There
    is no second key to fall back to: the `autonomy.mode` shadow is gone."""
    return [triggers.normalize(t) for t in (autonomy.get("triggers") or [])]


def _queue_snapshot(root: Path) -> dict:
    """The scope's task-queue counts + any pending operator prompts. `ready`
    counts todo tasks whose dependencies are all terminal (the same predicate
    `next_ready` uses); the rest are a status tally. `awaiting` surfaces the
    per-task `awaiting` payloads a sweep parks for operator input."""
    try:
        items = tasks.list_tasks(root, include_done=True)
    except Exception:
        items = []
    by_id = {t.get("id"): t for t in items}
    tally = {"todo": 0, "doing": 0, "in_review": 0, "blocked": 0,
             "parked": 0, "done": 0, "cancelled": 0}
    awaiting: list[dict] = []
    ready = 0
    for t in items:
        st = t.get("status")
        if st in tally:
            tally[st] += 1
        if st == "todo":
            deps = t.get("depends_on") or []
            if all((by_id.get(d) or {}).get("status") in tasks.TERMINAL
                   for d in deps if d in by_id):
                ready += 1
        aw = t.get("awaiting")
        if isinstance(aw, dict) and aw.get("question"):
            awaiting.append({
                "id": t.get("id"),
                "status": st,
                "text": (t.get("text") or "").split("\n")[0][:140],
                "question": aw.get("question"),
                "since": aw.get("since"),
            })
    return {
        "queue": {
            "ready": ready,
            "doing": tally["doing"],
            "in_review": tally["in_review"],
            "blocked": tally["blocked"],
            "parked": tally["parked"],
            "todo": tally["todo"],
        },
        "awaiting": awaiting,
    }


def _run_state(slug: str) -> tuple[bool, dict | None]:
    """(running, last_job) for a project, derived from the Job store."""
    recs = jobs.read(project_slug=slug, limit=1)
    last = recs[0] if recs else None
    running = False
    if last and last.get("status") == "running":
        started = last.get("started_at")
        stale = False
        if started:
            try:
                dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                stale = (datetime.now(timezone.utc) - dt).total_seconds() > RUNNING_STALE_S
            except (ValueError, TypeError):
                stale = False
        if stale:
            last = {**last, "status": "crashed"}
        else:
            running = True
    return running, last


class ProjectAutonomyOut(BaseModel):
    """The scope's autonomy config plus the state a reader needs to act on it.

    `autonomy` is the PRESET vocabulary (`off | on-demand | scheduled |
    continuous`, `services/triggers.py::PRESETS`) -- the trigger cadence for a
    project scope. It is a different axis from an AGENT's `autonomy_mode`
    (`reactive | supervised | autonomous`); see the note on
    `routes/agents.py::RosterAgent`."""

    autonomy: str
    limits: AgentLimits | None = None
    triggers: list[AgentTrigger] = []
    # ADR 0023 clause 4 bans `updated` as an instant field name in favour of
    # `updated_at`. Unchanged here: it is the key the stored registry record
    # uses and the key AutonomyPanel reads, so renaming it is a wire change
    # that must land with its frontend edit.
    updated: str | None = None
    running: bool
    last_run: Job | None = None
    queue: QueueCounts
    awaiting: list[AwaitingRow] = []


@router.get("/{slug}/autonomy", response_model=ProjectAutonomyOut, response_model_exclude_unset=True)
async def get_autonomy(slug: str) -> dict:
    raw = _ensure_project(slug)
    autonomy = raw.get("autonomy") or {}
    trigs = _stored_triggers(autonomy)
    running, last = _run_state(slug)
    # Preset is the primary control; prefer a stored one, else classify the
    # trigger list so pre-preset configs still display correctly.
    preset = autonomy.get("preset") or triggers.triggers_to_preset(trigs)
    snap = _queue_snapshot(_project_dir(slug))
    return {
        "autonomy": preset,
        "limits": autonomy.get("limits") or {},
        "triggers": trigs,
        "updated": autonomy.get("updated"),
        "running": running,
        "last_run": last,
        "queue": snap["queue"],
        "awaiting": snap["awaiting"],
    }


class ProjectAutonomyPutOut(BaseModel):
    """What was stored. Deliberately narrower than the GET: a write echoes the
    config it just persisted plus whether a sweep is in flight, and does not
    re-derive the queue snapshot the caller did not ask for."""

    autonomy: str
    limits: AgentLimits | None = None
    triggers: list[AgentTrigger] = []
    updated: str
    running: bool


@router.put("/{slug}/autonomy", response_model=ProjectAutonomyPutOut, response_model_exclude_unset=True)
async def put_autonomy(slug: str, body: TriggersIn) -> dict:
    # Preset shape wins when present; otherwise fall back to the raw trigger list
    # (the advanced escape hatch, and the pre-O6 request shape, unchanged).
    if body.preset is not None:
        if body.preset not in triggers.PRESETS:
            raise HTTPException(400, f"unknown autonomy preset: {body.preset}")
        norm = triggers.preset_to_triggers(body.preset, cron=body.cron, limits=body.limits)
        preset = body.preset
    else:
        try:
            norm = [triggers.normalize(t) for t in body.triggers]
        except ValueError as e:
            raise HTTPException(400, str(e))
        preset = triggers.triggers_to_preset(norm)
    data = _load_registry()
    proj = (data.get("projects") or {}).get(slug)
    if not proj:
        raise HTTPException(404, f"project not found: {slug}")
    limits = body.limits if body.limits is not None else \
        ((proj.get("autonomy") or {}).get("limits") or {})
    proj["autonomy"] = {
        "preset": preset,
        "limits": limits,
        "triggers": norm,
        "updated": _now(),
    }
    _save_registry(data)
    running, _ = _run_state(slug)
    return {
        "autonomy": preset,
        "limits": limits,
        "triggers": norm,
        "updated": proj["autonomy"]["updated"],
        "running": running,
    }


class AutonomyRunOut(BaseModel):
    """The sweep that was launched: the Job it adopts and the process running it."""

    started: bool
    pid: int
    slug: str
    job_id: str


@router.post("/{slug}/autonomy/run", response_model=AutonomyRunOut)
async def run_autonomy(slug: str) -> dict:
    """Fire a project Ralph sweep in the background as a manual Job. Uses --force
    so neither the global cron off-switch nor the per-project mode blocks a
    deliberate operator action; project_ralph.sh adopts the Job we open here as
    its run-state and finish()es it on exit.

    The developer-mode gate applies here too (ADR 0014 §2 door 6). The self
    project's registry row is an ordinary row to this route, so without the check
    a POST to this endpoint sweeps the Workshop's own source. `project_ralph.sh`
    would refuse it a second later, but as an exit code in a log rather than as
    an answer to the request — so the refusal is made here, where the caller can
    read it. A safety interlock, not a security boundary."""
    from wsplatform import dev_mode

    _ensure_project(slug)
    try:
        dev_mode.assert_run_target(_project_dir(slug), f"the {slug} autonomy run")
    except dev_mode.DevModeRefusal as e:
        raise HTTPException(e.status, e.message)
    running, _ = _run_state(slug)
    if running:
        raise HTTPException(409, "a sweep is already running for this project")
    script = SCRIPTS_ROOT / "project_ralph.sh"
    if not script.exists():
        raise HTTPException(500, f"driver missing: {script}")
    job = jobs.start(project_slug=slug, trigger="manual", status="running")
    log_dir = _project_dir(slug) / ".runtime"
    log_dir.mkdir(parents=True, exist_ok=True)
    logf = open(log_dir / "ralph.log", "a")  # noqa: SIM115 (handed to child)
    try:
        proc = subprocess.Popen(
            ["bash", str(script), slug, "--force", "--job-id", job["id"]],
            cwd=str(paths.workshop_root()),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        # THE PARENT'S COPY IS THE PARENT'S TO RELEASE. Popen dups this fd into
        # the child, so closing here does not disturb the sweep's logging. Left
        # open, every sweep ever launched cost this process one descriptor, for
        # as long as it runs, until it hit the open-file limit.
        logf.close()
    _touch(slug)
    return {"started": True, "pid": proc.pid, "slug": slug, "job_id": job["id"]}


class AutonomyLogOut(BaseModel):
    """The sweep history. Not the ADR 0023 envelope, for the same reason as
    `TaskJobsOut`: ProjectDetail reads these keys and cannot be edited in this
    commit."""

    jobs: list[Job]
    count: int
    running: bool


@router.get("/{slug}/autonomy/log", response_model=AutonomyLogOut)
async def get_autonomy_log(
    slug: str,
    # ADR 0023 clause 2: the ceiling is mandatory. The default is unchanged.
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
) -> dict:
    """Recent Jobs for this project (the sweep history), newest first."""
    _ensure_project(slug)
    recs = jobs.read(project_slug=slug, limit=limit)
    running, _ = _run_state(slug)
    return {"jobs": recs, "count": len(recs), "running": running}


# ---- File tree --------------------------------------------------------------

@router.get("/{slug}/tree", response_model=TreeOut)
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


@router.get("/{slug}/file", response_model=FileOut)
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


@router.put("/{slug}/file", response_model=OkOut)
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


@router.post("/import", response_model=Project)
async def import_project(body: ImportProjectIn) -> Project:
    """Clone an external git repo into _projects_root() and register it.

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

    target = _projects_root() / slug
    if target.exists():
        raise HTTPException(409, f"project dir already exists: {target}")
    data = _load_registry()
    if slug in (data.get("projects") or {}):
        raise HTTPException(409, f"slug already in registry: {slug}")

    _projects_root().mkdir(parents=True, exist_ok=True)
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
            f"# {title}\n\n> {purpose}\n\n## Goal\n\n(imported repo, fill in the goal)\n\n## Constraints\n\n- (fill in)\n"
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
        # Whose project this is. Stamped at creation so the "an absent
        # owner_id reads as the operator's" assumption in scope_owner ages out
        # instead of compounding.
        **scope_owner.stamp(),
    }
    data.setdefault("projects", {})[slug] = record
    _save_registry(data)
    return Project(slug=slug, **record)


# ---- Delete (retire) --------------------------------------------------------

@router.post("/{slug}/retire", response_model=OkOut)
async def retire_project(slug: str) -> dict:
    data = _load_registry()
    if slug not in (data.get("projects") or {}):
        raise HTTPException(404, f"project not found: {slug}")
    data["projects"][slug]["state"] = "retired"
    _save_registry(data)
    return {"ok": True}
