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
# That document specifies five assertions. ALL FIVE ARE IMPLEMENTED HERE as of
# 2026-09-01; until then A4 and A5 printed as NOT IMPLEMENTED, which is how the
# script that exists to prove separation came to be itself half built. A green
# stub would be worse than nothing: the whole reason this file was written is
# that a check nobody could run read, to every agent after it, exactly like a
# check that passed. That is also why "could not run here" is exit 3 and its
# own word, and is never folded into PASS.
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
#   A4  build_with_zero_apps  delete from the clone exactly the files A1
#                            counts, then `npm ci`, `npm run build:ui`,
#                            `npm run build:web`, a venv with
#                            backend/requirements.txt, and `python -c
#                            'import main'`. THE DELETION IS WHAT MAKES IT
#                            MEAN ANYTHING: a clone still carries the app
#                            source, so without the deletion this assertion
#                            passes today for the wrong reason.
#                            It is the assertion no other gate in this repo
#                            can make. `frontend/src/apps/registry.tsx`
#                            reaches app pages through `lazyPage(() =>
#                            import(...))` with LITERAL module specifiers, so
#                            a registry naming apps that are not present
#                            typechecks, lints and builds here and fails for
#                            a stranger. The backend fails the same way from
#                            the other side: platform routes import app
#                            services, and the resulting ImportError is one
#                            exception class too narrow for
#                            app_registry._has_module's ModuleNotFoundError
#                            guard to catch.
#   A5  shell_boots_with_empty_catalog  initdb a THROWAWAY PostgreSQL cluster
#                            inside the workdir on a unix socket with TCP
#                            disabled, migrate it, boot the clone's backend
#                            against it on a probed-free loopback port, and
#                            assert the app catalog comes back empty, that
#                            /agents and /chat/threads answer without a 5xx,
#                            that zero routers mounted from
#                            app_registry.installed_specs(), and that the
#                            built bundle references chunks that exist.
#                            It boots the tree A4 built, so it is BLOCKED,
#                            not passed and not skipped, whenever A4 fails.
#
# WHY A5 CANNOT BE MADE GREEN BY BOOTING THE UNSTRIPPED TREE. `--harness-selftest`
# does exactly that and is the control experiment: it tells you whether a
# BLOCKED A5 is blocked by app coupling or by a broken harness. It prints
# SELFTEST, never PASS, never counts toward an assertion, and cannot produce the
# separation-is-done line. If you are reaching for it to make a run look better,
# it is the wrong lever and it does not work.
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
#   bash scripts/check_fresh_clone.sh --only NAME  run one assertion by name
#   bash scripts/check_fresh_clone.sh --harness-selftest
#                                                  boot the clone WITH its app
#                                                  source, to check A5's own
#                                                  machinery. Not an assertion.
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
# Exit 0 = every assertion holds (absolute) or nothing regressed (review mode).
#          NEVER 0 with an assertion failing, skipped, or pinned.
# Exit 1 = an assertion failed, a pin went stale, or a pin did not match.
# Exit 2 = the check could not be run at all (bad ref, clone failed, missing
#          delegate, or a pin for an assertion that is not pinnable).
# Exit 3 = AN ASSERTION COULD NOT BE MEASURED IN THIS ENVIRONMENT and nothing
#          else failed. Its own code on purpose. No network for `npm ci`, no
#          PostgreSQL server binaries, no venv: each of those is a reason this
#          run does not know the answer, and "does not know" must not be
#          reachable from a caller that only tests for zero. A silent pass is
#          the exact failure mode this whole script exists to end.

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$WORKSHOP" || { echo "check_fresh_clone: workshop unreachable" >&2; exit 2; }

