"""Operating an app: the verbs every build chat has, whatever the app is.

Design of record: `docs/platform-redesign/operations-and-boundaries-2026-11-04.md`.
The platform owns the verb (status, logs, start, stop, restart) and the app
declares its particulars in `workshop.app.json`: how a service swaps in new code
without a stop (`reload`), what must pass before it is restarted (`preflight`),
how long it takes to come back (`ready_timeout`). An agent needs no path, port,
credential or script name to operate anything, and every answer says what
actually happened: a restart waits for the service to be healthy and reports
the old and new pid, or the error and the end of its log.

WHO MAY OPERATE WHAT comes from the turn's claimed scope, never from anything
the agent says: the development scope may operate any app, an app's build chat
its own app, and nothing else may operate anything.
"""
from __future__ import annotations

import subprocess
import time

from services import app_runner

READY_TIMEOUT_S = 180
START_TIMEOUT_S = 900
COMMAND_TIMEOUT_S = 900
LOG_TAIL_CHARS = 3000


class Refused(Exception):
    """The call must not run: a target the scope may not operate, an unknown
    service, unsaved work, a failed preflight. Nothing was changed."""


def authority(scope: str, slug: str) -> None:
    kind, _, own = (scope or "").partition(":")
    if scope == "development":
        return
    if slug == PLATFORM:
        raise Refused("only the development chat operates the platform itself.")
    if kind == "project" and own == slug:
        return
    if kind == "project":
        raise Refused(f"this chat builds {own}, so it can operate {own} only, not {slug}. "
                      f"Work on {slug} belongs in {slug}'s own build chat.")
    raise Refused("operating apps is for build chats: the development chat, or an app's "
                  "own build chat for that app.")


PLATFORM = "workshop"


def _manifest(slug: str) -> app_runner.AppManifest:
    if slug == PLATFORM or app_runner.is_self_project(slug):
        raise Refused("that is the platform itself: it is made live as a whole with "
                      "app_apply, tested with app_test and checked with app_check.")
    try:
        return app_runner.resolve_manifest(slug)
    except app_runner.AppError as e:
        raise Refused(e.message) from e


def _service(manifest: app_runner.AppManifest, name: str) -> app_runner.ServiceSpec:
    spec = next((s for s in manifest.services if s.name == name), None)
    if spec is None:
        names = ", ".join(s.name for s in manifest.services) or "none"
        raise Refused(f"{manifest.name} has no service {name!r} (its services: {names})")
    return spec


def _live(slug: str) -> dict[str, dict]:
    return {s["name"]: s for s in app_runner.app_status(slug).get("services", [])}


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= LOG_TAIL_CHARS else "..." + text[-LOG_TAIL_CHARS:]


def uncommitted(slug: str) -> list[str]:
    """Files in the app's own repo with uncommitted changes: what a restart
    would load that nobody has committed, possibly another session's half-done
    edit."""
    root = app_runner.project_dir(slug)
    if not (root / ".git").exists():
        return []
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-uall"],
                         capture_output=True, text=True, timeout=30)
    paths = [line[3:] for line in out.stdout.splitlines() if line.strip()]
    return [p for p in paths if not p.startswith(_PLATFORM_STATE)]


# What the platform itself writes into an app's folder (its runtime state and
# task store). Never anyone's code, so never a reason to refuse a restart.
_PLATFORM_STATE = (".runtime/", ".tasks.json")


