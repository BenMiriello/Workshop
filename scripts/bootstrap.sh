#!/usr/bin/env bash
# Bootstrap — take a fresh clone to a running Workshop.
#
# There was no script for this and no doc that described it correctly, so every
# new instance was reconstructed by hand from a README whose bring-up section
# had not worked since the frontend became an npm workspace. This is the one
# path. If a step here is wrong, fix it here rather than in your shell history.
#
# Idempotent. Every step is safe to repeat: existing config is never overwritten,
# the venv and node_modules are reused, and the installers it calls are
# themselves idempotent.
#
# It deliberately STOPS after creating config templates the first time. Two
# values (the public hostname and the tunnel id) cannot be guessed, and a
# half-configured install that starts anyway is worse than one that says what it
# needs.
#
#   bash scripts/bootstrap.sh                 full bring-up
#   bash scripts/bootstrap.sh --skip-services build everything, install no units
#   bash scripts/bootstrap.sh --no-frontend   backend only (headless/API)
#   bash scripts/bootstrap.sh --dev           also install test dependencies
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR="${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}"
SKIP_SERVICES=0
NO_FRONTEND=0
DEV=0
for arg in "$@"; do
  case "$arg" in
    --skip-services) SKIP_SERVICES=1 ;;
    --no-frontend)   NO_FRONTEND=1 ;;
    --dev)           DEV=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" = "0" ]; then
  echo "Refusing to run as root. Run as the user that will own the services." >&2
  exit 1
fi

cd "$REPO_ROOT"
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# ---- 1. config --------------------------------------------------------------
# Before preflight, because preflight REQUIRES instance.env and the whole point
# of the first run is to create it.
step "config in $CFG_DIR"
mkdir -p "$CFG_DIR"
FRESH_CONFIG=0
for pair in "instance.env:infra/systemd/instance.env.example" \
            "secrets.env:infra/systemd/secrets.env.example"; do
  name="${pair%%:*}"; example="${pair#*:}"
  if [ -f "$CFG_DIR/$name" ]; then
    echo "  $name exists, leaving it alone"
  elif [ -f "$REPO_ROOT/$example" ]; then
    cp "$REPO_ROOT/$example" "$CFG_DIR/$name"
    chmod 600 "$CFG_DIR/$name"
    echo "  created $name from $example (mode 600)"
    FRESH_CONFIG=1
  fi
done
if [ ! -f "$CFG_DIR/auth.env" ]; then
  # Not from an example: it holds one credential and an example would only ever
  # be copied verbatim and then not work.
  umask 077; printf '# Agent credentials. Subscription auth only.\nCLAUDE_CODE_OAUTH_TOKEN=\n' > "$CFG_DIR/auth.env"
  chmod 600 "$CFG_DIR/auth.env"
  echo "  created auth.env (mode 600) — it needs your token"
  FRESH_CONFIG=1
fi

if [ "$FRESH_CONFIG" = "1" ]; then
  cat <<EOF

Config templates are in place and need you before anything can start:

  $CFG_DIR/auth.env       CLAUDE_CODE_OAUTH_TOKEN=...   (required: every agent turn uses it)
  $CFG_DIR/instance.env   WORKSHOP_PUBLIC_ORIGIN, WORKSHOP_LOCAL_ORIGIN, and
                          WORKSHOP_PUBLIC_HOST + WORKSHOP_TUNNEL_ID if you want a public origin

Fill those in, then run this script again. Nothing has been started.
EOF
  exit 0
fi

# ---- 2. git hooks -----------------------------------------------------------
step "git hooks"
bash "$REPO_ROOT/scripts/install_hooks.sh"

# ---- 3. python --------------------------------------------------------------
step "python backend"
if [ ! -x "$REPO_ROOT/backend/.venv/bin/python" ]; then
  python3 -m venv "$REPO_ROOT/backend/.venv"
  echo "  created backend/.venv"
fi
"$REPO_ROOT/backend/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_ROOT/backend/.venv/bin/pip" install --quiet -r "$REPO_ROOT/backend/requirements.txt"
if [ "$DEV" = "1" ] && [ -f "$REPO_ROOT/backend/requirements-dev.txt" ]; then
  "$REPO_ROOT/backend/.venv/bin/pip" install --quiet -r "$REPO_ROOT/backend/requirements-dev.txt"