AS_JSON=0
SINCE=""
KEEP=0
REV="HEAD"
ONLY=""
SELFTEST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)    AS_JSON=1 ;;
    --keep)    KEEP=1 ;;
    --since)   SINCE="${2:-}"; shift ;;
    --since=*) SINCE="${1#--since=}" ;;
    --rev)     REV="${2:-HEAD}"; shift ;;
    --rev=*)   REV="${1#--rev=}" ;;
    --only)    ONLY="${2:-}"; shift ;;
    --only=*)  ONLY="${1#--only=}" ;;
    --harness-selftest) SELFTEST=1 ;;
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
  # The throwaway Postgres cluster and the temporary backend die here, before
  # the workdir goes, and on every path out including a signal. A cluster left
  # running on a socket inside a deleted directory is the one piece of this
  # check that could outlive it.
  a5_teardown 2>/dev/null || true
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
  # THE UNIQUE SUFFIX IS LOAD-BEARING. The directory used to be named after the
  # rev alone, so `--since HEAD` -- which is exactly what reviewer.sh passes by
  # default, since its own baseline defaults to HEAD -- asked for `clone-HEAD`
  # twice, and the second `git clone` refused a non-empty target. That surfaced
  # as exit 2, which _delta_check reads as `fail`, which reviewer.sh reads as
  # `fresh_clone_regression`: the gate rejected every worker over a collision in
  # its own scratch directory. Found 2026-09-01 by running it.
  #
  # mktemp rather than a counter, because make_clone is called in a command
  # substitution: a counter would increment inside the subshell and be lost.
  local dir
  dir="$(mktemp -d "$WORKDIR/clone-${rev//[^A-Za-z0-9]/_}-XXXXXX")" || return 1
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
# THE PIN FILE. A pinned expected failure is a load-bearing admission that an
# assertion DOES NOT HOLD. It is never a pass, it never changes the exit code in
# absolute mode, and its only effect is that review mode does not reject a
# worker for debt the worker inherited. See the design doc, section 5.
PIN_FILE="$WORKSHOP/scripts/fresh_clone_baseline.json"

# WHICH ASSERTIONS MAY BE PINNED AT ALL, and the set lives HERE, in code, rather
# than in the JSON. Assertion 1 is already a ratchet with its own pinned number
# and an upward-move refusal; assertions 2 and 3 are absolute at zero, and an
# absolute check with an escape hatch is not absolute. Pinning one of those
# would mean editing this line, which reads like an act in a diff. Adding a key
# to a data file does not.
PINNABLE=" build_with_zero_apps shell_boots_with_empty_catalog "

pin_get() { # $1 = assertion, $2 = field -> the value, or nothing if unpinned
  [[ -f "$PIN_FILE" ]] || return 0
  python3 - "$PIN_FILE" "$1" "$2" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
e = (d.get("expected_failures") or {}).get(sys.argv[2])
if isinstance(e, dict):
    print(str(e.get(sys.argv[3], "")))
PY
}

pin_keys() {
  [[ -f "$PIN_FILE" ]] || return 0
  python3 - "$PIN_FILE" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
for k in (d.get("expected_failures") or {}):
    print(k)
PY
}

# ---------------------------------------------------------------------------
# ISOLATION. Every process A4 and A5 start runs under `env -i`: the live
# instance's environment carries database URLs, an OAuth token and a WORKSHOP
# root, and a backend that inherited any of them would be a SECOND backend
# pointed at the operator's own state. main.py's lifespan starts seven
# background loops and at least four of them mutate shared state (jobs
# reconcile, work_driver reconcile, chat continuation, port reclaim), so this is
# not tidiness. HOME and TMPDIR are throwaway directories inside the workdir, so
# nothing reads the operator's config directory and nothing writes outside the
# workdir.
# The base environment, as an ARRAY expanded into `env -i "${ISO[@]}" ...`.
# It was briefly a NUL stream piped through `xargs -0 env -i`, which is wrong in
# a way worth recording: xargs appends its stdin arguments AFTER the command
# line, so the assignments landed as trailing argv of the program being run.
# alembic said so and failed; `python -c` accepted them silently as sys.argv and
# ran with almost no environment at all, which is a check that looks like it is
# isolating and is not.
ISO=()
iso_init() {
  ISO=(
    "PATH=/usr/local/bin:/usr/bin:/bin"
    "HOME=$WORKDIR/home"
    "TMPDIR=$WORKDIR/tmpdir"
    "LANG=C.UTF-8"
    "LC_ALL=C.UTF-8"
  )
}

# IS THIS FAILURE THE ENVIRONMENT'S FAULT OR THE REPOSITORY'S? The distinction
# decides between exit 3 and exit 1 and it is the one judgement call in this
# file, so it is made from the failing tool's own error text rather than
# guessed. "There is no network here" and "the build is broken" are opposite
# findings and only one of them is about what a stranger receives.
looks_like_no_network() { # $1 = log file
  grep -qEi 'ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ETIMEDOUT|ENETUNREACH|network is unreachable|temporary failure in name resolution|proxy|read timed out|failed to establish a new connection|retries exceeded' "$1"
}

# ---------------------------------------------------------------------------
# A4  build_with_zero_apps
A4_STATUS=""      # pass | fail | skip
A4_NOTE=""
A4_REMOVED=0
A4_LOG=""
VENV=""

a4_run() { # $1 = clone dir
  local dir="$1"
  A4_LOG="$WORKDIR/build.log"
  : >"$A4_LOG"
  mkdir -p "$WORKDIR/home" "$WORKDIR/tmpdir"

  if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
    A4_STATUS="skip"; A4_NOTE="node and npm are not on PATH here"; return
  fi
  if ! python3 -c 'import venv' >/dev/null 2>&1; then
    A4_STATUS="skip"; A4_NOTE="the python3 venv module is unavailable here"; return
  fi

  # 1. DELETE THE APP SOURCE. The list comes from the CLONE's own copy of
  # check_no_app_names.py, so "what is app source" has exactly one definition
  # and this script does not carry a second one. Skipped under
  # --harness-selftest, which measures the harness rather than the separation.
  if [[ $SELFTEST -eq 0 ]]; then
    local list="$WORKDIR/app-source.txt"
    if ! python3 "$dir/scripts/check_no_app_names.py" --list-content >"$list" 2>>"$A4_LOG"; then
      A4_STATUS="skip"
      A4_NOTE="the clone's classifier could not list app source (--list-content)"
      return
    fi
    A4_REMOVED="$(grep -c . "$list" || true)"
    if [[ "${A4_REMOVED:-0}" -gt 0 ]]; then
      ( cd "$dir" && tr '\n' '\0' <"$list" | xargs -0 --no-run-if-empty rm -f -- ) >>"$A4_LOG" 2>&1
    fi
    echo "== removed $A4_REMOVED app-source file(s) ==" >>"$A4_LOG"
  else
    echo "== SELFTEST: app source left in place ==" >>"$A4_LOG"
  fi

  # 2. Install. Workspaces hoist, so this is the only install.
  echo "== npm ci ==" >>"$A4_LOG"
  if ! ( cd "$dir" && npm ci --no-audit --no-fund --prefer-offline ) >>"$A4_LOG" 2>&1; then
    if ! npm ping >/dev/null 2>&1; then
      A4_STATUS="skip"
      A4_NOTE="npm ci failed and the configured npm registry does not answer a ping"
      return
    fi
    A4_STATUS="fail"; A4_NOTE="npm ci failed with the registry reachable"; return
  fi

  # 3-5. THE TWO HALVES ARE MEASURED INDEPENDENTLY, and the run does not stop at
  # the first one that breaks. They fail for unrelated reasons and only one of
  # them is visible to any other gate: the frontend break is the literal module
  # specifiers in the app registry, which every document cites; the backend break
  # runs through platform routes that import app services and is NOT counted by
  # assertion 1 at all, so driving assertion 1 to zero would not by itself make
  # this pass. Reporting only the first would hide exactly the half that nobody
  # is tracking.
  local frontend_note="" backend_note=""

  local step
  for step in build:ui build:web; do
    echo "== npm run $step ==" >>"$A4_LOG"
    if ! ( cd "$dir" && npm run "$step" ) >>"$A4_LOG" 2>&1; then
      frontend_note="npm run $step failed$(a4_frontend_detail)"
      break
    fi
  done

  # The backend's dependencies, in a venv OUTSIDE the clone so the tree the
  # assertions measure stays the tree git handed over.
  VENV="$WORKDIR/venv"
  echo "== python3 -m venv + pip install ==" >>"$A4_LOG"
  if ! python3 -m venv "$VENV" >>"$A4_LOG" 2>&1; then
    A4_STATUS="skip"; A4_NOTE="python3 -m venv failed here"; return
  fi
  local piplog="$WORKDIR/pip.log"
  if ! "$VENV/bin/pip" install -q -r "$dir/backend/requirements.txt" >"$piplog" 2>&1; then
    cat "$piplog" >>"$A4_LOG"
    if looks_like_no_network "$piplog"; then
      A4_STATUS="skip"
      A4_NOTE="pip install failed with a network error, so the backend half was not measured"
      return
    fi
    A4_STATUS="fail"
    A4_NOTE="pip install -r backend/requirements.txt failed"
    return
  fi

  # The backend's import graph. This is exactly what uvicorn does at startup and
  # what the repo's own restart preflight already uses for the same reason.
  echo "== python -c 'import main' ==" >>"$A4_LOG"
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" "WORKSHOP_ROOT=$dir" \
           "$VENV/bin/python" -c 'import main' ) >>"$A4_LOG" 2>&1; then
    backend_note="import main failed$(a4_backend_detail)"
  fi

  if [[ -n "$frontend_note" || -n "$backend_note" ]]; then
    A4_STATUS="fail"
    A4_NOTE="frontend: ${frontend_note:-ok}; backend: ${backend_note:-ok}"
    return
  fi

  A4_STATUS="pass"
  if [[ $SELFTEST -eq 1 ]]; then
    A4_NOTE="SELFTEST build, app source NOT removed: kit, bundle and backend import all succeeded"
  else
    A4_NOTE="$A4_REMOVED app-source file(s) removed; kit, bundle and backend import all succeeded"
  fi
}

# The one line that says WHAT broke, so the verdict is diagnostic without
# opening the log. Deliberately shaped: a count and the file, not a wall.
a4_frontend_detail() {
  local n first
  n="$(grep -c 'error TS' "$A4_LOG" 2>/dev/null || true)"
  first="$(grep -m1 -oE '[^ ]+\([0-9]+,[0-9]+\): error TS[0-9]+: [^\r]*' "$A4_LOG" 2>/dev/null || true)"
  [[ -z "$first" ]] && first="$(grep -m1 -E 'error|Error' "$A4_LOG" 2>/dev/null | tail -c 160 || true)"
  [[ -n "$first" ]] && printf ' (%s errors, first: %s)' "${n:-?}" "${first:0:140}"
}

a4_backend_detail() {
  # Only the tail after the import step's own marker: the frontend half wrote
  # first and a match from up there would name the wrong failure.
  local tail_txt first
  tail_txt="$(sed -n "/^== python -c /,\$p" "$A4_LOG" 2>/dev/null || true)"
  first="$(printf '%s\n' "$tail_txt" | grep -m1 -E "^[A-Za-z_.]*(Error|Exception):" || true)"
  [[ -z "$first" ]] && first="$(printf '%s\n' "$tail_txt" | tail -n 3 | tr '\n' ' ')"
  [[ -n "$first" ]] && printf ' (%s)' "${first:0:160}"
}

# ---------------------------------------------------------------------------
# A5  shell_boots_with_empty_catalog
A5_STATUS=""      # pass | fail | skip | blocked
A5_NOTE=""
A5_LOG=""
PGBIN=""
PG_DATA=""
PG_SOCK=""
UVICORN_PID=""

pg_start() { # brings the throwaway cluster up; TCP is off, socket is in WORKDIR
  "$PGBIN/pg_ctl" -D "$PG_DATA" -w -l "$WORKDIR/pg.log" \
    -o "-h '' -k $PG_SOCK" start
}

backend_stop() {
  if [[ -n "$UVICORN_PID" ]]; then
    kill "$UVICORN_PID" >/dev/null 2>&1 || true
    wait "$UVICORN_PID" 2>/dev/null || true
    UVICORN_PID=""
  fi
}

a5_teardown() {
  backend_stop
  if [[ -n "$PGBIN" && -n "$PG_DATA" && -d "$PG_DATA" ]]; then
    "$PGBIN/pg_ctl" -D "$PG_DATA" -m immediate stop >/dev/null 2>&1 || true
  fi
}

a5_start_backend() { # $1 = clone dir, $2 = port, $3 = app dsn ; sets UVICORN_PID
  local dir="$1" port="$2" dsn="$3"
  ( cd "$dir/backend" && exec env -i "${ISO[@]}" \
      "WORKSHOP_ROOT=$dir" \
      "WORKSHOP_DATABASE_URL=$dsn" \
      "WORKSHOP_REGISTRATION_OPEN=1" \
      "$VENV/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$port" \
  ) >>"$WORKDIR/uvicorn.log" 2>&1 &
  UVICORN_PID=$!
  local i
  for i in $(seq 1 60); do
    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then return 1; fi
    if python3 - "http://127.0.0.1:$port/healthz" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2).read()
PY
    then return 0; fi
    sleep 0.5
  done
  return 1
}

