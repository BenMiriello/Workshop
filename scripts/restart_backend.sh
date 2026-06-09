#!/usr/bin/env bash
# Restart the Workshop backend (nohup uvicorn on :8000, no systemd, no --reload).
# Carries the CURRENTLY-RUNNING process's environment over to the new one, so
# secrets that were passed at launch (WORKSHOP_511_API_KEY, HEALTH_USER,
# OWNTRACKS_USER, BIRDNET_* ...) are preserved without anyone re-typing them.
# Nothing is printed from the environment. Run it from your own shell.
set -euo pipefail
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
pkill -f 'uvicorn main:app.*8000' || true
sleep 1
env -i "${envv[@]}" nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
  >>/tmp/workshop-backend.log 2>&1 &
disown || true
sleep 2
newpid="$(pgrep -f 'uvicorn main:app.*8000' | head -1 || true)"
echo "Backend restarted (old pid $pid -> new pid ${newpid:-unknown}). Log: /tmp/workshop-backend.log"
