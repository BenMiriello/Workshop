"""The work verbs: build, test, check, apply and configure an app, the platform
included, from what each declares.

Design of record: `docs/platform-redesign/operations-and-boundaries-2026-11-04.md`
§2.2. An app declares its commands in `workshop.app.json` (`setup` and `build`
per service; `test`, `checks` and `migrate` for the whole app). The platform is
operated the same way, from the declaration below, which lives in platform
code rather than in the app catalog so a fresh clone still starts with an empty
catalog.

EVERY COMMAND RUNS CAPPED. A declared command runs on the host, in its own
transient systemd scope with a memory ceiling and no swap, at lowered CPU
priority, so a runaway test suite dies alone instead of taking the machine with
it (the 2026-08-05 outage: an uncapped suite grew to 44G and filled swap). An
agent no longer has to remember to wrap anything.

An agent's arguments never become shell text: they are passed as separate
arguments, and a declared command receives them only where it says `"$@"`.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from services import app_ops, app_runner
from services.app_runner import AppCommand

PLATFORM = app_ops.PLATFORM

_PY = "backend/.venv/bin/python"
_PLATFORM_APPLY = AppCommand(command="bash scripts/apply.sh", name="apply", timeout_s=900)
_PLATFORM_TEST = AppCommand(
    name="test", timeout_s=3600, memory="8G",
    command=('if [ $# -gt 0 ]; then cd backend && .venv/bin/python -m pytest -q '
             '-p no:cacheprovider "$@"; else (cd backend && .venv/bin/python -m pytest -q '
             '-p no:cacheprovider) && (cd frontend && npm test --silent); fi'))
_PLATFORM_CHECKS = [
    AppCommand(name="typecheck", command="npm run typecheck", timeout_s=600),
    AppCommand(name="design system", command="bash scripts/check_design.sh", timeout_s=600),
    AppCommand(name="prompt layers", command=f"{_PY} scripts/check_prompt_layers.py"),
    AppCommand(name="no app names in platform code", command=f"{_PY} scripts/check_no_app_names.py"),
    AppCommand(name="planned steps landed", command=f"{_PY} scripts/check_plan_landed.py --quiet"),
]


def is_platform(slug: str) -> bool:
    return slug == PLATFORM or app_runner.is_self_project(slug)


def _root(slug: str) -> Path:
    return app_runner.WORKSHOP_ROOT if is_platform(slug) else app_runner.project_dir(slug)


def _manifest(slug: str) -> app_runner.AppManifest:
    try:
        return app_runner.resolve_manifest(slug)
    except app_runner.AppError as e:
        raise app_ops.Refused(e.message) from e


def run_capped(slug: str, cmd: AppCommand, *, cwd: Path, env: dict[str, str] | None = None,
               args: list[str] | None = None) -> tuple[bool, str, float]:
    """(succeeded, output tail, seconds) for one declared command."""
    unit = f"ws-op-{secrets.token_hex(4)}"
    inner = ["bash", "-c", cmd.command, "_", *[str(a) for a in (args or [])]]
    capped = shutil.which("systemd-run") is not None
    argv = (["systemd-run", "--user", "--scope", "-q", f"--unit={unit}",
             "-p", f"MemoryMax={cmd.memory}", "-p", "MemorySwapMax=0",
             "nice", "-n", "10", *inner] if capped else ["nice", "-n", "10", *inner])
    started = time.monotonic()
    try:
        r = subprocess.run(argv, cwd=str(cwd), env=env or dict(os.environ),
                           capture_output=True, text=True, timeout=cmd.timeout_s)
    except subprocess.TimeoutExpired as e:
        if capped:
            subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"],
                           capture_output=True, timeout=30)
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return False, app_ops._tail(out) + f"\n(stopped: still running after {cmd.timeout_s} s)", \
            time.monotonic() - started
    out = app_ops._tail((r.stdout or "") + (r.stderr or ""))
    if r.returncode == 137 or (r.returncode == -9):
        out += f"\n(killed: it went over its {cmd.memory} memory ceiling)"
    if not capped:
        out += "\n(not memory-capped: this host has no systemd user manager)"
    return r.returncode == 0, out, time.monotonic() - started


def _report(label: str, ok: bool, out: str, secs: float) -> str:
    head = f"{label}: {'passed' if ok else 'FAILED'} in {secs:.0f} s"
    return head + (f"\n{out}" if out and not ok else "")


def test(slug: str, args: list[str] | None = None) -> tuple[str, bool]:
    cmd = _PLATFORM_TEST if is_platform(slug) else _manifest(slug).test
    if cmd is None:
        raise app_ops.Refused(f"{slug} declares no tests: add a `test` command to its "
                              "workshop.app.json.")
    ok, out, secs = run_capped(slug, cmd, cwd=_root(slug) / cmd.cwd, args=args)
    summary = "" if not ok else "\n" + out[-1500:]
    return _report(f"{slug} tests", ok, out, secs) + summary, not ok


def check(slug: str) -> tuple[str, bool]:
    cmds = _PLATFORM_CHECKS if is_platform(slug) else _manifest(slug).checks
    if not cmds:
        raise app_ops.Refused(f"{slug} declares no checks: add `checks` to its workshop.app.json.")
    lines, failed = [], False
    for c in cmds:
        ok, out, secs = run_capped(slug, c, cwd=_root(slug) / c.cwd)
        failed = failed or not ok
        lines.append(_report(c.name or c.command, ok, out, secs))
    return "\n".join(lines), failed


def build(slug: str, service: str | None = None, *, setup: bool = False) -> tuple[str, bool]:
    if is_platform(slug):
        raise app_ops.Refused("the platform is built and made live as a whole: use app_apply.")
    manifest = _manifest(slug)
    specs = [app_ops._service(manifest, service)] if service else manifest.services
    lines, failed = [], False
    for spec in specs:
        steps = ([("setup", spec.setup)] if setup and spec.setup else []) + \
                ([("build", spec.build)] if spec.build else [])
        if not steps:
            lines.append(f"{spec.name}: nothing to build")
            continue
        for label, command in steps:
            cmd = AppCommand(command=command, name=label, timeout_s=app_runner.BUILD_TIMEOUT)
            ok, out, secs = run_capped(slug, cmd, cwd=app_runner.project_dir(slug) / spec.cwd,
                                       env=app_runner.command_env(slug, spec))
            lines.append(_report(f"{spec.name} {label}", ok, out, secs))
            if not ok:
                return "\n".join(lines), True
    return "\n".join(lines), failed


def apply(slug: str, *, include_uncommitted: bool = False) -> tuple[str, bool]:
    """Make the running app match its source: migrate, build, restart what runs."""
    if is_platform(slug):
        ok, out, secs = run_capped(slug, _PLATFORM_APPLY, cwd=app_runner.WORKSHOP_ROOT)
        return f"the platform: apply {'finished' if ok else 'FAILED'} in {secs:.0f} s\n{out}", not ok
    manifest = _manifest(slug)
    dirty = app_ops.uncommitted(slug)
    if dirty and not include_uncommitted:
        raise app_ops.Refused(f"{slug} has uncommitted changes ({', '.join(dirty[:8])}"
                              f"{'...' if len(dirty) > 8 else ''}); commit first, or pass "
                              "include_uncommitted if they are yours and ready to run.")
    lines: list[str] = []
    if manifest.migrate:
        ok, out, secs = run_capped(slug, manifest.migrate,
                                   cwd=app_runner.project_dir(slug) / manifest.migrate.cwd)
        lines.append(_report("migrate", ok, out, secs))
        if not ok:
            return "\n".join(lines + ["nothing was restarted"]), True
    text, failed = build(slug)
    lines.append(text)
    if failed:
        return "\n".join(lines + ["nothing was restarted"]), True
    live = app_ops._live(slug)
    if not any((s or {}).get("status") == "running" for s in live.values()):
        lines.append(f"{slug} is not running, so nothing was restarted; start it with app_control.")
        return "\n".join(lines), False
    lines.append(app_ops.restart(slug, include_uncommitted=True))
    return "\n".join(lines), False


def env(slug: str, values: dict[str, str] | None = None) -> str:
    if is_platform(slug):
        raise app_ops.Refused("the platform's configuration belongs to this installation, "
                              "not to a declared app environment.")
    from services import secrets_store
    if values:
        declared = {r.key for r in _manifest(slug).env_requirements}
        unknown = sorted(set(values) - declared)
        if unknown:
            raise app_ops.Refused(f"{slug} does not declare {', '.join(unknown)}; declare it under "
                                  "env_requirements in its workshop.app.json first.")
        secrets_store.set_values(slug, {k: str(v) for k, v in values.items()})
    ov = app_runner.env_overview(slug)
    lines = [f"{slug}: {len(ov['requirements'])} declared setting(s)"]
    for r in ov["requirements"]:
        flag = "set" if r["set"] else ("MISSING (required)" if r["required"] else "not set")
        lines.append(f"  {r['key']}: {flag} - {r.get('description', '')[:120]}")
    if values:
        lines.append("Saved. A running service sees new values after it restarts (app_control).")
    return "\n".join(lines)


def preview(slug: str, path: str, *, width: int | None = None, height: int | None = None,
            out_root: str | None = None) -> tuple[str, Path | None, bool]:
    """Screenshot one page of an app as the operator's browser renders it, with
    the app's own declared preview settings (scripts/shoot.sh). The picture is
    written under the calling chat's own `tmp/preview/`, so the chat can open it
    again; (report, picture, failed)."""
    if not is_platform(slug):
        _manifest(slug)
    base = Path(out_root) if out_root else _root(slug)
    out = base / "tmp" / "preview" / f"{slug}-{secrets.token_hex(3)}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["--app", str(_root(slug)), path or "/"]
    if width and height:
        args += [str(int(width)), str(int(height))]
    args.append(str(out))
    cmd = AppCommand(command='bash scripts/shoot.sh "$@"', name="preview",
                     timeout_s=240, memory="4G")
    ok, text, secs = run_capped(slug, cmd, cwd=app_runner.WORKSHOP_ROOT, args=args)
    shot = out if out.exists() else None
    return _report(f"{slug} {path or '/'} preview", ok and shot is not None, text, secs) \
        + ("" if not ok else f"\n{text[-1200:]}"), shot, not (ok and shot)