a5_run() { # $1 = clone dir
  local dir="$1"
  A5_LOG="$WORKDIR/boot.log"
  : >"$A5_LOG"

  case "$A4_STATUS" in
    pass) ;;
    skip)
      A5_STATUS="skip"
      A5_NOTE="prerequisite build_with_zero_apps could not run here, so there is nothing to boot"
      return ;;
    *)
      # NOT a skip. The build failing is exactly what this assertion is
      # downstream of, and calling that "could not run" would launder a real
      # failure into an environment problem.
      A5_STATUS="blocked"
      A5_NOTE="prerequisite build_with_zero_apps did not hold; with the app source removed there is no bundle and no importable backend to boot"
      return ;;
  esac

  # PostgreSQL. GET /apps is served from Postgres with no file fallback, so this
  # assertion needs a database and cannot be reduced to a fileless boot.
  local c
  for c in /usr/lib/postgresql/*/bin /usr/pgsql-*/bin; do
    [[ -x "$c/initdb" && -x "$c/pg_ctl" ]] && PGBIN="$c"
  done
  if [[ -z "$PGBIN" ]] && command -v initdb >/dev/null 2>&1; then
    PGBIN="$(dirname "$(command -v initdb)")"
  fi
  if [[ -z "$PGBIN" ]]; then
    A5_STATUS="skip"
    A5_NOTE="no PostgreSQL server binaries (initdb/pg_ctl) on this host"
    return
  fi
  echo "== postgres binaries: $PGBIN ==" >>"$A5_LOG"

  # A THROWAWAY CLUSTER, ON A UNIX SOCKET, WITH TCP OFF. Stronger than a spare
  # TCP port: there is no port to collide with and nothing on loopback to reach
  # by accident, so the live cluster on this host is not a reachable target even
  # by mistake. It dies with the workdir.
  PG_DATA="$WORKDIR/pgdata"
  PG_SOCK="$WORKDIR/pgsock"
  mkdir -p "$PG_SOCK"
  if ! "$PGBIN/initdb" -D "$PG_DATA" -U workshop --auth=trust -E UTF8 >>"$A5_LOG" 2>&1; then
    A5_STATUS="skip"; A5_NOTE="initdb could not create a throwaway cluster"; return
  fi
  if ! pg_start >>"$A5_LOG" 2>&1; then
    A5_STATUS="skip"; A5_NOTE="pg_ctl could not start the throwaway cluster"; return
  fi
  if ! "$PGBIN/createdb" -h "$PG_SOCK" -U workshop workshop >>"$A5_LOG" 2>&1; then
    A5_STATUS="skip"; A5_NOTE="createdb failed on the throwaway cluster"; return
  fi

  local admin_dsn="postgresql+psycopg://workshop@/workshop?host=$PG_SOCK"
  local app_dsn="postgresql+psycopg://workshop_app:freshclone@/workshop?host=$PG_SOCK"

  # THE ROLE COMES FIRST, before alembic, and this is not the documented order.
  # db/bootstrap.py says "run after alembic upgrade"; run that way against an
  # empty cluster the migration dies on `REVOKE ... FROM workshop_app`, because
  # the role does not exist yet. Recorded in the design doc, section 3.2.
  if ! "$PGBIN/psql" -h "$PG_SOCK" -U workshop -d workshop -v ON_ERROR_STOP=1 \
        -c "CREATE ROLE workshop_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD 'freshclone'" \
        >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="could not create the workshop_app role"; return
  fi
  echo "== alembic upgrade head ==" >>"$A5_LOG"
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" \
          "WORKSHOP_ROOT=$dir" "WORKSHOP_DATABASE_ADMIN_URL=$admin_dsn" \
          "$VENV/bin/alembic" upgrade head ) >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="alembic upgrade head failed on an empty cluster"; return
  fi
  echo "== db.bootstrap ==" >>"$A5_LOG"
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" \
          "WORKSHOP_ROOT=$dir" "WORKSHOP_DATABASE_ADMIN_URL=$admin_dsn" \
          "WORKSHOP_DB_APP_PASSWORD=freshclone" \
          "$VENV/bin/python" -m db.bootstrap ) >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="python -m db.bootstrap failed"; return
  fi

  # A probed-free ephemeral loopback port. 8000, 5173, 5440, 5432 and friends
  # are in use on this host and none of them is a candidate.
  local port
  port="$(python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
)"
  echo "== uvicorn on 127.0.0.1:$port ==" >>"$A5_LOG"
  if ! a5_start_backend "$dir" "$port" "$app_dsn"; then
    A5_STATUS="fail"
    A5_NOTE="the backend did not answer GET /healthz within 30s"
    tail -n 30 "$WORKDIR/uvicorn.log" >>"$A5_LOG" 2>/dev/null || true
    return
  fi

  # A first user, promoted to operator, then the backfill that gives it a
  # tenant. Without the backfill /apps answers 500 TenantUnresolved, which is a
  # true finding about a stranger's first five minutes but is not this
  # assertion, and the design doc records it in section 3.2 rather than papering
  # over it here.
  local base="http://127.0.0.1:$port"
  if ! python3 "$WORKDIR/probe.py" "$base" register "$WORKDIR/session.txt" >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="POST /auth/register did not create the first user"; return
  fi
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" \
          "WORKSHOP_ROOT=$dir" "WORKSHOP_DATABASE_URL=$app_dsn" \
          "$VENV/bin/python" -c 'from services import auth_store; auth_store.set_operator(1)' \
       ) >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="could not promote the first user to operator"; return
  fi
  echo "== db/backfill.py --apply --force ==" >>"$A5_LOG"
  backend_stop
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" \
          "WORKSHOP_ROOT=$dir" "WORKSHOP_DATABASE_URL=$app_dsn" \
          "WORKSHOP_DATABASE_ADMIN_URL=$admin_dsn" \
          "$VENV/bin/python" db/backfill.py --apply --force ) >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="db/backfill.py --apply --force failed"; return
  fi
  if ! a5_start_backend "$dir" "$port" "$app_dsn"; then
    A5_STATUS="fail"
    A5_NOTE="the backend did not come back after the first user was seeded"
    tail -n 30 "$WORKDIR/uvicorn.log" >>"$A5_LOG" 2>/dev/null || true
    return
  fi

  # THE ASSERTION ITSELF: the shell's own calls, answered by an instance that
  # has never had an app.
  if ! python3 "$WORKDIR/probe.py" "$base" assert "$WORKDIR/session.txt" >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"
    A5_NOTE="a shell call did not answer as an empty instance: $(grep -m1 '^NOT-EMPTY' "$A5_LOG" | cut -c1-160)"
    return
  fi
  # Zero routers mounted from the installed-app catalog.
  if ! ( cd "$dir/backend" && env -i "${ISO[@]}" \
          "WORKSHOP_ROOT=$dir" "WORKSHOP_DATABASE_URL=$app_dsn" \
          "$VENV/bin/python" -c 'from services import app_registry; n=len(app_registry.installed_specs()); print("installed_specs", n); raise SystemExit(0 if n==0 else 1)' \
       ) >>"$A5_LOG" 2>&1; then
    A5_STATUS="fail"; A5_NOTE="app_registry.installed_specs() is not empty on a fresh instance"; return
  fi
  # The built bundle is an ARTIFACT assertion, not an HTTP one: static serving
  # is a separate service in a self-hosting deployment and the backend's GET /
  # only redirects, so asking the backend for the shell would test the wrong
  # thing.
  if ! python3 - "$dir/frontend/dist" <<'PY' >>"$A5_LOG" 2>&1
