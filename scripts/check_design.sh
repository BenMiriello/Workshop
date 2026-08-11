#!/bin/bash
# check_design.sh — Workshop Design System lint.
#
# Enforces frontend/CLAUDE.md mechanically. Two modes:
#
#   check_design.sh                  # full scan: frontend/src + packages/ui/src
#   check_design.sh --added <rev>    # gate mode: only lines ADDED vs <rev>
#
# Covers BOTH the app and the design system it consumes. The kit lives at
# packages/ui (the @workshop/ui package); frontend/ is one of its consumers.
#
# Gate mode mirrors reviewer.sh's stub check — it flags only NEW violations, so
# the large pre-existing inline-styled pages don't retroactively block an
# unrelated fix, but you can never ADD new inline hex / emoji / not-allowed.
#
# Flags in .tsx:
#   • raw hex colors        (#abc / #aabbcc) — use tokens or a primitive
#   • emoji                 — lucide icons only
#   • cursor:"not-allowed"  — disabled controls dim, never not-allowed
#   • maxWidth >= 600       — page width is the shell's job (--page-max), not a page's
#   • confirm() / alert()   — use <ConfirmDialog> / the app's own surfaces
#
# Exempt paths (concrete colors legitimately live here):
#   packages/ui/src/styles/**           (tokens.css / components.css / tokens.js)
#   packages/ui/src/chart.tsx           (CHART_SERIES — recharts needs literal colors)
#   packages/ui/src/Map.tsx             (maplibre StyleSpecification; token() reads CSS vars w/ hex fallback)
#   birds/recorder.tsx, birds/Spectrogram.tsx
#       (the spectrogram is a deliberate theme-independent near-black; a canvas
#        2D fillStyle cannot read CSS custom properties, and the wrapper matches it)
#
# Exit 0 = clean, 1 = violations, 2 = foundation broken.

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WORKSHOP
FE="$WORKSHOP/frontend"
# The design system itself lives outside the web app: the app is a consumer.
KIT="$WORKSHOP/packages/ui"
MODE="full"
REV="HEAD"
if [[ "${1:-}" == "--added" ]]; then MODE="added"; REV="${2:-HEAD}"; fi

cd "$WORKSHOP" || { echo "check_design: workshop unreachable"; exit 2; }

# --- Notes-and-docs tiering (N5) -------------------------------------------
# Mechanical guard against the flat-notes drift the notes-and-docs policy
# forbids: a NOTES.md at repo root or per-project, or an INTERVENTIONS.md
# outside build-agents/. The per-scope knowledge substrate + docs/ +
# docs/decisions/ + scratch/ cover every real notes surface; a flat NOTES.md
# creeps in when an agent forgets which tier it should be writing to.
# See ADR 0010 and docs/platform-redesign/coherence-migration-plan-2026-07-18.md
# section 8.5 N5.
_flat_notes_hits=$(
  ls "$WORKSHOP/NOTES.md" "$WORKSHOP/notes.md" 2>/dev/null
  find "$WORKSHOP/projects" -maxdepth 2 -type f \( -iname 'NOTES.md' -o -iname 'INTERVENTIONS.md' \) 2>/dev/null
  find "$WORKSHOP/apps"     -maxdepth 2 -type f -iname 'NOTES.md' 2>/dev/null
)
if [[ -n "$_flat_notes_hits" ]]; then
  echo "NOTES TIERING VIOLATION — see ADR 0010 (docs/decisions/0010-notes-vault-is-read-only-to-agents.md)"
  echo "the following flat notes surfaces are forbidden. Use per-scope"
  echo "knowledge (services/knowledge.py), scratch/, docs/, or docs/decisions/ instead:"
  echo "$_flat_notes_hits" | sed 's/^/  /'
  exit 1
fi

