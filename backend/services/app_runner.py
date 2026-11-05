"""Multi-service app runner — run a project made of several servers as one app.

`local_runner` runs a project's standalone surface as ONE subprocess. Real apps are
several cooperating servers (for example an API `core`, a Vite `frontend` and a
background `worker`, plus Postgres/Redis). This runner models that:

  - An app has N **services**, each its own managed subprocess with its own cwd,
    build step, start command, and port (allocated centrally via port_registry).
  - Services declare `depends_on` other services; we start them in dependency order
    and inject each dependency's URL into the dependent's env (`<DEP>_URL=...`), so a
    frontend can find its API without hardcoding a port.
  - Optional external **deps** (Postgres, Redis, ...) are brought up via docker
    compose before the services start.
  - Build + spawn happen on a background thread (installs take minutes), so the HTTP
    call returns immediately and status reflects a live state machine
    (pending → building → starting → running / failed).
  - A **desired state** (running/stopped) is persisted so an app comes back after a
    backend restart. Keeping a crashed service up is docker's job, via the
    `restart: unless-stopped` policy compose_gen writes into every generated file.

This is the LocalRunner of docs/workshop-v1/05 generalized to multiple services. The
manifest is the contract a future RemoteRunner reads unchanged; only the spawn backend
(subprocess vs container) differs.

Manifest: `workshop.app.json` in the project dir. Absent ⇒ auto-detected (a
`deploy.config.yaml` with `services:`; else a single service from
dev_servers.suggest_command). Env is SCOPED (allowlist + PORT) like local_runner;
per-service `env` in the manifest adds the non-secret config a service needs.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from services import (compose_gen, container_runtime, local_runner, port_registry,
                      relocated_artifacts)
from wsplatform import paths

log = logging.getLogger("workshop.app_runner")

PROJECTS_REGISTRY = local_runner.PROJECTS_REGISTRY

# The Workshop monorepo itself. A project whose dir resolves here (or is flagged
# `self` in the registry) is the SELF / platform project: editing it edits the
# running app, so it is never a separate deployment (frontend hot-reloads;
# backend changes need scripts/restart_backend.sh).
WORKSHOP_ROOT = paths.workshop_root()


def is_self_project(slug: str) -> bool:
    """True if this project IS the running Workshop (the monorepo), not a
    separate deployable app. Detected by the registry `self` flag or a dir that
    resolves to WORKSHOP_ROOT. Never raises."""
    try:
        data = json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8")) if PROJECTS_REGISTRY.exists() else {}
        rec = (data.get("projects") or {}).get(slug) or {}
    except (OSError, json.JSONDecodeError):
        rec = {}
    if rec.get("self"):
        return True
    d = rec.get("dir")
    try:
        return bool(d) and Path(d).resolve() == WORKSHOP_ROOT
    except OSError:
        return False


# MOVED to `wsplatform/dev_mode.py` (target-architecture-2026-08-17.md §2.2,
# ADR 0024): `is_self_dir`, `is_in_self_repo`, the developer-mode flag
# (`DEV_MODE_ENV`, `dev_mode_on`), `self_dir_refusal` and `assert_run_target`.
# This module's own business logic never called any of them -- it only
# defined them for its 8 other importers, which is the misplacement
# `wsplatform/dev_mode.py`'s header explains in full. `is_self_project` stays
# here: it IS this module's own logic (guards `start_app`/`stop_app`/
# `restart_service`/`app_status`/`manifest_dict`/`env_overview` below, 6
# internal call sites), not a chokepoint anything outside app_runner reaches
# for the write-safety question `wsplatform.dev_mode` now answers alone.

LOG_ROOT = paths.env_path("WORKSHOP_APP_LOG_ROOT", "app-logs")

STATE_STORE = paths.env_path("WORKSHOP_APP_STATE", "app-deploys.json")

MANIFEST_NAMES = ("workshop.app.json", "workshop.app.jsonc")

# A build can be a full `bun install` / `pip install torch` — minutes. Cap it so a
# wedged install can't pin the worker thread forever.
BUILD_TIMEOUT = int(os.environ.get("WORKSHOP_APP_BUILD_TIMEOUT", "1800"))


class AppError(Exception):
    """Caller-facing error; the route maps .status to HTTP. Raw OS/subprocess
    errors never escape this module."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---- manifest model ---------------------------------------------------------

@dataclass
class ServiceSpec:
    name: str
    start: str
    cwd: str = "."           # relative to the project dir
    build: str | None = None
    port_env: str = "PORT"
    #: A FIXED port for this service. When set, the runner always uses exactly
    #: this port and never allocates a different one - it waits for the port to
    #: come free instead. Leave it unset to keep the dynamic allocation.
    #:
    #: Dynamic allocation is fine for a service nobody addresses by hand, and
    #: wrong for one that people and scripts do. One app's api drifted
    #: 8700 -> 8703 -> 8704 -> 8706 in nineteen minutes, because `_stop_service`
    #: releases the allocation before the dying process has actually let go of
    #: the socket, so `allocate(preferred=...)` finds its own old port "in use"
    #: and takes the next free one instead. Every restart moved the address, and
    #: anything holding the old one got "could not reach the server".
    port: int | None = None
    health: str | None = None  # http path probed for readiness, e.g. "/health"
    expose: bool = False     # a screen of the app (app_surface.app_screens)
    label: str | None = None  # the screen's name in the app's sidebar
    enabled: bool = True
    disabled_reason: str | None = None  # why enabled=false, shown in the UI
    depends_on: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: How to swap new code into this service without a stop, when it knows a
    #: better way than stop-and-spawn (a graceful handover to its own launcher).
    #: A command, run in the service's cwd with the service's env; a restart
    #: runs this instead of re-spawning and then waits for health as usual.
    reload: str | None = None
    #: A command that must succeed before any restart of this service, run the
    #: same way. A failure leaves the running service untouched.
    preflight: str | None = None
    #: Seconds a restart waits for this service to be healthy again.
    ready_timeout: int | None = None
    #: Installs what the service depends on (a venv, packages). Kept apart from
    #: `build` so building does not reinstall every dependency each time.
    setup: str | None = None


@dataclass
class DepSpec:
    name: str
    compose_file: str
    compose_service: str


@dataclass
class EnvRequirement:
    """A declared env var the app needs (key + metadata, NO value). The builder
    writes these into the manifest; the operator fills values via the Environment
    panel; secrets_store holds the values. Schema synthesized from Heroku app.json
    + Render/Railway + the Replit ask_secrets pattern (a how_to_obtain pointer is
    the AI-builder differentiator)."""
    key: str
    description: str = ""
    required: bool = True
    secret: bool = True               # true → write-only in the UI; false → plain config
    example: str = ""                 # format hint, never a real value
    how_to_obtain: str = ""           # where/how to get it (URL or short steps)
    service: str = "*"                # which service needs it ("*" = all)
    group: str = ""                   # optional UI bundle (e.g. "google-oauth")

    def applies_to(self, service_name: str) -> bool:
        return self.service in ("", "*", None) or self.service == service_name


@dataclass
class AppCommand:
    """A whole-app command the app declares for one of the operation verbs
    (`test`, a check, `migrate`): what to run, where, and its limits."""
    command: str
    name: str = ""
    cwd: str = "."
    timeout_s: int = 1800
    memory: str = "8G"


@dataclass
class AppManifest:
    name: str
    services: list[ServiceSpec]
    deps: list[DepSpec] = field(default_factory=list)
    env_requirements: list[EnvRequirement] = field(default_factory=list)
    detected: bool = False   # True if auto-detected (no workshop.app.json on disk)
    test: AppCommand | None = None
    checks: list[AppCommand] = field(default_factory=list)
    migrate: AppCommand | None = None


def _command_from_raw(raw, name: str = "") -> AppCommand | None:
    """A declared command: a bare string, or {command, name, cwd, timeout_s, memory}."""
    if isinstance(raw, str) and raw.strip():
        return AppCommand(command=raw.strip(), name=name)
    if not isinstance(raw, dict) or not str(raw.get("command") or "").strip():
        return None
    timeout = str(raw.get("timeout_s") or "").strip()
    return AppCommand(command=str(raw["command"]).strip(),
                      name=str(raw.get("name") or name),
                      cwd=str(raw.get("cwd") or "."),
                      timeout_s=int(timeout) if timeout.isdigit() else 1800,
                      memory=str(raw.get("memory") or "8G"))


# ---- project dir + manifest resolution --------------------------------------

def _project_dir(slug: str) -> Path:
    try:
        data = json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8")) if PROJECTS_REGISTRY.exists() else {}
    except (OSError, json.JSONDecodeError) as e:
        raise AppError(f"could not read projects registry: {e}", status=500)
    rec = (data.get("projects") or {}).get(slug) if isinstance(data, dict) else None
    if not rec:
        raise AppError(f"project not found: {slug}", status=404)
    try:
        # One resolver for the tree, namespace-correct: see paths.project_dir.
        return paths.project_dir(slug, rec)
    except paths.TreeUnresolvable as e:
        raise AppError(str(e), status=400) from e


