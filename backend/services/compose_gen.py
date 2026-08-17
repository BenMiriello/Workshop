"""Generate a docker compose file from a Workshop app manifest.

The manifest (`workshop.app.json`, modeled in app_runner) is Workshop's contract for
what an app is: services (cwd/build/start/env/expose/depends_on) + external deps
(postgres/redis/...) + declared env requirements. This turns that into a single,
self-contained compose file the container runtime brings up:

  - each ENABLED service runs in a base runtime image (bun/node/python, detected from
    its start command) with its source **bind-mounted** — so the same container serves
    dev (hot reload) and deploy, the one model the operator picked;
  - services reach each other over the compose network by service name, so a peer URL
    is `http://<service>:<PORT>` (no host ports, no port registry);
  - external deps are copied out of their own compose file (the manifest points at it)
    with host port-publishing, container_name and bespoke networks STRIPPED, so they
    live on this project's private network and never collide with host services (e.g.
    an unrelated postgres already on :5434);
  - only the single exposed service publishes a port, to 127.0.0.1:<host_port>, which
    the backend reverse proxy points at exactly as before.

Secrets are written to a sibling 0600 env_file referenced with `env_file:` — never
inlined into the compose file. The compose file itself carries no secret values.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from wsplatform import paths

log = logging.getLogger("workshop.compose_gen")

# Every service listens on this port INSIDE its container. Containers are isolated,
# so one fixed port for all of them can't collide; only the exposed service is
# published to a host port. Peers connect to http://<service>:<INTERNAL_PORT>.
INTERNAL_PORT = 8080

COMPOSE_ROOT = paths.env_path("WORKSHOP_COMPOSE_ROOT", "compose")

_RUNTIME_IMAGES = [
    (("bun",), "oven/bun:1"),
    (("uvicorn", "gunicorn", "python", "python3", "pip", "poetry", "uv "), "python:3.12-slim"),
    (("deno",), "denoland/deno:latest"),
]
_DEFAULT_IMAGE = "node:22-bookworm-slim"  # node/npm/pnpm/yarn/vite/next and the fallback


def _runtime_image(build: str | None, start: str) -> str:
    blob = f"{build or ''} {start}".lower()
    for needles, image in _RUNTIME_IMAGES:
        if any(n in blob for n in needles):
            return image
    return _DEFAULT_IMAGE


def _containerize_start(start: str) -> str:
    """A service that binds 127.0.0.1 inside a container is unreachable from the
    published host port — the container IS the isolation boundary, so it must bind
    0.0.0.0. Rewrite an explicit loopback host flag; leave everything else."""
    s = re.sub(r"--host(\s+|=)127\.0\.0\.1", r"--host\g<1>0.0.0.0", start)
    s = re.sub(r"--host(\s+|=)localhost", r"--host\g<1>0.0.0.0", s)
    return s


def _peer_url(name: str) -> str:
    return f"http://{name}:{INTERNAL_PORT}"


def _substitute_peers(value: str, peers: list[str]) -> str:
    """Resolve ${<svc>.url} / ${<svc>.port} in a manifest env value to the peer's
    in-network address. Mirrors app_runner._service_env's substitution, but with
    compose service-name URLs instead of host ports.

    Two things compose forces that the subprocess runner didn't: (1) an unresolved
    placeholder (a peer that's disabled, e.g. ${core.url} when core is off) must be
    emptied — left in place, compose would try to interpolate `${core.url}` as one of
    ITS variables and fail on the dot; (2) any stray `$` is escaped to `$$` so compose
    doesn't interpolate a literal value we injected."""
    out = value
    for name in peers:
        out = out.replace(f"${{{name}.url}}", _peer_url(name))
        out = out.replace(f"${{{name}.port}}", str(INTERNAL_PORT))
    out = re.sub(r"\$\{[A-Za-z0-9_-]+\.(?:url|port)\}", "", out)  # unresolved peer refs
    if "$" in out:
        out = out.replace("$$", "$").replace("$", "$$")  # normalize, then escape for compose
    return out


def _sanitize_dep_block(block: dict) -> dict:
    """Copy an external dep's service definition onto THIS project's private network:
    drop host port-publishing (collision-free), the fixed container_name (compose
    namespaces it under the project), and any bespoke `networks` (use the default).
    Keep image/env/volumes/healthcheck/command; ensure it restarts on crash."""
    keep = ("image", "environment", "volumes", "healthcheck", "command", "entrypoint",
            "depends_on", "user", "tmpfs", "shm_size")
    out = {k: block[k] for k in keep if k in block}
    out.setdefault("restart", "unless-stopped")
    return out


def _collect_named_volumes(block: dict) -> set[str]:
    """Named volumes a dep block references (e.g. `postgres_data:/var/lib/...`) so we
    can declare them at the top level. Bind mounts (contain a `/` before the `:`) are
    not named volumes and are left alone."""
    vols = block.get("volumes") or []
    named: set[str] = set()
    for v in vols:
        if isinstance(v, str) and ":" in v:
            src = v.split(":", 1)[0]
            if src and "/" not in src and "." not in src:
                named.add(src)
    return named


def _load_dep_block(project_dir: Path, compose_file: str, service: str) -> dict | None:
    path = (project_dir / compose_file).resolve()
    try:
        if not str(path).startswith(str(project_dir.resolve())):
            return None  # never read outside the project
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    services = (data or {}).get("services") or {}
    return services.get(service)


def generate(slug: str, manifest, project_dir: Path, host_port: int,
             secrets_by_service: dict[str, dict[str, str]] | None = None) -> dict:
    """Write `.runtime/compose/<slug>.yml` from the manifest and return metadata:
    {compose_path, exposed_service, host_port, app_services, dep_services, warnings}.

    `secrets_by_service[name]` is the resolved secret VALUES for a service; they go
    into a sibling 0600 env_file, never into the compose file. Only ENABLED services
    are emitted; deps declared in the manifest are always included (they are the app's
    infra, brought up with it — matching the previous runner)."""
    secrets_by_service = secrets_by_service or {}
    COMPOSE_ROOT.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    enabled = [s for s in manifest.services if s.enabled]
    enabled_names = {s.name for s in enabled}
    # Peer URLs resolve to a service-name address for EVERY service, enabled or not:
    # a disabled peer (e.g. core) still yields a valid non-empty URL (http://core:8080)
    # so a frontend that requires VITE_API_URL boots, and it just works if that peer is
    # later enabled. depends_on stays gated to services actually in the compose, below.
    peer_names = [s.name for s in manifest.services]

    services: dict[str, dict] = {}
    named_volumes: dict[str, dict] = {}
    exposed_service: str | None = None
    app_service_names: list[str] = []
    dep_service_names: list[str] = []

    # ---- external deps first, so app services can depend_on + reach them ----
    for dep in manifest.deps:
        block = _load_dep_block(project_dir, dep.compose_file, dep.compose_service)
        if block is None:
            warnings.append(f"dep '{dep.name}': could not read {dep.compose_service} from {dep.compose_file}")
            continue
        named_volumes.update({v: {} for v in _collect_named_volumes(block)})
        services[dep.name] = _sanitize_dep_block(block)
        dep_service_names.append(dep.name)

    # ---- app services ----
    for spec in enabled:
        abs_cwd = (project_dir / spec.cwd).resolve()
        if not str(abs_cwd).startswith(str(project_dir.resolve())):
            warnings.append(f"service '{spec.name}': cwd escapes the project dir; skipped")
            continue

        env: dict[str, str] = {
            "PORT": str(INTERNAL_PORT),
            "WORKSHOP_APP_SLUG": slug,
            "WORKSHOP_SERVICE_NAME": spec.name,
            "WORKSHOP_PROXY_BASE": f"/base-apps/run/{slug}/proxy/{spec.name}/",
        }
        if spec.port_env and spec.port_env != "PORT":
            env[spec.port_env] = str(INTERNAL_PORT)
        # Auto-injected peer URLs for declared dependencies (app services + deps).
        for dep_name in spec.depends_on:
            if dep_name in enabled_names:
                env[f"{dep_name.upper().replace('-', '_')}_URL"] = _peer_url(dep_name)
        # Manifest env, with ${peer.url}/${peer.port} substituted to in-network addrs.
        for k, v in spec.env.items():
            env[k] = _substitute_peers(v, peer_names)

        # depends_on: declared app-service deps, plus any external dep this service
        # actually references (its service name shows up in the service's env — e.g.
        # core has DB_HOST=postgres / REDIS_URL=redis://redis:6379). This keeps a
        # frontend that touches no database from waiting on postgres to be healthy.
        depends: dict[str, dict] = {}
        for d in spec.depends_on:
            if d in enabled_names and d != spec.name:
                depends[d] = {"condition": "service_started"}
        env_blob = " ".join(env.values())
        for d in dep_service_names:
            if re.search(rf"(^|[^A-Za-z0-9_-]){re.escape(d)}([^A-Za-z0-9_-]|$)", env_blob):
                cond = "service_healthy" if "healthcheck" in services.get(d, {}) else "service_started"
                depends[d] = {"condition": cond}

        start = _containerize_start(spec.start)
        cmd = f"{spec.build} && exec {start}" if spec.build else f"exec {start}"
        # The command references $PORT / $WORKSHOP_PROXY_BASE, meant for the
        # container's shell to expand at runtime. Compose interpolates the whole file
        # at parse time, so escape `$` → `$$` or it would blank them out before the
        # container ever sees them.
        cmd = cmd.replace("$", "$$")

        svc: dict = {
            "image": _runtime_image(spec.build, spec.start),
            "working_dir": "/app",
            "volumes": [f"{abs_cwd}:/app"],
            "command": ["sh", "-c", cmd],
            "environment": env,
            "restart": "unless-stopped",
            # Block in-container privilege escalation (setuid/gain-caps). Cheap,
            # app-safe hardening. cap_drop:[ALL] + a non-root user are deferred
            # (can break apps that bind low ports / expect root) — see SEC-A6.
            # (security-audit-2026-07-18 A6)
            "security_opt": ["no-new-privileges:true"],
        }
        if depends:
            svc["depends_on"] = depends

        # Secrets → sibling 0600 env_file, never inlined into the compose file.
        secrets = secrets_by_service.get(spec.name) or {}
        if secrets:
            env_path = COMPOSE_ROOT / f"{slug}__{spec.name}.env"
            _write_env_file(env_path, secrets)
            svc["env_file"] = [str(env_path)]

        # Only the exposed service is published, to loopback; the proxy targets it.
        if spec.expose and exposed_service is None:
            svc["ports"] = [f"127.0.0.1:{host_port}:{INTERNAL_PORT}"]
            exposed_service = spec.name

        services[spec.name] = svc
        app_service_names.append(spec.name)

    compose: dict = {"services": services}
    if named_volumes:
        compose["volumes"] = named_volumes

    compose_path = COMPOSE_ROOT / f"{slug}.yml"
    compose_path.write_text(
        yaml.safe_dump(compose, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return {
        "compose_path": compose_path,
        "exposed_service": exposed_service,
        "host_port": host_port if exposed_service else None,
        "internal_port": INTERNAL_PORT,
        "app_services": app_service_names,
        "dep_services": dep_service_names,
        "warnings": warnings,
    }


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = []
    for k, v in values.items():
        # env_file is KEY=VALUE per line; keep values single-line.
        lines.append(f"{k}={str(v).splitlines()[0] if v else ''}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
