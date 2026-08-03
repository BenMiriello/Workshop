#!/usr/bin/env bash
# ui_source_id.sh — the ONE definition of "which version of the UI source is this".
#
# Everything that needs to compare a build against its source (the build stamp,
# the sync check, the post-commit hook) calls THIS. Two implementations of "what
# counts as the UI source" would drift the moment one of them learned about a new
# input, and drift between the checker and the thing it checks is worse than no
# checker at all.
#
# Content-hashed, not mtime-based: `git checkout` and `touch` both move mtimes
# without changing what the bundle would contain, and the mtime heuristic this
# replaces cried stale on every branch switch. It also covers UNTRACKED files,
# because a new component that has not been committed yet still changes the
# bundle (a `git ls-files` version would call that build fresh — wrongly).
#
# Prints a 12-char hex id on stdout. Nothing else, so it composes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Hash RELATIVE paths (sha1sum prints the name it hashed), so the same content
# in a worktree at another path yields the same id instead of a false mismatch.
cd "$REPO_ROOT"
FE="frontend"

# The inputs that can change the emitted bundle. index.html is the SPA shell,
# the configs decide how it is compiled, and package-lock pins what is compiled
# in. node_modules and dist are outputs/inputs-of-inputs and are excluded.
INPUTS=(
  "$FE/src"
  "$FE/index.html"
  "$FE/vite.config.ts"
  "$FE/package.json"
  "$FE/package-lock.json"
)
for tsc in "$FE"/tsconfig*.json; do
  [ -f "$tsc" ] && INPUTS+=("$tsc")
done

EXISTING=()
for p in "${INPUTS[@]}"; do
  [ -e "$p" ] && EXISTING+=("$p")
done
if [ ${#EXISTING[@]} -eq 0 ]; then
  echo "no-source"
  exit 0
fi

# -print0/-0 so a path with a space can't split into two hashed names, and a
# locale-independent sort so the same tree hashes the same on any shell.
find "${EXISTING[@]}" -type f -print0 2>/dev/null \
  | LC_ALL=C sort -z \
  | xargs -0 sha1sum 2>/dev/null \
  | sha1sum \
  | cut -c1-12
