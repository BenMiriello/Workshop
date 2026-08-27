#!/usr/bin/env bash
# check_fresh_clone.sh — what a stranger who clones this repository receives.
#
# THE ONLY EVIDENCE THAT APP SEPARATION IS DONE, and until 2026-08-27 it did not
# exist. `docs/INDEX-knowledge-2026-08-26.md:542` cited it as though it did, the
# operator was told more than once that it is the acceptable proof, and three
# planning documents reference it by name. It had therefore never run and never
# passed. This is it.
#
# WHY A CLONE AND NOT A GREP OF THE WORKING TREE. Every other gate here measures
# the tree it is standing in, where an untracked file, a gitignored directory
# and a tracked one all look the same from the inside. The question this asks is
# different and cannot be asked from inside: what does `git clone` actually
# hand somebody? So it clones, and every assertion runs against the clone.
#
# SPEC: docs/platform-redesign/fresh-clone-assertion-design-2026-08-24.md.
# That document specifies five assertions. THREE ARE IMPLEMENTED HERE and two
# are not; the two that are not print as NOT IMPLEMENTED, are counted
# separately, and make the summary refuse to claim separation is done. See
# "WHAT IS NOT IMPLEMENTED" below. A green stub would be worse than nothing:
# the whole reason this file is being written is that a check nobody could run
# read, to every agent after it, exactly like a check that passed.
#
# THE ASSERTIONS
#
#   A1  no_app_source        the clone contains zero files that ARE app source,
#                            classified by scripts/check_no_app_names.py's
#                            `content` category. Reuses that classifier rather
#                            than defining app-source a second time.
#   A2  no_personal_data     the clone contains none of the path patterns in
#                            PERSONAL_DATA_PATTERNS below, and `apps/` and
#                            `agents/` carry nothing but templates.
#   A3  no_hardcoded_identity  scripts/check_no_hardcoding.sh over the clone,
#                            INCLUDING docs and *.md (`--all`), absolute, with
#                            the needles derived from THIS machine (`--tree`),
#                            because a clone cannot answer who it belongs to.
#
# WHAT IS NOT IMPLEMENTED (design sections 2 and 3):
#
#   A4  build_with_zero_apps        delete the app source, npm ci, build the
#                                   kit and the bundle, `python -c 'import
#                                   main'`. Measured at ~21s and needs a venv.
#   A5  shell_boots_with_empty_catalog   boot the backend against a throwaway
#                                   Postgres cluster and assert GET /apps is
#                                   empty. Needs a database; cannot pass while
#                                   A4 fails, since there is nothing to boot.
#
# Both are real work, not a rewording of what is here, and both are stated as
# NOT IMPLEMENTED on every run so that nobody reads this script's exit code as
# meaning more than it does.
#
# MODES
#
#   bash scripts/check_fresh_clone.sh              absolute. Every implemented
#                                                  assertion must hold. This is
#                                                  the command to run before
#                                                  claiming separation is done.
#   bash scripts/check_fresh_clone.sh --since REV  review mode, used by
#                                                  reviewer.sh. Fails only on
#                                                  what the diff made worse.
#   bash scripts/check_fresh_clone.sh --json       machine-readable
#   bash scripts/check_fresh_clone.sh --keep       keep the clone, print its path
#
# WHY --since EXISTS, and why it is not a weakening. Absolute mode fails today,
# loudly, on real inherited debt: 106 app-source files and 212 identity/home
# references in the doc tree. Running THAT as a per-diff review gate would
# reject every worker for debt it inherited, which is the documented cause of
# 2,066 loop jobs reporting `tasks_done: 0` since 2026-07-18 (see the headers of
# check_no_hardcoding.sh and check_no_app_names.py). So review mode compares the
# clone at REV against the clone at HEAD and fails on a count that got worse.
# It measures exactly the same things; it changes only what it is compared to.
#
# WORKDIR is $WORKSHOP/tmp, not /tmp: CLAUDE.md designates tmp/ for ephemeral
# output, .gitignore covers it, and /tmp here is a RAM-backed tmpfs on a box
# where turns have been killed at a memory ceiling.
#
# Exit 0 = every implemented assertion holds (absolute) or nothing regressed
#          (review mode). NEVER 0 with an assertion failing.
# Exit 1 = an assertion failed.
# Exit 2 = the check could not be run (bad ref, clone failed, missing delegate).

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$WORKSHOP" || { echo "check_fresh_clone: workshop unreachable" >&2; exit 2; }