import pathlib, re, sys
dist = pathlib.Path(sys.argv[1])
index = dist / "index.html"
if not index.is_file():
    print("no frontend/dist/index.html"); raise SystemExit(1)
html = index.read_text()
refs = re.findall(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html)
if not refs:
    print("index.html references no entry chunk"); raise SystemExit(1)
missing = [r for r in refs if not (dist / r.lstrip("/")).is_file()]
if missing:
    print("index.html references chunks that are not on disk:", missing); raise SystemExit(1)
print(f"bundle ok: {len(refs)} referenced chunk(s) all present")
PY
  then
    A5_STATUS="fail"; A5_NOTE="the built bundle is missing or references chunks that are not on disk"; return
  fi

  A5_STATUS="pass"
  A5_NOTE="booted against a throwaway cluster; catalog empty, zero mounted app routers, bundle intact"
}

# The HTTP half of A5, written once into the workdir.
#
# IT CARRIES THE SESSION COOKIE BY HAND rather than through http.cookiejar, and
# the reason is a real property of the product: the session cookie is issued
# `Secure`, so a cookie jar correctly refuses to send it back over the plain
# HTTP this harness speaks on loopback, and every call answers 401. Reading the
# value out of Set-Cookie and setting the header explicitly is the harness
# adapting to the app, not the app being weakened for the harness.
write_probe() {
  cat >"$WORKDIR/probe.py" <<'PROBE_EOF'
import json, os, re, sys, urllib.error, urllib.request

base, phase, sessfile = sys.argv[1], sys.argv[2], sys.argv[3]
session = ""
if os.path.exists(sessfile):
    session = open(sessfile).read().strip()


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if session:
        headers["Cookie"] = session
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(errors="replace"), r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), []
    except Exception as e:
        return 0, repr(e), []


