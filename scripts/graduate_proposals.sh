#!/bin/bash
# graduate_proposals.sh — flips `[proposed]` items to `[ ]` after a quiet
# period so they enter the cron loop autonomously.
#
# Operator's stated intent (CLAUDE.md): "the plan IS the answer to what next".
# Items the cron itself proposed sat in `## Proposed` waiting for the operator
# to manually graduate them — that defeats the autonomy. Anything in the
# `## Proposed` section older than GRAD_HOURS gets auto-graduated.
#
# Age proxy: the script tracks first-seen-time per proposal line in
# `.runtime/proposal-seen.json`. First time we see a `[proposed] X` line, we
# record now() as its first_seen. Once now() − first_seen ≥ GRAD_HOURS, we
# rewrite the line in place from `[proposed]` to `[ ]` with a trailing
# `(auto-graduated YYYY-MM-DD)` marker. Subsequent runs ignore graduated lines.

set -u

WORKSHOP=/home/clawd-a/workshop
TODO=$WORKSHOP/TODO.md
SEEN=$WORKSHOP/.runtime/proposal-seen.json
LOG=$WORKSHOP/.runtime/graduate_proposals.log

GRAD_HOURS=6

mkdir -p "$(dirname "$SEEN")" "$(dirname "$LOG")"
[[ -e "$SEEN" ]] || echo '{}' > "$SEEN"

ts() { date -Iseconds; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

python3 - <<'PYEOF' "$TODO" "$SEEN" "$GRAD_HOURS" "$LOG"
import json
import sys
import re
import hashlib
from datetime import datetime, timezone, timedelta

todo_path, seen_path, grad_hours_s, log_path = sys.argv[1:5]
grad_hours = int(grad_hours_s)

with open(todo_path, 'r', encoding='utf-8') as f:
    lines = f.read().splitlines(keepends=False)

with open(seen_path, 'r', encoding='utf-8') as f:
    seen = json.load(f)

now = datetime.now(timezone.utc)
threshold = now - timedelta(hours=grad_hours)

graduated = 0
new_seen_changed = False
out_lines = []

def line_key(s: str) -> str:
    body = s.strip()
    return hashlib.sha1(body.encode('utf-8')).hexdigest()[:16]

# Identify the `## Proposed` section to scope the graduator to it.
# All other [proposed] tokens (e.g. inside Log) are ignored.
in_proposed = False
section_header_re = re.compile(r'^##\s+\S')

for i, line in enumerate(lines):
    if section_header_re.match(line):
        in_proposed = (line.strip() == '## Proposed')
        out_lines.append(line)
        continue

    if in_proposed and line.startswith('[proposed]'):
        key = line_key(line)
        first_seen_iso = seen.get(key)
        if first_seen_iso is None:
            # First time seeing this proposal. Record now and skip.
            seen[key] = now.isoformat()
            new_seen_changed = True
            out_lines.append(line)
            continue

        try:
            first_seen = datetime.fromisoformat(first_seen_iso)
        except Exception:
            first_seen = now
            seen[key] = now.isoformat()
            new_seen_changed = True

        if first_seen <= threshold:
            today = now.strftime('%Y-%m-%d')
            new_line = line.replace('[proposed]', '[ ]', 1) + f' (auto-graduated {today})'
            out_lines.append(new_line)
            graduated += 1
            # Clear from seen so re-graduation can't accidentally re-fire on the same key.
            seen.pop(key, None)
            new_seen_changed = True
        else:
            out_lines.append(line)
    else:
        out_lines.append(line)

if graduated:
    with open(todo_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')

if new_seen_changed:
    with open(seen_path, 'w', encoding='utf-8') as f:
        json.dump(seen, f, indent=2)

with open(log_path, 'a', encoding='utf-8') as f:
    f.write(f"[{now.isoformat()}] graduated={graduated} tracked={len(seen)}\n")

print(graduated)
PYEOF