AS_JSON=0
SINCE=""
KEEP=0
REV="HEAD"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)    AS_JSON=1 ;;
    --keep)    KEEP=1 ;;
    --since)   SINCE="${2:-}"; shift ;;
    --since=*) SINCE="${1#--since=}" ;;
    --rev)     REV="${2:-HEAD}"; shift ;;
    --rev=*)   REV="${1#--rev=}" ;;
    *) echo "check_fresh_clone: unknown argument '$1'" >&2; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# A2's list. THIS IS THE MAINTAINED PART OF THIS FILE.
#
# Every entry is a git pathspec, matched against the clone's tracked files. A
# match means the clone carries something that belongs to whoever ran the
# instance it was cloned from. Each entry says what it is and, where one was
# actually found in the repository, when.
#
# Add to this list when a new SHAPE of personal data is found. Do not add a
# single filename that an existing pattern already covers, and do not remove an
# entry because it currently matches nothing -- an entry matching nothing is the
# goal state, not dead weight, and removing it is how the next one gets in.
PERSONAL_DATA_PATTERNS=(
  # Screenshots of a live instance. Agent names, chat titles and file paths are
  # baked into the pixels and no grep will ever see them. Five were tracked at
  # the repo root until 2026-08-27 (agents-desktop.png, assistants*.png,
  # builder-mobile*.png).
  '*.png' '*.jpg' '*.jpeg' '*.gif' '*.webp'
  # Recordings. frontend/public/yard_sample.mp3 was 1.05 MB of audio captured
  # at the operator's home by his bird microphone, served publicly by the
  # frontend, tracked until 2026-08-27.
  '*.mp3' '*.wav' '*.m4a' '*.ogg' '*.aac' '*.flac'
  # A worker's ticket queue. `.tasks.json` and `TODO.md` were anchored to the
  # repo root in .gitignore, so a ticket directory one level down was fair game;
  # 4a79ffc515844ca2a48a369b7ca7b0a7/ was committed in 379d82fa and four more
  # such hex directories were on disk on 2026-08-27.
  ':(glob)**/.tasks.json'
  ':(glob)**/TODO.md'
  # Security audits of one operator's own machine: real account names, home
  # directory modes, ACLs, known-fixed weaknesses. Five were tracked until
  # 2026-08-27. The per-instance records directory they moved to is named after
  # the MACHINE, so it is read from .gitignore below rather than typed here --
  # this file must no more name one deployment than the platform source may.
  'docs/security-audit-*' 'docs/security-fixes-*'
  # Raw unedited voice dictation. Three navigation-shell transcripts, 38 KB,
  # tracked until 2026-08-27.
  ':(glob)**/TRANSCRIPT-*'
  # Instance state that is supposed to live in .runtime/ and be untracked.
  '.runtime/**' 'projects.json' 'build_agents.json' 'backend/apps.json'
  'backend/resource-grants.json' 'backend/store-policies.json'
  'shared/relay-corpus.jsonl' 'shared/queue/.tasks.json'
  # One operator's installed apps and their data.
  'apps/**' 'commute/**' 'backend/mcp/plaid/**'
  # Per-rig engineering records: hardware, tailnet addresses, bring-up history.
  ':(glob)**/BIRDSTATION-CONTEXT*'
  # Credentials, in any form. Belt and braces over .gitignore's *.env.
  '*.env' 'secrets.env' 'auth.env' ':(glob)**/*.pem' ':(glob)**/*.key'
  ':(glob)**/credentials.json' ':(glob)**/token.json'
)

