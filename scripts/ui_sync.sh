#!/usr/bin/env bash
# ui_sync.sh — is what each origin SERVES the same as what the source SAYS?
#
# The recurring failure this replaces: a UI fix lands, the tailnet origin shows
# it immediately (Vite, from source), and the public origin keeps serving the
# previous bundle — so the fix is reported as shipped while the site is stale.
# The drift is invisible from whichever origin you happen to be looking at.
#
# Three surfaces (docs/ui-origins.md), and they are NOT the same kind of thing:
#   :5173  Vite, from SOURCE  — in sync by construction; there is nothing to compare
#   :8002  the public edge    — serves frontend/dist, and is asked over HTTP
#   dist/  the artifact       — compared on disk, to tell "not built" from
#                               "built, but the edge is pointed somewhere else"
#
# Usage:
#   scripts/ui_sync.sh            full status, exit 0
#   scripts/ui_sync.sh --warn     print ONLY when something is out of sync (for
#                                 check_design.sh, which runs before every UI change)
#   scripts/ui_sync.sh --strict   exit 1 on drift (for a gate)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$REPO_ROOT/frontend/dist"
PUBLIC_ORIGIN="${WORKSHOP_PUBLIC_ORIGIN:-http://127.0.0.1:8002}"
VITE_ORIGIN="${WORKSHOP_VITE_ORIGIN:-http://127.0.0.1:5173}"

mode="full"
case "${1:-}" in
  --warn) mode="warn" ;;
  --strict) mode="strict" ;;
esac

SRC_ID="$(bash "$REPO_ROOT/scripts/ui_source_id.sh")"

json_field() { # $1=json $2=key — small enough not to need a JSON parser
  printf '%s' "$1" | tr -d '\n' | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\).*/\1/p"
}

DIST_JSON=""
[ -f "$DIST/build-info.json" ] && DIST_JSON="$(cat "$DIST/build-info.json")"
DIST_ID="$(json_field "$DIST_JSON" source_id)"
DIST_COMMIT="$(json_field "$DIST_JSON" commit_short)"
DIST_AT="$(json_field "$DIST_JSON" built_at)"
DIST_DIRTY="$(json_field "$DIST_JSON" dirty)"

# The edge answers 200 with the SPA SHELL for any path it has no file for, so a
# bare `curl -sf .../build-info.json` "succeeds" against an origin that has no
# stamp at all. Reachability and stamped-ness are therefore two separate
# questions: ask the first with the shell, and only trust the second if the body
# actually carries a source_id.
PUBLIC_UP=0
curl -sf --max-time 4 -o /dev/null "$PUBLIC_ORIGIN/" 2>/dev/null && PUBLIC_UP=1
SERVED_JSON="$(curl -sf --max-time 4 "$PUBLIC_ORIGIN/build-info.json" 2>/dev/null)"
SERVED_ID="$(json_field "$SERVED_JSON" source_id)"
[ -z "$SERVED_ID" ] && SERVED_JSON=""
VITE_UP=0
curl -sf --max-time 4 -o /dev/null "$VITE_ORIGIN/" 2>/dev/null && VITE_UP=1

problems=()
if [ -z "$DIST_JSON" ]; then
  problems+=("frontend/dist has no build stamp — build once: bash scripts/build_workshop_public.sh")
elif [ "$DIST_ID" != "$SRC_ID" ]; then
  problems+=("the public bundle is STALE (dist=$DIST_ID, source=$SRC_ID) — run: bash scripts/build_workshop_public.sh")
fi
if [ -n "$SERVED_JSON" ] && [ -n "$DIST_ID" ] && [ "$SERVED_ID" != "$DIST_ID" ]; then
  # Same repo, different artifact: the edge is reading some other directory.
  problems+=("the edge on $PUBLIC_ORIGIN serves $SERVED_ID but frontend/dist is $DIST_ID — check WORKSHOP_DIST on workshop-public.service")
fi

if [ "$mode" = "warn" ]; then
  for p in "${problems[@]:-}"; do [ -n "$p" ] && echo "warn: $p"; done
  exit 0
fi

fmt_id() { [ -n "$1" ] && printf '%-14s' "$1" || printf '%-14s' "—"; }
echo "UI origins (docs/ui-origins.md)"
printf '  %-16s %s\n' "source" "$(fmt_id "$SRC_ID")"
if [ -n "$DIST_JSON" ]; then
  extra="built $DIST_AT from $DIST_COMMIT"
  [ "$DIST_DIRTY" = "true" ] && extra="$extra (uncommitted frontend edits)"
  printf '  %-16s %s %s  %s\n' "frontend/dist" "$(fmt_id "$DIST_ID")" "$extra" \
    "$([ "$DIST_ID" = "$SRC_ID" ] && echo 'in sync' || echo 'STALE')"
else
  printf '  %-16s %s %s\n' "frontend/dist" "$(fmt_id "")" "no build stamp"
fi
if [ -n "$SERVED_JSON" ]; then
  printf '  %-16s %s %s\n' ":8002 public" "$(fmt_id "$SERVED_ID")" \
    "$([ "$SERVED_ID" = "$SRC_ID" ] && echo 'in sync' || echo 'SERVING OLDER SOURCE')"
elif [ "$PUBLIC_UP" = "1" ]; then
  printf '  %-16s %s %s\n' ":8002 public" "$(fmt_id "")" "up, but serving an unstamped bundle — rebuild to stamp it"
else
  printf '  %-16s %s %s\n' ":8002 public" "$(fmt_id "")" "unreachable (workshop-public.service down?)"
fi
printf '  %-16s %s %s\n' ":5173 vite" "$(fmt_id "from source")" \
  "$([ "$VITE_UP" = "1" ] && echo 'live — nothing to build' || echo 'not running')"

if [ ${#problems[@]} -gt 0 ] && [ -n "${problems[0]:-}" ]; then
  echo
  for p in "${problems[@]}"; do echo "  ! $p"; done
  [ "$mode" = "strict" ] && exit 1
fi
exit 0
