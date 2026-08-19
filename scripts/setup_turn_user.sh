#!/usr/bin/env bash
# setup_turn_user.sh -- create the dedicated, lower-privilege system account a
# chat/build turn runs as under the uid layer ([SEC-A1]).
#
#   sudo bash scripts/setup_turn_user.sh            create/repair everything
#   sudo bash scripts/setup_turn_user.sh --check     report what is missing, change nothing
#
# WHY THIS IS A SEPARATE, ROOT-RUN SCRIPT. Creating a system account and a
# sudoers rule needs root, and an agent turn cannot do this to itself even if
# its own account had a NOPASSWD sudo grant: turns already run inside a bwrap
# sandbox by default (Layer 1), and bwrap sets PR_SET_NO_NEW_PRIVS, which makes
# `sudo` refuse unconditionally from inside one. This is the operator's own
# step, run once, out of band -- see the "irreducibly the operator's" section
# of docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md.
#
# WHAT THIS DOES NOT DO. It does not flip anything on: WORKSHOP_TURN_USER stays
# unset until you add it to ~/.config/workshop/instance.env yourself, after
# reading the verification steps this script prints at the end. Idempotent and
# safe to re-run -- it only creates what is missing and never removes or
# demotes anything that already exists more permissively than it would set.
set -euo pipefail

if [ "$(id -u)" != "0" ]; then
  echo "Run this with sudo: sudo bash scripts/setup_turn_user.sh" >&2
  exit 1
fi

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

# The real user this instance's backend runs as -- SUDO_USER when invoked via
# sudo (the normal path), else whoever owns this checkout's .git directory.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="${SUDO_USER:-$(stat -c '%U' "$REPO_ROOT/.git")}"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
[ -n "$OWNER_HOME" ] || { echo "Could not resolve a home directory for $OWNER" >&2; exit 1; }

TURN_USER=workshop-turn
TURN_HOME=/var/lib/workshop-turn
WRAPPER_DIR=/usr/local/libexec/workshop
WRAPPER="$WRAPPER_DIR/turn-exec"
SUDOERS=/etc/sudoers.d/workshop-turn

echo "setup_turn_user: platform owner=$OWNER (home=$OWNER_HOME), turn account=$TURN_USER"
[ "$CHECK" = "1" ] && echo "  --check: reporting only, changing nothing"
echo

report() { printf '  %-8s %s\n' "$1" "$2"; }

# --- 1. the account ----------------------------------------------------------
if id "$TURN_USER" >/dev/null 2>&1; then
  report ok "account $TURN_USER exists"
else
  report missing "account $TURN_USER"
  if [ "$CHECK" = "0" ]; then
    useradd --system --home-dir "$TURN_HOME" --create-home --shell /usr/sbin/nologin \
      --comment "Workshop scoped-turn execution identity ([SEC-A1])" "$TURN_USER"
    report created "account $TURN_USER"
  fi
fi

# --- 2. group membership: workshop-turn joins the platform owner's group -----
# Deliberately this direction, not the reverse (adding $OWNER to a NEW group):
# supplementary groups are fixed at session/login time, so widening $OWNER's
# groups would need `loginctl terminate-user` or a reboot before the already-
# running backend saw it -- a confusing half-applied state on flip day. Adding
# the NEW account to the EXISTING group takes effect the moment sudo calls
# initgroups() for it, which is every spawn.
if id -nG "$TURN_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$OWNER"; then
  report ok "$TURN_USER is in group $OWNER"
else
  report missing "$TURN_USER in group $OWNER"
  [ "$CHECK" = "0" ] && usermod -aG "$OWNER" "$TURN_USER" && report added "$TURN_USER to group $OWNER"
fi

# --- 3. the turn-user's own home: policy files land here, never in $OWNER's --
if [ -d "$TURN_HOME/.claude" ]; then
  report ok "$TURN_HOME/.claude exists"
else
  report missing "$TURN_HOME/.claude"
  if [ "$CHECK" = "0" ]; then
    mkdir -p "$TURN_HOME/.claude"
    chown -R "$TURN_USER:$TURN_USER" "$TURN_HOME"
    chmod 0755 "$TURN_HOME" "$TURN_HOME/.claude"
    report created "$TURN_HOME/.claude"
  fi
fi

if [ -f "$TURN_HOME/.claude.json" ]; then
  report ok "$TURN_HOME/.claude.json exists"
