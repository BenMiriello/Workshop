"""Multi-service app runner — run a project made of several servers as one app.

`local_runner` runs a project's standalone surface as ONE subprocess. Real apps are
several cooperating servers (gengui = a Bun API `core` + a Vite `frontend` + a Python
`inference-worker`, plus Postgres/Redis). This runner models that:

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
  - A **desired state** (running/stopped) is persisted so app_monitor can restart a
    service that crashed while the app is meant to be up — the "autonomously monitor
    and fix" requirement.

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
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from services import local_runner, port_registry

log = logging.getLogger("workshop.app_runner")

PROJECTS_REGISTRY = local_runner.PROJECTS_REGISTRY

# The Workshop monorepo itself. A project whose dir resolves here (or is flagged
# `self` in the registry) is the SELF / platform project: editing it edits the
# running app, so it is never a separate deployment (frontend hot-reloads;
# backend changes need scripts/restart_backend.sh).
WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path.home() / "workshop"))).resolve()


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


def is_self_dir(path: str | Path) -> bool:
    """True if a working dir resolves to the Workshop monorepo root (the self
    project). Used to suppress auto-checkpoint there — never auto-commit the live
    repo's working tree."""
    try:
        return Path(path).resolve() == WORKSHOP_ROOT
    except OSError:
        return False


LOG_ROOT = Path(os.environ.get(
    "WORKSHOP_APP_LOG_ROOT",
    str(Path.home() / "workshop" / ".runtime" / "app-logs"),
))

STATE_STORE = Path(os.environ.get(
    "WORKSHOP_APP_STATE",
    str(Path.home() / "workshop" / ".runtime" / "app-deploys.json"),
))

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
    health: str | None = None  # http path probed for readiness, e.g. "/health"
    expose: bool = False     # the app's primary UI surface (reflected as the URL)
    enabled: bool = True
    depends_on: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


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
class AppManifest:
    name: str
    services: list[ServiceSpec]
    deps: list[DepSpec] = field(default_factory=list)
    env_requirements: list[EnvRequirement] = field(default_factory=list)
    detected: bool = False   # True if auto-detected (no workshop.app.json on disk)


# ---- project dir + manifest resolution --------------------------------------

