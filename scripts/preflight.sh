#!/usr/bin/env bash
# Preflight — can this machine actually run Workshop, and what is missing?
#
# Read-only. It changes nothing, starts nothing, and installs nothing. Run it on
# a fresh clone before bootstrap, and any time the deployment is behaving as if
# something is absent, because the honest answer to "why is X empty" is usually
# that a prerequisite was never there.
#
# THE POINT IS TO FAIL LOUDLY AND ALL AT ONCE. Every check runs; the failures are
# collected and printed together with what to do about each. Stopping at the
# first missing thing turns bring-up into a dozen sequential guesses, which is
# how a downstream instance ended up hand-creating directories under a root that
# was wrong in the first place.
#
# Exit 0 = ready. Exit 1 = something REQUIRED is missing. Optional gaps are
# reported and never fail the run: a box with no cloudflared is a fine box, it
# just has no public origin.
#
#   bash scripts/preflight.sh            human-readable
#   bash scripts/preflight.sh --json     machine-readable, for a gate
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR="${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}"
JSON=0
[ "${1:-}" = "--json" ] && JSON=1

FAILS=()   # required, missing -> exit 1
WARNS=()   # optional, missing -> reported only
OKS=()

req() {  # req <label> <ok?> <remedy>
  if [ "$2" = "1" ]; then OKS+=("$1"); else FAILS+=("$1|$3"); fi
}
opt() {
  if [ "$2" = "1" ]; then OKS+=("$1"); else WARNS+=("$1|$3"); fi
}
have() { command -v "$1" >/dev/null 2>&1 && echo 1 || echo 0; }

# ---- binaries ---------------------------------------------------------------
req "python3"      "$(have python3)"  "apt install python3 python3-venv"
# Debian splits venv into its own package and the failure only shows up later,
# at the point where bootstrap tries to create one.
if command -v python3 >/dev/null 2>&1; then
  python3 -m venv --help >/dev/null 2>&1 && v=1 || v=0
  req "python3 -m venv" "$v" "apt install python3-venv"
  PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null && pv=1 || pv=0
  req "python >= 3.10 (found $PYV)" "$pv" "the backend uses 3.10+ syntax; install a newer python3"
fi
req "git"          "$(have git)"      "apt install git"
req "curl"         "$(have curl)"     "apt install curl"
req "flock"        "$(have flock)"    "apt install util-linux"
req "node"         "$(have node)"     "install node 20+ (see .nvmrc)"
req "npm"          "$(have npm)"      "ships with node"
req "systemctl"    "$(have systemctl)" "this deployment model is systemd --user; see docs/deploying.md"

# The agent CLI. Without it the backend serves fine and every chat turn fails,
# which reads as a broken app rather than a missing dependency.
CLAUDE_BIN="${WORKSHOP_CLAUDE_CLI:-${CLAUDE_CLI:-claude}}"
command -v "$CLAUDE_BIN" >/dev/null 2>&1 && c=1 || c=0
req "claude CLI ($CLAUDE_BIN)" "$c" "npm i -g @anthropic-ai/claude-code, or set WORKSHOP_CLAUDE_CLI"

opt "docker"       "$(have docker)"   "the app runner needs it; without it apps fall back to bare subprocesses"
opt "gh"           "$(have gh)"       "apt install gh && gh auth login (needed for PRs and issue trackers)"
opt "jq"           "$(have jq)"       "apt install jq (the reviewer gate uses it)"
opt "ffmpeg"       "$(have ffmpeg)"   "apt install ffmpeg (birds audio only)"
opt "rsync"        "$(have rsync)"    "apt install rsync (rsync/ssh deploy provider only)"

# ---- node/python version detail --------------------------------------------
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  # 20 is the floor, not a preference: package.json `engines`, .nvmrc, the
  # screenshot driver and several dependencies all need it, and on 18 an install
  # silently skips optional packages and later commands fail far from the cause.
  # scripts/require_node.cjs refuses the build/typecheck/test path on the same
  # floor.
  [ "${NODE_MAJOR:-0}" -ge 20 ] && n=1 || n=0
  req "node >= 20 (found $(node -v 2>/dev/null))" "$n" "install node 20 (see .nvmrc)"
fi

