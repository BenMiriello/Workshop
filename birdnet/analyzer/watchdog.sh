#!/usr/bin/env bash
# Keep the BirdNET analyzer service AND the Workshop backend up while testing.
# Checks health every 15s and restarts whichever is down. The permanent fix is
# the systemd units (Restart=always) in birdnet/analyzer/ and for the backend;
# this is the no-sudo stand-in for live testing.
#
#   nohup birdnet/analyzer/watchdog.sh > /tmp/birdnet-watchdog.log 2>&1 &
set -u

PIDFILE=/tmp/birdnet-watchdog.pid
# Singleton guard: if a live watchdog already holds the pidfile, exit.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "watchdog already running (pid $(cat "$PIDFILE"))"; exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

ANALYZER_DIR=/home/clawd-a/workshop/birdnet/analyzer
BACKEND_DIR=/home/clawd-a/workshop/backend
LOG() { echo "[$(date '+%F %T')] $*"; }

# Capture the backend's service env once, so a restart preserves it.
bpid=$(pgrep -f 'uvicorn main:app.*8000' | head -1)
KEY=""
[ -n "$bpid" ] && KEY=$(tr '\0' '\n' < "/proc/$bpid/environ" 2>/dev/null | grep '^WORKSHOP_511_API_KEY=' | cut -d= -f2-)

restart_analyzer() {
  LOG "analyzer DOWN -> restarting"
  pkill -f "$ANALYZER_DIR/service.py" 2>/dev/null
  sleep 1
  ( cd "$ANALYZER_DIR" && nohup .venv/bin/python service.py > /tmp/birdnet-analyzer.log 2>&1 & )
}

restart_backend() {
  LOG "backend DOWN -> restarting"
  pkill -f 'uvicorn main:app.*8000' 2>/dev/null
  sleep 2
  ( cd "$BACKEND_DIR" && WORKSHOP_511_API_KEY="$KEY" nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 >> /tmp/workshop-backend.log 2>&1 & )
}

LOG "watchdog started (analyzer :8012, backend :8000)"
while true; do
  curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8012/health 2>/dev/null || restart_analyzer
  curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null || restart_backend
  sleep 15
done
