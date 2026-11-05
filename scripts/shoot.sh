#!/usr/bin/env bash
# Screenshot a page of a running app. One command, and it sets itself up.
#
#   bash scripts/shoot.sh /memory                     first declared viewport
#   bash scripts/shoot.sh /memory 1440 900            an explicit one
#   bash scripts/shoot.sh /apps/<slug>/files 390 800 scratch/files.png
#   bash scripts/shoot.sh --app projects/<slug> /  some other app tree
#   bash scripts/shoot.sh --setup                     bootstrap only
#   bash scripts/shoot.sh --config                    print the resolved config
#
# Credentials, when an app declares a way to get them, are cached under
# scratch/preview/. Delete the .token file there to mint a fresh one.
#
# This file is the CALLER and the DRIVER BOOTSTRAP. It knows nothing about which
# app it is shooting: what origin to hit, how to authenticate, which viewports
# and which browser all come from scripts/preview_config.py, which reads the
# app's own declaration (workshop.app.json `preview`, or workshop.preview.json)
# and falls back to a documented per-stack default. The verb itself is
# scripts/shoot.mjs. See docs/previewing-an-app.md.
#
# Everything it installs lives under scratch/, which is gitignored.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/scratch/preview"
PY="$ROOT/backend/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

APP_DIR="$ROOT"
SETUP_ONLY=0
SHOW_CONFIG=0
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --setup)  SETUP_ONLY=1; shift ;;
    --config) SHOW_CONFIG=1; shift ;;
    --app)    APP_DIR="$2"; shift 2 ;;
    *)        ARGS+=("$1"); shift ;;
  esac
done
case "$APP_DIR" in /*) ;; *) APP_DIR="$ROOT/$APP_DIR" ;; esac

# --- 1. what this app declares ----------------------------------------------
CFG="$("$PY" "$ROOT/scripts/preview_config.py" "$APP_DIR")"
# field a.b -> the value at that path, or empty.
field() { printf '%s' "$CFG" | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
for k in sys.argv[1].split('.'):
    d = d.get(k) if isinstance(d, dict) else None
print(d if d is not None else '')" "$1"; }

if [ "$SHOW_CONFIG" = 1 ]; then printf '%s\n' "$CFG"; exit 0; fi

# --- 2. the driver, bootstrapped per its own rules ---------------------------
# A driver's bootstrap is particular to that driver, so adding one is a case arm
# here rather than a change anywhere else. The two Node traps below are real and
# cost an afternoon to find.
DRIVER_NAME="$(field driver.name)"
DRIVER_BROWSER="$(field driver.browser)"
DRIVER_VERSION="$(field driver.version)"
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"

if [ -z "$DRIVER_VERSION" ]; then
  case "$DRIVER_NAME" in
    playwright-core)
      # TRAP 1: playwright-core above 1.49 hard-refuses to start on Node 18
      # ("requires Node.js 20 or higher"). 1.49.1 is the last line that runs
      # there, so on an older Node the version is pinned rather than floated.
      if [ "$NODE_MAJOR" -lt 20 ]; then DRIVER_VERSION="1.49.1"; else DRIVER_VERSION="latest"; fi ;;
    *) DRIVER_VERSION="latest" ;;
  esac
fi

DRIVER_DIR="$WORK/$DRIVER_NAME@$DRIVER_VERSION"
bootstrap_driver() {
  mkdir -p "$DRIVER_DIR"
  [ -f "$DRIVER_DIR/package.json" ] \
    || echo '{"name":"ws-preview-driver","private":true,"version":"1.0.0"}' > "$DRIVER_DIR/package.json"
  echo "shoot: installing $DRIVER_NAME@$DRIVER_VERSION (once)" >&2
  ( cd "$DRIVER_DIR" && npm i "$DRIVER_NAME@$DRIVER_VERSION" --no-audit --no-fund >/dev/null )
  case "$DRIVER_NAME" in
    playwright-core)
      ( cd "$DRIVER_DIR" && node "node_modules/$DRIVER_NAME/cli.js" install "$DRIVER_BROWSER" ) ;;
  esac
}
# TRAP 3: the package and the BROWSER BINARY are two installs, and only the
# first one lives in this directory. The browser lands in a shared per-user
# cache, so anything that clears or rotates that cache leaves the package here
# and the binary gone, and the guard below used to pass. Measured 2026-08-23:
# shoot died with "Executable doesn't exist at .../chromium_headless_shell-1148"
# while node_modules was intact. Check for both, and let a missing browser
# re-trigger the bootstrap rather than needing a person to know this.
# TRAP 3b, 2026-09-04: the guard accepted a browser the LAUNCHER cannot use.
# `chromium` and `chromium_headless_shell` are two separate downloads, and
# playwright >=1.49 launching `headless: true` wants the SHELL. The `||` above
# meant a cache holding only `chromium-<build>` satisfied the check, bootstrap
# was skipped, and the run then died with "Executable doesn't exist at
# .../chromium_headless_shell-1148" -- the exact error TRAP 3 was written to
# prevent, passing through the guard that was supposed to catch it.
#
# So: require the shell when the driver launches headless, which is every call
# here. An `||` in a presence check is a claim that either thing will do, and
# that claim has now been wrong twice in this file.
browser_present() {
  case "$DRIVER_NAME" in
    playwright-core)
      [ -n "$(ls -d "$HOME/.cache/ms-playwright/${DRIVER_BROWSER}_headless_shell-"*/ 2>/dev/null)" ] ;;
    *) return 0 ;;
  esac
}
{ [ -d "$DRIVER_DIR/node_modules/$DRIVER_NAME" ] && browser_present; } || bootstrap_driver

