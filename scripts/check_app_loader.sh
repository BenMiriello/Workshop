#!/usr/bin/env bash
# check_app_loader.sh — install an app, see it, remove it, see it go, and prove
# the platform bundle was never rebuilt in between.
#
# THIS IS THE STAGE'S ONE CLAIM, AS A COMMAND. Everything else about the runtime
# loader is a unit test or a static assertion; this is the property itself, end
# to end: a real app directory, a real build with @workshop/app-build, the real
# manifest reader, the real routes, and the real installed-apps registry.
#
# It runs against a THROWAWAY INSTANCE ROOT under tmp/, never the operator's
# own: WORKSHOP_ROOT and WORKSHOP_INSTALLED_APPS both point into it, so
# installing and removing here cannot touch what is installed there. The app is
# `tests/fixtures/app-loader`, a fictional app that is deliberately NOT in the
# shell's build graph, because an app the shell already compiled cannot prove
# anything about an app it did not.
#
#   bash scripts/check_app_loader.sh              # the whole proof
#   bash scripts/check_app_loader.sh --keep       # leave tmp/app-loader-check/
#   bash scripts/check_app_loader.sh --dist DIR   # measure trap A against DIR
#                                                 # rather than frontend/dist
#
# Exit 0 pass, 1 fail. A missing prerequisite (no backend venv, no node) exits 2:
# not measurable is not a pass.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$REPO/tmp/app-loader-check"
FIXTURE="$REPO/tests/fixtures/app-loader"
SLUG="sample-widget"
VENV_PY="$REPO/backend/.venv/bin/python"
VITE="$REPO/node_modules/vite/bin/vite.js"
KEEP=0
DIST="$REPO/frontend/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --dist) DIST="$2"; shift 2 ;;
    *) echo "check_app_loader: unknown argument $1" >&2; exit 2 ;;
  esac
done

for prereq in "$VENV_PY" "$VITE"; do
  if [ ! -f "$prereq" ]; then
    echo "check_app_loader: $prereq is missing; nothing to measure" >&2
    exit 2
  fi
done

rm -rf "$WORK"
mkdir -p "$WORK/apps"
cp -r "$FIXTURE" "$WORK/apps/$SLUG"

echo "== the app builds against the shell's published runtime =="
# Built INSIDE the repo tree on purpose: node resolves @workshop/app-build and
# vite by walking up to the workspace root, which is what an app in the
# operator's own apps/ directory does too.
( cd "$WORK/apps/$SLUG" && node "$VITE" build --logLevel warn )

echo "== its build declares what it emits (trap B) =="
python3 "$REPO/scripts/check_app_loader.py" --app "$WORK/apps/$SLUG"

echo "== the shell's build publishes what it imports (trap A) =="
if python3 "$REPO/scripts/check_app_loader.py" --dist "$DIST" --app "$WORK/apps/$SLUG"; then
  :
else
  status=$?
  if [ "$status" = "2" ]; then
    echo "   (the built bundle predates the import map. Run the apply step, then re-run.)"
  else
    exit "$status"
  fi
fi

echo "== it appears when installed and goes when removed, with nothing rebuilt =="
WORKSHOP_ROOT="$WORK" \
WORKSHOP_INSTALLED_APPS="$WORK/installed-apps.json" \
PYTHONPATH="$REPO/backend" \
"$VENV_PY" - "$SLUG" "$REPO" <<'PY'
import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import base_apps
from services import installed_apps

slug, repo = sys.argv[1], Path(sys.argv[2])

app = FastAPI()
app.include_router(base_apps.router, prefix="/base-apps")
client = TestClient(app)

# WHAT "WITHOUT A REBUILD" MEANS, MEASURED. The shell's build stamp is read
# before and after the whole install/remove cycle: if it moved, something
# rebuilt and the proof is worthless.
stamp = repo / "frontend" / "dist" / "build-info.json"
before = stamp.read_text() if stamp.is_file() else None

def listed():
    r = client.get("/base-apps/contributions")
    assert r.status_code == 200, r.text
    return {a["id"]: a for a in r.json()["apps"]}

failures = []

if slug in listed():
    failures.append("the app was listed before it was installed")

installed_apps.install(slug)
apps = listed()
entry = None
if slug not in apps:
    failures.append("the app was installed and the platform did not list it")
else:
    app_decl = apps[slug]
    if app_decl["error"]:
        failures.append(f"the installed app reported: {app_decl['error']}")
    entry = app_decl["urls"]["entry"]
    if not entry:
        failures.append("the installed app declared no entry URL")
    if not app_decl["pages"]:
        failures.append("the installed app contributed no routed page")
    if app_decl["icon"] != "sliders" or not app_decl["link"]:
        failures.append("the declaration lost the fields the shell draws before loading anything")

if entry:
    r = client.get(entry)
    if r.status_code != 200:
        failures.append(f"the declared bundle URL answered {r.status_code}")
    elif "contract" not in r.text or "exports" not in r.text:
        failures.append("the served bundle does not carry the contract shape")
    elif not r.headers.get("cache-control", "").startswith("public, max-age=31536000"):
        failures.append("a content-hashed URL is not served immutable")
    css = client.get(entry.replace("app.js", "app.css").split("?")[0])
    if css.status_code != 200 or "--wsfix-loaded" not in css.text:
        failures.append("the app's own stylesheet is not served")

installed_apps.remove(slug)
if slug in listed():
    failures.append("the app was removed and the platform still listed it")
if entry and client.get(entry).status_code != 404:
    failures.append("a removed app's bundle is still reachable")

after = stamp.read_text() if stamp.is_file() else None
if before != after:
    failures.append("the shell's build stamp changed during the run, so something rebuilt")

if failures:
    print("check_app_loader: FAIL")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)

print(f"check_app_loader: an app was installed and removed at runtime; the shell's build "
      f"stamp is unchanged ({json.loads(before)['source_id'] if before else 'no build yet'})")
PY

if [ "$KEEP" = "0" ]; then rm -rf "$WORK"; fi
echo "check_app_loader: PASS"