if phase == "register":
    st, text, setc = call("POST", "/auth/register", {
        "email": "first-user@example.invalid",
        "password": "fresh-clone-assertion-passphrase",
        "name": "first user",
    })
    pairs = [m.group(1) for c in setc for m in [re.match(r"([^;]+)", c)] if m]
    if pairs:
        open(sessfile, "w").write("; ".join(pairs))
    print("register:", st, text[:300])
    raise SystemExit(0 if st < 400 and pairs else 1)


def is_empty(value):
    """An empty catalog, in whichever envelope the route happens to use."""
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        for key in ("apps", "agents", "threads", "items", "results"):
            if key in value:
                return isinstance(value[key], list) and not value[key]
    return False


failed = False
for path in ("/apps", "/agents", "/chat/threads"):
    st, text, _ = call("GET", path)
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if st == 0 or st >= 400 or not is_empty(parsed):
        print("NOT-EMPTY %s: HTTP %s %s" % (path, st, text[:200]))
        failed = True
    else:
        print("ok %s: HTTP %s empty" % (path, st))
raise SystemExit(1 if failed else 0)
PROBE_EOF
}

# ---------------------------------------------------------------------------
for delegate in check_no_app_names.py check_no_hardcoding.sh; do
  if [[ ! -f "$WORKSHOP/scripts/$delegate" ]]; then
    echo "check_fresh_clone: missing delegate scripts/$delegate" >&2
    exit 2
  fi