def _read_manifest_file(project_dir: Path) -> dict | None:
    for name in MANIFEST_NAMES:
        p = project_dir / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            # Tolerate //-comments in a .jsonc-style file.
            if name.endswith("jsonc"):
                text = "\n".join(
                    line for line in text.splitlines()
                    if not line.lstrip().startswith("//")
                )
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as e:
            raise AppError(f"manifest {name} is malformed: {e}", status=400)
    return None


def _spec_from_raw(raw: dict, idx: int) -> ServiceSpec:
    name = str(raw.get("name") or f"service-{idx}")
    start = str(raw.get("start") or "").strip()
    if not start:
        raise AppError(f"service '{name}' has no start command", status=400)
    pinned_port = int(raw["port"]) if str(raw.get("port") or "").strip().isdigit() else None
    if pinned_port is not None:
        port_registry.reserve_pinned(pinned_port)
    return ServiceSpec(
        name=name,
        start=start,
        cwd=str(raw.get("cwd") or "."),
        build=(str(raw["build"]).strip() if raw.get("build") else None),
        port_env=str(raw.get("port_env") or "PORT"),
        port=pinned_port,
        health=(str(raw["health"]) if raw.get("health") else None),
        expose=bool(raw.get("expose", False)),
        label=str(raw.get("label") or "").strip() or None,
        enabled=bool(raw.get("enabled", True)),
        disabled_reason=(str(raw["_disabled_reason"]).strip() if raw.get("_disabled_reason") else None),
        depends_on=[str(d) for d in (raw.get("depends_on") or []) if isinstance(d, str)],
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        setup=(str(raw["setup"]).strip() if raw.get("setup") else None),
        reload=(str(raw["reload"]).strip() if raw.get("reload") else None),
        preflight=(str(raw["preflight"]).strip() if raw.get("preflight") else None),
        ready_timeout=(int(raw["ready_timeout"])
                       if str(raw.get("ready_timeout") or "").strip().isdigit() else None),
    )


def _env_requirements_from_raw(raw: dict) -> list[EnvRequirement]:
    """Parse the manifest's optional `env_requirements` list. Tolerant: skips
    entries without a `key`; a how_to_obtain object {url,steps} is flattened to a
    short string."""
    out: list[EnvRequirement] = []
    for item in (raw.get("env_requirements") or []):
        if not isinstance(item, dict) or not item.get("key"):
            continue
        how = item.get("how_to_obtain")
        if isinstance(how, dict):
            steps = str(how.get("steps") or "").strip()
            url = str(how.get("url") or "").strip()
            if steps and url and not steps.endswith((".", ":", "!", "?")):
                steps += "."
            how = " ".join(p for p in (steps, url) if p)
        out.append(EnvRequirement(
            key=str(item["key"]).strip(),
            description=str(item.get("description") or "").strip(),
            required=bool(item.get("required", True)),
            secret=bool(item.get("secret", True)),
            example=str(item.get("example") or "").strip(),
            how_to_obtain=str(how or "").strip(),
            service=str(item.get("service") or "*").strip() or "*",
            group=str(item.get("group") or "").strip(),
        ))
    return out


def _autodetect(project_dir: Path) -> AppManifest:
    """Build a manifest when none is declared.

    1. A `deploy.config.yaml` with a `services:` map (a multi-service app's shape) → one service
       per entry, using its `path` + `start_command`/`build_command`.
    2. Else a single service from dev_servers.suggest_command (back-compat with the
       single-process projects local_runner handled).
    """
    dc = project_dir / "deploy.config.yaml"
    if dc.exists():
        services = _services_from_deploy_config(dc)
        if services:
            return AppManifest(name=project_dir.name, services=services, detected=True)

    try:
        cmd = local_runner.resolve_command(str(project_dir))
    except local_runner.DeployError as e:
        # No declared manifest and nothing auto-detectable to run. Normalize to
        # AppError so every caller (manifest_dict, start_app, the route) handles a
        # not-runnable project uniformly instead of a 500 leaking a DeployError.
        raise AppError(str(e), status=400) from e
    return AppManifest(
        name=project_dir.name,
        services=[ServiceSpec(name="app", start=cmd, cwd=".", expose=True)],
        detected=True,
    )


def _services_from_deploy_config(path: Path) -> list[ServiceSpec]:
    """Parse the `services:` block of a deploy.config.yaml into ServiceSpecs.

    Uses PyYAML if available, else a tiny indentation parser for the simple
    two-level shape we expect. Frontend services (those that build a static
    bundle / run a vite dev server) are marked `expose` so the UI surface is the
    one reflected as the app URL."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = _mini_yaml(path.read_text(encoding="utf-8"))
    svc_map = data.get("services") if isinstance(data, dict) else None
    if not isinstance(svc_map, dict):
        return []
    out: list[ServiceSpec] = []
    for name, body in svc_map.items():
        if not isinstance(body, dict):
            continue
        sub = str(body.get("path") or ".").lstrip("./") or "."
        start = str(body.get("start_command") or "").strip()
        build = str(body.get("build_command") or "").strip() or None
        depends = [str(d) for d in (body.get("depends_on") or []) if isinstance(d, str)]
        # If the deploy config gives no start command (e.g. a Vercel frontend that
        # is normally a static deploy), fall back to a dev command per package.json.
        if not start:
            detected = local_runner.dev_servers.suggest_command(str((path.parent / sub)))
            start = (detected.get("command") or "").replace("$WORKSHOP_DEV_PORT", "$PORT").strip()
        if not start:
            continue
        expose = "frontend" in str(name).lower() or "web" in str(name).lower() or "ui" in str(name).lower()
        out.append(ServiceSpec(
            name=str(name),
            start=start,
            cwd=sub,
            build=build,
            health=(str(body.get("healthcheck")) if body.get("healthcheck") else None),
            expose=expose,
            depends_on=depends,
        ))
    return out


def _mini_yaml(text: str) -> dict:
    """Minimal YAML reader for the flat-ish deploy.config shape (no PyYAML dep).
    Handles nested maps by indentation and `key: value` scalars + `[a, b]` lists.
    Good enough for services.<name>.{path,start_command,build_command,depends_on}."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            if val.startswith("[") and val.endswith("]"):
                parent[key] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
            else:
                parent[key] = val.strip().strip('"').strip("'")
    return root


def resolve_manifest(slug: str) -> AppManifest:
    """Resolve a project's app manifest: the declared workshop.app.json, else an
    auto-detected one. Raises AppError(400/404) when neither yields a runnable
    service."""
    project_dir = _project_dir(slug)
    raw = _read_manifest_file(project_dir)
    if raw is None:
        return _autodetect(project_dir)
    raw_services = raw.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise AppError("manifest has no `services` list", status=400)
    services = [_spec_from_raw(s, i) for i, s in enumerate(raw_services) if isinstance(s, dict)]
    deps = [
        DepSpec(
            name=str(d.get("name") or "dep"),
            compose_file=str(d.get("compose_file") or "docker-compose.yml"),
            compose_service=str(d.get("compose_service") or d.get("name") or ""),
        )
        for d in (raw.get("deps") or []) if isinstance(d, dict)
    ]
    if not services:
        raise AppError("manifest `services` had no valid entries", status=400)
    # If exactly one service and none marked expose, it is the surface.
    if len(services) == 1 and not any(s.expose for s in services):
        services[0].expose = True
    checks = [c for c in (_command_from_raw(r, f"check-{i + 1}")
                          for i, r in enumerate(raw.get("checks") or [])) if c]
    return AppManifest(
        name=str(raw.get("name") or slug), services=services, deps=deps,
        env_requirements=_env_requirements_from_raw(raw),
        test=_command_from_raw(raw.get("test"), "test"),
        checks=checks,
        migrate=_command_from_raw(raw.get("migrate"), "migrate"),
    )


def _topo_order(services: list[ServiceSpec]) -> list[ServiceSpec]:
    """Order services so dependencies start first. Unknown / cyclic deps are
    tolerated (a service is emitted once all its KNOWN deps are emitted, and any
    remaining are flushed in declared order) — we never refuse to start over a
    dependency typo."""
    by_name = {s.name: s for s in services}
    emitted: list[str] = []
    out: list[ServiceSpec] = []

    def visit(s: ServiceSpec, seen: set[str]) -> None:
        if s.name in emitted or s.name in seen:
            return
        seen.add(s.name)
        for dep in s.depends_on:
            if dep in by_name:
                visit(by_name[dep], seen)
        if s.name not in emitted:
            emitted.append(s.name)
            out.append(s)

    for s in services:
        visit(s, set())
    return out


