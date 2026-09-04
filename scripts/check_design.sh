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
#   whatever an installed app declares in its own workshop.app.json, as
#   ui.design_exempt (paths relative to that app's ui/ directory). This file
#   names no app.
#
# Scanned trees: frontend/src, packages/ui/src, and apps/*/ui. The last is not
# optional politeness -- an app's pages are pages a person looks at, and when
# app source left this repo on 2026-09-04 the design rules would otherwise have
# stopped applying to exactly the surfaces the operator uses most.
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
  # The kit resolves as a real package through the workspace link that a root
  # `npm install` creates. Without it every @workshop/ui import fails with a
  # bare "cannot find module", which reads like a broken import rather than a
  # missing install step.
  [[ -e "$WORKSHOP/node_modules/@workshop/ui" ]]      || miss+=("node_modules/@workshop/ui missing — run 'npm install' at the repo root")
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
import json, os, re, subprocess, sys

mode, rev = sys.argv[1], sys.argv[2]
FE = os.environ["WORKSHOP"] + "/frontend"
KIT = os.environ["WORKSHOP"] + "/packages/ui"

EXEMPT = [
    "packages/ui/src/styles/",
    "packages/ui/src/chart.tsx",
    "packages/ui/src/Map.tsx",  # maplibre StyleSpecification needs literal colors; token() reads CSS vars w/ hex fallback
]

# AN APP DECLARES ITS OWN EXEMPTIONS; this file names none.
#
# Three entries here used to be one app's canvas files, spelled as
# `frontend/src/pages/apps/<name>/...`. That is the exact shape the operator
# named on 2026-09-03: platform code carrying an app's name for a reason that is
# true of the app, not of the platform. When the app left the repo on 2026-09-04
# those three paths also stopped existing, so the gate was carrying dead strings
# that happened to be app names.
#
# An app states them in its own manifest instead:
#
#     "ui": { "design_exempt": ["recorder.tsx", "Spectrogram.tsx"] }
#
# paths relative to the app's `ui/` directory. A canvas 2D `fillStyle` cannot
# read CSS custom properties, which is a real and narrow reason; an app that
# claims it is making a claim in its own file, where its own reviewer sees it.
APPS_DIR = os.path.join(os.environ["WORKSHOP"], "apps")
for _slug in sorted(os.listdir(APPS_DIR)) if os.path.isdir(APPS_DIR) else []:
    _mf = os.path.join(APPS_DIR, _slug, "workshop.app.json")
    if not os.path.isfile(_mf):
        continue
    try:
        with open(_mf, encoding="utf-8") as _fh:
            _ui = (json.load(_fh) or {}).get("ui") or {}
    except (OSError, ValueError):
        continue          # a broken manifest is the app's problem, not this gate's
    for _rel in _ui.get("design_exempt") or []:
        EXEMPT.append(f"apps/{_slug}/ui/{_rel}")
