"""Command resolution, scoped env and surface reflection for a project tree.

WHAT THIS IS NOW. Three helpers `services/app_runner.py` builds on, and nothing
else:

  - `resolve_command(cwd)` — the launch command for a project, read from its
    manifest (`MANIFEST_NAMES`) or detected by `dev_servers.suggest_command`,
    so the app runner and the builder Preview form agree on how to start a
    project rather than each guessing separately.
  - `scoped_env(port)` — the env a child process is given: an ALLOWLIST plus
    `PORT`, never `{**os.environ}`. The platform's secret-bearing variables are
    dropped rather than inherited, which is the whole point of the function.
  - `_reflect_surface(slug, standalone)` — best-effort mirror of a running
    surface into the projects registry.

WHAT THIS WAS, AND WHY THE REST IS GONE. Until 2026-09-04 this was "LocalRunner
v1" from docs/workshop-v1/05-deployment-isolation-safety.md §2, and it also
carried a full process lifecycle: a `Deploy` dataclass, an in-memory `RUNNING`
map, a `.runtime/deploys.json` runtime store, a port window at 8600-8699, and
`start_deploy` / `stop_deploy` / `deploy_status`.

That lifecycle had ZERO callers and was actively dangerous: it shared the
`.runtime/deploys.json` path with the deploy subsystem's own record store under
an INCOMPATIBLE schema, so the first write through `start_deploy` would have
overwritten every Deploy record with its own `{"deploys": {slug: ...}}` shape.
It was deleted with the deploy subsystem (ticket `726d75a9`). Real apps are run
by `services/app_runner.py`, which is a different and living thing; this module
survives only because that one imports these three helpers. Do not grow a
process lifecycle back in here.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services import dev_servers
from wsplatform import paths

log = logging.getLogger("workshop.local_runner")
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


# ---- persistence helpers ----------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