# ---- persisted state machine ------------------------------------------------
# In-memory STATE is authoritative for live pids; mirrored to STATE_STORE so the
# UI + monitor see it after a backend restart (pids from before a restart are
# reconciled as dead, since we did not spawn them in this process).

_STATE: dict[str, dict] = {}
_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCK = threading.Lock()


def _slug_lock(slug: str) -> threading.Lock:
    with _STATE_LOCK:
        return _LOCKS.setdefault(slug, threading.Lock())


_PERSIST_LOCK = threading.Lock()


def _persist_state() -> None:
    """Write the whole of _STATE to STATE_STORE. Every bring-up thread calls
    this, several at once at boot, so the snapshot, write and rename happen
    under one lock: a writer that takes the lock later snapshots later state,
    so the last rename is always the newest state. The temp file is unique per
    write, so even a writer outside this process cannot rename it away."""
    with _PERSIST_LOCK:
        tmp: str | None = None
        try:
            # The compact dump runs in C without releasing the GIL, so it is a
            # consistent snapshot while other threads mutate their own apps.
            # The pretty dump of the copy can then take its time.
            snapshot = json.loads(json.dumps(_STATE))
            STATE_STORE.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=STATE_STORE.parent,
                                       prefix=STATE_STORE.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, indent=2) + "\n")
            os.replace(tmp, STATE_STORE)
            tmp = None
        except OSError as e:
            log.warning("could not persist app state: %s", e)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