# Exceptions, with the reason each is source rather than instance state. A
# pattern above is deliberately broad; this is where breadth is paid for.
PERSONAL_DATA_ALLOWED=(
  # Scaffold content: a TODO.md a new project starts FROM. Its sibling
  # .tasks.json is machine state and is NOT excepted.
  'projects/_templates/*/TODO.md'
  # The tracked human-readable work queue. Its .tasks.json is not tracked.
  'shared/queue/TODO.md'
  # Example/template config, which is the documented way to learn what to set.
  'infra/systemd/instance.env.example'
  'infra/systemd/secrets.env.example'
  'backend/store-policies.example.json'
  # Design-source mockups and diagrams that ship with a design document. These
  # are drawings, not screenshots of a running instance.
  'docs/platform-redesign/tickets-in-chat-design/*'
  'packages/ui/**'
  'frontend/public/favicon*'
  'mobile/assets/*'
)

# The per-instance documentation directories, ASKED OF THE REPO rather than
# typed. .gitignore already declares which doc subtrees are one deployment's
# operational records ("Tenant operational records: bring-up playbook and
# security audits of one operator's own box"), and their names are machine
# names. Reading them keeps this list identity-free and, more usefully, means a
# NEW instance-records directory is covered the moment it is gitignored,
# instead of the day somebody remembers to add it here.
while IFS= read -r _d; do
  [[ -n "$_d" ]] && PERSONAL_DATA_PATTERNS+=("${_d}**")
done < <(grep -E '^docs/[A-Za-z0-9_.-]+/$' "$WORKSHOP/.gitignore" 2>/dev/null)

# `apps/` and `agents/` must carry templates and nothing else.
TEMPLATE_ONLY_ROOTS=("agents" "apps" "projects")
TEMPLATE_ONLY_ALLOWED='^(agents/_templates/|agents/apps/|apps/_templates/|projects/_templates/)'

# ---------------------------------------------------------------------------
say() { [[ $AS_JSON -eq 1 ]] || printf '%s\n' "$*"; }

WORKDIR=""
cleanup() {
  if [[ -n "$WORKDIR" && $KEEP -eq 0 ]]; then rm -rf "$WORKDIR"; fi
}
trap cleanup EXIT

# Clone by PATH, never by remote: this installation has an origin it must not
# push to, and the check never touches the network. --no-hardlinks so the clone
# shares no object file with the live repository and a gc on either side cannot
# surprise the other.
make_clone() { # $1 = rev  -> echoes the clone dir
  # Two statements, not one: in a single `local a=.. b=$a`, `a` is declared and
  # still unset when `b` is expanded, which `set -u` reports as unbound.
  local rev="$1"
  local dir="$WORKDIR/clone-${rev//[^A-Za-z0-9]/_}"
  git clone --quiet --no-hardlinks "$WORKSHOP" "$dir" >/dev/null 2>&1 || return 1
  git -C "$dir" checkout --quiet --detach "$rev" >/dev/null 2>&1 || return 1
  printf '%s' "$dir"
}

# --- A1 -------------------------------------------------------------------
# Reuses check_no_app_names.py's `content` classifier. It is path-shaped, has
# zero false positives by construction, and already carries the roster logic.
# Defining "app source" a second time here is exactly the duplication that
# check_one_definition.py exists to catch.
a1_count() { # $1 = tree
  python3 "$WORKSHOP/scripts/check_no_app_names.py" --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["content_files"])' 2>/dev/null
}
# The classifier reads its own ROOT, so it is invoked with the clone as cwd via
# a copy of the script path; ROOT is derived from the script location, which is
# the workshop. Run it against the clone by pointing it at the clone's own copy.
a1_count_in() { # $1 = clone dir
  local dir="$1"
  [[ -f "$dir/scripts/check_no_app_names.py" ]] || { echo ""; return; }
  python3 "$dir/scripts/check_no_app_names.py" --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["content_files"])' 2>/dev/null
}

