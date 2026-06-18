#!/usr/bin/env bash
# Restart the Workshop backend (nohup uvicorn on :8000, no systemd, no --reload).
# Carries the CURRENTLY-RUNNING process's environment over to the new one, so
# secrets that were passed at launch (WORKSHOP_511_API_KEY, HEALTH_USER,
# OWNTRACKS_USER, BIRDNET_* ...) are preserved without anyone re-typing them.
# Nothing is printed from the environment. Run it from your own shell.
set -euo pipefail

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

cd /home/clawd-a/workshop/backend

pid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
if [ -z "${pid:-}" ]; then
  echo "No running backend found on :8000."
  echo "Start it with your usual command (it needs WORKSHOP_511_API_KEY etc.)."
  exit 1
fi

# Snapshot the live env (NUL-delimited), dropping any empty trailing entry, then
# stop the old process and relaunch with exactly that environment.
envv=()
while IFS= read -r -d '' line; do
  [ -n "$line" ] && envv+=("$line")
done < "/proc/$pid/environ"

# Overlay auth.env LAST so its values win over the live snapshot. A prior restart
# can carry a BLANK WORKSHOP_511_API_KEY forward (the recurring "commute went dumb,
# 511 feeds need_key" gotcha); sourcing auth.env here repopulates the key (and the
# OAuth token) every restart so it cannot drift blank again. In `env -i K=a K=b`,
# the LAST K wins, so appending these after the snapshot makes them authoritative.
AUTH_ENV="${WORKSHOP_AUTH_ENV:-$HOME/.config/clawdbot/auth.env}"
if [ -f "$AUTH_ENV" ]; then
  while IFS= read -r line; do
    line="${line#export }"
    case "$line" in ''|\#*) continue;; *=*) envv+=("$line");; esac
  done < "$AUTH_ENV"
fi
pkill -f 'uvicorn main:app.*8000' || true
sleep 1
env -i "${envv[@]}" nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
  >>/tmp/workshop-backend.log 2>&1 &
disown || true
sleep 2
newpid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
echo "Backend restarted (old pid $pid -> new pid ${newpid:-unknown}). Log: /tmp/workshop-backend.log"