# ---- config files -----------------------------------------------------------
[ -f "$CFG_DIR/instance.env" ] && f=1 || f=0
req "$CFG_DIR/instance.env" "$f" \
    "cp infra/systemd/instance.env.example $CFG_DIR/instance.env && chmod 600 it, then fill it in"

if [ -f "$CFG_DIR/instance.env" ]; then
  MODE="$(stat -c %a "$CFG_DIR/instance.env" 2>/dev/null || echo '?')"
  [ "$MODE" = "600" ] && m=1 || m=0
  opt "instance.env is mode 600 (found $MODE)" "$m" "chmod 600 $CFG_DIR/instance.env"
  # `export KEY=value` parses as a variable literally named exportKEY in
  # scripts/lib/instance.sh, and systemd's EnvironmentFile rejects the line
  # outright. Plain KEY=value is the only form every reader agrees on.
  if grep -qE '^\s*export\s' "$CFG_DIR/instance.env" 2>/dev/null; then
    WARNS+=("instance.env has 'export' lines|remove the export prefix; use plain KEY=value (systemd and lib/instance.sh both mis-read it)")
  fi
fi

[ -f "$CFG_DIR/secrets.env" ] && f=1 || f=0
opt "$CFG_DIR/secrets.env" "$f" "scripts/install_service.sh seeds it from infra/systemd/secrets.env.example"

AUTH_ENV="${WORKSHOP_AUTH_ENV:-$CFG_DIR/auth.env}"
[ -s "$AUTH_ENV" ] && f=1 || f=0
req "$AUTH_ENV (agent credentials)" "$f" \
    "write CLAUDE_CODE_OAUTH_TOKEN=... into it and chmod 600"
if [ -s "$AUTH_ENV" ]; then
  grep -q 'CLAUDE_CODE_OAUTH_TOKEN' "$AUTH_ENV" && f=1 || f=0
  # Note: `claude auth status` is NOT a liveness probe. It reports local config
  # and says logged-in for a token the server has already rejected.
  req "CLAUDE_CODE_OAUTH_TOKEN present" "$f" "add CLAUDE_CODE_OAUTH_TOKEN=... to $AUTH_ENV"
fi

# ---- build artifacts --------------------------------------------------------
[ -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ] && f=1 || f=0
req "backend/.venv" "$f" "bash scripts/bootstrap.sh (or: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt)"

if [ -x "$REPO_ROOT/backend/.venv/bin/python" ]; then
  # A missing python-multipart does not fail at install, it fails at app import,
  # because FastAPI only raises when a route declares Form/UploadFile.
  "$REPO_ROOT/backend/.venv/bin/python" -c \
    'import fastapi, uvicorn, httpx, pydantic, yaml, jwt, multipart' >/dev/null 2>&1 && f=1 || f=0
  req "backend dependencies importable" "$f" "backend/.venv/bin/pip install -r backend/requirements.txt"
fi

[ -d "$REPO_ROOT/node_modules/@workshop/ui" ] && f=1 || f=0
req "node_modules/@workshop/ui (workspace link)" "$f" \
    "npm install AT THE REPO ROOT, not in frontend/ (this is an npm workspace)"

# tsc resolves @workshop/ui through the package's exports map, which points at
# dist. vite reads the sources instead, so a missing dist builds fine and fails
# typecheck: invisible locally, fatal in the gate.
[ -f "$REPO_ROOT/packages/ui/dist/index.d.ts" ] && f=1 || f=0
req "packages/ui/dist" "$f" "npm run build:ui (needed before any typecheck)"

[ -f "$REPO_ROOT/frontend/dist/index.html" ] && f=1 || f=0
opt "frontend/dist" "$f" "npm run build:web (the public origin serves this from disk)"

[ "$(git -C "$REPO_ROOT" config --get core.hooksPath 2>/dev/null)" = ".githooks" ] && f=1 || f=0
opt "git hooks installed" "$f" "bash scripts/install_hooks.sh (post-commit rebuilds the public bundle)"

# ---- writable state ---------------------------------------------------------
RUNTIME="${WORKSHOP_RUNTIME:-$REPO_ROOT/.runtime}"
mkdir -p "$RUNTIME" 2>/dev/null
[ -w "$RUNTIME" ] && f=1 || f=0
req "$RUNTIME writable" "$f" "chown/chmod it to the user running the service"

