#!/usr/bin/env bash
# Run replay.py over every fetched clip and print a per-clip species summary —
# the quick correctness scan for the fine-tuning loop. For a single-bird clip the
# summary should list ONLY that species; extra/wrong species = a mis-ID to chase.
#
# Pass range args through to apply the location filter, e.g.:
#   ./test_all.sh --lat 37.4 --lon -122.1 --week 23
# Needs the clips fetched (./fetch.sh), ffmpeg, and the analyzer on :8012.
set -uo pipefail
cd "$(dirname "$0")"
shopt -s nullglob
clips=(single_* repeated_* urban_* group_*)
if [[ ${#clips[@]} -eq 0 ]]; then echo "no clips present — run ./fetch.sh first"; exit 1; fi
echo "Running ${#clips[@]} clips (first 60s each)${*:+ with args: $*}"
for f in "${clips[@]}"; do
  echo "============================================================"
  echo "## $f"
  python3 replay.py "$f" --max-seconds 60 "$@" 2>&1 | sed -n '/Per-species/,$p'
done
