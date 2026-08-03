#!/usr/bin/env bash
# stamp_build.sh — write frontend/dist/build-info.json so a BUILD can say what
# it was built from, and any origin serving it can be ASKED over HTTP.
#
# Why a stamp at all: inspecting frontend/dist on disk proves nothing about what
# an origin returned (see docs/ui-origins.md — that mistake has been made here).
# With a stamp, "is the public site running my fix" is one curl against the
# origin, not an inference from file times on a box.
#
# Two phases, because a build takes seconds and the source can change during it:
#   --capture  (npm prebuild)  record the source id BEFORE compiling
#   --write    (npm postbuild) write the stamp using the captured id
# Stamping the id read AFTER the build would label a bundle with source it does
# not contain — the exact false-clean the sync check exists to prevent. If the
# capture is missing (someone ran vite directly), the stamp records that instead
# of guessing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAPTURE_FILE="$REPO_ROOT/tmp/ui-build-src-id"
DIST="$REPO_ROOT/frontend/dist"

mode="${1:---write}"

if [ "$mode" = "--capture" ]; then
  mkdir -p "$REPO_ROOT/tmp"
  bash "$REPO_ROOT/scripts/ui_source_id.sh" > "$CAPTURE_FILE"
  exit 0
fi

if [ ! -d "$DIST" ]; then
  echo "stamp_build: no frontend/dist to stamp (build first)" >&2
  exit 1
fi

src_id="unknown-no-capture"
if [ -f "$CAPTURE_FILE" ]; then
  src_id="$(cat "$CAPTURE_FILE")"
  rm -f "$CAPTURE_FILE"
fi

cd "$REPO_ROOT"
commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
# "dirty" is about the FRONTEND only: an unrelated backend edit in the tree does
# not make this bundle unreproducible, and flagging it would train the eye to
# ignore the flag.
if [ -n "$(git status --porcelain -- frontend 2>/dev/null)" ]; then dirty=true; else dirty=false; fi

cat > "$DIST/build-info.json" <<EOF
{
  "source_id": "$src_id",
  "commit": "$commit",
  "commit_short": "$(printf '%s' "$commit" | cut -c1-7)",
  "branch": "$branch",
  "dirty": $dirty,
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "stamped dist: source_id=$src_id commit=$(printf '%s' "$commit" | cut -c1-7) dirty=$dirty"