fi
echo "  dependencies installed"

# Any app present in this checkout gets installed into the same venv, so its
# `workshop.apps` entry point exists and the catalog can resolve it. A fresh
# clone has no `apps/` directory and this is a no-op; it is here so that an
# instance that DOES have apps never boots with an empty catalog and no error.
"$REPO_ROOT/backend/.venv/bin/python" "$REPO_ROOT/scripts/install_apps.py" || true

# ---- 4. node ----------------------------------------------------------------
if [ "$NO_FRONTEND" = "0" ]; then
  step "node workspace"
  # AT THE ROOT. This is an npm workspace ("packages/*" + "frontend"), so
  # installing inside frontend/ leaves @workshop/ui unresolvable and is what the
  # old README told people to do.
  # `npm ci` when the lockfile is there, because that is the whole reason it is
  # committed: an exact, reproducible tree rather than a fresh resolve that can
  # drift (a downstream instance re-resolved its way to two React majors). It
  # refuses to run if package.json and the lock disagree, which is the correct
  # loud failure; fall back only when there is no lockfile at all.
  if [ -f "$REPO_ROOT/package-lock.json" ]; then
    npm ci --silent
  else
    npm install --silent
  fi
  # The kit's dist must exist BEFORE any typecheck: vite resolves @workshop/ui to
  # the sources via a custom condition, but tsc goes through the exports map to
  # dist/index.d.ts. A missing dist therefore builds fine and fails typecheck.
  npm run build:ui --silent
  npm run build:web --silent
  echo "  workspace installed, kit and web bundle built"
fi

# ---- 5. state ---------------------------------------------------------------
step "runtime directories"
mkdir -p "${WORKSHOP_RUNTIME:-$REPO_ROOT/.runtime}" "$REPO_ROOT/tmp"
echo "  ready"

# ---- 6. preflight -----------------------------------------------------------
# After the build, so it checks the state bootstrap just produced rather than
# the state it started from.
step "preflight"
if ! bash "$REPO_ROOT/scripts/preflight.sh"; then
  echo
  echo "Preflight found something required still missing. Fix the items above and re-run." >&2
  exit 1
fi

# ---- 7. services ------------------------------------------------------------
if [ "$SKIP_SERVICES" = "1" ]; then
  step "done (services skipped)"
  echo "Everything is built. No systemd units were installed."
  exit 0
fi

step "systemd units"
bash "$REPO_ROOT/scripts/install_service.sh"
if [ "$NO_FRONTEND" = "0" ] && [ -f "$REPO_ROOT/infra/systemd/workshop-frontend.service" ]; then
  bash "$REPO_ROOT/scripts/install_frontend_service.sh"
fi

# The public edge needs two values that cannot be derived, so it is opt-in on
# their presence rather than always attempted.
# shellcheck source=lib/instance.sh
. "$REPO_ROOT/scripts/lib/instance.sh" 2>/dev/null || true
if [ -n "${WORKSHOP_PUBLIC_HOST:-}" ] && [ -n "${WORKSHOP_TUNNEL_ID:-}" ]; then
  bash "$REPO_ROOT/scripts/install_public_service.sh"
else
  echo "  skipping the public edge: WORKSHOP_PUBLIC_HOST / WORKSHOP_TUNNEL_ID are not set in instance.env"
fi

# Cron is the scheduler for everything periodic; there are no systemd timers.
# Opt-in because a clone that only serves the UI wants none of it, and because
# the block includes host-specific schedules (the operator's commute windows)
# that are meaningless elsewhere. Autonomous entries ship commented out and
# install_cron.sh does not change that.
if [ "${WITH_CRON:-0}" = "1" ]; then
  step "scheduled work"
  bash "$REPO_ROOT/scripts/install_cron.sh"
else
  echo "  skipping cron: re-run with WITH_CRON=1, or bash scripts/install_cron.sh"
fi

step "done"
cat <<EOF
Workshop is installed.

  backend    systemctl --user status workshop-backend
  frontend   systemctl --user status workshop-frontend
  check      bash scripts/preflight.sh
  origins    bash scripts/ui_sync.sh
  schedule   bash scripts/install_cron.sh --check

After any change under frontend/, rebuild the public bundle:
  bash scripts/build_workshop_public.sh
EOF