# --- Foundation integrity (full mode only) ---------------------------------
if [[ "$MODE" == "full" ]]; then
  miss=()
  grep -q '@workshop/ui/tokens.css' "$FE/src/main.tsx" || miss+=("main.tsx missing @workshop/ui/tokens.css import")
  grep -q '@workshop/ui/styles.css' "$FE/src/main.tsx" || miss+=("main.tsx missing @workshop/ui/styles.css import")
  [[ -f "$KIT/src/styles/tokens.css" ]]               || miss+=("packages/ui/src/styles/tokens.css missing")
  [[ -f "$KIT/src/styles/components.css" ]]           || miss+=("packages/ui/src/styles/components.css missing")
  [[ -f "$KIT/src/styles/palette.mjs" ]]              || miss+=("packages/ui/src/styles/palette.mjs (color model) missing")
  grep -q 'palettes.css' "$KIT/src/styles/tokens.css" || miss+=("tokens.css no longer imports palettes.css — color layer detached")
  [[ -f "$FE/CLAUDE.md" ]]                            || miss+=("frontend/CLAUDE.md missing")
  [[ -f "$KIT/package.json" ]]                        || miss+=("packages/ui/package.json missing")
  # The kit is consumed by package name, never by reaching into its source. A
  # relative import back into the kit means the boundary has been re-crossed
  # and out-of-repo consumers are silently diverging again.
  if grep -rqE 'from "(\.\./)+(packages/)?ui"' "$FE/src" --include=*.tsx --include=*.ts 2>/dev/null; then
    miss+=("frontend imports the kit by relative path — use the @workshop/ui package name")
  fi
  if [[ ${#miss[@]} -gt 0 ]]; then
    printf 'FOUNDATION BROKEN:\n'; printf '  - %s\n' "${miss[@]}"; exit 2
  fi

  # palettes.css / palettes.gen.ts / tokens.generated.js are GENERATED from
  # palette.mjs. Editing the model and forgetting to regenerate is invisible
  # until the app paints the old colors, so fail here instead.
  if ! node "$KIT/scripts/gen-tokens.mjs" --check; then
    echo "COLOR TOKENS STALE — run 'npm run gen:tokens' from packages/ui/"
    exit 2
  fi

  # The kit's public API is a contract other apps compile against, and out-of-
  # repo consumers are NOT compiled by anything here. An export that vanishes
  # without a version bump is exactly how Menu's items -> MenuItem change broke
  # them silently, so the snapshot is foundation, not advisory.
  if ! node "$KIT/scripts/api-snapshot.mjs" --check; then
    echo "KIT API DRIFTED — see packages/ui/CONTRIBUTING.md"
    exit 2
  fi
fi

python3 - "$MODE" "$REV" <<'PYEOF'
import os, re, subprocess, sys

mode, rev = sys.argv[1], sys.argv[2]
FE = os.environ["WORKSHOP"] + "/frontend"
KIT = os.environ["WORKSHOP"] + "/packages/ui"

EXEMPT = (
    "packages/ui/src/styles/",
    "packages/ui/src/chart.tsx",
    "packages/ui/src/Map.tsx",  # maplibre StyleSpecification needs literal colors; token() reads CSS vars w/ hex fallback
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
# Page width belongs to the shell (--page-max / --page-max-wide / --measure), not
# to a page. Small element caps are fine (a 150px number input, a 420px filter
# column), so this only fires at >=600px, which is container territory: nothing
# INSIDE a page legitimately wants a cap that wide. This is the rule that stopped
# the app having 720/760/820/880/980/1040/1280 all at once.
PAGE_WIDTH = re.compile(r"maxWidth:\s*[\"']?(\d{3,})(?:px)?[\"']?")
# Native browser dialogs. CLAUDE.md rule 8: a destructive action gets
# <ConfirmDialog> — danger treatment, a body naming the consequence, and that it
# can't be undone. window.confirm() is a blocking OS chrome box that can carry
# none of that, and alert() is the same hole for the reply. Five of these were
# live in the app before this rule existed, three of them destructive.
# The lookbehind is what keeps `confirmLabel`, `onConfirm`, `<ConfirmDialog`,
# `setConfirmOpen(` and `foo.alert(` out; requiring the "(" to sit flush against
# the name is what keeps prose like "a non-destructive confirm (e.g. publish)"
# out of the tally.
BROWSER_DIALOG = re.compile(r"(?<![\w.])(?:window\.)?(?:confirm|alert)\(")

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
    if BROWSER_DIALOG.search(text):
        out.append((path, lineno, "browser-dialog", text.strip()[:80]))
    m = PAGE_WIDTH.search(text)
    if m and int(m.group(1)) >= 600:
        out.append((path, lineno, "page-width", text.strip()[:80]))

violations = []

if mode == "full":
    # Both the app AND the kit. The kit's primitives used to sit under
    # frontend/src/ui and were scanned by walking one tree; now that they live
    # in packages/ui they need their own root, or the design system would be the
    # one place the design rules stopped applying.
    for scan_root in (os.path.join(FE, "src"), os.path.join(KIT, "src")):
        for root, _, files in os.walk(scan_root):
            for f in files:
                if not f.endswith(".tsx"):
                    continue
                abs_p = os.path.join(root, f)
                rel = os.path.relpath(abs_p, os.environ["WORKSHOP"])
                if exempt(rel):
                    continue
                with open(abs_p, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        scan_line(rel, i, line, violations)
else:
    # Added lines only. Parse unified diff; track new-file line numbers.
    diff = subprocess.run(
        ["git", "diff", "--unified=0", rev, "--", "frontend/src", "packages/ui/src"],
        capture_output=True, text=True, cwd=os.environ["WORKSHOP"],
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
# The scan's verdict, held so the advisory warnings below can still print. Two
# non-fatal warnings used to run AFTER the scanner and silently become the exit
# code, so the script announced "DESIGN VIOLATIONS" and exited 0 — and
# reviewer.sh gates on exactly this exit code (`if bash check_design.sh
# --added ...`), so no design rule has ever actually blocked a review.
design_status=$?

# --- published-bundle freshness ---------------------------------------------
# Not a design rule and never fatal: a WARNING when what an origin SERVES is not
# what the source SAYS (docs/ui-origins.md). Delegated to ui_sync.sh rather than
# reimplemented here — a second definition of "stale" is exactly how a checker
# and the thing it checks drift apart. This first read mtimes, which meant every
# `git checkout` cried stale and every `touch` hid a real one.
bash "$WORKSHOP/scripts/ui_sync.sh" --warn

# The automation only helps if it is switched on, and core.hooksPath is local
# config that no clone or fresh checkout inherits. Say so rather than let a tree
# silently stop rebuilding.
if [ "$(git -C "$WORKSHOP" config core.hooksPath 2>/dev/null)" != ".githooks" ]; then
  echo "warn: tracked git hooks are not installed — the public bundle will NOT rebuild on commit."
  echo "      run: bash scripts/install_hooks.sh"
fi

exit "$design_status"
