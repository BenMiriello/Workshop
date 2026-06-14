"""Per-agent dev-server lifecycle.

Each Build agent can have at most one dev server running. The operator picks
the command + port via the Preview tab; we spawn it as a child of the workshop
backend with cwd=agent.cwd, capture pid, and tail stdout into a per-agent
log file. Stopping = SIGTERM (escalate to SIGKILL after 5s).

State for currently-running servers lives in-memory (PIDs only). On backend
restart, leftover children orphan; the operator can hit Stop on the stale
UI state (idempotent) and Boot again. Acceptable for v1.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("workshop.dev_servers")

# Reserved port window for project dev servers (Vite typically auto-selects;
# we ASK the operator for an explicit port to make the URL predictable).
PORT_MIN = 8500
PORT_MAX = 8599

LOG_ROOT = Path(os.environ.get(
    "WORKSHOP_DEV_LOG_ROOT",
    str(Path.home() / "workshop" / ".runtime" / "dev-server-logs"),
))


@dataclass
class DevServer:
    slug: str
    pid: int
    port: int
    command: str
    started_at: float
    log_path: str


# slug → DevServer
RUNNING: dict[str, DevServer] = {}


def next_free_port() -> int:
    """Return the lowest port in [PORT_MIN, PORT_MAX] not currently claimed
    by a running dev server. Used by the Preview UI to default the port
    picker (ADR-009 section 6). Raises RuntimeError if the window is full.
    """
    used = {s.port for s in RUNNING.values()}
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p not in used:
            return p
    raise RuntimeError(f"no free ports in [{PORT_MIN},{PORT_MAX}]")


def status(slug: str) -> dict:
    s = RUNNING.get(slug)
    if not s:
        return {"running": False}
    # Verify the process is still alive (it may have crashed; SIGKILL=0 probes).
    try:
        os.kill(s.pid, 0)
    except OSError:
        # Stale entry; clear.
        RUNNING.pop(slug, None)
        return {"running": False, "stale_pid": s.pid}
    return {
        "running": True,
        "pid": s.pid,
        "port": s.port,
        "command": s.command,
        "started_at": s.started_at,
        "log_path": s.log_path,
    }


def start(slug: str, *, cwd: str, command: str, port: int) -> dict:
    if slug in RUNNING:
        existing = status(slug)
        if existing.get("running"):
            return {"ok": False, "reason": "already running", "status": existing}
    if not (PORT_MIN <= port <= PORT_MAX):
        return {"ok": False, "reason": f"port must be in [{PORT_MIN}, {PORT_MAX}]"}
    cmd = command.strip()
    if not cmd:
        return {"ok": False, "reason": "empty command"}

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / f"{slug}.log"

    # Run via /bin/bash -lc so the operator can use shell features (env, &&,
    # || etc) without having to split argv themselves. Detach into its own
    # process group so we can SIGTERM the whole tree on stop. Don't pipe
    # stdout into a Python reader — tee to a file so it survives backend
    # restarts and the UI can tail it.
    with open(log_path, "ab", buffering=0) as logf:
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", cmd],
            cwd=cwd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # equivalent to setsid; new process group
            env={**os.environ, "WORKSHOP_DEV_PORT": str(port)},
        )
    RUNNING[slug] = DevServer(
        slug=slug,
        pid=proc.pid,
        port=port,
        command=cmd,
        started_at=time.time(),
        log_path=str(log_path),
    )
    return {"ok": True, "status": status(slug)}


def stop(slug: str) -> dict:
    s = RUNNING.pop(slug, None)
    if not s:
        return {"ok": True, "reason": "not running"}
    try:
        # Kill the whole process group (Vite spawns workers; need to nuke them all).
        os.killpg(s.pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "reason": "already gone"}
    # Wait up to 5s for graceful exit, then SIGKILL.
    for _ in range(50):
        try:
            os.kill(s.pid, 0)
        except OSError:
            return {"ok": True}
        time.sleep(0.1)
    try:
        os.killpg(s.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {"ok": True, "force_killed": True}


def tail_log(slug: str, max_bytes: int = 8192) -> str:
    s = RUNNING.get(slug)
    path = Path(s.log_path) if s else (LOG_ROOT / f"{slug}.log")
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


def suggest_command(cwd: str) -> dict:
    """Heuristic auto-detect: look for package.json + 'dev'/'start' script,
    or a requirements.txt + 'app.py'. The operator can override; this just
    populates the form on first open."""
    d = Path(cwd)
    pkg = d / "package.json"
    if pkg.exists():
        try:
            import json
            data = json.loads(pkg.read_text())
            scripts = data.get("scripts") or {}
            if "dev" in scripts:
                return {"command": "npm run dev -- --port $WORKSHOP_DEV_PORT", "kind": "vite-or-npm"}
            if "start" in scripts:
                return {"command": "npm start", "kind": "npm"}
        except Exception:
            pass
    # FastAPI / Flask via uvicorn.
    if (d / "app.py").exists() or (d / "main.py").exists():
        entry = "app:app" if (d / "app.py").exists() else "main:app"
        return {"command": f"uvicorn {entry} --port $WORKSHOP_DEV_PORT --host 127.0.0.1 --reload", "kind": "uvicorn"}
    # Plain static-site: serve with python http.server.
    if (d / "src" / "index.html").exists() or (d / "index.html").exists():
        sub = "src" if (d / "src" / "index.html").exists() else "."
        return {"command": f"python3 -m http.server $WORKSHOP_DEV_PORT --directory {sub}", "kind": "static"}
    return {"command": "", "kind": "unknown"}