EXEMPT = tuple(EXEMPT)
HEX = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
NOT_ALLOWED = re.compile(r"not-allowed")
# Emoji = decorative pictographs (📝 🛠 🔍 …), all in the astral planes + flags.
# Deliberately NOT typographic marks (→ ✓ ≈ ·) which are legitimate in labels
# and the design system itself uses ("Saved ✓", "View → Edit → Save").
# The em-dash used to be on that allowed list. It is now its own rule below.
EMOJI = re.compile("[" "\U0001F000-\U0001FAFF" "\U0001F1E6-\U0001F1FF" "]")
# CLAUDE.md rule 11: no em-dash in anything a user reads. Operator instruction,
# 2026-08-11. A sweep removed 144 of them; this is what stops the 145th, because
# a rule nobody checks is a rule that decays.
#
# It looks only INSIDE double-quoted and backtick string literals, and that
# precision is the point. The obvious version — flag the character anywhere on
# the line — fires on every comment in the app, and rule 11 exempts comments
# because they are not copy. A check that cries wolf gets switched off, so this
# one only speaks when it is right.
#
# Single-quoted strings are deliberately NOT scanned. Prose is full of
# apostrophes, so a naive single-quote matcher reads "can't show — built so the
# loudest birds don't dominate" as a string literal running from `can'` to
# `don'`, and reports a comment as copy. Double quotes and backticks are what
# this codebase writes copy in anyway.
#
# Known limit, stated so nobody assumes more than it does: bare JSX prose
# (<p>text — more</p>) is not caught, because telling it from a comment
# continuation line needs parser state this scanner does not carry. Rule 11
# still applies there; this catches the common case, not every case.
#
# Exempt: code that MATCHES or STRIPS an em-dash rather than displaying one, a
# regex character class or a .replace() removing it from model output. Those are
# the enforcement, and flagging them would mean deleting the fix.
_DQ_OR_BACKTICK = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`\\]*(?:\\.[^`\\]*)*)`')
_HANDLES_DASH = re.compile(r"replace\s*\(|RegExp|re\.compile|\[\^?[^\]]*—")


_COMMENT_LINE = re.compile(r"^\s*(//|\{\s*/\*|/\*|\*)")


def em_dash_in_copy(text):
    if "—" not in text or _HANDLES_DASH.search(text):
        return False
    # A whole-line comment is never copy, even when it quotes a string to
    # describe a data format ("steps — url"). Only the leading-marker case is
    # skipped, which is unambiguous; a trailing comment after code still gets
    # scanned, and that has been harmless.
    if _COMMENT_LINE.match(text):
        return False
    return any("—" in (m.group(1) or m.group(2) or "")
               for m in _DQ_OR_BACKTICK.finditer(text))
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
    if em_dash_in_copy(text):
        out.append((path, lineno, "em-dash", text.strip()[:80]))
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
    # An installed app's ui/ is scanned as well. `apps/` is absent on a fresh
    # clone, so this adds nothing there and everything here.
    _app_uis = []
    if os.path.isdir(APPS_DIR):
        _app_uis = [os.path.join(APPS_DIR, d, "ui") for d in sorted(os.listdir(APPS_DIR))
                    if os.path.isdir(os.path.join(APPS_DIR, d, "ui"))]
    for scan_root in [os.path.join(FE, "src"), os.path.join(KIT, "src")] + _app_uis:
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
    # `apps/` is its OWN repository, so the platform's diff cannot see a change
    # to an app's page. Ask that repo the same question against its own HEAD:
    # uncommitted app work is what a gate run mid-change is looking at, and
    # without this an app's UI could take on new violations with the gate green.
    if os.path.isdir(os.path.join(APPS_DIR, ".git")):
        diff += subprocess.run(
            ["git", "diff", "--unified=0", "HEAD", "--", "."],
            capture_output=True, text=True, cwd=APPS_DIR,
        ).stdout.replace("+++ b/", "+++ b/apps/")
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
# What an origin SERVES vs what the source SAYS (docs/ui-origins.md). Delegated
# to ui_sync.sh rather than reimplemented here — a second definition of "stale"
# is exactly how a checker and the thing it checks drift apart. This first read
# mtimes, which meant every `git checkout` cried stale and every `touch` hid a
# real one.
#
# FATAL, but only when this diff actually touched the bundle's inputs.
#
# It was advisory, and advisory was not enough: the operator's origin serves the
# BUILT bundle, so a UI change that is correct in source and never built is
# invisible to the agent and total to him. On 2026-08-09 a whole session of width
# fixes shipped that way and he said three times it was not done, while this
# exact warning printed after every commit and went unread. A warning that is
# always survivable is one that is eventually always survived.
#
# Scoped to the diff so it can never block unrelated work: a backend-only or
# docs-only change is not answerable for a bundle someone else left stale. Both
# path prefixes matter — packages/ui compiles INTO the bundle via vite's
# `workshop-source` condition, so a kit edit is a UI change.
#
# WHAT THIS SCOPING MISSED, AND THE DECISION TAKEN 2026-08-23. "Touched the
# bundle's inputs" was read off `git diff --name-only $REV`, which on a SHARED
# CHECKOUT is not this run's diff: it also carries every other agent's
# UNCOMMITTED edit. So one agent with an open .tsx made every other run in the
# tree fail this clause — and the failure was UNFIXABLE BY THE RUN THAT GOT IT.
# The only prescribed remedy is `scripts/apply.sh`, and apply.sh DELIBERATELY
# REFUSES to build while any uncommitted UI work is in the tree ("THE BUILT
# ORIGIN SERVES COMMITTED WORK ... uncommitted is the definition of unfinished",
# apply.sh, the ui_wip guard). `--publish-wip` would override it by dragging
# somebody's half-written screen onto the origin the operator looks at, which is
# the exact accident that guard was written for. And the run cannot commit those
# files either: CLAUDE.md says stage only your own. A gate whose only remedies
# are forbidden is not a gate, it is a deadlock, and it is one of the things
# that held every reviewer run at `reject` while 2,066 loop jobs reported
# tasks_done: 0.
#
# So the clause is now judged against WHAT A BUILD CAN ACTUALLY PUBLISH, which
# is the committed tree — the same line apply.sh and .githooks/post-commit
# already draw. FATAL when a UI input THIS RUN changed differs between the
# commit the bundle was built from and HEAD: that means this run's UI work is
# committed and the operator's origin does not have it. That is 2026-08-09
# exactly — those width fixes were correct in source, committed, and never
# built — so the case the rule exists for still fails, loudly.
#
# NOT fatal when the remaining staleness is uncommitted work, whoever's. It is
# reported by name instead. Nothing is lost by that: committing rebuilds
# automatically via the post-commit hook, committing as you go is already the
# repo rule, and :5173 serves the working tree live meanwhile. An uncommitted
# edit is BY POLICY not on the built origin, so failing a review over one is
# asserting a state the platform refuses to enter.
#
# Deliberately NOT done: dropping the clause, exempting the reviewer, or
# comparing source ids after stashing. ui_source_id.sh stays the ONE definition
# of "which UI source is this" and is not touched; this only decides who is
# ANSWERABLE for a mismatch it reports.
ui_inputs_touched=0
run_ui_files=""
if [ "$MODE" = "added" ]; then
  run_ui_files="$(git -C "$WORKSHOP" diff --name-only "$REV" 2>/dev/null \
                  | grep -E '^(frontend|packages/ui)/' || true)"
  [ -n "$run_ui_files" ] && ui_inputs_touched=1
fi
# A LINKED WORKTREE CANNOT ANSWER THE BUNDLE QUESTION, and failing it for that
# is how the completion gate became unusable for per-run worktrees.
#
# The check below asks "is this change's committed UI work in the built bundle".
# In a worktree the honest answer is "not yet, and it cannot be": there is no
# `frontend/dist` (it is gitignored, and provisioning hardlinks node_modules and
# the venv, not dist), and the built bundle belongs to the CANONICAL tree, which
# does not have this branch until it merges. `apply.sh` states the same fact from
# the other side: a UI change applied from a worktree reaches nobody.
#
# So it warns here instead of failing, and the question is asked for real on the
# canonical tree after the merge, where it can be answered. Detected exactly as
# apply.sh detects it, rather than by a second rule: in a linked worktree the
# git-dir and the git-common-dir differ.
IN_LINKED_WORKTREE=0
if [ "$(git -C "$WORKSHOP" rev-parse --git-dir 2>/dev/null)" \
     != "$(git -C "$WORKSHOP" rev-parse --git-common-dir 2>/dev/null)" ]; then
  IN_LINKED_WORKTREE=1
fi

if [ "$ui_inputs_touched" = 1 ] && [ "$IN_LINKED_WORKTREE" = 1 ]; then
  echo "warn: this change touches UI and is being checked inside a linked worktree,"
  echo "      which has no built bundle and whose branch the canonical tree does not"
  echo "      have yet. The bundle question is asked on the canonical tree after this"
  echo "      merges; failing it here would fail every UI change the loop ever works."
elif [ "$ui_inputs_touched" = 1 ]; then
  if ! bash "$WORKSHOP/scripts/ui_sync.sh" --strict; then
    # Which commit is the bundle built from? Recorded by the build itself, so
    # this is not a second definition of freshness — it is the same stamp
    # ui_sync.sh just read, asked a narrower question.
    dist_commit="$(python3 -c '
import json, sys
try:
    print(json.load(open("frontend/dist/build-info.json")).get("commit") or "")
except Exception:
    print("")' 2>/dev/null)"
    unbuilt=""
    if [ -n "$dist_commit" ] && git -C "$WORKSHOP" cat-file -e "$dist_commit^{commit}" 2>/dev/null; then
      # This run's UI files whose COMMITTED content is not what the bundle holds.
      unbuilt="$(git -C "$WORKSHOP" diff --name-only "$dist_commit" HEAD -- frontend packages/ui 2>/dev/null \
                 | grep -E '^(frontend|packages/ui)/' \
                 | grep -Fxf <(printf '%s\n' "$run_ui_files") || true)"
    else
      # No stamp, or a bundle built from a commit this repo does not have. We
      # cannot show the change is published, so we must not claim it is.
      unbuilt="(no usable build stamp on frontend/dist)"
    fi
    if [ -n "$unbuilt" ]; then
      echo "check_design: this change's COMMITTED UI work is not in the built bundle."
      echo "              the operator views the BUILT origin, so it does not have this change."
      printf '%s\n' "$unbuilt" | sed 's/^/              /'
      echo "              run: bash scripts/apply.sh"
      design_status=1
    else
      echo "warn: the bundle is stale, but not from this change's committed UI work —"
      echo "      it is uncommitted UI edits in this shared tree, which apply.sh"
      echo "      declines to publish on purpose. Commit them and the post-commit"
      echo "      hook builds; :5173 serves them live meanwhile."
      git -C "$WORKSHOP" status --porcelain -- frontend/src packages/ui frontend/index.html \
        2>/dev/null | sed 's/^/      /'
    fi
  fi
else
  # FULL MODE (no --added): there is no "this run's diff" to attribute the
  # staleness to, and until 2026-08-29 that meant staleness was advisory here
  # and fatal nowhere a person actually types. CLAUDE.md tells every agent to
  # run `bash scripts/check_design.sh` before calling a UI task done, which is
  # this branch, so the one command the rule names could never enforce it.
  # Measured that day: the bundle and the backend were both stale, this printed
  # `warn` and exited 0, and the operator reloaded and did not see his change.
  #
  # So the question is asked without attribution: is the bundle missing
  # COMMITTED work? That is operator-visible whoever committed it, and the
  # remedy is never forbidden (apply.sh builds committed work; the guard it
  # refuses on is UNcommitted work). Uncommitted staleness stays a warning for
  # the reasons above: apply.sh declines to publish it on purpose, so failing
  # over one asserts a state the platform will not enter.
  if ! bash "$WORKSHOP/scripts/ui_sync.sh" --strict; then
    dist_commit="$(python3 -c '
import json
try:
    print(json.load(open("frontend/dist/build-info.json")).get("commit") or "")
except Exception:
    print("")' 2>/dev/null)"
    unbuilt_committed=""
    if [ -n "$dist_commit" ] && git -C "$WORKSHOP" cat-file -e "$dist_commit^{commit}" 2>/dev/null; then
      unbuilt_committed="$(git -C "$WORKSHOP" diff --name-only "$dist_commit" HEAD -- frontend packages/ui 2>/dev/null \
                           | grep -E '^(frontend|packages/ui)/' || true)"
    else
      unbuilt_committed="(no usable build stamp on frontend/dist)"
    fi
    if [ -n "$unbuilt_committed" ] && [ "$IN_LINKED_WORKTREE" = 0 ]; then
      echo "check_design: COMMITTED UI work is not in the built bundle."
      echo "              the operator views the BUILT origin, so it does not have it."
      printf '%s\n' "$unbuilt_committed" | sed 's/^/              /'
      echo "              run: bash scripts/apply.sh"
      design_status=1
    else
      bash "$WORKSHOP/scripts/ui_sync.sh" --warn
    fi
  fi
fi

# A className no stylesheet defines renders UNSTYLED, and nothing else here sees
# it: tsc sees a string, the checks above look for inline hex and emoji, and the
# test suite covers pure modules with no DOM. Five live cases the first time this
# ran, one of which had been giving the Chats page a horizontal scroll on a phone
# for as long as the class had existed.
if ! python3 "$WORKSHOP/scripts/check_undefined_css.py"; then
  design_status=1
fi

# The automation only helps if it is switched on, and core.hooksPath is local
# config that no clone or fresh checkout inherits. Say so rather than let a tree
# silently stop rebuilding.
if [ "$(git -C "$WORKSHOP" config core.hooksPath 2>/dev/null)" != ".githooks" ]; then
  echo "warn: tracked git hooks are not installed — the public bundle will NOT rebuild on commit."
  echo "      run: bash scripts/install_hooks.sh"
fi

exit "$design_status"
