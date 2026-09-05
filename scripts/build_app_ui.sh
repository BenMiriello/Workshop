#!/usr/bin/env bash
# build_app_ui.sh — rebuild the installed apps' UI bundles that have drifted.
#
# WHY THIS SURFACE EXISTS AT ALL. Until the runtime loader landed, an app's
# frontend was compiled INTO the shell, so `apply.sh` rebuilding the bundle
# rebuilt the apps too. It is not any more: an app's screens are its own ESM
# bundle under `<app>/<ui.dir>`, fetched by URL, and NOTHING in the deploy path
# compiled it. So editing an app page and running the apply step published
# nothing, silently, on the one origin the operator actually looks at -- the
# exact shape of failure the stale-bundle warning exists to prevent, arriving
# from a direction that warning cannot see (`ui_source_id.sh` does not hash an
# app's tree, correctly, because the shell no longer compiles it).
#
# HOW IT DECIDES. Content hash of the app's own build inputs against the stamp
# its last build left in the output directory. Content, not mtime: `git
# checkout` and `touch` both move mtimes without changing what would be emitted.
# Idempotent, so running it twice does nothing the second time.
#
# THIS NAMES NO APP. It walks `apps/*/workshop.app.json`, and an app is in scope
# because its own manifest declares a `ui.entry`. A headless app declares none
# and is skipped; an app with no manifest is not an app.
#
#   bash scripts/build_app_ui.sh            rebuild whatever drifted
#   bash scripts/build_app_ui.sh --check    report drift, change nothing
#
# Exit 0 when everything in scope is current (or was made current). Exit 1 when
# a build failed, or, under --check, when something is stale. A missing
# toolchain is reported and is not a pass.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
APPS_DIR="$REPO/apps"
VITE="$REPO/node_modules/vite/bin/vite.js"
STAMP=".source-id"
CHECK=0
DRIFT=0
FAILED=0
BUILT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    *) echo "build_app_ui: unknown argument $1" >&2; exit 2 ;;
  esac
done

# A fresh clone has no apps/ at all. Nothing to build is the correct state of an
# empty container, not a problem.
[ -d "$APPS_DIR" ] || { echo "app-ui: no apps installed"; exit 0; }

# `ui.dir` and `ui.entry` from one app's manifest, as two lines, or nothing when
# this app declares no UI bundle. The manifest reader here is deliberately
# minimal: the AUTHORITY is backend/services/app_ui.py, which the shell reads
# through, and duplicating its tolerance would be a second reader to keep in
# step. All this needs to know is where to build and whether to bother.
read_ui() {
  python3 - "$1" <<'PY'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
for name in ("workshop.app.json", "workshop.app.jsonc"):
    p = root / name
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    if name.endswith("jsonc"):
        text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))
    try:
        ui = (json.loads(text) or {}).get("ui") or {}
    except json.JSONDecodeError:
        raise SystemExit(0)
    if not isinstance(ui, dict) or not ui.get("entry"):
        raise SystemExit(0)
    print(ui.get("dir") or "dist/ui")
    print(ui["entry"])
    raise SystemExit(0)
PY
}

# What the app's own build would consume. Its source, its config, and its
# manifest -- the manifest because it names the entry, the styles and the
# provider entry, so editing it can change whether a build is correct.
source_id() {
  local root="$1" inputs=()
  local p
  for p in "$root/ui" "$root/vite.config.js" "$root/vite.config.mjs" \
           "$root/vite.config.ts" "$root/workshop.app.json" \
           "$root/workshop.app.jsonc" "$root/package.json"; do
    [ -e "$p" ] && inputs+=("$p")
  done
  [ ${#inputs[@]} -eq 0 ] && { echo "no-source"; return; }
  find "${inputs[@]}" -type f -print0 2>/dev/null \
    | LC_ALL=C sort -z \
    | xargs -0 sha1sum 2>/dev/null \
    | sha1sum \
    | cut -c1-12
}

for manifest in "$APPS_DIR"/*/workshop.app.json "$APPS_DIR"/*/workshop.app.jsonc; do
  [ -f "$manifest" ] || continue
  root="$(dirname "$manifest")"
  slug="$(basename "$root")"
  mapfile -t ui < <(read_ui "$root")
  [ "${#ui[@]}" -eq 2 ] || continue          # declares no bundle: nothing to build
  out="$root/${ui[0]}"
  want="$(source_id "$root")"
  have="$(cat "$out/$STAMP" 2>/dev/null || echo none)"

  if [ -f "$out/${ui[1]}" ] && [ "$want" = "$have" ]; then
    echo "  ok      $slug ui ($want)"
    continue
  fi
  DRIFT=1
  if [ "$CHECK" = 1 ]; then
    echo "  STALE   $slug ui: built=$have source=$want"
    continue
  fi
  if [ ! -f "$VITE" ]; then
    echo "  warn    $slug ui is stale and vite is not installed; run npm install" >&2
    FAILED=1
    continue
  fi
  echo "  apply   $slug ui: built=$have source=$want"
  if ( cd "$root" && node "$VITE" build --logLevel warn >/dev/null ); then
    # AFTER the build, never before: the preset empties the output directory,
    # so a stamp written first is deleted and every run rebuilds.
    echo "$want" > "$out/$STAMP"
    BUILT=1
    echo "  ok      $slug ui rebuilt ($want)"
  else
    echo "  FAILED  $slug ui build failed; rerun by hand: cd $root && npm run build" >&2
    FAILED=1
  fi
done

[ "$FAILED" = 1 ] && exit 1
if [ "$CHECK" = 1 ]; then exit $DRIFT; fi
[ "$BUILT" = 0 ] && [ "$DRIFT" = 0 ] && echo "app-ui: every installed app's bundle is current"
exit 0