def _run_declared(slug: str, spec: app_runner.ServiceSpec, command: str,
                  timeout: int) -> tuple[bool, str]:
    cwd = (app_runner.project_dir(slug) / spec.cwd).resolve()
    try:
        r = subprocess.run(["bash", "-c", command], cwd=str(cwd),
                           env=app_runner.command_env(slug, spec),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"`{command}` did not finish within {timeout} s"
    return r.returncode == 0, _tail((r.stdout or "") + (r.stderr or ""))


def _wait_ready(slug: str, spec: app_runner.ServiceSpec, old_pid, *,
                same_pid_ok: bool) -> tuple[bool, dict, float]:
    limit = READY_TIMEOUT_S if spec.ready_timeout is None else spec.ready_timeout
    started = time.monotonic()
    svc: dict = {}
    while time.monotonic() - started < limit:
        svc = _live(slug).get(spec.name) or {}
        status = svc.get("status")
        if status == "failed":
            break
        new_pid = svc.get("pid")
        fresh = same_pid_ok or (new_pid and new_pid != old_pid)
        healthy = svc.get("healthy") is not False if spec.health is None else svc.get("healthy") is True
        if status == "running" and new_pid and fresh and healthy:
            return True, svc, time.monotonic() - started
        time.sleep(1.0)
    return False, svc, time.monotonic() - started


def _restart(slug: str, spec: app_runner.ServiceSpec) -> str:
    before = (_live(slug).get(spec.name) or {}).get("pid")
    if spec.preflight:
        ok, out = _run_declared(slug, spec, spec.preflight, COMMAND_TIMEOUT_S)
        if not ok:
            raise Refused(f"{slug}/{spec.name}: its preflight check failed, so nothing was "
                          f"restarted and the running service is untouched.\n{out}")
    if spec.reload:
        ok, out = _run_declared(slug, spec, spec.reload, max(spec.ready_timeout or 0, COMMAND_TIMEOUT_S))
        if not ok:
            raise RuntimeError(f"{slug}/{spec.name}: its reload command failed.\n{out}")
    else:
        try:
            app_runner.restart_service(slug, spec.name)
        except app_runner.AppError as e:
            raise RuntimeError(f"{slug}/{spec.name}: {e.message}") from e
    ready, svc, secs = _wait_ready(slug, spec, before, same_pid_ok=bool(spec.reload))
    if not ready:
        log = app_runner.app_logs(slug, spec.name, max_bytes=LOG_TAIL_CHARS)
        why = svc.get("error") or f"not healthy after {secs:.0f} s"
        raise RuntimeError(f"{slug}/{spec.name}: restart did not come back ({why}).\n"
                           f"End of its log:\n{_tail(log)}")
    how = "swapped in new code" if spec.reload else "restarted"
    return (f"{slug}/{spec.name}: {how}, pid {before or 'none'} -> {svc.get('pid')}, "
            f"healthy in {secs:.0f} s")


def restart(slug: str, service: str | None = None, *, include_uncommitted: bool = False) -> str:
    manifest = _manifest(slug)
    if service:
        targets = [_service(manifest, service)]
    else:
        live = _live(slug)
        targets = [s for s in manifest.services
                   if (live.get(s.name) or {}).get("status") == "running"]
        if not targets:
            raise Refused(f"{slug} has no running service to restart; start it instead.")
    dirty = uncommitted(slug)
    if dirty and not include_uncommitted:
        shown = "\n".join(f"  {p}" for p in dirty[:20])
        more = f"\n  ...and {len(dirty) - 20} more" if len(dirty) > 20 else ""
        raise Refused(f"{slug} has uncommitted changes, so a restart would load work nobody "
                      f"has committed, possibly another session's half-done edit:\n{shown}{more}\n"
                      "Commit it first, or call again with include_uncommitted if these "
                      "changes are yours and ready to run.")
    return "\n".join(_restart(slug, spec) for spec in targets)


def start(slug: str) -> str:
    _manifest(slug)
    try:
        app_runner.start_app(slug)
    except app_runner.AppError as e:
        raise RuntimeError(e.message) from e
    started = time.monotonic()
    while time.monotonic() - started < START_TIMEOUT_S:
        st = app_runner.app_status(slug)
        if st.get("phase") == "up":
            return f"{slug}: up in {time.monotonic() - started:.0f} s\n" + status_lines(st)
        failed = [s for s in st.get("services", []) if s.get("status") == "failed"]
        if failed and st.get("phase") != "starting":
            lines = [f"{s['name']}: {s.get('error') or 'failed'}\n"
                     f"{_tail(app_runner.app_logs(slug, s['name'], max_bytes=LOG_TAIL_CHARS))}"
                     for s in failed]
            raise RuntimeError(f"{slug} did not start:\n" + "\n".join(lines))
        time.sleep(2.0)
    raise RuntimeError(f"{slug} was still starting after {START_TIMEOUT_S} s\n"
                       + status_lines(app_runner.app_status(slug)))


def stop(slug: str) -> str:
    _manifest(slug)
    st = app_runner.stop_app(slug)
    return f"{slug}: stopped\n" + status_lines(st)


def status_lines(st: dict) -> str:
    lines = []
    for s in st.get("services", []):
        health = {True: "healthy", False: "NOT healthy", None: ""}[s.get("healthy")]
        bits = [s.get("status") or "?", health,
                f"port {s['port']}" if s.get("port") else "",
                f"pid {s['pid']}" if s.get("pid") else ""]
        line = f"  {s['name']}: " + ", ".join(b for b in bits if b)
        if s.get("error"):
            line += f" (last error: {s['error']})"
        lines.append(line)
    for d in st.get("deps") or []:
        lines.append(f"  dependency {d.get('name')}: {d.get('status') or '?'}")
    return "\n".join(lines) or "  no services are tracked"


def _platform_status() -> str:
    """The platform's own state: what is running against what is on disk."""
    r = subprocess.run(["bash", "scripts/apply.sh", "--check"], cwd=str(app_runner.WORKSHOP_ROOT),
                       capture_output=True, text=True, timeout=300)
    return "workshop (the platform), running against what is on disk:\n" + _tail(
        (r.stdout or "") + (r.stderr or ""))


def _platform_logs(service: str, max_chars: int) -> str:
    import os
    units = {"backend": os.environ.get("WORKSHOP_BACKEND_UNIT") or "workshop-backend.service"}
    unit = units.get(service)
    if unit is None:
        raise Refused(f"the platform's services with a log here: {', '.join(units)}")
    r = subprocess.run(["journalctl", "--user", "-u", unit, "-n", "400", "--no-pager"],
                       capture_output=True, text=True, timeout=60)
    text = (r.stdout or "") + (r.stderr or "")
    return text[-max(1000, min(max_chars, 60000)):].strip() or f"{unit} has no journal entries."


def status(slug: str) -> str:
    if slug == PLATFORM:
        return _platform_status()
    manifest = _manifest(slug)
    st = app_runner.app_status(slug)
    declared = ", ".join(s.name for s in manifest.services) or "none"
    dirty = uncommitted(slug)
    tail = f"\n  {len(dirty)} uncommitted file(s) in its repo" if dirty else ""
    return (f"{slug}: {st.get('phase')} (wanted: {st.get('desired')}); services: {declared}\n"
            + status_lines(st) + tail)


def logs(slug: str, service: str, max_chars: int = 8000) -> str:
    if slug == PLATFORM:
        return _platform_logs(service, max_chars)
    _service(_manifest(slug), service)
    text = app_runner.app_logs(slug, service, max_bytes=max(1000, min(max_chars, 60000)))
    return text.strip() or f"{slug}/{service} has written nothing to its log yet."