else
  report missing "$TURN_HOME/.claude.json"
  if [ "$CHECK" = "0" ]; then
    echo '{}' > "$TURN_HOME/.claude.json"
    chown "$TURN_USER:$TURN_USER" "$TURN_HOME/.claude.json"
    chmod 0644 "$TURN_HOME/.claude.json"
    report created "$TURN_HOME/.claude.json"
  fi
fi

# git identity + the safe.directory exemption: .git under the scope is owned by
# $OWNER, the turn runs as $TURN_USER, and git refuses "dubious ownership"
# without this on every single command otherwise.
GIT_NAME="$(sudo -u "$OWNER" git -C "$REPO_ROOT" config user.name 2>/dev/null || true)"
GIT_EMAIL="$(sudo -u "$OWNER" git -C "$REPO_ROOT" config user.email 2>/dev/null || true)"
if [ -f "$TURN_HOME/.gitconfig" ]; then
  report ok "$TURN_HOME/.gitconfig exists"
else
  report missing "$TURN_HOME/.gitconfig"
  if [ "$CHECK" = "0" ]; then
    {
      echo "[user]"
      [ -n "$GIT_NAME" ] && echo "    name = $GIT_NAME"
      [ -n "$GIT_EMAIL" ] && echo "    email = $GIT_EMAIL"
      echo "[safe]"
      echo "    directory = /workspace"
    } > "$TURN_HOME/.gitconfig"
    chown "$TURN_USER:$TURN_USER" "$TURN_HOME/.gitconfig"
    chmod 0644 "$TURN_HOME/.gitconfig"
    report created "$TURN_HOME/.gitconfig ($([ -n "$GIT_NAME" ] && echo "$GIT_NAME <$GIT_EMAIL>" || echo "no identity found -- edit it by hand"))"
  fi
fi

# --- 4. traversal: workshop-turn must be able to STAT its way down to a scope
# root and to the individual policy files scoped_exec._grants() binds. A bind
# mount does not change ownership or mode, so bwrap (running as workshop-turn
# by the time it does this) needs +x on every directory between / and each
# real source path -- $OWNER_HOME itself, and ~/.claude specifically, since
# CLAUDE.md/settings.json are 0644 already but sit inside a 0700 directory.
# 0710 grants search ONLY to the owning group, not read: workshop-turn can
# reach a named file inside, not list what else is there.
# `need` is the group-permission bits (in the group's rwx position, e.g. 0010
# for search-only, 0050 for read+search) that must ALREADY be set for this
# path to count as satisfied -- so a directory some other process already made
# more permissive than the target is left alone, never demoted.
loosen() {
  local path="$1" want="$2" need="$3"
  local have
  have="$(stat -c '%a' "$path" 2>/dev/null || echo "")"
  if [ -z "$have" ]; then
    report skip "$path does not exist"
  elif [ "$((8#$have & 8#$need))" = "$((8#$need))" ]; then
    report ok "$path is $have (group access already present)"
  else
    report missing "$path is $have, needs group bits $need (target $want)"
    [ "$CHECK" = "0" ] && chmod "$want" "$path" && report set "$path -> $want"
  fi
}
# Search-only: these are traversed to reach a single named file inside, never
# listed, so the group does not need to read their contents.
loosen "$OWNER_HOME" 0710 0010
loosen "$OWNER_HOME/.claude" 0710 0010
loosen "$OWNER_HOME/.config" 0710 0010
# ~/.config/workshop is bound as a whole read-only SUBTREE (instance.env and
# friends), not individual files, so the group needs read+search here, not
# just search -- bwrap's bind of a directory it will later be read through
# needs to list what is in it.
loosen "$OWNER_HOME/.config/workshop" 0750 0050

# --- 5. the turn-exec wrapper --------------------------------------------
WRAPPER_BODY='#!/bin/sh
# Workshop turn-exec ([SEC-A1]). The ONE binary the workshop-turn sudoers rule
# may run. Does not validate its argv -- that would be theatre, since the rule
# already lets the platform owner run anything AS AN UNPRIVILEGED ACCOUNT. What
# this buys: PATH restored from a name sudo does not touch (secure_path in
# sudoers Defaults overrides PATH even under -E), an explicit umask so files a
# turn creates stay group-writable, and one auditable name in sudoers rather
# than a bare "ALL".
set -eu
mode=$1; shift
case "$mode" in
  run)
    [ -n "${WORKSHOP_TURN_PATH:-}" ] && { PATH=$WORKSHOP_TURN_PATH; export PATH; }
    umask 002
    exec "$@"
    ;;
  signal)
    sig=$1; pgid=$2
    exec /bin/kill -s "$sig" -- "-$pgid"
    ;;
  *)
    echo "turn-exec: unknown mode: $mode" >&2
    exit 64
    ;;
