#!/usr/bin/env bash
# Instance identity — sourced by any script that needs to know WHICH deployment
# it is running for. None of this belongs in the repo: a fork is a different
# operator with a different domain, bundle id and name.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/instance.sh"
#
# Values come from $WORKSHOP_CONFIG_DIR/instance.env (default
# ~/.config/workshop/instance.env), which is created by install_service.sh and
# never tracked. Anything already in the environment wins, so CI and one-off
# runs can override without touching the file.
#
# Keys (all optional; a script that REQUIRES one should say so itself):
#   WORKSHOP_PUBLIC_HOST     public hostname served through the tunnel
#   WORKSHOP_LOCAL_ORIGIN    the LAN/tailnet origin the operator browses
#   WORKSHOP_OPERATOR_NAME   how agents should address the operator
#   WORKSHOP_MOBILE_BUNDLE   iOS bundle identifier for the mobile build
#   WORKSHOP_MOBILE_SCHEME   URL scheme for the mobile build
#   WORKSHOP_AUTH_ENV        the secret env file; defaults under the config dir
#                            and is exported below so every script agrees

WORKSHOP_CONFIG_DIR="${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}"
_INSTANCE_ENV="$WORKSHOP_CONFIG_DIR/instance.env"

# The secret env file (OAuth token, app API keys), for the shell half of the
# system. Its Python counterpart is services/auth_env.py and the two must agree:
# one file was previously reached under two variable names and two default
# directories, and only a symlink between them kept the system working.
export WORKSHOP_AUTH_ENV="${WORKSHOP_AUTH_ENV:-$WORKSHOP_CONFIG_DIR/auth.env}"

if [ -f "$_INSTANCE_ENV" ]; then
  # Existing environment wins: only set what is still unset.
  while IFS='=' read -r _k _v; do
    case "$_k" in ''|\#*) continue ;; esac
    _k="${_k// /}"
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    if [ -z "${!_k:-}" ]; then export "$_k=$_v"; fi
  done < "$_INSTANCE_ENV"
fi
unset _k _v _INSTANCE_ENV

# THE INTERPRETER, resolved rather than inherited.
#
# Every shell entrypoint calls a bare `python3` and expects the backend's venv,
# which holds psycopg, numpy and the rest. That worked only because the callers
# that mattered were spawned BY the backend, whose PATH already had the venv on
# it. Started the documented way instead -- cron, or the `setsid nohup` line in
# queue_runner.sh's own header -- `python3` is the system one, and the very
# first thing the sweep does is read the ticket store, which then fails as:
#
#   RelationalStoreUnavailable: a database is configured but did not answer
#
# That message blames Postgres for a missing import. Measured 2026-08-22: a
# sweep launched from a plain shell could not read a single ticket, while the
# same sweep launched from the backend worked, which is the worst shape a bug
# can have.
#
# Prepending it here fixes every `python3` in every script that sources this,
# rather than editing forty call sites into disagreement.
_WS_ROOT="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
if [ -x "$_WS_ROOT/backend/.venv/bin/python" ]; then
  case ":$PATH:" in
    *":$_WS_ROOT/backend/.venv/bin:"*) ;;
    *) PATH="$_WS_ROOT/backend/.venv/bin:$PATH"; export PATH ;;
  esac
fi
unset _WS_ROOT

# require_instance <VAR> [hint] — fail loudly rather than half-configured.
require_instance() {
  local var="$1" hint="${2:-}"
  if [ -z "${!var:-}" ]; then
    echo "error: $var is not set." >&2
    echo "  set it in $WORKSHOP_CONFIG_DIR/instance.env${hint:+ ($hint)}" >&2
    return 1
  fi
}