# --- A2 -------------------------------------------------------------------
a2_hits_in() { # $1 = clone dir -> one path per line
  local dir="$1"
  {
    git -C "$dir" ls-files -- "${PERSONAL_DATA_PATTERNS[@]}" 2>/dev/null
    # Anything under a template-only root that is not a template.
    local r
    for r in "${TEMPLATE_ONLY_ROOTS[@]}"; do
      git -C "$dir" ls-files -- "$r" 2>/dev/null
    done | grep -Ev "$TEMPLATE_ONLY_ALLOWED" || true
  } | sort -u | {
    # Subtract the excepted paths.
    if [[ ${#PERSONAL_DATA_ALLOWED[@]} -gt 0 ]]; then
      local allowed
      allowed="$(git -C "$dir" ls-files -- "${PERSONAL_DATA_ALLOWED[@]}" 2>/dev/null | sort -u)"
      if [[ -n "$allowed" ]]; then comm -23 - <(printf '%s\n' "$allowed"); else cat; fi
    else
      cat
    fi
  }
}

# --- A3 -------------------------------------------------------------------
# --all so docs and *.md are in scope (they were outside every gate this repo
# runs until 2026-08-27), and --tree so the CLONE is scanned while the needles
# still come from THIS machine: `git clone` does not copy .git/config, and this
# checkout sets user.name repo-locally, so needles re-derived inside a clone are
# a shorter list and the clone reports cleaner than the tree it came from.
a3_count_in() { # $1 = clone dir -> number of violation categories
  local out
  out="$(bash "$WORKSHOP/scripts/check_no_hardcoding.sh" --all --tree "$1" --json 2>/dev/null)"
  printf '%s' "$out" | python3 -c '
import json,sys
try: print(len(json.load(sys.stdin).get("violations", [])))
except Exception: print("")' 2>/dev/null
}
a3_detail_in() {
  bash "$WORKSHOP/scripts/check_no_hardcoding.sh" --all --tree "$1" 2>/dev/null \
    | grep '^no-hardcoding: ' || true
}

# ---------------------------------------------------------------------------
for delegate in check_no_app_names.py check_no_hardcoding.sh; do
  if [[ ! -f "$WORKSHOP/scripts/$delegate" ]]; then
    echo "check_fresh_clone: missing delegate scripts/$delegate" >&2
    exit 2
  fi
done

mkdir -p "$WORKSHOP/tmp" || { echo "check_fresh_clone: cannot create tmp" >&2; exit 2; }
WORKDIR="$(mktemp -d -p "$WORKSHOP/tmp" fresh-clone-XXXXXX)" || exit 2

HEAD_SHA="$(git -C "$WORKSHOP" rev-parse --short "$REV" 2>/dev/null)"
[[ -n "$HEAD_SHA" ]] || { echo "check_fresh_clone: cannot resolve rev '$REV'" >&2; exit 2; }

say "fresh-clone: cloning $REV ($HEAD_SHA). Committed content only -- uncommitted work is not measured."
CLONE="$(make_clone "$REV")" || { echo "check_fresh_clone: clone failed" >&2; exit 2; }

A1_NOW="$(a1_count_in "$CLONE")"
A2_HITS="$(a2_hits_in "$CLONE")"
A2_NOW=0; [[ -n "$A2_HITS" ]] && A2_NOW="$(printf '%s\n' "$A2_HITS" | wc -l | tr -d ' ')"
A3_NOW="$(a3_count_in "$CLONE")"

if [[ -z "$A1_NOW" || -z "$A3_NOW" ]]; then
  echo "check_fresh_clone: a delegate checker did not report a number; refusing to guess" >&2
  exit 2
fi

# --- review mode: the same measurements, at the base ref too ---------------
DELTA=0
A1_BASE=""; A2_BASE=""; A3_BASE=""
if [[ -n "$SINCE" ]]; then
  BASE_CLONE="$(make_clone "$SINCE")" || {
    echo "check_fresh_clone: cannot clone base ref '$SINCE'" >&2; exit 2; }
  A1_BASE="$(a1_count_in "$BASE_CLONE")"
  BH="$(a2_hits_in "$BASE_CLONE")"
  A2_BASE=0; [[ -n "$BH" ]] && A2_BASE="$(printf '%s\n' "$BH" | wc -l | tr -d ' ')"
  A3_BASE="$(a3_count_in "$BASE_CLONE")"
  if [[ -z "$A1_BASE" || -z "$A3_BASE" ]]; then
    echo "check_fresh_clone: base ref '$SINCE' did not measure; refusing to guess" >&2
    exit 2
  fi
  DELTA=1
fi

# --- verdicts --------------------------------------------------------------
# Three words, and PASS is one of them. A count that is above zero but not
# above the base is REPORTED and does not gate in review mode; it is somebody's
# problem, just not this run's, and it must not go quiet.
FAILED=0
INHERITED=()
verdict() { # $1=name $2=now $3=base(or empty) $4=unit
  local name="$1" now="$2" base="$3" unit="$4"
  if [[ "$now" -eq 0 ]]; then
    say "  PASS  $name  (0 $unit)"
    return
  fi
  if [[ $DELTA -eq 1 ]]; then
    if [[ "$now" -gt "$base" ]]; then
      say "  FAIL  $name  $base -> $now $unit; this change made it worse"
      FAILED=1
    else
      say "  ....  $name  $now $unit, $base of them already at $SINCE (inherited, not gating)"
      INHERITED+=("$name: $now $unit, $base inherited")
    fi
  else
    say "  FAIL  $name  $now $unit"
    FAILED=1
  fi
}

say ""
say "IMPLEMENTED ASSERTIONS"
verdict "no_app_source        " "$A1_NOW" "$A1_BASE" "tracked files that ARE app source"
verdict "no_personal_data     " "$A2_NOW" "$A2_BASE" "tracked personal-data path(s)"
verdict "no_hardcoded_identity" "$A3_NOW" "$A3_BASE" "violation categor(y/ies), docs included"

if [[ $AS_JSON -eq 0 && "$A2_NOW" -gt 0 ]]; then
  say "        the paths:"
  printf '%s\n' "$A2_HITS" | head -25 | sed 's/^/          /'
  [[ "$A2_NOW" -gt 25 ]] && say "          ... and $((A2_NOW - 25)) more"
fi
if [[ $AS_JSON -eq 0 && "$A3_NOW" -gt 0 ]]; then
  a3_detail_in "$CLONE" | sed 's/^/        /'
fi

say ""
say "NOT IMPLEMENTED -- these are not passing, they have never been run"
say "  ????  build_with_zero_apps          delete the app source, npm ci, build the"
say "                                      kit and the bundle, import backend/main."
say "  ????  shell_boots_with_empty_catalog  boot against a throwaway Postgres and"
say "                                      assert the app catalog comes back empty."
say "  spec: docs/platform-redesign/fresh-clone-assertion-design-2026-08-24.md, sections 2 and 3."

say ""
if [[ ${#INHERITED[@]} -gt 0 ]]; then
  say "  FYI ${#INHERITED[@]} assertion(s) carry findings this change did not add (not gating):"
  for i in "${INHERITED[@]}"; do say "      $i"; done
fi

# The claim "app separation is done" has exactly one code path, and two
# unimplemented assertions make it unreachable. That is the point: the pin file
# and the implementation status, not a document and not an agent's summary,
# decide whether the claim may be made.
if [[ $FAILED -eq 0 && "$A1_NOW" -eq 0 && "$A2_NOW" -eq 0 && "$A3_NOW" -eq 0 ]]; then
  say "fresh-clone: the three implemented assertions hold. NOT 'app separation is done':"
  say "             build_with_zero_apps and shell_boots_with_empty_catalog are unwritten."
else
  say "fresh-clone: NOT clean. A stranger cloning this repository receives the above."
fi

if [[ $KEEP -eq 1 ]]; then say "  clone kept at: $CLONE"; fi

if [[ $AS_JSON -eq 1 ]]; then
  python3 - "$A1_NOW" "$A2_NOW" "$A3_NOW" "$A1_BASE" "$A2_BASE" "$A3_BASE" "$DELTA" "$SINCE" "$FAILED" <<'PYEOF'
import json, sys
a1, a2, a3, b1, b2, b3, delta, since, failed = sys.argv[1:10]
out = {
    "fresh_clone": "fail" if failed == "1" else "ok",
    "implemented": {
        "no_app_source": int(a1),
        "no_personal_data": int(a2),
        "no_hardcoded_identity": int(a3),
    },
    "not_implemented": ["build_with_zero_apps", "shell_boots_with_empty_catalog"],
    "separation_done": False,
}
if delta == "1":
    out["since_ref"] = since
    out["base"] = {"no_app_source": int(b1), "no_personal_data": int(b2),
                   "no_hardcoded_identity": int(b3)}
print(json.dumps(out, indent=2))
PYEOF
fi

exit "$FAILED"
