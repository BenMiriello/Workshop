#!/usr/bin/env python3
"""Make sure every app present in this checkout is installed into the backend venv.

WHY THIS EXISTS, and it is a measured outage rather than a tidiness step.

An app's backend now reaches the platform through ONE seam: a
`[project.entry-points."workshop.apps"]` line in the app's own `pyproject.toml`,
which `app_registry._entry_point_factories()` reads via `importlib.metadata`.
That seam only exists after the app's distribution has been installed into the
backend's virtualenv. Nothing installed it. `bootstrap.sh` installed the
BACKEND's requirements and stopped; `restart_backend.sh` restarted a process
that then discovered an empty catalog; and an empty catalog is not an error --
`_build_catalog` is deliberately tolerant, because a slimmed instance is
allowed to ship fewer apps.

So on 2026-09-04, with every app's source correctly out of the platform repo,
the running backend served ZERO app routes and logged nothing about it. The
registry said eight apps were installed and the catalog resolved none. That
disagreement is the bug, and `backend/tests/test_catalog_agrees_with_registry.py`
now fails on it.

The fix has to be a step the SYSTEM runs, not one a person remembers, so this
is called from `restart_backend.sh` (before its import preflight, so a restart
can never bring up a backend whose apps are missing) and from `bootstrap.sh`
(so a fresh install has them from the first boot).

IT NAMES NO APP. The roster is a glob over `apps/*/pyproject.toml` and the ids
come from the entry points themselves, so an app appears by existing and
disappears by being deleted, exactly as `app_registry` already requires.

Idempotent and cheap: it installs only the apps whose entry point is absent from
the venv, so the common case is one `importlib.metadata` read and no pip run.

Usage:
    python3 scripts/install_apps.py            # install what is missing
    python3 scripts/install_apps.py --check    # report, change nothing (exit 1 if any missing)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS_DIR = REPO / "apps"
VENV_PY = REPO / "backend" / ".venv" / "bin" / "python"
ENTRY_POINT_GROUP = "workshop.apps"


def app_dirs() -> list[Path]:
    """Every directory under `apps/` that declares itself a Python distribution."""
    if not APPS_DIR.is_dir():
        return []
    return sorted(d for d in APPS_DIR.iterdir() if (d / "pyproject.toml").is_file())


def declared_id(app_dir: Path) -> str:
    """The app's own id, from its manifest, falling back to the directory name.

    Read rather than assumed: the manifest is the app's declaration and the
    directory is only where it happens to sit.
    """
    manifest = app_dir / "workshop.app.json"
    try:
        return str(json.loads(manifest.read_text()).get("name") or app_dir.name)
    except Exception:
        return app_dir.name


def installed_entry_point_names(py: Path) -> set[str]:
    """Entry-point names whose declaration actually LOADS, asked of the venv that
    will run the app.

    IT MUST LOAD, NOT MERELY BE REGISTERED, and that distinction cost two days.
    An app's metadata and its code are installed separately: a distribution can
    publish a perfectly good `workshop.apps` entry point whose module cannot be
    imported at all. When that happens the roster reads N apps, every
    declaration raises, `app_registry._build_catalog` logs and SKIPS each one by
    design (a slimmed instance is allowed to resolve fewer), and the process
    mounts zero routers while every surface reports healthy. Measured 2026-09-05:
    eight apps registered, eight declarations unloadable, every app route 404 for
    two days. A name-only check said "all installed" the entire time.
    """
    code = (
        "from importlib.metadata import entry_points\n"
        f"for e in sorted(entry_points(group={ENTRY_POINT_GROUP!r}), key=lambda e: e.name):\n"
        "    try:\n"
        "        e.load()\n"
        "    except Exception:\n"
        "        continue\n"
        "    print(e.name)\n"
    )
    # cwd=backend, because that is the service's WorkingDirectory and an app's
    # declaration imports the platform's own `services` package by bare name.
    # Asking from anywhere else makes every app look broken -- a false negative
    # exactly as damaging as the false positive this check replaced.
    try:
        out = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                             timeout=120, cwd=str(REPO / "backend"))
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def missing(py: Path) -> list[Path]:
    have = installed_entry_point_names(py)
    return [d for d in app_dirs() if declared_id(d) not in have]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report what is missing and change nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not VENV_PY.exists():
        # No venv yet is not a failure here: bootstrap creates it and calls this
        # afterwards. Inventing a failure would break the first install.
        if not args.quiet:
            print(f"install-apps: no backend venv at {VENV_PY}; nothing to do")
        return 0

    todo = missing(VENV_PY)
    if not todo:
        if not args.quiet:
            print(f"install-apps: {len(app_dirs())} app(s) present, all installed")
        return 0

    names = ", ".join(d.name for d in todo)
    if args.check:
        print(f"install-apps: {len(todo)} app(s) NOT installed into the backend venv: {names}")
        print("              their routes, personas, stores and edge blocks are all absent.")
        print("              fix: python3 scripts/install_apps.py")
        return 1

    print(f"install-apps: installing {len(todo)} app(s): {names}")
    # `python -m pip`, never the `pip` shim beside it. A console script carries a
    # shebang written at creation time, and this venv's says
    # `#!/home/clawd-a/...` -- the path the SERVICE sees. From an agent sandbox,
    # where the same tree is bind-mounted elsewhere, that interpreter does not
    # resolve and the shim dies with "required file not found" before pip runs.
    # Reaching pip through the interpreter that is already known-good has no
    # such dependency. Same root cause as the editable-install note below: a
    # path baked into a file by one process is not portable to another.
    pip = [str(VENV_PY), "-m", "pip"]
    failed: list[str] = []
    for d in todo:
        # `--no-deps`: an app's runtime dependencies belong to the backend's own
        # requirements, and letting pip resolve them here would let one app's
        # pin move the platform's.
        #
        # NOT `-e`. An editable install writes THIS PROCESS'S absolute path into
        # a finder module in site-packages, e.g.
        #     MAPPING = {'birds_app': '/workspace/apps/birds/birds_app'}
        # and the process that runs the app is not this one. An agent installs
        # from a sandbox where the tree is bind-mounted at /workspace; the
        # backend service runs in the host namespace where that path does not
        # exist. The metadata still resolves, so the entry point is registered
        # and the roster reads the app -- and the import fails, the catalog
        # comes up empty, and every route 404s while every check says installed.
        # Measured 2026-09-05: that is exactly what happened, to all eight apps,
        # for two days. A copy into site-packages has no such path dependency
        # and is correct from any namespace. The cost is that editing an app's
        # backend needs a reinstall, which is what apply.sh is for.
        r = subprocess.run([*pip, "install", str(d), "--no-deps", "--quiet"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(d.name)
            print(f"install-apps: FAILED {d.name}\n{r.stdout}{r.stderr}", file=sys.stderr)

    still = missing(VENV_PY)
    if failed or still:
        print("install-apps: still missing after install: "
              + (", ".join(d.name for d in still) or "(none, but pip reported an error)"),
              file=sys.stderr)
        return 1
    print("install-apps: done; the catalog will resolve them on the next import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
