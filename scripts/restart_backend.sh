#!/usr/bin/env bash
# Restart the Workshop backend.
#
# Preferred path: the systemd --user service installed by
# scripts/install_service.sh (`systemctl --user restart workshop-backend`).
# This is the canonical setup — it auto-starts on boot and auto-restarts on
# crash. If the unit is not installed (e.g. a minimal/dev box without systemd),
# this falls back to the legacy nohup launch on :8000.
set -euo pipefail

SERVICE=workshop-backend.service
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

have_unit() {
  command -v systemctl >/dev/null 2>&1 \
    && systemctl --user cat "$SERVICE" >/dev/null 2>&1
}

# Deferred mode: schedule the restart DETACHED and after a short delay, then exit
# immediately. A builder agent editing the Workshop runs `restart_backend.sh
# --deferred` as its final action so this backend — which is serving the agent's
# own chat turn — only goes down AFTER the reply has been delivered. setsid
# detaches the restarter from the agent's process tree so it survives the backend
# dying. (See docs/app-platform-runner.md, "restart the server that hosts the agent".)
if [ "${1:-}" = "--deferred" ]; then
  delay="${2:-5}"
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  setsid bash -c "sleep ${delay}; bash '${self}'" >>/tmp/workshop-backend.log 2>&1 </dev/null &
  disown || true
  echo "Backend restart scheduled in ${delay}s (deferred + detached) — this reply lands first, then it reloads with your changes."
  exit 0
fi

# --- Refuse to restart on top of a live chat turn --------------------------
# Restarting mid-turn is operator-visible damage, not just a blip. The turn's
# process is killed; on startup reattach_running_turns() adopts the orphan and
# re-tails its journal FROM THE BEGINNING, rebuilding the assistant message's
# segments from scratch and persisting the partial rebuild over the stored copy.
# From the chat it looks like the conversation spontaneously rewound to an
# earlier state, and the client's SSE drops at the same moment, so a re-hydrate
# replaces what was on screen with the mid-rebuild version. Nothing is lost —
# the replay restores it — but the operator has no way to know that while it is
# happening.
#
# A turn is live if any turn journal is still marked running with a live pid.
# Pass --force to override (or --deferred, which is usually what you want).
turns_dir="$REPO_ROOT/.runtime/turns"
if [ "${1:-}" != "--force" ] && [ -d "$turns_dir" ]; then
  live=""
  for meta in "$turns_dir"/*/meta.json; do
    [ -f "$meta" ] || continue
    grep -q '"status": *"running"' "$meta" || continue
    pid="$(sed -n 's/.*"pid": *\([0-9]\+\).*/\1/p' "$meta" | head -1)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { live="$pid"; break; }
  done
  if [ -n "$live" ]; then
    echo "REFUSING: a chat turn is running right now (pid $live)." >&2
    echo "Restarting would kill it and visibly rewind the transcript mid-reply." >&2
    echo "Wait for it to finish, or use:" >&2
    echo "  scripts/restart_backend.sh --deferred   # restart after the reply lands" >&2
    echo "  scripts/restart_backend.sh --force      # restart anyway" >&2
    exit 2
  fi
fi

# --- Preferred: systemd --user service -------------------------------------
if have_unit; then
  systemctl --user restart "$SERVICE"
  sleep 2
  if systemctl --user is-active --quiet "$SERVICE"; then
    echo "Backend restarted via systemd ($SERVICE active)."
  else
    echo "systemd restart did not come up active. Recent logs:" >&2
    journalctl --user -u "$SERVICE" --no-pager -n 20 >&2 || true
    exit 1
  fi
  exit 0
fi

# --- Fallback: legacy nohup launch -----------------------------------------
# No systemd unit installed. Snapshot the env of any running backend (to preserve
# secrets passed at launch), overlay auth.env so the OAuth token + 511 key can't
# drift blank, then relaunch. If nothing is running, launch from auth.env +
# secrets.env directly. Consider running scripts/install_service.sh instead.
echo "No systemd unit found; using legacy nohup path. (Run scripts/install_service.sh to adopt systemd.)" >&2
cd "$REPO_ROOT/backend"

envv=()
pid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
if [ -n "${pid:-}" ]; then
  while IFS= read -r -d '' line; do
    [ -n "$line" ] && envv+=("$line")
  done < "/proc/$pid/environ"
fi

# Overlay auth/secrets files LAST so their values win over any stale snapshot.
for f in "${WORKSHOP_AUTH_ENV:-$HOME/.config/clawdbot/auth.env}" "$HOME/.config/workshop/secrets.env"; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    line="${line#export }"
    case "$line" in ''|\#*) continue;; *=*) envv+=("$line");; esac
  done < "$f"
done
# Defaults the unit would otherwise provide.
envv+=("LETTA_URL=${LETTA_URL:-http://127.0.0.1:8283}")
envv+=("LETTA_SERVER_PASS=${LETTA_SERVER_PASS:-workshop-local}")
envv+=("WORKSHOP_PROJECTS_ROOT=${WORKSHOP_PROJECTS_ROOT:-$HOME}")
envv+=("WORKSHOP_ROOT=${WORKSHOP_ROOT:-$REPO_ROOT}")
envv+=("HOME=$HOME" "PATH=$PATH")

pkill -f 'uvicorn main:app.*8000' || true
sleep 1
env -i "${envv[@]}" nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
  >>/tmp/workshop-backend.log 2>&1 &
disown || true
sleep 2
newpid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
echo "Backend restarted via nohup (new pid ${newpid:-unknown}). Log: /tmp/workshop-backend.log"
