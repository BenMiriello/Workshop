"""LocalRunner — run a project's STANDALONE deployment as a scoped subprocess.

This is LocalRunner v1 from docs/workshop-v1/05-deployment-isolation-safety.md
section 2 ("the run model: worktrees for build, runners for deploy"). A project's
standalone surface is launched as a managed subprocess with:

  - its own working dir (the project's `dir`),
  - a dedicated port,
  - a SCOPED environment: only a safe allowlist (PATH/HOME/...) plus PORT. The
    platform's full secret env (WORKSHOP_511_API_KEY, HEALTH_USER, plaid creds,
    OwnTracks, etc) is NEVER passed to the deployed app. This is the runtime
    isolation layer in the doc's table.

Out of scope for v1 (noted, not implemented here):
  - INTEGRATED surface (route-mount): needs frontend route injection.
  - RemoteRunner: builds an OCI image + runs a container; same lifecycle verbs,
    added later (doc section 2 + 8, phase 05c).

Why a parallel registry instead of services/dev_servers.RUNNING: dev_servers is
the BUILDER Preview lifecycle. Its `start()` spawns with `{**os.environ, ...}`
(full platform env, by design for the operator's own preview) and shares one
slug-keyed RUNNING map with the gc reaper. A deploy needs a scoped env and a
separate namespace so deploy slugs never collide with preview/agent entries.
So this module mirrors the DevServer dataclass / RUNNING / status / stop SHAPE
exactly (same process-group + SIGTERM-then-SIGKILL discipline), without
duplicating dev_servers' own bookkeeping or touching its registry.

Command detection reuses dev_servers.suggest_command (the same heuristic the
Preview form uses), so deploy and preview agree on how to launch a project.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from services import dev_servers, paths, port_registry

log = logging.getLogger("workshop.local_runner")

# Deploy port window, distinct from the dev_servers Preview window (8500-8599)
# so a running preview and a running deploy of the same project don't fight over
# a port.
PORT_MIN = 8600
PORT_MAX = 8699

# Per-deploy log files (mirrors dev_servers' LOG_ROOT layout, separate subdir).
LOG_ROOT = paths.env_path("WORKSHOP_DEPLOY_LOG_ROOT", "deploy-logs")

# Runtime store: persisted record of the current standalone deploy per slug, so
# status survives within the process and the project's surfaces.standalone can be
# reflected. PIDs are also kept in-memory in RUNNING for lifecycle control.
RUNTIME_STORE = paths.env_path("WORKSHOP_DEPLOY_RUNTIME", "deploys.json")

# Projects registry — same file routes/projects.py owns. We reflect a deploy into
# surfaces.standalone here (best-effort) so the Projects hub sees the running URL.
#
# The default is `.runtime/projects.json`, which is where the registry actually
# is (routes/projects.REGISTRY, build_agents.PROJECTS_REGISTRY and tasks_all all
# read that path under the same env var). This used to default to a repo-root
# `projects.json` that has not existed for a long time, so every read through
# here silently found nothing: `app_runner.is_self_project` — which shares this
# constant — answered False for the self project itself. Harmless while it only
# suppressed an auto-checkpoint; not harmless now that ADR 0014 makes it half of
# the developer-mode predicate.
PROJECTS_REGISTRY = paths.env_path("WORKSHOP_PROJECTS_REGISTRY", "projects.json")

# Per-project manifest filenames that may carry a `run` field (the contract both
# runners read, living with the project). apps.json is keyed by app id, not
# project slug, so the per-project run command lives in the project dir.
MANIFEST_NAMES = ("workshop.manifest.json", "manifest.json", "app.manifest.json")

# Scoped-env allowlist: the ONLY host env vars passed through to the child. No
# secret-bearing var (tokens, API keys, db creds) is on this list. PORT is added
# explicitly by start_deploy. Anything not here is dropped.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
)


class DeployError(Exception):
    """Raised internally for a clear, caller-facing message. The route layer maps
    this to an HTTP 400; raw subprocess/OS errors never escape this module."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Deploy:
    slug: str
    pid: int
    port: int
    command: str
    url: str
    started_at: float
    log_path: str


# slug -> Deploy (in-memory authoritative for lifecycle; mirrored to RUNTIME_STORE)
RUNNING: dict[str, Deploy] = {}


# ---- persistence helpers ----------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_runtime() -> dict:
    if not RUNTIME_STORE.exists():
        return {"deploys": {}}
    try:
        data = json.loads(RUNTIME_STORE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("deploys"), dict):
            return {"deploys": {}}
        return data
    except (OSError, json.JSONDecodeError):
        return {"deploys": {}}


