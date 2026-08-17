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
  # WAIT FOR THE TURN TO END, don't guess how long it will take.
  #
  # This used to sleep a fixed 5s and then run the restart, which hits the
  # live-turn guard below and is REFUSED whenever the turn is still going. A
  # long turn therefore never restarted at all: the refusal went to a log
  # nobody reads, the command reported success, and the backend silently kept
  # serving old code. That failure is invisible from the chat and it cost a
  # whole session's backend work being "shipped" but not running.
  #
  # So: poll the same condition the guard checks, and restart the moment it
  # clears. `--deferred <seconds>` still caps the wait so a wedged turn can't
  # leave a restarter parked forever.
  #
  # The cap was 30 minutes, and that became wrong when the turn time limit was
  # removed: an agentic turn now runs until the work is done, routinely past
  # half an hour. The old default reproduced the exact failure the polling was
  # added to fix — /tmp/workshop-backend.log has "gave up waiting for turn to
  # end" followed by hours of the backend serving code nobody shipped. Six
  # hours is longer than any turn that is still healthy, and a wedged one is a
  # problem the operator wants to see rather than have quietly restarted.
  max_wait="${2:-21600}"
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  turns="$REPO_ROOT/.runtime/turns"
  chat="$REPO_ROOT/.runtime/users"
  # THE CHILD EXITING IS NOT THE REPLY LANDING.
  #
  # A turn's claude process writes its last line and exits; the BACKEND then
  # folds the result and appends the assistant message. That gap is short, and
  # restarting inside it kills the commit: the process is already gone, so the
  # pid check says "no turn running", the backend dies before the append, and
  # the reply exists nowhere. On 2026-08-09 that destroyed a long reply
  # outright, and from the chat it read as the assistant never answering at all.
  #
  # So there are two waits. First for the process, then for the commit: a thread
  # holds `active_run_id` in its meta from the moment a run is claimed until the
  # moment its reply is committed, which is exactly the window that must be
  # clear. The second wait is bounded, because a crashed run can leave the field
  # set until the next startup reconcile and must not park a restarter forever.
  commit_grace=180
  # WAIT FOR *MY* TURN, NOT FOR EVERY TURN ON THE BOX.
  #
  # `turn_live` used to scan every turn under .runtime/turns and block while ANY
  # of them was running. That is correct on a machine running one turn at a
  # time, and it never fires on this one: a background worker or a second chat
  # is almost always mid-turn, so the condition is true forever. Measured on
  # 2026-08-11: NINE armed restarters stacked up over an hour, none fired, and
  # the backend served hour-old code while every arming call cheerfully printed
  # success. That is the same silent-no-op this whole block was written to kill,
  # wearing a different hat.
  #
  # The turn that armed this restart is the one whose reply must land first, and
  # it is identifiable: this process was spawned by it, so that turn's recorded
  # pid is one of our ancestors. Walk the parent chain and match.
  own_turn=""
  _pid=$$
  _ancestors=""
  while [ -n "$_pid" ] && [ "$_pid" != "1" ] && [ "$_pid" != "0" ]; do
    _ancestors="$_ancestors $_pid"
    _pid="$(awk '{print $4}' "/proc/$_pid/stat" 2>/dev/null)"
  done
  for meta in "$turns"/*/meta.json; do
    [ -f "$meta" ] || continue
    tpid="$(sed -n 's/.*"pid": *\([0-9]\+\).*/\1/p' "$meta" | head -1)"
    [ -n "$tpid" ] || continue
    case " $_ancestors " in *" $tpid "*) own_turn="$tpid"; break ;; esac
  done
  # A concurrent turn still gets a chance to land, but a BOUNDED one. Restarting
  # under another agent mid-turn is real damage (see the guard below), and never
  # restarting at all is worse, so this waits minutes rather than forever.
  other_cap=180
  setsid bash -c '
    self="$1"; turns="$2"; chat="$3"; max="$4"; grace="$5"; mine="$6"; ocap="$7"; root="$8"
    live() {  # live <pid-filter>: "" = any turn, else only that pid
      [ -d "$turns" ] || return 1
      for meta in "$turns"/*/meta.json; do
        [ -f "$meta" ] || continue
        grep -q "\"status\": *\"running\"" "$meta" || continue
        pid="$(sed -n "s/.*\"pid\": *\([0-9]\+\).*/\1/p" "$meta" | head -1)"
        [ -n "$pid" ] || continue
        [ -n "$1" ] && [ "$pid" != "$1" ] && continue
        kill -0 "$pid" 2>/dev/null && return 0
      done
      return 1
    }
    commit_pending() {
      [ -d "$chat" ] || return 1
      grep -rlq "\"active_run_id\": *\"[0-9a-f-]" "$chat"/*/chat/*/meta.json 2>/dev/null
    }
    # 1. My own turn, generously: it is the reply this restart must not eat.
    waited=0
    if [ -n "$mine" ]; then
      while live "$mine" && [ "$waited" -lt "$max" ]; do sleep 3; waited=$((waited+3)); done
      [ "$waited" -ge "$max" ] && echo "warn: arming turn $mine still running after ${max}s; restarting anyway"
    fi
    # 2. Anyone else, briefly.
    waited=0
    while live "" && [ "$waited" -lt "$ocap" ]; do sleep 3; waited=$((waited+3)); done
    [ "$waited" -ge "$ocap" ] && echo "warn: another turn was still running after ${ocap}s; restarting anyway"
    # 3. The commit window: a thread holds active_run_id until its reply lands.
    settled=0
    while commit_pending && [ "$settled" -lt "$grace" ]; do sleep 2; settled=$((settled+2)); done
    [ "$settled" -ge "$grace" ] && echo "warn: a run still held its thread after ${grace}s; restarting anyway"
    sleep 1
    # --force IS THE POINT. The three waits above ARE the guard: this waiter has
    # already served every condition the synchronous guard checks, and the warns
    # above are it deliberately giving up after a bounded wait.
    #
    # Without --force this line re-invoked the script, the guard found the very
    # turn the waiter had just decided to restart on top of, printed REFUSING and
    # exited 2 -- while the caller had long since been told "restart armed" and
    # exit 0. Measured in /tmp/workshop-backend.log before this fix: 61 restarts,
    # 14 silent refusals. That is the third instance of the silent-no-op this
    # file has been fixed for twice; both earlier fixes were right and both left
    # this path open. A bounded wait that ends in a refusal is not a wait, it is
    # a lie, and an agent cannot be instructed out of a tool that lies.
    old_pid="$(systemctl --user show -p MainPID --value workshop-backend.service 2>/dev/null || echo 0)"
    bash "$self" --force; code=$?
    new_pid="$(systemctl --user show -p MainPID --value workshop-backend.service 2>/dev/null || echo 0)"
    served="$(curl -sf --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get(\"source_id\") or \"\")" 2>/dev/null || true)"
    # VERIFY, do not assume. A zero exit that left the same pid running means the
    # restart did not happen, which is precisely the failure this whole block
    # exists to make impossible to report as success. Exit 3 is the preflight
    # refusing a tree that does not import -- a correct, deliberate no-op, and it
    # must NOT be recorded as a failure of the restart machinery.
    if [ "$code" = 3 ]; then
      outcome=refused-tree-broken
    elif [ "$code" = 0 ] && [ -n "$new_pid" ] && [ "$new_pid" != "0" ] && [ "$new_pid" != "$old_pid" ]; then
      outcome=restarted
    elif [ "$code" = 0 ]; then
      outcome=no-pid-change
    else
      outcome=failed
    fi
    echo "deferred restart: $outcome (pid $old_pid -> $new_pid, serving ${served:-unreachable})"
    # The receipt. Until now the ONLY record of a deferred restart was this log
    # under /tmp, which nothing reads and nothing surfaces, so a failure was
    # invisible to the agent that armed it and to the operator. apply.sh --check
    # and the chat runtime briefing both read this file.
    mkdir -p "$root/.runtime" 2>/dev/null || true
    printf "{\"when\": \"%s\", \"action\": \"backend-restart\", \"outcome\": \"%s\", \"old_pid\": %s, \"new_pid\": %s, \"serving\": \"%s\", \"arming_turn\": \"%s\"}\n" \
      "$(date -Is)" "$outcome" "${old_pid:-0}" "${new_pid:-0}" "${served:-}" "${mine:-none}" \
      > "$root/.runtime/apply-last.json" 2>/dev/null || true
  ' _ "$self" "$turns" "$chat" "$max_wait" "$commit_grace" "$own_turn" "$other_cap" "$REPO_ROOT" \
    >>/tmp/workshop-backend.log 2>&1 </dev/null &
  disown || true
  if [ -n "$own_turn" ]; then
    echo "Backend restart armed: fires when this turn (pid $own_turn) ends, then within ${other_cap}s."
  else
    # Say so rather than implying a wait that is not happening. No owning turn
    # means this was run from a plain shell, not from inside a turn.
    echo "Backend restart armed: no arming turn found, so it fires within ${other_cap}s."
  fi
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
  # And refuse during the COMMIT window, which the pid check above cannot see:
  # the child is gone, the backend is still assembling and appending the reply.
  # Restarting here loses the reply outright rather than rewinding it.
  #
  # Only a RECENT holder counts. A crashed run leaves `active_run_id` set until
  # the next startup reconcile clears it, and reconcile only runs on a restart:
  # refusing on a stale field would mean the one action that fixes it is the one
  # action forbidden, with --force as the only way out. A commit takes seconds,
  # so a meta untouched for STALE_HOLD_S is not a commit in progress.
  STALE_HOLD_S=300
  holder=""
  for meta in "$REPO_ROOT"/.runtime/users/*/chat/*/meta.json; do
    [ -f "$meta" ] || continue
    grep -q '"active_run_id": *"[0-9a-f-]' "$meta" || continue
    age=$(( $(date +%s) - $(stat -c %Y "$meta" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$STALE_HOLD_S" ] && { holder="$meta"; break; }
  done
  if [ -n "$holder" ]; then
    echo "REFUSING: a run still holds its thread, so its reply is not committed yet." >&2
    echo "  $holder" >&2
    echo "Restarting now would lose that reply. Use --deferred, which waits for it." >&2
    exit 2
  fi
fi

# --- Preflight: will this tree even start? ----------------------------------
# THE TREE IS SHARED AND ALWAYS PARTLY MID-EDIT. Several agents work in this one
# checkout at once, so "restart the backend" routinely means "restart onto
# somebody else's half-written file". Measured 2026-08-13: four live turns and
# two backend services uncommitted at the same moment. If any of it does not
# import, uvicorn cannot start, Restart=on-failure retries every 5s, and the
# whole app -- including the chat someone would use to fix it -- is down.
#
# `import main` in a subprocess is EXACTLY what uvicorn does at startup, so it is
# an exact predictor rather than an approximation, and it costs 0.78s against a
# restart that takes seconds anyway. Refusing here is always recoverable; a
# crash-looping backend is not.
#
# This runs for --force too. --force means "do not wait for the turn to finish",
# which is a scheduling decision; it has never meant "start code that cannot run".
preflight_import() {
  local py="$REPO_ROOT/backend/.venv/bin/python"
  [ -x "$py" ] || return 0   # no venv to check with: do not invent a failure
  ( cd "$REPO_ROOT/backend" && "$py" -c 'import sys; sys.path.insert(0, "."); import main' ) 2>&1
}

if [ "${SKIP_PREFLIGHT:-0}" != "1" ]; then
  if ! pf_out="$(preflight_import)"; then
    echo "REFUSING: the backend tree does not import, so restarting it would leave" >&2
    echo "          the service crash-looping and the app down. Nothing was restarted." >&2
    echo "" >&2
    printf '%s\n' "$pf_out" | tail -15 >&2
    echo "" >&2
    echo "This is usually another agent mid-edit; re-run in a moment." >&2
    echo "To restart anyway: SKIP_PREFLIGHT=1 scripts/restart_backend.sh --force" >&2
    exit 3
  fi
fi

# --- Preferred: systemd --user service -------------------------------------
if have_unit; then
  # What we EXPECT to be serving afterwards, captured before the restart so a
  # tree that moves underneath us is reported as that, not as a failure.
  want_id="$(bash "$REPO_ROOT/scripts/backend_source_id.sh" 2>/dev/null || echo unknown)"
  systemctl --user restart "$SERVICE"

  # VERIFY BY ASKING THE BACKEND, not by asking systemd.
  #
  # `is-active` after a fixed 2s sleep answers "did a process get spawned",
  # which is not the question. A backend that dies on its first request, or one
  # still importing, passes it. And a unit in auto-restart backoff can read
  # active moments before it dies again. So: poll /healthz until it answers with
  # a source id, which proves the process is up AND which code it is running.
  served=""; ok=0
  for _ in $(seq 1 30); do
    served="$(curl -sf --max-time 2 http://127.0.0.1:8000/healthz 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source_id") or "")' 2>/dev/null || true)"
    [ -n "$served" ] && { ok=1; break; }
    sleep 1
  done

  if [ "$ok" != 1 ]; then
    echo "RESTART FAILED: the backend did not answer /healthz within 30s." >&2
    journalctl --user -u "$SERVICE" --no-pager -n 20 >&2 || true
    exit 1
  fi
  if [ "$served" = "$want_id" ]; then
    echo "Backend restarted and verified (serving $served)."
  else
    # Not a failure: the tree is shared, so another agent committing mid-restart
    # legitimately changes the answer. Say which code is actually live rather
    # than pretending the expected one is.
    echo "Backend restarted and healthy, serving $served (expected $want_id; the tree moved during the restart)."
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
for f in "${WORKSHOP_AUTH_ENV:-$HOME/.config/workshop/auth.env}" "${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}/secrets.env"; do
  [ -f "$f" ] || continue
  while IFS= read -r line; do
    line="${line#export }"
    case "$line" in ''|\#*) continue;; *=*) envv+=("$line");; esac
  done < "$f"
done
# Defaults the unit would otherwise provide.
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
