"""Container runtime — a thin driver over `docker compose`.

The app runner used to be a from-scratch process supervisor (port registry, health
monitor, orphan reaper, zombie detection via /proc). A container runtime already does
all of that correctly, so this module replaces that apparatus with `docker compose`
calls against a per-project compose file:

  - one compose project per app: `-p ws_<slug>` (isolated network + names);
  - `up`/`down`/`ps`/`logs`/`restart` map straight to the runner's lifecycle;
  - docker owns restart-on-crash (`restart: unless-stopped` in the generated file),
    health (compose healthchecks), and cleanup (`down` reaps everything).

Nothing here knows about manifests — `compose_gen` turns a manifest into a compose
file, and `app_runner` drives this. `available()` is the gate: until the backend
process can reach the docker socket (it needs the `docker` group, applied on a fresh
login/reboot), the runner falls back to the legacy subprocess path so the running app
is never interrupted mid-transition.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("workshop.container_runtime")

# Cap a compose call. `up` triggers image pulls + builds (minutes); ps/logs are fast.
DEFAULT_TIMEOUT = int(os.environ.get("WORKSHOP_COMPOSE_TIMEOUT", "1800"))

_AVAIL_CACHE: dict[str, bool] = {}


def project_name(slug: str) -> str:
    """Compose project name for an app. Sanitized to compose's [a-z0-9_-] rule."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in slug.lower())
    return f"ws_{safe}"


def _docker() -> str | None:
    return shutil.which("docker")


def available(refresh: bool = False) -> bool:
    """True if this process can actually reach a docker daemon. Cached because it
    shells out; pass refresh=True after a runtime change. The check is `docker
    version --format {{.Server.Version}}` — it fails fast with a clear non-zero when
    the socket is unreachable (the pre-reboot 'permission denied' case)."""
    if not refresh and "ok" in _AVAIL_CACHE:
        return _AVAIL_CACHE["ok"]
    docker = _docker()
    if not docker:
        _AVAIL_CACHE["ok"] = False
        return False
    try:
        r = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        ok = r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        ok = False
    _AVAIL_CACHE["ok"] = ok
    return ok


class ComposeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _compose(slug: str, compose_file: Path, *args: str,
             timeout: int = DEFAULT_TIMEOUT, cwd: Path | None = None) -> subprocess.CompletedProcess:
    docker = _docker()
    if not docker:
        raise ComposeError("docker not found on PATH")
    cmd = [docker, "compose", "-p", project_name(slug), "-f", str(compose_file), *args]
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as e:
        raise ComposeError(f"compose {' '.join(args[:1])} timed out after {timeout}s") from e
    except OSError as e:
        raise ComposeError(f"compose could not run: {e}") from e


def config_ok(slug: str, compose_file: Path, cwd: Path | None = None) -> tuple[bool, str]:
    """Validate a generated compose file (`docker compose config -q`). Cheap gate
    before an up, and used by tests. Returns (ok, error-text)."""
    r = _compose(slug, compose_file, "config", "-q", timeout=60, cwd=cwd)
    return (r.returncode == 0, (r.stderr or r.stdout or "").strip())


def up(slug: str, compose_file: Path, services: list[str] | None = None,
       build: bool = True, cwd: Path | None = None) -> tuple[bool, str]:
    """Bring the app up detached. `services` limits which to start (compose also
    starts their declared dependencies). `build` builds images with a Dockerfile.
    Returns (ok, combined-output-tail)."""
    args = ["up", "-d", "--remove-orphans"]
    if build:
        args.append("--build")
    if services:
        args.extend(services)
    r = _compose(slug, compose_file, *args, cwd=cwd)
    out = (r.stderr or "") + (r.stdout or "")
    return (r.returncode == 0, out[-4000:])


def down(slug: str, compose_file: Path, volumes: bool = False, cwd: Path | None = None) -> tuple[bool, str]:
    """Stop + remove the app's containers (and its network). `volumes=True` also
    drops named volumes (a fresh-start reset). Idempotent."""
    args = ["down", "--remove-orphans"]
    if volumes:
        args.append("-v")
    r = _compose(slug, compose_file, *args, timeout=300, cwd=cwd)
    out = (r.stderr or "") + (r.stdout or "")
    return (r.returncode == 0, out[-2000:])


def restart(slug: str, compose_file: Path, service: str, cwd: Path | None = None) -> tuple[bool, str]:
    r = _compose(slug, compose_file, "restart", service, timeout=120, cwd=cwd)
    out = (r.stderr or "") + (r.stdout or "")
    return (r.returncode == 0, out[-2000:])


def logs(slug: str, compose_file: Path, service: str, tail: int = 400, cwd: Path | None = None) -> str:
    r = _compose(slug, compose_file, "logs", "--no-color", "--tail", str(tail), service,
                 timeout=30, cwd=cwd)
    return (r.stdout or "") + (r.stderr or "")


def ps(slug: str, compose_file: Path, cwd: Path | None = None) -> list[dict]:
    """Per-container status as normalized dicts. Parses `compose ps --format json`
    (compose v2 emits either a JSON array or one object per line). Each entry:
    {service, state, health, running, exit_code, ports:[{host_ip,host_port,target}]}"""
    r = _compose(slug, compose_file, "ps", "--all", "--format", "json", timeout=30, cwd=cwd)
    if r.returncode != 0:
        raise ComposeError((r.stderr or r.stdout or "compose ps failed").strip())
    raw = (r.stdout or "").strip()
    if not raw:
        return []
    rows: list[dict] = []
    # Tolerate both shapes: a single JSON array, or newline-delimited objects.
    try:
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out: list[dict] = []
    for row in rows:
        publishers = row.get("Publishers") or []
        ports = [
            {
                "host_ip": p.get("URL") or p.get("HostIp") or "",
                "host_port": p.get("PublishedPort") or p.get("HostPort"),
                "target": p.get("TargetPort") or p.get("Target"),
            }
            for p in publishers if (p.get("PublishedPort") or p.get("HostPort"))
        ]
        out.append({
            "service": row.get("Service") or row.get("Name"),
            "state": (row.get("State") or "").lower(),          # running | exited | created | ...
            "health": (row.get("Health") or "").lower() or None,  # healthy | unhealthy | starting | ""
            "running": (row.get("State") or "").lower() == "running",
            "exit_code": row.get("ExitCode"),
            "ports": ports,
        })
    return out