def _persist_runtime() -> None:
    """Mirror the in-memory RUNNING map to the runtime store (best-effort)."""
    try:
        _atomic_write_json(RUNTIME_STORE, {"deploys": {s: asdict(d) for s, d in RUNNING.items()}})
    except OSError as e:
        log.warning("could not persist deploy runtime store: %s", e)


def _reflect_surface(slug: str, standalone: dict | None) -> None:
    """Reflect the running deploy into projects.json surfaces.standalone.

    Best-effort: a missing project, an unknown slug, or an unwritable registry is
    logged and ignored (the deploy itself still ran). Atomic write. `standalone`
    is the surface dict to set, or None to clear it.
    """
    try:
        if not PROJECTS_REGISTRY.exists():
            return
        data = json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read projects registry to reflect surface: %s", e)
        return
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict) or slug not in projects:
        return
    rec = projects[slug]
    if not isinstance(rec, dict):
        return
    surfaces = rec.get("surfaces")
    if not isinstance(surfaces, dict):
        surfaces = {"integrated": None, "standalone": None, "public": None}
    surfaces["standalone"] = standalone
    rec["surfaces"] = surfaces
    try:
        _atomic_write_json(PROJECTS_REGISTRY, data)
    except OSError as e:
        log.warning("could not write projects registry to reflect surface: %s", e)


# ---- project + command resolution -------------------------------------------

def _project_dir(slug: str) -> str:
    """Return the project's working dir from the projects registry, or raise
    DeployError(404) if the slug is unknown / the dir is missing."""
    try:
        data = json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8")) if PROJECTS_REGISTRY.exists() else {}
    except (OSError, json.JSONDecodeError) as e:
        raise DeployError(f"could not read projects registry: {e}", status=500)
    rec = (data.get("projects") or {}).get(slug) if isinstance(data, dict) else None
    if not rec:
        raise DeployError(f"project not found: {slug}", status=404)
    cwd = rec.get("dir")
    if not cwd or not Path(cwd).is_dir():
        raise DeployError(f"project dir missing for {slug}: {cwd}", status=400)
    return cwd


def _manifest_run(cwd: str) -> str | None:
    """Return the `run` command from a per-project manifest if present, else None.

    Tolerant: an unreadable / malformed manifest is treated as "no run field"
    (detection takes over). A `run` that is not a non-empty string is ignored.
    """
    for name in MANIFEST_NAMES:
        p = Path(cwd) / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run = data.get("run") if isinstance(data, dict) else None
        if isinstance(run, str) and run.strip():
            return run.strip()
    return None


def resolve_command(cwd: str) -> str:
    """Determine the start command for a project dir.

    Order: per-project manifest `run` field, then dev_servers.suggest_command's
    heuristic (package.json scripts.dev/start, else a Python app, else static).
    Raises DeployError(400) with a clear message when nothing is detectable.

    Note: suggest_command emits commands using $WORKSHOP_DEV_PORT for the
    Preview lifecycle. For deploy we normalize that token to $PORT so the
    scoped-env PORT (which the deployed app expects) drives the bind. A
    manifest `run` field is used verbatim (the project author owns it).
    """
    run = _manifest_run(cwd)
    if run:
        return run
    detected = dev_servers.suggest_command(cwd)
    cmd = (detected.get("command") or "").strip()
    if not cmd:
        raise DeployError(
            "no run command: add a `run` field to the project manifest, or a "
            "package.json (scripts.dev/start) / app.py / main.py / index.html "
            f"so the runner can detect one (dir: {cwd})"
        )
    # Normalize the Preview port token to the deploy PORT contract.
    return cmd.replace("$WORKSHOP_DEV_PORT", "$PORT").replace("${WORKSHOP_DEV_PORT}", "$PORT")


# ---- scoped env -------------------------------------------------------------

def scoped_env(port: int) -> dict[str, str]:
    """Build the child's environment: ONLY the allowlisted host vars plus PORT.

    Deliberately does not start from os.environ, so no platform secret can leak
    by omission. Missing allowlisted vars are simply absent (e.g. no LC_ALL).
    """
    env: dict[str, str] = {}
    for key in ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    # Guarantee a PATH even on a stripped host env so the child can find python/node.
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PORT"] = str(port)
    return env


# ---- port allocation --------------------------------------------------------

def _next_free_port(slug: str) -> int:
    """Allocate a deploy port for `slug` via the central port_registry, so the
    deploy never collides with a preview, an app service, or any other process on
    the box. The pid is recorded after the child spawns (set_pid)."""
    try:
        return port_registry.allocate(f"deploy:{slug}", "deploy")
    except RuntimeError as e:
        raise DeployError(str(e), status=503)


