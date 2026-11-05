#!/usr/bin/env bash
# Install this checkout's scheduled work into the user crontab.
#
# Renders infra/cron/workshop.crontab (substituting @WORKSHOP_ROOT@ with this
# checkout's absolute path) and splices it into the crontab between marker
# lines. Idempotent: run it again after editing the template and the block is
# replaced in place.
#
# IT SPLICES, IT DOES NOT REPLACE. Everything outside the markers is preserved
# byte for byte. `crontab <file>` would have been one line, but it silently
# discards every entry the template does not know about, and this user's crontab
# is not guaranteed to hold only Workshop's.
#
# The previous crontab is written to .runtime/backups/ first, because crontab
# has no undo and the failure mode is silent: the jobs simply stop happening.
#
#   bash scripts/install_cron.sh           install / update the block
#   bash scripts/install_cron.sh --check   report drift, change nothing (exit 1 if drifted)
#   bash scripts/install_cron.sh --remove  remove the block, leave everything else
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/infra/cron/workshop.crontab"
BEGIN="# >>> workshop (managed by scripts/install_cron.sh) >>>"
END="# <<< workshop (managed by scripts/install_cron.sh) <<<"
MODE="${1:-install}"

[ -f "$TEMPLATE" ] || { echo "missing template: $TEMPLATE" >&2; exit 1; }
command -v crontab >/dev/null 2>&1 || { echo "no crontab command; is cron installed?" >&2; exit 1; }

# The platform template, then every installed app's own cron fragment.
#
# AN APP'S SCHEDULE BELONGS TO THE APP. The platform template used to carry one
# app's seventeen lines directly, including wall-clock times that were a
# specific person's daily routine; a platform file must not name an app, let
# alone describe its owner's day. An app drops `apps/<id>/*.crontab` beside its
# code and it is picked up here, in sorted order so the rendered block is
# stable across runs and --check does not report phantom drift.
#
# `apps/` is untracked, so on a fresh clone this loop finds nothing and the
# managed block is exactly the platform's own lines. That is the correct
# outcome, not a degraded one.
render() {
  sed "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" "$TEMPLATE"
  local frag
  for frag in $(ls "$REPO_ROOT"/apps/*/*.crontab 2>/dev/null | sort); do
    printf '\n# ---- %s ----\n' "${frag#"$REPO_ROOT/"}"
    sed "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" "$frag"
  done
}

# Rendered block, markers included.
RENDERED="$(printf '%s\n' "$BEGIN"; render; printf '%s\n' "$END")"

# Current crontab ("no crontab for user" is not an error here).
CURRENT="$(crontab -l 2>/dev/null || true)"

# Everything outside the markers, in order. awk rather than sed so an absent
# block is simply "all of it" with no special case.
OUTSIDE="$(printf '%s\n' "$CURRENT" | awk -v b="$BEGIN" -v e="$END" '
  $0 == b { skip = 1; next }
  $0 == e { skip = 0; next }
  !skip   { print }
')"

EXISTING="$(printf '%s\n' "$CURRENT" | awk -v b="$BEGIN" -v e="$END" '
  $0 == e { skip = 0; next }
  skip    { print }
  $0 == b { skip = 1 }
')"
EXPECTED="$(render)"

if [ "$MODE" = "--check" ]; then
  if ! printf '%s\n' "$CURRENT" | grep -qF "$BEGIN"; then
    echo "cron: the workshop block is NOT installed (run: bash scripts/install_cron.sh)"
    exit 1
  fi
  if [ "$EXISTING" = "$EXPECTED" ]; then
    echo "cron: installed block matches infra/cron/workshop.crontab"
    exit 0
  fi
  echo "cron: installed block DIFFERS from infra/cron/workshop.crontab"
  diff <(printf '%s\n' "$EXPECTED") <(printf '%s\n' "$EXISTING") || true
  exit 1
fi

# Back up before touching anything.
BACKUP_DIR="$REPO_ROOT/.runtime/backups"
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/crontab.$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\n' "$CURRENT" > "$BACKUP"
echo "backed up current crontab -> $BACKUP"

if [ "$MODE" = "--remove" ]; then
  printf '%s\n' "$OUTSIDE" | crontab -
  echo "removed the workshop block; $(printf '%s\n' "$OUTSIDE" | grep -cvE '^\s*(#|$)' || true) unrelated entries kept"
  exit 0
fi

# FIRST-INSTALL MIGRATION. Before markers existed these entries lived loose in
# the crontab. Appending the block without absorbing them would leave two copies
# of every schedule, so an app's ticks and the frontend watchdog would each
# fire twice a cycle -- the exact opposite of what installing this is for.
#
# An unmarked line is absorbed when it is unmistakably ours: it invokes a path
# under this checkout, or it is a comment that appears verbatim in the template.
# Anything else is somebody else's crontab and is kept.
ABSORBED=0
KEEP=""
TEMPLATE_COMMENTS="$(printf '%s\n' "$EXPECTED" | grep -E '^\s*#' || true)"
while IFS= read -r line; do
  if [ -n "$line" ] && printf '%s' "$line" | grep -qF "$REPO_ROOT"; then
    ABSORBED=$((ABSORBED + 1)); continue
  fi
  if printf '%s' "$line" | grep -qE '^\s*#' && printf '%s\n' "$TEMPLATE_COMMENTS" | grep -qxF "$line"; then
    ABSORBED=$((ABSORBED + 1)); continue
  fi
  KEEP="${KEEP}${line}"$'\n'
done <<< "$OUTSIDE"
[ "$ABSORBED" -gt 0 ] && echo "absorbed $ABSORBED pre-existing unmanaged line(s) into the managed block"

# Splice: everything genuinely unrelated, then the fresh block.
{
  printf '%s' "$KEEP" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
  printf '%s\n' "$RENDERED"
} | crontab -

ACTIVE="$(printf '%s\n' "$RENDERED" | grep -cvE '^\s*(#|$)' || true)"
echo "installed the workshop cron block ($ACTIVE active entries, root=$REPO_ROOT)"
echo "verify with: crontab -l"