BOOT_STAMP = STATE_STORE.with_suffix(".boot")


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _load_persisted() -> None:
    """Load STATE_STORE into memory once at import/startup. Pids from a previous
    process are NOT live here, so reconcile() marks them stopped — unless the host
    rebooted in between, when the kernel reissues low pids and a recorded one can
    land on an unrelated process (or thread) and read as alive. So a state file
    written under a different boot id has every pid dropped and every running
    service marked failed, which is what makes the monitor bring it back."""
    if not STATE_STORE.exists():
        return
    try:
        data = json.loads(STATE_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    boot = _boot_id()
    try:
        stamped = BOOT_STAMP.read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    if boot and stamped and stamped != boot:
        for app in data.values():
            if not isinstance(app, dict):
                continue
            for s in (app.get("services") or {}).values():
                if s.get("pid"):
                    s["pid"] = None
                if s.get("status") in ("running", "starting"):
                    s["status"] = "failed"
                    s["error"] = "host rebooted"
        log.info("app state predates this boot; dropped all recorded pids")
    # No bring-up survives the process that ran it, so a `starting` phase on
    # disk is a build that was cut off. Left in place it reads as in progress
    # forever, and start_app and restart_service both stand down for it.
    for app in data.values():
        if not isinstance(app, dict) or app.get("phase") != "starting":
            continue
        app["phase"] = "failed"
        for s in (app.get("services") or {}).values():
            if s.get("status") in ("pending", "building", "starting"):
                s["status"] = "failed"
                s["error"] = "bring-up interrupted"
    _STATE.update(data)
    if boot:
        try:
            BOOT_STAMP.write_text(boot + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("could not write boot stamp: %s", e)


def _app_owner(slug: str, svc: str) -> str:
    return f"app:{slug}/{svc}"


def _alive(pid: int | None) -> bool:
    """Zombie-aware liveness (port_registry.process_alive): a child that exited but
    has not been reaped is a zombie that os.kill(pid,0) still reports alive — that
    would hide a crash forever. process_alive treats zombies as dead and reaps them."""
    return port_registry.process_alive(pid)


# ---- env + spawn ------------------------------------------------------------

def _secret_env_for_service(slug: str, spec_name: str) -> dict[str, str]:
    """The stored secret VALUES this service should receive, scoped by each env
    requirement's `service` field. Loads from secrets_store; returns {} if the
    manifest can't be resolved. Runner-only (handles real values)."""
    from services import secrets_store
    try:
        man = resolve_manifest(slug)
    except AppError:
        return {}
    keys = [r.key for r in man.env_requirements if r.applies_to(spec_name)]
    return secrets_store.env_for(slug, keys) if keys else {}


def _missing_required_env(slug: str, spec_name: str, manifest: AppManifest) -> list[str]:
    """Required env requirement keys for this service that have NO stored value.
    Presence-only (never reads values)."""
    from services import secrets_store
    reqs = [r for r in manifest.env_requirements if r.required and r.applies_to(spec_name)]
    if not reqs:
        return []
    st = secrets_store.status(slug, [r.key for r in reqs])
    return [k for k, present in st.items() if not present]


def _service_env(port: int, spec: ServiceSpec, peers: dict[str, dict],
                 secrets: dict[str, str] | None = None) -> dict[str, str]:
    """Scoped env (allowlist + PORT) plus this service's manifest env, with peer
    service URLs available two ways:

      - auto-injected `<DEP>_URL` for each declared depends_on (generic);
      - `${<service>.url}` / `${<service>.port}` placeholders in any manifest env
        value, substituted from the peer map (so e.g. a Vite frontend can set
        `VITE_API_URL=${core.url}` — the exact var name it requires).

    `peers` maps service name → {"url": ..., "port": ...} for every service in the
    app (ports are pre-allocated, so forward references resolve too)."""
    env = local_runner.scoped_env(port)
    if spec.port_env and spec.port_env != "PORT":
        env[spec.port_env] = str(port)
    for dep_name in spec.depends_on:
        peer = peers.get(dep_name)
        if peer and peer.get("url"):
            env[f"{dep_name.upper().replace('-', '_')}_URL"] = peer["url"]
    for k, v in spec.env.items():
        out = v
        for name, peer in peers.items():
            out = out.replace(f"${{{name}.url}}", str(peer.get("url") or ""))
            out = out.replace(f"${{{name}.port}}", str(peer.get("port") or ""))
        env[k] = out
    # Declared secret values last (distinct key names; the operator-supplied
    # GEMINI_API_KEY / GOOGLE_* etc.). Injected only into the process env here —
    # never logged, never written to the manifest.
    if secrets:
        env.update(secrets)
    return env


def _build_log(slug: str, svc: str) -> Path:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return LOG_ROOT / f"{slug}__{svc}.log"


# App logs are the child's own stdout (opened O_APPEND) and otherwise grow without
# bound — one tight error loop once produced a 172GB log that filled the disk. Big
# logs are fine; monster logs are not. The monitor calls cap_logs() each pass to
# keep only the most recent LOG_KEEP bytes once a file passes LOG_CAP.
LOG_CAP_BYTES = int(os.environ.get("WORKSHOP_APP_LOG_CAP_MB", "512")) * 1024 * 1024
LOG_KEEP_BYTES = int(os.environ.get("WORKSHOP_APP_LOG_KEEP_MB", "64")) * 1024 * 1024


def cap_logs() -> list[str]:
    """Trim any app log past LOG_CAP down to its last LOG_KEEP bytes. Safe while the
    child keeps appending (O_APPEND writes at EOF; we only rewrite the head then
    truncate). Returns the names trimmed. Never raises — a logging guard must not
    take down the monitor."""
    trimmed: list[str] = []
    if not LOG_ROOT.exists():
        return trimmed
    for p in sorted(LOG_ROOT.glob("*.log")):
        try:
            sz = p.stat().st_size
            if sz <= LOG_CAP_BYTES:
                continue
            with open(p, "r+b") as f:
                f.seek(max(0, sz - LOG_KEEP_BYTES))
                tail = f.read()
                f.seek(0)
                f.write(f"[log capped: dropped {sz - len(tail)} older bytes]\n".encode() + tail)
                f.truncate()
            trimmed.append(p.name)
        except OSError:
            continue
    return trimmed


def _service_healthy(port: int, health: str | None, timeout: float = 1.5) -> bool:
    """Readiness probe. With a health path, an HTTP status < 500 counts as healthy.
    Without one, a successful TCP connect to the port counts."""
    import socket as _socket
    if health:
        url = f"http://127.0.0.1:{port}{health if health.startswith('/') else '/' + health}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status < 500
        except urllib.error.HTTPError as e:
            return e.code < 500
        except Exception:
            return False
    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_listening_on(port: int) -> int | None:
    """The pid bound to 127.0.0.1:`port`, or None. Reads the OS, not the registry."""
    try:
        out = subprocess.run(["ss", "-lntpH", f"sport = :{port}"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    marker = "pid="
    if marker not in out:
        return None
    try:
        return int(out.split(marker, 1)[1].split(",", 1)[0])
    except (ValueError, IndexError):
        return None


def _evict_squatter(slug: str, svc_name: str, port: int) -> None:
    """Clear an UNTRACKED process off this service's pinned port.

    A pin means the service will not move, which is the point - and it turns one
    failure into a deadlock unless this exists. One app's api ended up with a
    healthy uvicorn on 8700 that the runner had not spawned and did not know
    about: the runner called its own service `failed` (the pid it tracked was
    gone), tried to restart, could not have its own port, and would have parked
    as crashlooping while a working server sat on the address. The app 409s
    throughout, because the proxy believes the runner, not the socket.

    Only a process the runner does NOT currently track as a live service is
    evicted, so this can never kill a healthy sibling. SIGTERM, then SIGKILL.
    """
    pid = _pid_listening_on(port)
    if not pid:
        return
    tracked = {
        s.get("pid")
        for app in _STATE.values()
        for s in (app.get("services") or {}).values()
        if s.get("pid") and _alive(s.get("pid"))
    }
    if pid in tracked:
        return  # a live service the runner owns; the caller's own stop path handles it
    log.warning("evicting untracked pid %s from %s/%s's pinned port %d",
                pid, slug, svc_name, port)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(50):
            if not _alive(pid):
                return
            time.sleep(0.1)


def _serving_group(port: int, health: str | None, service_dir: Path) -> int | None:
    """The process already serving a service, or None.

    Serving means the port passes the service's readiness probe AND the process
    bound to it runs from the service's own directory, so another app, or
    another installation's copy, on the same port is never mistaken for it.
    Returns the process group leader when there is one, because that is what the
    runner tracks and stops."""
    if not _service_healthy(port, health):
        return None
    pid = _pid_listening_on(port)
    if not pid:
        return None
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        pgid = os.getpgid(pid)
    except OSError:
        return None
    root = str(service_dir.resolve())
    if not (cwd == root or cwd.startswith(root + os.sep)):
        return None
    if pgid != os.getpgid(0) and _alive(pgid):
        return pgid
    return pid


def _adopt(slug: str, spec: ServiceSpec, sstate: dict, project_dir: Path) -> bool:
    """Take over a service that is serving but that the runner does not track.

    THE RECORD IS A CACHE OF WHAT IS RUNNING; THE SOCKET IS THE TRUTH. A reboot
    restore overlapping the monitor, two starts from two tabs, and a restart
    that raced a bring-up have each left a working service recorded `failed`.
    Everything downstream believed the record: the screen showed "Starting"
    over an app that was up in another tab, the proxy refused it with 409, and
    the next start reaped the working copy to spawn another. Adopting makes the
    record agree with the socket, so none of those paths ever sees a phantom
    outage."""
    port = sstate.get("port") or spec.port
    if not port:
        return False
    pid = _serving_group(port, spec.health, project_dir / spec.cwd)
    if not pid or not port_registry.adopt(_app_owner(slug, spec.name), "app", port, pid):
        return False
    sstate.update(pid=pid, port=port, url=f"http://127.0.0.1:{port}",
                  status="running", error=None,
                  started_at=sstate.get("started_at") or time.time())
    log.info("adopted %s/%s, already serving on %d as pid %s", slug, spec.name, port, pid)
    return True


def _adopt_untracked(slug: str, app: dict) -> None:
    """Adopt every service of `app` whose tracked process is gone but which is
    still serving. A bring-up in progress owns its services, so it is left alone."""
    if app.get("phase") == "starting" or _slug_lock(slug).locked():
        return
    stale = [name for name, s in (app.get("services") or {}).items()
             if s.get("status") not in ("disabled", "building", "starting")
             and not _alive(s.get("pid"))]
    if not stale:
        return
    try:
        manifest = resolve_manifest(slug)
        project_dir = _project_dir(slug)
    except AppError:
        return
    specs = {s.name: s for s in manifest.services if s.enabled}
    adopted = [name for name in stale if name in specs
               and _adopt(slug, specs[name], app["services"][name], project_dir)]
    if adopted:
        _persist_state()


#: Set to "0" to spawn services as plain children again (the old behaviour).
DETACH_SERVICES = os.environ.get("WORKSHOP_DETACH_SERVICES", "1") != "0"


#: Vars systemd-run needs to reach the user bus. The service env is an ALLOWLIST
#: (that is the point of `_service_env`), so these have to be added back
#: deliberately for the wrapper - not for the service, which never sees the bus.
_BUS_ENV = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")

#: None = not probed yet. Set once per process by `_detachment_works`.
_DETACH_OK: bool | None = None


def _detachment_works(env: dict[str, str]) -> bool:
    """Can we actually spawn into a transient scope, right now, with this env?

    ASKED RATHER THAN ASSUMED, because assuming it cost an outage. `systemd-run
    --user` needs a session bus, the scoped service env does not carry one, and
    the failure is not a fallback - it is `Failed to connect to bus: No medium
    found` and an instant exit. Every service the runner started died on spawn,
    which is a far worse failure than the cgroup coupling this was fixing.

    So the capability is PROVED once with a throwaway command before any real
    service depends on it, and a box that cannot do it degrades to the old
    behaviour instead of failing to run anything.
    """
    global _DETACH_OK
    if _DETACH_OK is None:
        if not shutil.which("systemd-run") or not env.get("DBUS_SESSION_BUS_ADDRESS"):
            _DETACH_OK = False
        else:
            try:
                probe = subprocess.run(
                    ["systemd-run", "--user", "--scope", "--collect", "--quiet",
                     "/bin/true"],
                    env=env, capture_output=True, timeout=15)
                _DETACH_OK = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _DETACH_OK = False
        if not _DETACH_OK:
            log.warning("cannot spawn services into their own scope; falling back "
                        "to plain children (they will die with the backend)")
    return _DETACH_OK


def _detached_argv(argv: list[str], env: dict[str, str] | None = None) -> list[str]:
    """Wrap a start command so the service gets its OWN cgroup.

    `start_new_session=True` gives a new session and process group, and neither
    of those is what systemd kills by. Stopping a unit kills its whole CGROUP,
    and a plain child of the backend is in the backend's cgroup - so every
    `systemctl restart control-panel@workshop-backend` also killed every app the
    runner was supervising. That is a supervisor taking down the things it
    exists to keep up, and on 2026-08-17 it did exactly that to one app three
    times in one hour while the operator was using it.

    A transient scope puts the process under `app.slice` instead, as a sibling
    of the backend rather than a child. The pid is preserved (systemd-run
    `--scope` execs in place), so pid liveness, `killpg` and the port registry
    all keep working unchanged, and the runner re-adopts the still-running
    service by pid when it loads its persisted state.

    Falls back to the bare command when systemd-run is unavailable, because a
    box without it should still run apps - just with the old coupling.
    """
    if not DETACH_SERVICES or not _detachment_works(env or {}):
        return argv
    return ["systemd-run", "--user", "--scope", "--collect", "--quiet", *argv]


def _spawn_service(slug: str, spec: ServiceSpec, project_dir: Path, port: int,
                   peers: dict[str, dict]) -> int:
    """Spawn one service's start command as a process group, env scoped + injected.
    Returns the child pid. Raises AppError on launch failure."""
    cwd = (project_dir / spec.cwd).resolve()
    if not str(cwd).startswith(str(project_dir.resolve())):
        raise AppError(f"service '{spec.name}' cwd escapes project dir", status=400)
    env = _service_env(port, spec, peers, secrets=_secret_env_for_service(slug, spec.name))
    # Identity + the path this service is reachable at through the backend reverse
    # proxy. A web service can use WORKSHOP_PROXY_BASE as its base path (e.g. Vite
    # `--base $WORKSHOP_PROXY_BASE`) so its assets resolve when viewed over the
    # tailnet through the proxy.
    env["WORKSHOP_APP_SLUG"] = slug
    env["WORKSHOP_SERVICE_NAME"] = spec.name
    env["WORKSHOP_PROXY_BASE"] = f"/base-apps/run/{slug}/proxy/{spec.name}/"
    # Where the service reaches the Workshop's own machine-local API (GPU claims).
    api_port = (os.environ.get("WORKSHOP_BACKEND_PORT") or "8000").strip()
    env["WORKSHOP_API_URL"] = f"http://127.0.0.1:{api_port}"
    # The wrapper's own requirements, not the service's: systemd-run talks to the
    # user bus, and `_service_env` allowlists the environment.
    spawn_env = dict(env)
    for key in _BUS_ENV:
        value = os.environ.get(key)
        if value:
            spawn_env[key] = value
    log_path = _build_log(slug, spec.name)
    try:
        with open(log_path, "ab", buffering=0) as logf:
            logf.write(f"\n--- start {spec.name} on :{port} @ {time.ctime()} ---\n".encode())
            proc = subprocess.Popen(
                _detached_argv(["/bin/bash", "-c", spec.start], spawn_env),
                cwd=str(cwd),
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=spawn_env,
            )
    except (OSError, ValueError) as e:
        raise AppError(f"failed to launch service '{spec.name}': {e}", status=500)
    return proc.pid


def _run_build(slug: str, spec: ServiceSpec, project_dir: Path,
               peers: dict[str, dict]) -> tuple[bool, str]:
    """Run a service's build command (blocking, in the worker thread). Returns
    (ok, message). Output is appended to the service log. Build env is the full
    host env (npm/bun caches, PATH, registry creds) PLUS this service's resolved
    manifest env — so a build step that needs config (e.g. a DB migration) sees
    the same values the running service will."""
    if not spec.build:
        return True, "no build step"
    cwd = (project_dir / spec.cwd).resolve()
    log_path = _build_log(slug, spec.name)
    relocated_artifacts.drop_broken(project_dir, cwd)
    build_env = {**os.environ}
    for k, v in spec.env.items():
        out = v
        for name, peer in peers.items():
            out = out.replace(f"${{{name}.url}}", str(peer.get("url") or ""))
            out = out.replace(f"${{{name}.port}}", str(peer.get("port") or ""))
        build_env[k] = out
    # Builds may need the declared secrets too (a migration, a typed-codegen
    # against an API). Same scope as the runtime service.
    build_env.update(_secret_env_for_service(slug, spec.name))
    try:
        with open(log_path, "ab", buffering=0) as logf:
            logf.write(f"\n--- build {spec.name}: {spec.build} @ {time.ctime()} ---\n".encode())
            r = subprocess.run(
                ["/bin/bash", "-c", spec.build],
                cwd=str(cwd),
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=build_env,
                timeout=BUILD_TIMEOUT,
            )
        if r.returncode != 0:
            return False, f"build exited {r.returncode} (see log)"
        return True, "built"
    except subprocess.TimeoutExpired:
        return False, f"build timed out after {BUILD_TIMEOUT}s"
    except (OSError, ValueError) as e:
        return False, f"build could not run: {e}"


def _bring_up_deps(manifest: AppManifest, project_dir: Path) -> list[dict]:
    """docker compose up -d each declared external dep. Best-effort; each result is
    reported so the UI can show a dep that failed to come up."""
    results: list[dict] = []
    for dep in manifest.deps:
        compose = (project_dir / dep.compose_file).resolve()
        if not compose.exists():
            results.append({"name": dep.name, "ok": False, "error": f"compose file missing: {dep.compose_file}"})
            continue
        try:
            r = subprocess.run(
                ["docker", "compose", "-f", str(compose), "up", "-d", dep.compose_service],
                cwd=str(project_dir), capture_output=True, text=True, timeout=300,
            )
            ok = r.returncode == 0
            results.append({"name": dep.name, "ok": ok,
                            "error": None if ok else (r.stderr or r.stdout or "")[:300]})
        except subprocess.TimeoutExpired:
            results.append({"name": dep.name, "ok": False, "error": "docker compose timed out"})
        except (OSError, FileNotFoundError) as e:
            results.append({"name": dep.name, "ok": False, "error": f"docker unavailable: {e}"})
    return results


# ---- lifecycle --------------------------------------------------------------

_RUNTIME_HINTS = ("node", "bun", "vite", "python", "python3", "uvicorn", "deno", "gunicorn")


def _reap_project_orphans(project_dir: Path, keep_pids: set[int]) -> list[int]:
    """Kill leftover service processes for this project that we no longer track.

    A backend restart detaches previously-spawned children (they keep running on
    their old port), so a later start picks a new port and the old ones pile up as
    orphans. On stop/start we reap them: a process is reaped only if its cwd is
    under the project dir AND its command is a known runtime (node/bun/python/...),
    so an operator shell that happens to be cd'd into the project is never killed.
    `keep_pids` (currently-tracked children) are spared."""
    root = str(project_dir.resolve())
    killed: list[int] = []
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return killed
    for pid in pids:
        if pid in keep_pids or pid == os.getpid():
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if not (cwd == root or cwd.startswith(root + os.sep)):
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\x00")
        except OSError:
            continue
        first = os.path.basename((argv[0] or b"").decode("utf-8", "replace")).lower()
        if not any(h in first for h in _RUNTIME_HINTS):
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            killed.append(pid)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass
    if killed:
        log.info("reaped %d orphan process(es) under %s: %s", len(killed), root, killed)
    return killed


def _init_service_state(spec: ServiceSpec, port: int | None) -> dict:
    return {
        "name": spec.name,
        "status": "pending",   # pending|building|starting|running|failed|stopped|disabled
        "pid": None,
        "port": port,
        "url": f"http://127.0.0.1:{port}" if port else None,
        "health": spec.health,
        "expose": spec.expose,
        "error": None,
        "restarts": 0,
        "started_at": None,
    }


def _bring_up(slug: str, manifest: AppManifest) -> None:
    """Worker-thread body: deps → per-service build → spawn, updating state as it
    goes. Holds the per-slug lock for the whole bring-up so two starts can't race."""
    lock = _slug_lock(slug)
    with lock:
        try:
            project_dir = _project_dir(slug)
        except AppError as e:
            app = _STATE.get(slug, {})
            app["error"] = e.message
            app["phase"] = "failed"
            _persist_state()
            return

        # Clean slate: kill any leftover service processes for this project (from a
        # prior backend run / a previous start) so we never accumulate orphans or
        # double-spawn. Then a fresh, predictable bring-up.
        _reap_project_orphans(project_dir, set())

        dep_results = _bring_up_deps(manifest, project_dir)
        _STATE[slug]["deps"] = dep_results
        _persist_state()

        enabled = [s for s in manifest.services if s.enabled]
        ordered = _topo_order(enabled)

        # Pre-allocate every service's port up front so peer references
        # (`${core.url}`, auto-injected <DEP>_URL) resolve even for a service that
        # has not spawned yet, and so ports are stable for the whole bring-up.
        peers: dict[str, dict] = {}
        for spec in enabled:
            sstate = _STATE[slug]["services"].setdefault(spec.name, _init_service_state(spec, None))
            # ALWAYS allocate through the registry (idempotent: returns the same
            # port if still valid, else a fresh one). A persisted sstate["port"]
            # from a prior run must not short-circuit this, or the central registry
            # loses track of the claim after a stop()/restart freed it.
            try:
                port = port_registry.allocate(
                    _app_owner(slug, spec.name), "app",
                    preferred=spec.port or sstate.get("port"),
                    pinned=spec.port is not None)
            except RuntimeError as e:
                sstate["status"] = "failed"
                sstate["error"] = str(e)
                continue
            sstate["port"] = port
            sstate["url"] = f"http://127.0.0.1:{port}"
            peers[spec.name] = {"url": sstate["url"], "port": sstate["port"]}
        _persist_state()

        for spec in ordered:
            sstate = _STATE[slug]["services"][spec.name]
            if sstate.get("port") is None:
                continue  # port allocation already failed above

            # Required-secret gate: refuse to spawn a service whose required env
            # is unfilled — surface a clear, actionable error instead of letting
            # it crash-loop on a missing key.
            missing = _missing_required_env(slug, spec.name, manifest)
            if missing:
                sstate["status"] = "failed"
                sstate["error"] = ("Missing required env: " + ", ".join(missing)
                                   + ". Set it in the project's Environment panel.")
                _persist_state()
                continue

            sstate["status"] = "building"
            sstate["error"] = None
            _persist_state()
            ok, msg = _run_build(slug, spec, project_dir, peers)
            if not ok:
                sstate["status"] = "failed"
                sstate["error"] = msg
                _persist_state()
                continue

            sstate["status"] = "starting"
            _persist_state()
            try:
                pid = _spawn_service(slug, spec, project_dir, sstate["port"], peers)
            except AppError as e:
                sstate["status"] = "failed"
                sstate["error"] = e.message
                _persist_state()
                continue
            sstate["pid"] = pid
            sstate["status"] = "running"
            sstate["started_at"] = time.time()
            port_registry.set_pid(_app_owner(slug, spec.name), pid)
            _persist_state()

        _STATE[slug]["phase"] = "up"
        _reflect_primary_surface(slug, manifest)
        _persist_state()


def _reflect_primary_surface(slug: str, manifest: AppManifest) -> None:
    """Reflect the exposed service's URL into projects.json surfaces.standalone so
    the Projects hub shows where to open the running app."""
    primary = next((s for s in manifest.services if s.expose and s.enabled), None)
    if primary is None:
        return
    sstate = _STATE.get(slug, {}).get("services", {}).get(primary.name)
    if not sstate or sstate.get("status") != "running":
        return
    local_runner._reflect_surface(slug, {
        "runner": "app",
        "service": primary.name,
        "port": sstate.get("port"),
        "url": sstate.get("url"),
        "started_at": sstate.get("started_at"),
    })


_SELF_MSG = ("This project is Workshop itself, not a separate app you can start. "
             "The frontend updates on save; backend changes need a backend restart.")


# ---- container backend (docker compose) -------------------------------------
# When the backend process can reach docker, an app runs as containers via a
# generated compose file (compose_gen) driven by container_runtime — docker owns
# ports, restart-on-crash, health, and cleanup, so the port_registry / orphan-reaper
# / monitor apparatus below is bypassed. Until docker is reachable (it needs the
# `docker` group, applied on a fresh login/reboot), start_app uses the subprocess
# path so a running app is never interrupted mid-transition.

def _use_containers() -> bool:
    """Which backend start_app uses, from `WORKSHOP_APP_BACKEND`: `auto` (the
    default) containerizes whenever docker is reachable; `subprocess` and
    `container` pin one. A deployment whose apps run straight off the host
    filesystem must be able to say so, because "docker is reachable" is not the
    same question as "these apps are built to run in a container", and auto
    answering the first silently moves working apps onto a path that has never
    run them."""
    mode = os.environ.get("WORKSHOP_APP_BACKEND", "auto").strip().lower()
    if mode == "subprocess":
        return False
    if mode == "container":
        return True
    return container_runtime.available()


def _compose_path_for(slug: str) -> Path:
    return compose_gen.COMPOSE_ROOT / f"{slug}.yml"


# container state → our five-status vocabulary
_CSTATE = {
    "running": "running", "restarting": "crashlooping", "exited": "failed",
    "dead": "failed", "created": "starting", "paused": "stopped", "removing": "stopped",
}


def _container_services(slug: str, manifest: AppManifest, project_dir: Path) -> tuple[list[dict], list[dict]]:
    """Live per-service + per-dep status from `docker compose ps`, mapped to the same
    shapes app_status returns for the subprocess backend. Missing rows fall back to
    the persisted intent (disabled / missing-env failure)."""
    compose_path = _compose_path_for(slug)
    rows: dict[str, dict] = {}
    if compose_path.exists():
        try:
            for r in container_runtime.ps(slug, compose_path, cwd=project_dir):
                if r.get("service"):
                    rows[r["service"]] = r
        except container_runtime.ComposeError:
            pass
    # Restart history, which `ps` does not carry. Docker counts only restart-policy
    # restarts here, so this is crashes and never a deliberate restart.
    chealth = container_runtime.health([r.get("id", "") for r in rows.values()])
    persisted = _STATE.get(slug, {}).get("services", {})
    exposed = next((s for s in manifest.services if s.expose and s.enabled), None)
    services: list[dict] = []
    for spec in manifest.services:
        ps = rows.get(spec.name)
        ps_state = (ps or {}).get("state", "")
        if not spec.enabled:
            status = "disabled"
        elif ps is None:
            # Enabled but not in compose ps: either never started or gated (missing
            # required env). Keep the persisted status if it carries a reason.
            status = persisted.get(spec.name, {}).get("status") or "stopped"
        else:
            status = _CSTATE.get(ps_state, "stopped")
        health = (ps or {}).get("health")
        healthy = None
        if status == "running":
            healthy = True if health in ("healthy", None, "") else False
        port = persisted.get(spec.name, {}).get("port") if spec is exposed else None
        h = chealth.get((ps or {}).get("id", ""), {})
        services.append({
            "name": spec.name,
            "status": status,
            "pid": None,
            "port": port,
            "url": f"http://127.0.0.1:{port}" if port else None,
            "expose": spec.expose,
            "healthy": healthy,
            "error": persisted.get(spec.name, {}).get("error"),
            "restarts": h.get("restarts", 0),
            "oom_killed": h.get("oom_killed", False),
            "exit_code": h.get("exit_code"),
            "started_at": h.get("started_at"),
        })
    deps: list[dict] = []
    for dep in manifest.deps:
        ps = rows.get(dep.name)
        if ps is None:
            deps.append({"name": dep.name, "ok": False, "error": None})
            continue
        ok = ps.get("state") == "running" and ps.get("health") in ("healthy", None, "")
        err = None if ok else f"{ps.get('state')}" + (f" ({ps.get('health')})" if ps.get("health") else "")
        deps.append({"name": dep.name, "ok": ok, "error": err})
    return services, deps


def _container_bring_up(slug: str, manifest: AppManifest) -> None:
    """Worker-thread body for the container backend: generate the compose file and
    bring the enabled services up (compose pulls their linked deps). A service whose
    required env is unfilled is gated out with a clear error, exactly like the
    subprocess path — never upped to crash-loop on a missing key."""
    lock = _slug_lock(slug)
    with lock:
        try:
            project_dir = _project_dir(slug)
        except AppError as e:
            app = _STATE.get(slug, {})
            app["error"] = e.message
            app["phase"] = "failed"
            _persist_state()
            return

        app = _STATE[slug]
        app["backend"] = "container"
        exposed = next((s for s in manifest.services if s.expose and s.enabled), None)
        host_port = 0
        if exposed:
            try:
                host_port = port_registry.allocate(
                    _app_owner(slug, exposed.name), "app",
                    preferred=app.get("services", {}).get(exposed.name, {}).get("port"),
                )
                app["services"].setdefault(exposed.name, {})["port"] = host_port
                app["services"][exposed.name]["url"] = f"http://127.0.0.1:{host_port}"
            except RuntimeError as e:
                app["error"] = f"could not allocate a port: {e}"

        secrets = {s.name: _secret_env_for_service(slug, s.name)
                   for s in manifest.services if s.enabled}
        meta = compose_gen.generate(slug, manifest, project_dir, host_port,
                                    secrets_by_service=secrets)
        app["compose_path"] = str(meta["compose_path"])

        runnable: list[str] = []
        for spec in [s for s in manifest.services if s.enabled]:
            missing = _missing_required_env(slug, spec.name, manifest)
            sstate = app["services"].setdefault(spec.name, _init_service_state(spec, None))
            if missing:
                sstate["status"] = "failed"
                sstate["error"] = ("Missing required env: " + ", ".join(missing)
                                   + ". Set it in the project's Environment panel.")
            else:
                sstate["status"] = "starting"
                sstate["error"] = None
                runnable.append(spec.name)
        _persist_state()

        if runnable:
            ok, out = container_runtime.up(slug, meta["compose_path"], services=runnable,
                                           build=True, cwd=project_dir)
            if not ok:
                app["error"] = out[-400:]

        services, deps = _container_services(slug, manifest, project_dir)
        app["services"] = {s["name"]: {**app["services"].get(s["name"], {}), **s} for s in services}
        app["deps"] = deps
        running = sum(1 for s in services if s["status"] == "running")
        total = sum(1 for s in services if s["status"] != "disabled")
        app["phase"] = "up" if running and running == total else ("partial" if running else "stopped")
        _reflect_primary_surface(slug, manifest)
        _persist_state()


def _container_stop(slug: str) -> None:
    project_dir = None
    try:
        project_dir = _project_dir(slug)
    except AppError:
        pass
    compose_path = _compose_path_for(slug)
    if compose_path.exists():
        try:
            container_runtime.down(slug, compose_path, cwd=project_dir)
        except container_runtime.ComposeError as e:
            log.warning("compose down failed for %s: %s", slug, e.message)


def _run_bring_up(target, slug: str, manifest: AppManifest) -> None:
    """Run a bring-up, and never let one that raised leave the app `starting`:
    that phase is never recomputed, and restart_service stands down for it."""
    try:
        target(slug, manifest)
    finally:
        app = _STATE.get(slug) or {}
        if app.get("phase") == "starting":
            app["phase"] = "failed"
            _persist_state()


def start_app(slug: str) -> dict:
    """Start (or resume) all enabled services of an app. Returns immediately with a
    'starting' snapshot; build + spawn run on a background thread. Idempotent: an
    already-running app returns its status. Sets desired state = running."""
    if is_self_project(slug):
        raise AppError(_SELF_MSG, status=409)
    manifest = resolve_manifest(slug)  # raises AppError
    lock = _slug_lock(slug)
    # Checked and claimed in one step: two tabs opening the same stopped app
    # both passed a bare `locked()` test, and the second bring-up then reaped
    # the services the first had just spawned.
    with _STATE_LOCK:
        app = _STATE.setdefault(slug, {})
        in_progress = lock.locked() or app.get("phase") == "starting"
        if not in_progress:
            app["phase"] = "starting"
    if in_progress:
        return {"slug": slug, **app_status(slug)}
    app["slug"] = slug
    app["desired"] = "running"
    app["manifest_detected"] = manifest.detected
    app.setdefault("services", {})
    for spec in manifest.services:
        if not spec.enabled:
            # Reset disabled services so a stale port/pid from a prior run (when it
            # may have been enabled) does not linger in the status/registry view.
            port_registry.release(_app_owner(slug, spec.name))
            app["services"][spec.name] = {**_init_service_state(spec, None), "status": "disabled"}
        else:
            app["services"].setdefault(spec.name, _init_service_state(spec, None))
    # Record which backend this app is using so status/stop/restart route to the
    # same one for the app's whole lifetime (a mid-run docker availability change
    # must not split an app across both backends).
    app["backend"] = "container" if _use_containers() else "subprocess"
    _persist_state()

    target = _container_bring_up if app["backend"] == "container" else _bring_up
    t = threading.Thread(target=_run_bring_up, args=(target, slug, manifest), daemon=True)
    t.start()
    return {"slug": slug, "phase": "starting", **app_status(slug)}


def _stop_service(slug: str, svc_name: str, sstate: dict) -> None:
    pid = sstate.get("pid")
    if pid and _alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as e:
            log.warning("error stopping %s/%s pid %s: %s", slug, svc_name, pid, e)
    port_registry.release(_app_owner(slug, svc_name))
    sstate["pid"] = None
    sstate["status"] = "stopped"
    sstate["started_at"] = None


def stop_app(slug: str) -> dict:
    """Stop all services of an app (SIGTERM→SIGKILL each process group), release
    their ports, clear the surface, and set desired state = stopped (so the monitor
    won't restart them). Idempotent."""
    if is_self_project(slug):
        raise AppError(_SELF_MSG, status=409)
    app = _STATE.get(slug)
    if not app:
        local_runner._reflect_surface(slug, None)
        return {"slug": slug, "phase": "stopped", "services": []}
    app["desired"] = "stopped"
    if app.get("backend") == "container":
        _container_stop(slug)
        for name, sstate in app.get("services", {}).items():
            port_registry.release(_app_owner(slug, name))
            sstate["pid"] = None
            sstate["status"] = "disabled" if sstate.get("status") == "disabled" else "stopped"
            sstate["started_at"] = None
        app["phase"] = "stopped"
        app["deps"] = []
        local_runner._reflect_surface(slug, None)
        _persist_state()
        return {"slug": slug, **app_status(slug)}
    for name, sstate in app.get("services", {}).items():
        _stop_service(slug, name, sstate)
    # Reap any orphaned service processes too (leftovers from prior runs), so Stop
    # really means everything for this project is down.
    try:
        _reap_project_orphans(_project_dir(slug), set())
    except AppError:
        pass
    app["phase"] = "stopped"
    local_runner._reflect_surface(slug, None)
    _persist_state()
    return {"slug": slug, **app_status(slug)}


def restart_service(slug: str, svc_name: str) -> dict:
    """Re-spawn one already-built service (no rebuild). Used by the monitor when a
    service crashed while the app is desired-running, and exposed for manual use."""
    if is_self_project(slug):
        raise AppError(_SELF_MSG, status=409)
    manifest = resolve_manifest(slug)
    spec = next((s for s in manifest.services if s.name == svc_name), None)
    if spec is None:
        raise AppError(f"no service '{svc_name}' in app '{slug}'", status=404)
    project_dir = _project_dir(slug)
    # A BRING-UP IN PROGRESS WILL SPAWN THIS SERVICE ITSELF. At boot the restore's
    # start_app is still building when the monitor sees the service `failed`;
    # spawning here took the port, the bring-up's own spawn then died on it, and
    # the runner tracked the dead one and parked the service as crashlooping
    # while an orphan it no longer knew about served the port.
    # start_app marks the phase before its thread takes the lock, so the phase
    # covers the moment in between. The lock is HELD for the respawn, not just
    # tested: a test-then-spawn let a start slip in between the two.
    lock = _slug_lock(slug)
    if _STATE.get(slug, {}).get("phase") == "starting" or not lock.acquire(blocking=False):
        return app_status(slug)
    try:
        return _restart_locked(slug, svc_name, spec, manifest, project_dir)
    finally:
        lock.release()


def _restart_locked(slug: str, svc_name: str, spec: ServiceSpec,
                    manifest: AppManifest, project_dir: Path) -> dict:
    app = _STATE.setdefault(slug, {"slug": slug, "services": {}})
    if app.get("backend") == "container":
        compose_path = _compose_path_for(slug)
        if not compose_path.exists():
            raise AppError(f"app '{slug}' is not running", status=409)
        ok, out = container_runtime.restart(slug, compose_path, svc_name, cwd=project_dir)
        if not ok:
            raise AppError(f"restart failed: {out[-200:]}", status=500)
        return app_status(slug)
    sstate = app["services"].setdefault(svc_name, _init_service_state(spec, None))
    if sstate.get("pid") and _alive(sstate["pid"]):
        _stop_service(slug, svc_name, sstate)
    # ASK FOR THE PORT IT ALREADY HAD, exactly as the bring-up path does. The
    # registry frees an allocation as soon as its process dies, so without
    # `preferred` a crash-restart takes the lowest free port in the window
    # instead of its own — which is how one app's api and web swapped 8703/8706
    # and then walked down to 8700/8701 over successive restarts. A service's
    # address moving on crash is not a detail: a sibling that resolved the port
    # at ITS startup keeps calling the old one and answers 500.
    # A manifest `port` is a pin and outranks whatever the last run recorded, so
    # a service that has already drifted comes home on its next restart.
    port = port_registry.allocate(_app_owner(slug, svc_name), "app",
                                  preferred=spec.port or sstate.get("port"),
                                  pinned=spec.port is not None)
    sstate["port"] = port
    sstate["url"] = f"http://127.0.0.1:{port}"
    if spec.port is not None:
        _evict_squatter(slug, svc_name, port)
    # Peer map (url+port) from sibling services, so env injection/substitution
    # resolves on a restart exactly as it did on the initial bring-up.
    peers = {
        name: {"url": s.get("url"), "port": s.get("port")}
        for name, s in app["services"].items()
        if s.get("port")
    }
    sstate["status"] = "starting"
    _persist_state()
    try:
        pid = _spawn_service(slug, spec, project_dir, port, peers)
    except AppError as e:
        sstate["status"] = "failed"
        sstate["error"] = e.message
        _persist_state()
        raise
    sstate["pid"] = pid
    sstate["status"] = "running"
    sstate["started_at"] = time.time()
    sstate["error"] = None
    # `restarts` deliberately does NOT count this. Every call here is somebody
    # pressing Restart, and a tally of operator restarts is not health: it read as a
    # crashloop on a service that had simply been redeployed 425 times. The number
    # only means crash restarts now, and only docker is in a position to count those.
    port_registry.set_pid(_app_owner(slug, svc_name), pid)
    _reflect_primary_surface(slug, manifest)
    _persist_state()
    return app_status(slug)


def app_status(slug: str) -> dict:
    """Live status: per-service status + health, the app phase, desired state, and
    declared deps. Probes liveness (pid) and readiness (health/TCP) on each call so
    the snapshot is current, not cached."""
    if is_self_project(slug):
        return {"slug": slug, "self": True, "phase": "self", "desired": "self",
                "services": [], "deps": []}
    app = _STATE.get(slug)
    if not app:
        return {"slug": slug, "phase": "stopped", "desired": "stopped", "services": [], "deps": []}
    if app.get("backend") == "container":
        try:
            manifest = resolve_manifest(slug)
            project_dir = _project_dir(slug)
        except AppError:
            return {"slug": slug, "phase": app.get("phase", "stopped"),
                    "desired": app.get("desired", "stopped"), "services": [], "deps": []}
        services, deps = _container_services(slug, manifest, project_dir)
        running = sum(1 for s in services if s["status"] == "running")
        total = sum(1 for s in services if s["status"] != "disabled")
        phase = app.get("phase", "stopped")
        if phase not in ("starting",):
            phase = "up" if running and running == total else ("partial" if running else "stopped")
        return {
            "slug": slug, "phase": phase, "desired": app.get("desired", "stopped"),
            "manifest_detected": app.get("manifest_detected", False),
            "backend": "container", "services": services, "deps": deps,
        }
    _adopt_untracked(slug, app)
    # A bring-up owns its services' records and their port claims. It reaps the
    # processes a record still calls `running` and only overwrites that record
    # when it reaches the service, so a status read in between saw a crash and
    # released the port the bring-up had just claimed; the spawn then ran with
    # no allocation at all.
    bringing_up = app.get("phase") == "starting" or _slug_lock(slug).locked()
    services = []
    for name, s in app.get("services", {}).items():
        pid = s.get("pid")
        alive = _alive(pid)
        status = s.get("status")
        healthy = None
        if status == "running" and not alive and not bringing_up:
            # Crashed since last touch. Nothing restarts it: the subprocess backend is
            # the fallback for a host without docker and has no supervisor.
            status = "failed"
            s["status"] = "failed"
            s["error"] = s.get("error") or "process exited"
            port_registry.release(_app_owner(slug, name))
        elif status == "running" and alive and s.get("port"):
            healthy = _service_healthy(s["port"], s.get("health"))
        services.append({
            "name": name,
            "status": status,
            "pid": pid if alive else None,
            "port": s.get("port"),
            "url": s.get("url"),
            "expose": s.get("expose", False),
            "healthy": healthy,
            "error": s.get("error"),
            # Always 0 on this path: nothing observes crashes for a bare subprocess,
            # so there is no honest number to report. The container backend fills
            # these from docker's own history.
            "restarts": s.get("restarts", 0),
            "oom_killed": False,
            "exit_code": None,
            "started_at": None,
        })
    running = sum(1 for s in services if s["status"] == "running")
    total = sum(1 for s in services if s["status"] != "disabled")
    phase = app.get("phase", "stopped")
    if phase not in ("starting",):
        phase = "up" if running and running == total else ("partial" if running else "stopped")
    return {
        "slug": slug,
        "phase": phase,
        "desired": app.get("desired", "stopped"),
        "manifest_detected": app.get("manifest_detected", False),
        "services": services,
        "deps": app.get("deps", []),
    }


def running_services() -> list[dict]:
    """Every service the runner has up, as `{project, service, pid, port}`,
    read from state without probing health (a probe is an HTTP call per
    service). Container-backed apps have no process of ours to name."""
    out = []
    for slug, app in list(_STATE.items()):
        if app.get("backend") == "container" or is_self_project(slug):
            continue
        for name, s in list((app.get("services") or {}).items()):
            if s.get("status") == "running" and _alive(s.get("pid")):
                out.append({"project": slug, "service": name,
                            "pid": s["pid"], "port": s.get("port")})
    return out


def wants_running(slug: str) -> bool:
    """Whether the runner means this app to be up: the monitor respawns a service
    of an app it wants running, so a `failed` or `stopped` reading is temporary."""
    return (_STATE.get(slug) or {}).get("desired") == "running"


def command_env(slug: str, spec: ServiceSpec) -> dict[str, str]:
    """The environment an app-declared command for this service runs with
    (`reload`, `preflight`): the host's, plus exactly what the service itself is
    started with, so the command sees the same port, peers and secrets."""
    app = _STATE.get(slug) or {}
    services = app.get("services") or {}
    peers = {name: {"url": s.get("url"), "port": s.get("port")}
             for name, s in services.items() if s.get("port")}
    port = (services.get(spec.name) or {}).get("port") or spec.port or 0
    return {**os.environ,
            **_service_env(int(port), spec, peers, _secret_env_for_service(slug, spec.name))}


def project_dir(slug: str) -> Path:
    return _project_dir(slug)


def service_target(slug: str, svc_name: str) -> tuple[str | None, int | None]:
    """A service's status and port WITHOUT probing its health: what a proxy needs
    to route one request. `app_status` probes every service of the app over HTTP
    or TCP, so a slow service held each lookup for its probe timeout. A tracked,
    live, running service process is answered from state; anything else goes through
    `app_status`, which also adopts a service whose tracked process is gone but
    which is still serving."""
    app = _STATE.get(slug)
    if app and app.get("backend") != "container" and not is_self_project(slug):
        s = (app.get("services") or {}).get(svc_name) or {}
        if s.get("status") == "running" and s.get("port") and _alive(s.get("pid")):
            return "running", s["port"]
    st = app_status(slug)
    svc = next((x for x in st.get("services", []) if x["name"] == svc_name), None)
    return (svc.get("status"), svc.get("port")) if svc else (None, None)


def app_logs(slug: str, svc_name: str, max_bytes: int = 16384) -> str:
    app = _STATE.get(slug)
    if app and app.get("backend") == "container":
        compose_path = _compose_path_for(slug)
        if not compose_path.exists():
            return "(app not running)"
        try:
            project_dir = _project_dir(slug)
        except AppError:
            project_dir = None
        try:
            return container_runtime.logs(slug, compose_path, svc_name, cwd=project_dir)[-max_bytes:]
        except container_runtime.ComposeError as e:
            return f"(could not read logs: {e.message})"
    path = _build_log(slug, svc_name)
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def is_builtin_app(slug: str) -> bool:
    """True if this project is a built-in Workshop app (registered in the manifest
    store), whose live code is in the monorepo rather than the project dir. Running
    such a project as a separate app is meaningless — it already runs as part of
    Workshop. Never raises."""
    try:
        from services import manifests
        return manifests.get_manifest(slug) is not None
    except Exception:
        return False


def manifest_dict(slug: str) -> dict:
    """Resolved manifest as a plain dict for the API/UI (so the operator can see
    exactly what the runner will start, detected or declared). Never raises for a
    known project that simply has nothing runnable yet — returns an empty-services
    manifest (flagged `builtin` for a monorepo app, else a plain empty scaffold) so
    the panel shows a calm state instead of an error."""
    if is_self_project(slug):
        return {"name": slug, "self": True, "detected": False, "builtin": False,
                "services": [], "deps": [], "env_requirements": []}
    _project_dir(slug)  # raises 404 for a genuinely unknown project
    builtin = is_builtin_app(slug)
    try:
        m = resolve_manifest(slug)
    except AppError:
        # Known project, nothing runnable yet (empty scaffold / built-in shadow).
        return {"name": slug, "self": False, "builtin": builtin, "detected": True,
                "services": [], "deps": [], "env_requirements": []}
    return {
        "name": m.name,
        "detected": m.detected,
        "builtin": builtin,
        "services": [
            {
                "name": s.name, "start": s.start, "cwd": s.cwd, "build": s.build,
                "port_env": s.port_env, "port": s.port,
                "health": s.health, "expose": s.expose,
                "enabled": s.enabled, "disabled_reason": s.disabled_reason,
                "depends_on": s.depends_on, "env_keys": sorted(s.env.keys()),
            }
            for s in m.services
        ],
        "deps": [{"name": d.name, "compose_file": d.compose_file, "compose_service": d.compose_service}
                 for d in m.deps],
        "env_requirements": [_req_dict(r) for r in m.env_requirements],
    }


def _req_dict(r: EnvRequirement) -> dict:
    return {
        "key": r.key, "description": r.description, "required": r.required,
        "secret": r.secret, "example": r.example, "how_to_obtain": r.how_to_obtain,
        "service": r.service, "group": r.group,
    }


def env_overview(slug: str) -> dict:
    """Declared env requirements + per-key SET status (presence only, never the
    value — the `check_secrets` view). Drives the Environment panel and lets any
    agent reason about completeness without touching secrets. Also reports any
    stored keys that are NOT declared requirements (so they aren't invisible)."""
    from services import secrets_store
    if is_self_project(slug):
        # The platform itself isn't a deployable app with declared env; its
        # secrets live in the operator's own config, not this registry.
        return {"slug": slug, "self": True, "requirements": [],
                "undeclared_set": [], "missing_required": []}
    try:
        m = resolve_manifest(slug)
        reqs = m.env_requirements
    except Exception:
        reqs = []
    set_keys = set(secrets_store.set_keys(slug))
    declared = {r.key for r in reqs}
    requirements = []
    for r in reqs:
        d = _req_dict(r)
        d["set"] = r.key in set_keys
        requirements.append(d)
    extra = sorted(set_keys - declared)  # values set without a declared requirement
    missing_required = [r["key"] for r in requirements if r["required"] and not r["set"]]
    return {"slug": slug, "requirements": requirements,
            "undeclared_set": extra, "missing_required": missing_required}


def restore_desired_running() -> list[str]:
    """Start every process-mode app whose persisted desired state is `running`
    and none of whose services is alive. Called once at backend startup: a
    process-mode app dies with the process that spawned it, and without this it
    stays down until someone presses Start. Containers are skipped, docker's
    restart policy already brings them back. An app with some service still
    alive is left alone rather than restarted underneath itself. Returns the
    slugs it started."""
    started: list[str] = []
    for slug, app in list(_STATE.items()):
        if app.get("desired") != "running" or app.get("backend") == "container":
            continue
        # app_status adopts a service that is serving untracked, so an app that
        # is up is never started again (and reaped) underneath itself.
        if app_status(slug)["phase"] != "stopped":
            continue
        try:
            start_app(slug)
        except AppError as e:
            log.warning("could not restore app %s: %s", slug, e)
            continue
        started.append(slug)
    return started


def list_apps() -> list[dict]:
    """Status of every app the runner currently knows about (this process +
    persisted)."""
    return [app_status(slug) for slug in sorted(_STATE.keys())]


# Load any persisted state at import so a backend restart sees prior apps (their
# pids are dead in this process, so reconcile/monitor will mark them stopped and,
# if desired-running, restart them).
_load_persisted()