# ---- lifecycle --------------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def deploy_status(project_slug: str) -> dict:
    """Status of the standalone deploy for a slug.

    Returns {running: False} when nothing is registered or the process has died
    (a dead entry is reaped as a side effect, mirroring dev_servers.status). When
    running: {running, port, url, pid, command, started_at}.
    """
    d = RUNNING.get(project_slug)
    if not d:
        return {"running": False}
    if not _alive(d.pid):
        # Process gone (crashed / killed externally); reap and clear the surface.
        RUNNING.pop(project_slug, None)
        port_registry.release(f"deploy:{project_slug}")
        _persist_runtime()
        _reflect_surface(project_slug, None)
        return {"running": False, "stale_pid": d.pid}
    return {
        "running": True,
        "pid": d.pid,
        "port": d.port,
        "url": d.url,
        "command": d.command,
        "started_at": d.started_at,
        "log_path": d.log_path,
    }


def start_deploy(project_slug: str) -> dict:
    """Start the project's standalone deployment as a scoped subprocess.

    Idempotent on an already-running deploy (returns its current status). Raises
    DeployError (mapped to HTTP by the route) for an unknown project, an
    undetectable command, or no free port. Never raises a raw subprocess/OS error.

    Returns {running: True, port, url, pid, command}.
    """
    existing = deploy_status(project_slug)
    if existing.get("running"):
        return {
            "running": True,
            "port": existing["port"],
            "url": existing["url"],
            "pid": existing["pid"],
            "command": existing["command"],
            "already_running": True,
        }

    cwd = _project_dir(project_slug)
    command = resolve_command(cwd)
    port = _next_free_port(project_slug)
    url = f"http://127.0.0.1:{port}"

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / f"{project_slug}.log"

    env = scoped_env(port)

    # Same spawn discipline as dev_servers.start: /bin/bash -lc so shell features
    # work; tee stdout+stderr to a file; new session (own process group) so stop
    # can kill the whole tree. The crucial difference is `env=` here is the SCOPED
    # env, not {**os.environ, ...}.
    #
    # We pass env to bash directly; -l would normally source the user profile and
    # re-introduce host vars, but a non-interactive `bash -lc` started with an
    # explicit env only inherits what we hand it plus whatever the profile sets.
    # To keep the env strictly scoped we use `bash -c` (no login profile) so no
    # profile-sourced secret leaks in.
    try:
        with open(log_path, "ab", buffering=0) as logf:
            proc = subprocess.Popen(
                ["/bin/bash", "-c", command],
                cwd=cwd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
    except (OSError, ValueError) as e:
        raise DeployError(f"failed to launch deploy for {project_slug}: {e}", status=500)

    deploy = Deploy(
        slug=project_slug,
        pid=proc.pid,
        port=port,
        command=command,
        url=url,
        started_at=time.time(),
        log_path=str(log_path),
    )
    RUNNING[project_slug] = deploy
    port_registry.set_pid(f"deploy:{project_slug}", proc.pid)
    _persist_runtime()
    _reflect_surface(project_slug, {
        "runner": "local",
        "pid": deploy.pid,
        "port": deploy.port,
        "url": deploy.url,
        "started_at": deploy.started_at,
    })

    return {
        "running": True,
        "port": deploy.port,
        "url": deploy.url,
        "pid": deploy.pid,
        "command": deploy.command,
    }


def stop_deploy(project_slug: str) -> dict:
    """Stop the standalone deploy: SIGTERM the process group, escalate to SIGKILL
    after 5s. Idempotent (stopping a non-running deploy returns stopped: True).
    Clears the runtime store entry and the project's surfaces.standalone."""
    d = RUNNING.pop(project_slug, None)
    if not d:
        # Nothing tracked; make sure the surface + port claim are clear anyway.
        port_registry.release(f"deploy:{project_slug}")
        _reflect_surface(project_slug, None)
        return {"stopped": True, "reason": "not running"}

    forced = False
    try:
        os.killpg(d.pid, signal.SIGTERM)
        for _ in range(50):
            if not _alive(d.pid):
                break
            time.sleep(0.1)
        else:
            os.killpg(d.pid, signal.SIGKILL)
            forced = True
    except ProcessLookupError:
        pass  # already gone
    except OSError as e:
        log.warning("error stopping deploy %s (pid %s): %s", project_slug, d.pid, e)

    port_registry.release(f"deploy:{project_slug}")
    _persist_runtime()
    _reflect_surface(project_slug, None)
    result = {"stopped": True}
    if forced:
        result["force_killed"] = True
    return result