def _project_dir(slug: str) -> Path:
    try:
        data = json.loads(PROJECTS_REGISTRY.read_text(encoding="utf-8")) if PROJECTS_REGISTRY.exists() else {}
    except (OSError, json.JSONDecodeError) as e:
        raise AppError(f"could not read projects registry: {e}", status=500)
    rec = (data.get("projects") or {}).get(slug) if isinstance(data, dict) else None
    if not rec:
        raise AppError(f"project not found: {slug}", status=404)
    cwd = rec.get("dir")
    if not cwd or not Path(cwd).is_dir():
        raise AppError(f"project dir missing for {slug}: {cwd}", status=400)
    return Path(cwd)


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
    return ServiceSpec(
        name=name,
        start=start,
        cwd=str(raw.get("cwd") or "."),
        build=(str(raw["build"]).strip() if raw.get("build") else None),
        port_env=str(raw.get("port_env") or "PORT"),
        health=(str(raw["health"]) if raw.get("health") else None),
        expose=bool(raw.get("expose", False)),
        enabled=bool(raw.get("enabled", True)),
        depends_on=[str(d) for d in (raw.get("depends_on") or []) if isinstance(d, str)],
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
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
            parts = [str(how.get("steps") or "").strip(), str(how.get("url") or "").strip()]
            how = " — ".join(p for p in parts if p)
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

    1. A `deploy.config.yaml` with a `services:` map (gengui's shape) → one service
       per entry, using its `path` + `start_command`/`build_command`.
    2. Else a single service from dev_servers.suggest_command (back-compat with the
       single-process projects local_runner handled).
    """
    dc = project_dir / "deploy.config.yaml"
    if dc.exists():
        services = _services_from_deploy_config(dc)
        if services:
            return AppManifest(name=project_dir.name, services=services, detected=True)

    cmd = local_runner.resolve_command(str(project_dir))  # raises AppError-equivalent
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
    return AppManifest(
        name=str(raw.get("name") or slug), services=services, deps=deps,
        env_requirements=_env_requirements_from_raw(raw),
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


def _persist_state() -> None:
    try:
        STATE_STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_STORE.with_suffix(STATE_STORE.suffix + ".tmp")
        tmp.write_text(json.dumps(_STATE, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, STATE_STORE)
    except OSError as e:
        log.warning("could not persist app state: %s", e)


def _load_persisted() -> None:
    """Load STATE_STORE into memory once at import/startup. Pids from a previous
    process are NOT live here, so reconcile() marks them stopped."""
    if not STATE_STORE.exists():
        return
    try:
        data = json.loads(STATE_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict):
        _STATE.update(data)


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
    log_path = _build_log(slug, spec.name)
    try:
        with open(log_path, "ab", buffering=0) as logf:
            logf.write(f"\n--- start {spec.name} on :{port} @ {time.ctime()} ---\n".encode())
            proc = subprocess.Popen(
                ["/bin/bash", "-c", spec.start],
                cwd=str(cwd),
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
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
                port = port_registry.allocate(_app_owner(slug, spec.name), "app",
                                              preferred=sstate.get("port"))
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
                                   + " — set in the project's Environment panel.")
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


_SELF_MSG = ("This project is the running Workshop itself — it is not a separate "
             "deployable app. The frontend hot-reloads on save; backend changes need "
             "`bash scripts/restart_backend.sh`.")


def start_app(slug: str) -> dict:
    """Start (or resume) all enabled services of an app. Returns immediately with a
    'starting' snapshot; build + spawn run on a background thread. Idempotent: an
    already-running app returns its status. Sets desired state = running."""
    if is_self_project(slug):
        raise AppError(_SELF_MSG, status=409)
    manifest = resolve_manifest(slug)  # raises AppError
    lock = _slug_lock(slug)
    if lock.locked():
        return {"slug": slug, "phase": "starting", **app_status(slug)}

    app = _STATE.setdefault(slug, {})
    app["slug"] = slug
    app["desired"] = "running"
    app["phase"] = "starting"
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
    _persist_state()

    t = threading.Thread(target=_bring_up, args=(slug, manifest), daemon=True)
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
    app = _STATE.setdefault(slug, {"slug": slug, "services": {}})
    sstate = app["services"].setdefault(svc_name, _init_service_state(spec, None))
    if sstate.get("pid") and _alive(sstate["pid"]):
        _stop_service(slug, svc_name, sstate)
    port = port_registry.allocate(_app_owner(slug, svc_name), "app")
    sstate["port"] = port
    sstate["url"] = f"http://127.0.0.1:{port}"
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
    sstate["restarts"] = sstate.get("restarts", 0) + 1
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
    services = []
    for name, s in app.get("services", {}).items():
        pid = s.get("pid")
        alive = _alive(pid)
        status = s.get("status")
        healthy = None
        if status == "running" and not alive:
            # Crashed since last touch — reflect it (monitor decides on restart).
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
            "restarts": s.get("restarts", 0),
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


def app_logs(slug: str, svc_name: str, max_bytes: int = 16384) -> str:
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


def manifest_dict(slug: str) -> dict:
    """Resolved manifest as a plain dict for the API/UI (so the operator can see
    exactly what the runner will start, detected or declared)."""
    if is_self_project(slug):
        return {"name": slug, "self": True, "detected": False,
                "services": [], "deps": [], "env_requirements": []}
    m = resolve_manifest(slug)
    return {
        "name": m.name,
        "detected": m.detected,
        "services": [
            {
                "name": s.name, "start": s.start, "cwd": s.cwd, "build": s.build,
                "port_env": s.port_env, "health": s.health, "expose": s.expose,
                "enabled": s.enabled, "depends_on": s.depends_on, "env_keys": sorted(s.env.keys()),
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


def list_apps() -> list[dict]:
    """Status of every app the runner currently knows about (this process +
    persisted)."""
    return [app_status(slug) for slug in sorted(_STATE.keys())]


# Load any persisted state at import so a backend restart sees prior apps (their
# pids are dead in this process, so reconcile/monitor will mark them stopped and,
# if desired-running, restart them).
_load_persisted()