# ---- services ---------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  docker version --format '{{.Server.Version}}' >/dev/null 2>&1 && f=1 || f=0
  opt "docker daemon reachable" "$f" \
      "start it. For rootless: systemctl --user start docker, and set DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/docker.sock"
fi

if command -v loginctl >/dev/null 2>&1; then
  [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ] && f=1 || f=0
  opt "linger enabled for $USER" "$f" \
      "sudo loginctl enable-linger $USER, or user services stop at logout and never start at boot"
fi

# A port held by SOMEONE ELSE is the nastiest of these, because the squatter
# answers health checks and the instance looks up while serving another tenant.
port_note() {  # port_note <port> <what>
  command -v ss >/dev/null 2>&1 || return 0
  # -p is what attaches the owning process; without it NOTHING ever shows a
  # `users:` field and every bound port reads as foreign. The kernel only names
  # processes belonging to this uid, so an empty users: on a bound port is
  # exactly the "someone else holds it" case.
  local line; line="$(ss -ltnHp "sport = :$1" 2>/dev/null | head -1)"
  [ -z "$line" ] && return 0
  if ! printf '%s' "$line" | grep -q "users:"; then
    WARNS+=(":$1 is bound by another user|$2 cannot bind it. A foreign process on this port answers health checks and looks healthy.")
  fi
}
port_note 8000 "the backend"
port_note 5173 "the vite dev server"
port_note 8002 "the public edge"

# ---- scheduled work ---------------------------------------------------------
# Cron is this deployment's scheduler and nothing else reports on it: there are
# no systemd timers. A missing or drifted block means the commute briefs, the
# daily digest and the frontend watchdog silently do not happen, and the only
# symptom is an absence -- which is the hardest kind of failure to notice.
# Optional, not required: a clone that only serves the UI needs none of it.
if command -v crontab >/dev/null 2>&1; then
  if bash "$REPO_ROOT/scripts/install_cron.sh" --check >/dev/null 2>&1; then
    OKS+=("cron block installed and matches infra/cron/workshop.crontab")
  elif crontab -l 2>/dev/null | grep -qF "managed by scripts/install_cron.sh"; then
    WARNS+=("cron block DRIFTED from infra/cron/workshop.crontab|bash scripts/install_cron.sh --check shows the diff; re-run install_cron.sh to reconcile")
  else
    WARNS+=("cron block not installed|bash scripts/install_cron.sh — without it nothing scheduled runs (commute briefs, daily digest, frontend watchdog)")
  fi
fi

# ---- output -----------------------------------------------------------------
if [ "$JSON" = "1" ]; then
  printf '{"ok":%s,"failures":[' "$([ ${#FAILS[@]} -eq 0 ] && echo true || echo false)"
  for i in "${!FAILS[@]}"; do
    [ "$i" -gt 0 ] && printf ','
    printf '{"check":"%s","remedy":"%s"}' "${FAILS[$i]%%|*}" "${FAILS[$i]#*|}"
  done
  printf '],"warnings":['
  for i in "${!WARNS[@]}"; do
    [ "$i" -gt 0 ] && printf ','
    printf '{"check":"%s","remedy":"%s"}' "${WARNS[$i]%%|*}" "${WARNS[$i]#*|}"
  done
  printf '],"passed":%d}\n' "${#OKS[@]}"
else
  echo "preflight: ${#OKS[@]} ok, ${#WARNS[@]} optional missing, ${#FAILS[@]} required missing"
  if [ ${#WARNS[@]} -gt 0 ]; then
    echo
    echo "OPTIONAL (a feature is off, the app still runs):"
    for w in "${WARNS[@]}"; do printf '  - %s\n      %s\n' "${w%%|*}" "${w#*|}"; done
  fi
  if [ ${#FAILS[@]} -gt 0 ]; then
    echo
    echo "REQUIRED (bring-up will not work until these are fixed):"
    for f in "${FAILS[@]}"; do printf '  - %s\n      %s\n' "${f%%|*}" "${f#*|}"; done
  fi
fi

[ ${#FAILS[@]} -eq 0 ]
