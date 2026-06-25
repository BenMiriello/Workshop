#!/bin/bash
# check_design.sh — Workshop Design System lint.
#
# Enforces frontend/CLAUDE.md mechanically. Two modes:
#
#   check_design.sh                  # full scan: every frontend/src/**/*.tsx
#   check_design.sh --added <rev>    # gate mode: only lines ADDED vs <rev>
#
# Gate mode mirrors reviewer.sh's stub check — it flags only NEW violations, so
# the large pre-existing inline-styled pages don't retroactively block an
# unrelated fix, but you can never ADD new inline hex / emoji / not-allowed.
#
# Flags in .tsx:
#   • raw hex colors        (#abc / #aabbcc) — use tokens or a primitive
#   • emoji                 — lucide icons only
#   • cursor:"not-allowed"  — disabled controls dim, never not-allowed
#
# Exempt paths (concrete colors legitimately live here):
#   src/styles/**                       (tokens.css / components.css / tokens.js)
#   src/ui/chart.tsx                    (CHART_SERIES — recharts needs literal colors)
#   src/ui/Map.tsx                      (maplibre StyleSpecification; token() reads CSS vars w/ hex fallback)
#   birds/recorder.tsx, birds/Spectrogram.tsx
#       (the spectrogram is a deliberate theme-independent near-black; a canvas
#        2D fillStyle cannot read CSS custom properties, and the wrapper matches it)
#
# Exit 0 = clean, 1 = violations, 2 = foundation broken.

set -u
WORKSHOP=/home/clawd-a/workshop
FE="$WORKSHOP/frontend"
MODE="full"
REV="HEAD"
if [[ "${1:-}" == "--added" ]]; then MODE="added"; REV="${2:-HEAD}"; fi

cd "$WORKSHOP" || { echo "check_design: workshop unreachable"; exit 2; }

# --- Foundation integrity (full mode only) ---------------------------------
if [[ "$MODE" == "full" ]]; then
  miss=()
  grep -q 'styles/tokens.css' "$FE/src/main.tsx"      || miss+=("main.tsx missing tokens.css import")
  grep -q 'styles/components.css' "$FE/src/main.tsx"  || miss+=("main.tsx missing components.css import")
  [[ -f "$FE/src/styles/tokens.css" ]]                || miss+=("src/styles/tokens.css missing")
  [[ -f "$FE/src/styles/components.css" ]]            || miss+=("src/styles/components.css missing")
  [[ -f "$FE/CLAUDE.md" ]]                            || miss+=("frontend/CLAUDE.md missing")
  if [[ ${#miss[@]} -gt 0 ]]; then
    printf 'FOUNDATION BROKEN:\n'; printf '  - %s\n' "${miss[@]}"; exit 2
  fi
fi

python3 - "$MODE" "$REV" <<'PYEOF'
import os, re, subprocess, sys

mode, rev = sys.argv[1], sys.argv[2]
FE = "/home/clawd-a/workshop/frontend"

EXEMPT = (
    "frontend/src/styles/",
    "frontend/src/ui/chart.tsx",
    "frontend/src/ui/Map.tsx",  # maplibre StyleSpecification needs literal colors; token() reads CSS vars w/ hex fallback
    "frontend/src/pages/apps/birds/recorder.tsx",
    "frontend/src/pages/apps/birds/Spectrogram.tsx",
    "frontend/src/pages/apps/birds/LiveFeed.tsx",  # remote spectrogram canvas; literal colors like Spectrogram.tsx
)
HEX = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
NOT_ALLOWED = re.compile(r"not-allowed")
# Emoji = decorative pictographs (📝 🛠 🔍 …), all in the astral planes + flags.
# Deliberately NOT typographic marks (→ ✓ ≈ · —) which are legitimate in labels
# and the design system itself uses ("Saved ✓", "View → Edit → Save").
EMOJI = re.compile("[" "\U0001F000-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "]")

def exempt(path):
    return any(path.startswith(p) or path == p for p in EXEMPT)

def scan_line(path, lineno, text, out):
    # Ignore the hex inside a code comment that's documenting a token value is
    # still flagged — comments shouldn't carry colors in tsx. Keep it strict.
    if HEX.search(text):
        out.append((path, lineno, "raw-hex", text.strip()[:80]))
    if EMOJI.search(text):
        out.append((path, lineno, "emoji", text.strip()[:80]))
    if NOT_ALLOWED.search(text):
        out.append((path, lineno, "not-allowed", text.strip()[:80]))

violations = []

if mode == "full":
    for root, _, files in os.walk(os.path.join(FE, "src")):
        for f in files:
            if not f.endswith(".tsx"):
                continue
            abs_p = os.path.join(root, f)
            rel = os.path.relpath(abs_p, "/home/clawd-a/workshop")
            if exempt(rel):
                continue
            with open(abs_p, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    scan_line(rel, i, line, violations)
else:
    # Added lines only. Parse unified diff; track new-file line numbers.
    diff = subprocess.run(
        ["git", "diff", "--unified=0", rev, "--", "frontend/src/**/*.tsx", "frontend/src"],
        capture_output=True, text=True, cwd="/home/clawd-a/workshop",
    ).stdout
    cur_file = None
    new_ln = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur_file = line[6:]
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if cur_file and cur_file.endswith(".tsx") and not exempt(cur_file):
                scan_line(cur_file, new_ln, line[1:], violations)
            new_ln += 1
        elif not line.startswith("-"):
            new_ln += 1

if violations:
    print(f"DESIGN VIOLATIONS ({len(violations)}) — see frontend/CLAUDE.md:")
    for path, ln, kind, snippet in violations:
        print(f"  {path}:{ln}  [{kind}]  {snippet}")
    sys.exit(1)
print("design: clean" + (f" (added lines vs {rev})" if mode == "added" else ""))
PYEOF
