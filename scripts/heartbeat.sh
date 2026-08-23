#!/usr/bin/env bash
# heartbeat.sh — write a liveness heartbeat for an agent.
#
# Usage: heartbeat.sh <agent_id> [meta-key=val ...]
#
# MIGRATED ONTO THE PRESENCE CHANNEL (step B4 of docs/platform-redesign/
# workspace-ownership-and-leases-2026-08-22.md). This script used to compose its
# own JSON; it now shells `scripts/presence.py`, which writes the SAME
# .runtime/heartbeats/<id>.json file through backend/services/presence.py.
#
# That is deliberate and it is the whole point of the step: section 6 says the
# heartbeats channel "is already this shape and is the thing to generalize
# rather than replace", so there is ONE writer implementation and one expiry
# rule, not a heartbeat mechanism beside a presence mechanism. The record gained
# a server-computed `expires_at`; `ts`, `pid` and `hostname` keep their old
# names and meanings, and the extra `key=val` args now land under `meta` instead
# of at the top level.
#
# The record is written with the default `heartbeat` liveness profile, so the
# pid is recorded but NOT probed: this shell exits immediately after the write,
# so probing its pid would expire every agent's presence the instant it appeared.
# A long-running loop that wants the accelerator calls presence.py directly with
# `--profile pid`.
#
# Called from cron_agent.sh and the supervisor's per-agent loop to mark "I am
# alive at <ts>"; GET /agents/{id}/runs reads it back as heartbeat_age_s +
# liveness, and GET /presence lists it beside every other session.
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

WORKSHOP_DIR="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PID="${HEARTBEAT_PID:-$$}"

python3 "$WORKSHOP_DIR/scripts/presence.py" heartbeat "$AGENT_ID" \
    --kind run --pid "$PID" --activity "agent run" --path -- "$@" \
    || { echo "heartbeat write failed: $AGENT_ID" >&2; exit 2; }