esac
'
if [ -f "$WRAPPER" ] && [ "$(cat "$WRAPPER")" = "$WRAPPER_BODY" ]; then
  report ok "$WRAPPER is current"
else
  if [ -f "$WRAPPER" ]; then report missing "$WRAPPER is stale"; else report missing "$WRAPPER"; fi
  if [ "$CHECK" = "0" ]; then
    mkdir -p "$WRAPPER_DIR"
    printf '%s' "$WRAPPER_BODY" > "$WRAPPER"
    chown root:root "$WRAPPER"
    chmod 0755 "$WRAPPER"
    report wrote "$WRAPPER"
  fi
fi

# --- 6. the sudoers rule ------------------------------------------------
# Grants NO root: the runas target is workshop-turn, strictly less privileged
# than $OWNER (no docker group, no sudo of its own). The tight Cmnd_Alias is
# for auditability, not for safety -- see turn-exec's own comment.
SUDOERS_BODY="# Workshop [SEC-A1]. Generated by scripts/setup_turn_user.sh -- edit that
# script, not this file, or a re-run will overwrite your change.
Cmnd_Alias WORKSHOP_TURN = $WRAPPER
Defaults>$TURN_USER !use_pty, !pam_session, !pam_setcred
$OWNER ALL=($TURN_USER:$TURN_USER) NOPASSWD: SETENV: WORKSHOP_TURN
"
if [ -f "$SUDOERS" ] && [ "$(cat "$SUDOERS")" = "$SUDOERS_BODY" ]; then
  report ok "$SUDOERS is current"
else
  if [ -f "$SUDOERS" ]; then report missing "$SUDOERS is stale"; else report missing "$SUDOERS"; fi
  if [ "$CHECK" = "0" ]; then
    TMP="$(mktemp)"
    printf '%s' "$SUDOERS_BODY" > "$TMP"
    if visudo -cf "$TMP"; then
      install -o root -g root -m 0440 "$TMP" "$SUDOERS"
      report wrote "$SUDOERS"
    else
      echo "visudo rejected the generated sudoers file; not installing it." >&2
      rm -f "$TMP"
      exit 1
    fi
    rm -f "$TMP"
  fi
fi

echo
if [ "$CHECK" = "1" ]; then
  echo "Check complete. Re-run without --check to create/repair the above."
  exit 0
fi

cat <<EOF
Done. WORKSHOP_TURN_USER is NOT yet set anywhere -- nothing changed for a live
turn. Before you add it to ~/.config/workshop/instance.env, verify by hand:

  1. The wrapper runs as the turn-user with no password prompt:
       sudo -n -u $TURN_USER -- $WRAPPER run /bin/sh -c 'id'
     Expect: uid=...($TURN_USER) with no docker group.

  2. Credentials and the docker socket are unreachable from that identity:
       sudo -n -u $TURN_USER -- $WRAPPER run /bin/sh -c \\
         'cat $OWNER_HOME/.claude/.credentials.json' 2>&1
     Expect: Permission denied.

  3. The policy files ARE reachable (traversal loosened correctly):
       sudo -n -u $TURN_USER -- $WRAPPER run /bin/sh -c \\
         'cat $OWNER_HOME/.claude/CLAUDE.md | head -1'
     Expect: the file's first line, not a permission error.

  4. systemctl --user, which apply.sh needs, is the one open question this
     script cannot fully settle: systemd recreates its private control socket
     under \$XDG_RUNTIME_DIR/systemd/ on every restart of the user manager, so
     a one-time chmod on it would not survive that. Layer 1's own self-scope
     carve-out (services/scoped_exec.turn_user) already keeps a turn scoped to
     THIS platform's own repo OUT of the uid layer for exactly this reason, so
     apply.sh always runs as $OWNER regardless. For every OTHER scope, confirm
     systemctl is simply never needed from inside the turn before you rely on
     it being reachable -- if a future need arises, extend the D-Bus session
     bus grant (already bound) or add a small apply broker instead of loosening
     the private socket.

Once satisfied, add to ~/.config/workshop/instance.env:

    WORKSHOP_TURN_USER=$TURN_USER

then restart the backend and follow the attended checklist in
docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md
(section 9, "Attended flip checklist") before leaving it set.
EOF