done

ASSERTIONS=" no_app_source no_personal_data no_hardcoded_identity build_with_zero_apps shell_boots_with_empty_catalog "
if [[ -n "$ONLY" && "$ASSERTIONS" != *" $ONLY "* ]]; then
  echo "check_fresh_clone: unknown assertion '$ONLY'. One of:$ASSERTIONS" >&2
  exit 2
fi
want() { [[ -z "$ONLY" || "$ONLY" == "$1" ]]; }

# THE PIN FILE MAY ONLY SPEAK ABOUT PINNABLE ASSERTIONS. A key outside the set
# declared in code above is a refusal, not a warning: pinning "no app source" or
# "no operator identity" would turn an absolute assertion into an advisory, and
# doing it by adding a line to a data file is exactly the quiet move this whole
# instrument exists to make impossible.
for pinned_key in $(pin_keys); do
  if [[ "$PINNABLE" != *" $pinned_key "* ]]; then
    echo "check_fresh_clone: assertion '$pinned_key' is not pinnable; it must hold or fail" >&2
    exit 2
  fi
done

mkdir -p "$WORKSHOP/tmp" || { echo "check_fresh_clone: cannot create tmp" >&2; exit 2; }
WORKDIR="$(mktemp -d -p "$WORKSHOP/tmp" fresh-clone-XXXXXX)" || exit 2
iso_init
write_probe

HEAD_SHA="$(git -C "$WORKSHOP" rev-parse --short "$REV" 2>/dev/null)"
[[ -n "$HEAD_SHA" ]] || { echo "check_fresh_clone: cannot resolve rev '$REV'" >&2; exit 2; }

say "fresh-clone: cloning $REV ($HEAD_SHA). Committed content only -- uncommitted work is not measured."
CLONE="$(make_clone "$REV")" || { echo "check_fresh_clone: clone failed" >&2; exit 2; }

# --- harness selftest ------------------------------------------------------
# The control experiment, and not an assertion. It builds and boots the clone
# WITH its app source, which answers one question and only one: is A5 blocked
# because the platform cannot stand without the apps, or because this harness is
# broken? It cannot print PASS, it cannot reach the separation-is-done line, and
# it exits on its own terms.
if [[ $SELFTEST -eq 1 ]]; then
  say ""
  say "HARNESS SELFTEST -- app source LEFT IN PLACE. This is not an assertion and"
  say "                   it says nothing about app separation."
  a4_run "$CLONE"
  a5_run "$CLONE"
  say "  SELFTEST  build   $A4_STATUS: $A4_NOTE"
  say "            log: $A4_LOG"
  say "  SELFTEST  boot    $A5_STATUS: $A5_NOTE"
  say "            log: $A5_LOG"
  KEEP=1
  say "  workdir kept at: $WORKDIR"
  [[ "$A4_STATUS" == "pass" && "$A5_STATUS" == "pass" ]] && exit 0
  exit 1
fi

# --- A1..A3, the whole-tree measurements -----------------------------------
A1_NOW=""; A2_NOW=0; A2_HITS=""; A3_NOW=""
MEASURE_TREE=0
for a in no_app_source no_personal_data no_hardcoded_identity; do
  want "$a" && MEASURE_TREE=1
done
if [[ $MEASURE_TREE -eq 1 ]]; then
  A1_NOW="$(a1_count_in "$CLONE")"
  A2_HITS="$(a2_hits_in "$CLONE")"
  A2_NOW=0; [[ -n "$A2_HITS" ]] && A2_NOW="$(printf '%s\n' "$A2_HITS" | wc -l | tr -d ' ')"
  A3_NOW="$(a3_count_in "$CLONE")"
  if [[ -z "$A1_NOW" || -z "$A3_NOW" ]]; then
    echo "check_fresh_clone: a delegate checker did not report a number; refusing to guess" >&2
    exit 2
  fi
fi

# --- review mode: the same measurements, at the base ref too ---------------
DELTA=0
A1_BASE=""; A2_BASE=""; A3_BASE=""
if [[ -n "$SINCE" ]]; then
  if [[ $MEASURE_TREE -eq 1 ]]; then
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
  fi
  DELTA=1
fi

# --- verdicts --------------------------------------------------------------
# Four words, and PASS is one of them. A count that is above zero but not above
# the base is REPORTED and does not gate in review mode; it is somebody's
# problem, just not this run's, and it must not go quiet.
#
#   PASS     the assertion holds
#   FAIL     it does not hold, and nobody said so in advance
#   NOT YET  it does not hold, it is pinned, and the pin says why. NEVER a pass:
#            in absolute mode it still fails the run. The pin's only effect is
#            that a review does not reject a worker for debt it inherited.
#   SKIP     it could not be measured here. Exit 3, never folded into PASS.
FAILED=0
SKIPPED=0
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
say "TREE ASSERTIONS -- what git handed over"
if want no_app_source; then
  verdict "no_app_source        " "$A1_NOW" "$A1_BASE" "tracked files that ARE app source"
