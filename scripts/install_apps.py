#!/usr/bin/env python3
"""Make sure every app present in this checkout is installed into the backend venv,
and that each installed copy is the app's current source.

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

Idempotent and cheap: it installs the apps whose entry point is absent from the
venv, and reinstalls those whose installed package files differ from the source
(`stale`), so the common case is one `importlib.metadata` read and no pip run.
The install is a COPY (see the `-e` note in main), so an edit to an app's backend
is not what the backend runs until this has reinstalled it; on 2026-10-24 the
running backend of one app was six days and two commits behind its source because
only a missing app was ever installed. `backend_source_id.sh` hashes the same
files (`--source-files`), so an app edit is a backend change to apply.sh.

Usage:
    python3 scripts/install_apps.py            # install what is missing
    python3 scripts/install_apps.py --check    # report, change nothing (exit 1 if any missing or stale)
    python3 scripts/install_apps.py --source-files   # the app source files an install copies, NUL-separated
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
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


def package_dirs(app_dir: Path) -> list[Path]:
    """The package directories an install copies, as the app's pyproject names them."""
    try:
        conf = tomllib.loads((app_dir / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    names = conf.get("tool", {}).get("setuptools", {}).get("packages") or []
    out = []
    for name in names if isinstance(names, list) else []:
        rel = Path(*str(name).split("."))
        for base in (app_dir, app_dir / "src"):
            if (base / rel).is_dir():
                out.append(base / rel)
                break
    return out


def source_files(app_dir: Path) -> dict[str, Path]:
    """Each Python file an install of this app copies, keyed by its path inside
    site-packages."""
    files: dict[str, Path] = {}
    for pkg in package_dirs(app_dir):
        root = pkg.parent
        for f in pkg.rglob("*.py"):
            if "__pycache__" not in f.parts:
                files[f.relative_to(root).as_posix()] = f
    return files


def installed_files(py: Path) -> dict[str, dict[str, str]]:
    """Per entry-point name: each installed Python file of its distribution, keyed
    like `source_files`, with where it is on disk."""
    code = (
        "import json\n"
        "from importlib.metadata import entry_points\n"
        "out = {}\n"
        f"for e in entry_points(group={ENTRY_POINT_GROUP!r}):\n"
        "    d = e.dist\n"
        "    if d is None: continue\n"
        "    out[e.name] = {str(f): str(d.locate_file(f)) for f in (d.files or [])\n"
        "                   if str(f).endswith('.py') and not str(f).startswith('..')}\n"
        "print(json.dumps(out))\n"
    )
    try:
        out = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def stale(py: Path) -> list[Path]:
    """Installed apps whose installed files are not their current source."""
    have = installed_files(py)
    out = []
    for d in app_dirs():
        inst = have.get(declared_id(d))
        src = source_files(d)
        if inst is None or not src:
            continue
        if set(inst) != set(src) or any(Path(inst[k]).read_bytes() != src[k].read_bytes() for k in src):
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report what is missing and change nothing")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--source-files", action="store_true",
                    help="print the app source files an install copies, NUL-separated, and exit")
    args = ap.parse_args()

    if args.source_files:
        for d in app_dirs():
            for f in sorted(source_files(d).values()):
                sys.stdout.write(f"{f.relative_to(REPO).as_posix()}\0")
        return 0

    if not VENV_PY.exists():
        # No venv yet is not a failure here: bootstrap creates it and calls this
        # afterwards. Inventing a failure would break the first install.
        if not args.quiet:
            print(f"install-apps: no backend venv at {VENV_PY}; nothing to do")
        return 0

    absent = missing(VENV_PY)
    old = [d for d in stale(VENV_PY) if d not in absent]
    todo = absent + old
    if not todo:
        if not args.quiet:
            print(f"install-apps: {len(app_dirs())} app(s) present, all installed and current")
        return 0

    if args.check:
        if absent:
            print(f"install-apps: {len(absent)} app(s) NOT installed into the backend venv: "
                  + ", ".join(d.name for d in absent))
            print("              their routes, personas, stores and edge blocks are all absent.")
        if old:
            print(f"install-apps: {len(old)} app(s) installed from older source: "
                  + ", ".join(d.name for d in old))
            print("              the backend runs the old copy until they are reinstalled.")
        print("              fix: python3 scripts/install_apps.py")
        return 1

    print(f"install-apps: installing {len(todo)} app(s): {', '.join(d.name for d in todo)}")
    # `python -m pip`, never the `pip` shim beside it. A console script carries a
    # shebang written at creation time, and this venv's says
    # `#!/home/user/...` -- the path the SERVICE sees. From an agent sandbox,
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
        #     MAPPING = {'widgets_app': '/workspace/apps/widgets/widgets_app'}
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
        # `--force-reinstall` for a stale one: its version number has not moved,
        # and the copy, not the version, is what is out of date.
        again = ["--force-reinstall"] if d in old else []
        r = subprocess.run([*pip, "install", str(d), "--no-deps", "--quiet", *again],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(d.name)
            print(f"install-apps: FAILED {d.name}\n{r.stdout}{r.stderr}", file=sys.stderr)

    still = missing(VENV_PY) + stale(VENV_PY)
    if failed or still:
        print("install-apps: still missing or stale after install: "
              + (", ".join(d.name for d in still) or "(none, but pip reported an error)"),
              file=sys.stderr)
        return 1
    print("install-apps: done; the catalog will resolve them on the next import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
