#!/usr/bin/env bash
# heartbeat.sh — write a liveness heartbeat for an agent (Phase 5c writer side).
#
# Usage: heartbeat.sh <agent_id> [meta-key=val ...]
#
# Writes ~/workshop/.runtime/heartbeats/<agent_id>.json with current ISO-8601 ts.
# Any additional `key=value` args are folded into the heartbeat JSON as
# top-level fields. Use this from cron_agent.sh or the supervisor's per-agent
# loop to mark "I am alive at <ts>" — the GET /agents/{id}/runs endpoint
# (backend/routes/agents.py) reads it and computes heartbeat_age_s/liveness.
#
# Output: prints the absolute heartbeat-file path to stdout on success.
# Exit: 0 on write success, 1 on usage error, 2 on write failure.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: heartbeat.sh <agent_id> [key=val ...]" >&2
    exit 1
fi

AGENT_ID="$1"
shift

HEARTBEATS_DIR="${WORKSHOP_HEARTBEATS_DIR:-/home/clawd-a/workshop/.runtime/heartbeats}"
TARGET="${HEARTBEATS_DIR}/${AGENT_ID}.json"

mkdir -p "$HEARTBEATS_DIR"

# Build JSON body. Always include ts + agent_id + hostname + pid.
TS="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
HOST="$(hostname -s 2>/dev/null || echo unknown)"
PID="${HEARTBEAT_PID:-$$}"

# Collect optional key=val extras into a JSON object.
EXTRAS_JSON="{}"
if command -v python3 >/dev/null 2>&1 && [[ $# -gt 0 ]]; then
    EXTRAS_JSON="$(python3 -c '
import json, sys
out = {}
for arg in sys.argv[1:]:
    if "=" in arg:
        k, v = arg.split("=", 1)
        out[k] = v
print(json.dumps(out))
' "$@")"
fi

# Compose final JSON with python (no jq dependency).
python3 -c '
import json, sys
body = {
    "ts": sys.argv[1],
    "agent_id": sys.argv[2],
    "hostname": sys.argv[3],
    "pid": int(sys.argv[4]),
}
extras = json.loads(sys.argv[5])
body.update(extras)
print(json.dumps(body))
' "$TS" "$AGENT_ID" "$HOST" "$PID" "$EXTRAS_JSON" > "$TARGET.tmp"

# Atomic move so readers never see a half-written file.
mv "$TARGET.tmp" "$TARGET" || { echo "heartbeat write failed: $TARGET" >&2; exit 2; }

echo "$TARGET"