fi
if want no_personal_data; then
  verdict "no_personal_data     " "$A2_NOW" "$A2_BASE" "tracked personal-data path(s)"
fi
if want no_hardcoded_identity; then
  verdict "no_hardcoded_identity" "$A3_NOW" "$A3_BASE" "violation categor(y/ies), docs included"
fi

if [[ $AS_JSON -eq 0 ]] && want no_personal_data && [[ "$A2_NOW" -gt 0 ]]; then
  say "        the paths:"
  printf '%s\n' "$A2_HITS" | head -25 | sed 's/^/          /'
  [[ "$A2_NOW" -gt 25 ]] && say "          ... and $((A2_NOW - 25)) more"
fi
if [[ $AS_JSON -eq 0 ]] && want no_hardcoded_identity && [[ -n "$A3_NOW" && "$A3_NOW" -gt 0 ]]; then
  a3_detail_in "$CLONE" | sed 's/^/        /'
fi

# --- A4 and A5, the build and the boot -------------------------------------
# These two are the only assertions that answer "does what git handed over
# WORK". Everything above counts files; only these run the thing.
A4_VERDICT="not run"
A5_VERDICT="not run"

# `judge` turns one binary assertion's raw status into a printed verdict, and it
# is where the pin is honoured. Three properties from the design, section 5:
# a pin never prints PASS; a pin whose assertion started passing is STALE and
# fails; a pin whose assertion failed for some OTHER reason is a MISMATCH and
# fails, because "this area may fail for any reason" is a mute button.
JUDGE_VERDICT=""
judge() { # $1 = assertion name, $2 = status, $3 = note, $4 = log path
  # SETS $JUDGE_VERDICT rather than echoing it. It also sets FAILED, SKIPPED and
  # INHERITED, and a command substitution would run it in a subshell and discard
  # all three -- a gate that quietly cannot fail, which is the exact defect this
  # file exists to prevent.
  local name="$1" status="$2" note="$3" log="$4"
  local pin_because pin_blocked pin_sig pin_date
  pin_because="$(pin_get "$name" because)"
  pin_date="$(pin_get "$name" pinned)"
  pin_blocked="$(pin_get "$name" blocked_on)"
  pin_sig="$(pin_get "$name" signature)"

  if [[ "$status" == "skip" ]]; then
    say "  SKIP  $name"
    say "        $note"
    say "        NOT A PASS. This run does not know the answer."
    SKIPPED=1
    JUDGE_VERDICT="skip"
    return
  fi
  if [[ "$status" == "pass" ]]; then
    if [[ -n "$pin_because" ]]; then
      say "  FAIL  $name  PIN STALE"
      say "        the assertion now HOLDS but is still pinned as an expected failure"
      say "        (pinned $pin_date). Delete the entry from scripts/fresh_clone_baseline.json"
      say "        and let it gate; a pin that outlives its problem is a mute button."
      FAILED=1
      JUDGE_VERDICT="pin_stale"
      return
    fi
    say "  PASS  $name"
    say "        $note"
    JUDGE_VERDICT="pass"
    return
  fi
  # fail or blocked
  if [[ -n "$pin_because" ]]; then
    if [[ -n "$pin_sig" ]] && [[ -n "$log" && -f "$log" ]] \
       && ! grep -qE "$pin_sig" "$log" && [[ "$note" != *"$pin_sig"* ]]; then
      say "  FAIL  $name  PIN MISMATCH"
      say "        pinned for: $pin_because"
      say "        expected signature: $pin_sig"
      say "        what happened:      $note"
      say "        A pin covers one named failure, not any failure in the area."
      FAILED=1
      JUDGE_VERDICT="pin_mismatch"
      return
    fi
    say "  NOT YET  $name  pinned expected failure ($pin_date). THIS IS NOT A PASS."
    say "        because:    $pin_because"
    [[ -n "$pin_blocked" ]] && say "        blocked on: $pin_blocked"
    say "        observed:   $note"
    [[ -n "$log" ]] && say "        log:        $log"
    # A pin buys exactly one thing: a review does not reject a worker for debt
    # it inherited. It does not buy a green absolute run.
    if [[ $DELTA -eq 1 ]]; then
      INHERITED+=("$name: pinned expected failure, not caused by this change")
    else
      FAILED=1
    fi
    JUDGE_VERDICT="not_yet"
    return
  fi
  say "  FAIL  $name"
  say "        $note"
  [[ -n "$log" ]] && say "        log: $log"
  FAILED=1
  JUDGE_VERDICT="fail"
}

RUN_BUILD=0
want build_with_zero_apps && RUN_BUILD=1
want shell_boots_with_empty_catalog && RUN_BUILD=1