# TRAP 2: the entry point is resolved to an absolute path and handed to the
# runner. A bare `import "<driver>"` from scripts/ walks up and finds the ROOT
# workspace's own newer copy instead of this pinned one, which is how a pinned
# 1.49 install still died with "requires Node.js 20 or higher".
ENTRY="$DRIVER_DIR/node_modules/$DRIVER_NAME/index.mjs"
[ -f "$ENTRY" ] || ENTRY="$DRIVER_DIR/node_modules/$DRIVER_NAME/index.js"

# --- 3. credentials, if this app needs any -----------------------------------
# The resolver never executes anything; a declared token command runs HERE, with
# the app dir as cwd, and its result is cached under scratch/.
AUTH_MODE="$(field auth.mode)"
AUTH_VALUE=""
if [ "$AUTH_MODE" != "none" ]; then
  AUTH_VALUE="$(field auth.value)"
  CMD="$(field auth.token_command)"
  [ -n "$CMD" ] || CMD="$(field auth.value_command)"
  if [ -z "$AUTH_VALUE" ] && [ -n "$CMD" ]; then
    KEY="$(printf '%s' "$APP_DIR|$AUTH_MODE" | "$PY" -c "import hashlib,sys;print(hashlib.sha1(sys.stdin.read().encode()).hexdigest()[:12])")"
    TOK_FILE="$WORK/$KEY.token"
    if [ ! -s "$TOK_FILE" ]; then
      mkdir -p "$WORK"
      ( cd "$APP_DIR" && sh -c "$CMD" ) > "$TOK_FILE"
      chmod 600 "$TOK_FILE"
    fi
    AUTH_VALUE="$(tr -d '\n' < "$TOK_FILE")"
  fi
fi

# --- 4. hand the resolved config to the generic verb -------------------------
CFG="$(printf '%s' "$CFG" | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
d['driver']['version'] = sys.argv[1]
d['driver']['entry'] = sys.argv[2]
if sys.argv[3]:
    d['auth']['value'] = sys.argv[3]
print(json.dumps(d))" "$DRIVER_VERSION" "$ENTRY" "$AUTH_VALUE")"

if [ "$SETUP_ONLY" = 1 ]; then
  echo "shoot: ready ($DRIVER_NAME@$DRIVER_VERSION, auth $AUTH_MODE)"
  exit 0
fi

cd "$DRIVER_DIR"
printf '%s' "$CFG" | node "$ROOT/scripts/shoot.mjs" ${ARGS[@]+"${ARGS[@]}"}
