#!/bin/bash
# todo_stats.sh — one-line health summary of TODO.md.
#
# Emits open/blocked/proposed counts, total line count, and a spam-ratio
#   spam-ratio = (dead `[x]` lines + `## Log` lines) / total lines
# and prints a WARN line when spam-ratio > 0.5 so runaway autogeneration is
# visible in the next tick's SessionStart context.
#
# No external dependencies — pure shell over the local TODO.md file.

set -u

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TODO=${1:-$WORKSHOP/TODO.md}

if [[ ! -f "$TODO" ]]; then
  echo "todo_stats: no such file: $TODO" >&2
  exit 1
fi

total=$(wc -l < "$TODO" | tr -d ' ')
# `grep -c` always prints a count (0 included) but exits non-zero on no match;
# `|| true` swallows that exit without emitting a second "0" line (which would
# corrupt the count with a newline and break `printf %d`).
open=$(grep -c '^\[ \]' "$TODO" 2>/dev/null || true)
blocked=$(grep -c '^\[~\]' "$TODO" 2>/dev/null || true)
proposed=$(grep -c '^\[proposed\]' "$TODO" 2>/dev/null || true)
done=$(grep -c '^\[x\]' "$TODO" 2>/dev/null || true)

# Log lines: everything after the `## Log` header.
log_lines=$(awk '/^## Log/{f=1; next} f{c++} END{print c+0}' "$TODO")

# spam = dead `[x]` lines + Log lines. Guard against divide-by-zero.
spam=$((done + log_lines))
if [[ "$total" -gt 0 ]]; then
  ratio=$(awk -v s="$spam" -v t="$total" 'BEGIN{printf "%.2f", s/t}')
else
  ratio="0.00"
fi

printf 'todo_stats: open=%d blocked=%d proposed=%d done=%d total=%d spam-ratio=%s\n' \
  "$open" "$blocked" "$proposed" "$done" "$total" "$ratio"

# WARN threshold — awk comparison since ratio is a float.
if awk -v r="$ratio" 'BEGIN{exit !(r > 0.5)}'; then
  printf 'WARN: TODO.md spam-ratio %s > 0.50; dead [x]/Log lines dominate. TODO.md is now regenerated wholesale from the task store on each persist, so a high ratio means stale done/log tasks; clear them in the store.\n' "$ratio"
fi