if [[ $RUN_BUILD -eq 1 ]]; then
  say ""
  say "BUILD AND BOOT ASSERTIONS -- what git handed over, actually run"

  # In REVIEW MODE a pinned assertion is not re-executed. The pin file IS the
  # recorded base state: it is committed, it says the assertion did not hold as
  # of a date, and check_ratchets_not_loosened.py rejects growing it inside a
  # review. So a diff cannot regress a pinned assertion without first deleting
  # a pin, which is a visible edit. That is what keeps a per-diff review at a
  # tenth of a second instead of a minute -- and the cost arrives on the day
  # the pin is deleted, which is the day this starts being able to catch
  # something. Absolute mode always runs them, because a pin that has silently
  # started passing is itself a failure and only running it can find that out.
  SKIP_PINNED_RUN=0
  if [[ $DELTA -eq 1 ]]; then
    if [[ -n "$(pin_get build_with_zero_apps because)" ]]; then SKIP_PINNED_RUN=1; fi
  fi

  if [[ $SKIP_PINNED_RUN -eq 1 ]]; then
    for name in build_with_zero_apps shell_boots_with_empty_catalog; do
      because="$(pin_get "$name" because)"
      if [[ -n "$because" ]]; then
        say "  NOT YET  $name  pinned; not re-run in review mode. THIS IS NOT A PASS."
        say "        because: $because"
        INHERITED+=("$name: pinned expected failure, not caused by this change")
        [[ "$name" == "build_with_zero_apps" ]] && A4_VERDICT="not_yet" || A5_VERDICT="not_yet"
      else
        say "  ....  $name  not run in review mode: its prerequisite is pinned."
        [[ "$name" == "build_with_zero_apps" ]] && A4_VERDICT="not run" || A5_VERDICT="not run"
      fi
    done
  else
    a4_run "$CLONE"
    judge build_with_zero_apps "$A4_STATUS" "$A4_NOTE" "$A4_LOG"
    A4_VERDICT="$JUDGE_VERDICT"
    if want shell_boots_with_empty_catalog; then
      a5_run "$CLONE"
      judge shell_boots_with_empty_catalog "$A5_STATUS" "$A5_NOTE" "$A5_LOG"
      A5_VERDICT="$JUDGE_VERDICT"
    fi
  fi
fi

say ""
if [[ ${#INHERITED[@]} -gt 0 ]]; then
  say "  FYI ${#INHERITED[@]} assertion(s) carry findings this change did not add (not gating):"
  for i in "${INHERITED[@]}"; do say "      $i"; done
fi

# The claim "app separation is done" has exactly one code path: every assertion
# ran, every assertion passed, and the pin file is empty. It is unreachable
# while any pin exists, which is what makes the pin file -- rather than a
# document, a status line or an agent's summary -- the thing that decides
# whether the claim may be made.
PIN_COUNT="$(pin_keys | grep -c . || true)"
if [[ $FAILED -eq 0 && $SKIPPED -eq 0 && -z "$ONLY" && $DELTA -eq 0 \
      && "${A1_NOW:-1}" -eq 0 && "$A2_NOW" -eq 0 && "${A3_NOW:-1}" -eq 0 \
      && "$A4_VERDICT" == "pass" && "$A5_VERDICT" == "pass" && "$PIN_COUNT" -eq 0 ]]; then
  say "fresh-clone: APP SEPARATION IS DONE. A clone of $HEAD_SHA carries no app source,"
  say "             no personal data and no operator identity, it builds with the app"
  say "             source removed, and it boots with an empty catalog."
elif [[ $FAILED -eq 0 && $SKIPPED -eq 1 ]]; then
  say "fresh-clone: INCOMPLETE. Nothing failed, but an assertion could not be measured"
  say "             here, so this run does not know what a stranger receives."
elif [[ $FAILED -eq 0 ]]; then
  say "fresh-clone: nothing this change touched got worse. NOT 'app separation is done' --"
  say "             run with no arguments for the absolute answer."
else
  say "fresh-clone: NOT clean. A stranger cloning this repository receives the above."
fi

if [[ $KEEP -eq 1 ]]; then say "  clone kept at: $CLONE"; fi

if [[ $AS_JSON -eq 1 ]]; then
  python3 - "${A1_NOW:-null}" "$A2_NOW" "${A3_NOW:-null}" "$A1_BASE" "$A2_BASE" "$A3_BASE" \
            "$DELTA" "$SINCE" "$FAILED" "$SKIPPED" "$A4_VERDICT" "$A5_VERDICT" "$PIN_COUNT" <<'PYEOF'
import json, sys
(a1, a2, a3, b1, b2, b3, delta, since, failed, skipped,
 a4v, a5v, pins) = sys.argv[1:14]


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


out = {
    "fresh_clone": "fail" if failed == "1" else ("incomplete" if skipped == "1" else "ok"),
    "tree": {
        "no_app_source": num(a1),
        "no_personal_data": num(a2),
        "no_hardcoded_identity": num(a3),
    },
    "run": {
        "build_with_zero_apps": a4v,
        "shell_boots_with_empty_catalog": a5v,
    },
    "pinned_expected_failures": num(pins) or 0,
    # One code path, and it is the same one the human summary uses.
    "separation_done": (
        failed == "0" and skipped == "0" and delta == "0"
        and num(a1) == 0 and num(a2) == 0 and num(a3) == 0
        and a4v == "pass" and a5v == "pass" and (num(pins) or 0) == 0
    ),
}
if delta == "1":
    out["since_ref"] = since
    out["base"] = {"no_app_source": num(b1), "no_personal_data": num(b2),
                   "no_hardcoded_identity": num(b3)}
print(json.dumps(out, indent=2))
PYEOF
fi

# A real failure outranks a skip: if something is known broken, say that rather
# than "could not measure".
if [[ $FAILED -ne 0 ]]; then exit 1; fi
if [[ $SKIPPED -ne 0 ]]; then exit 3; fi
exit 0
