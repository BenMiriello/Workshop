#!/usr/bin/env bash
# check_no_hardcoding.sh — the tracked tree must not identify a machine or a person.
#
# The platform is meant to be deployable by anyone. That property is invisible
# from inside a working install: every hardcoded path and hostname keeps working
# perfectly for the one operator who has that path and that hostname, and the
# breakage only shows up in a clone nobody has made yet. So it has to be checked
# mechanically.
#
# THE TRICK, AND THE POINT: this script contains no names. It asks the MACHINE
# who it is (login, home directory, git identity, ~/.config/workshop/instance.env)
# and then greps the tracked tree for those answers. A hit means source is
# carrying something that belongs to this deployment. The same script is exactly
# as strict on a fork, without editing a line — unlike the check it replaces,
# which grepped for one operator's literal name and was therefore both a guard
# and a violation.
#
# DELTA MODE (`--since <ref>`), added 2026-08-23, and why it does not weaken the
# rule. Everything above describes measuring the WHOLE tracked tree and failing
# on ANY hit, and that is still exactly what a bare run does — a human, or a
# release check, still sees the real absolute debt. But used that way as
# `scripts/reviewer.sh`'s completion gate it judged a worker on the absolute
# value of debt the worker INHERITED. This check is UNCONDITIONAL in reviewer.sh
# (on purpose: an identity can land in any file, and it looks fine from inside
# the install that has it), so on 2026-08-23 it was rejecting EVERY run in this
# tree over 25 references — 19 operator-name hits across two backend test files,
# 3 login-name hits in one frontend test, 3 more from instance.env — none of
# them written by the run being reviewed, most of them older than the check.
# Each worker collected a rejection it could not act on, retried, hit the
# no-progress thrash guard in `scripts/ralph.sh` and exited 6. 2,066 loop jobs
# since 2026-07-18 all reported `tasks_done: 0`.
#
# So with `--since <ref>` the script counts hits at `<ref>` (a `git archive`
# extract of the base tree, via scripts/_ratchet_delta.py — the SAME extraction
# the two Python ratchets use, deliberately not a second one) AND on the working
# tree, and fails only on a category that got WORSE between those two points.
# It still scans EVERY tracked file on both sides and still looks for exactly
# the same things: delta mode changes WHAT IS COMPARED, not what is measured.
# That is why it is `--since` and not `--only-changed` — moving an operator's
# name into a file this diff never touched must still be caught.
#
# THE NEEDLES ARE ASKED ONCE, of the machine, and used for BOTH sides. They
# cannot be re-derived per tree: they come from `id -un`, $HOME, git config and
# an UNTRACKED instance.env, none of which a `git archive` extract contains.
# Re-deriving them inside the extract would find fewer needles there, undercount
# the base, and report a regression that is really just the needle list
# shrinking underneath the scan. Same reason `check_no_app_names.py` shares one
# roster across both of its scans.
#
# TRACKED FILES ONLY, ON BOTH SIDES. `git grep` without `--untracked` is what
# makes the two scans comparable: a `git archive` extract cannot contain an
# untracked file, so counting them on the working-tree side would read every
# scratch file in the tree as a brand-new regression. The rule is about what the
# repo CARRIES, and a worker commits its work before review (CLAUDE.md), so the
# gate still sees it.
#
# Inherited references are still REPORTED in delta mode, never hidden — they are
# somebody's problem, just not this run's.
#
#   scripts/check_no_hardcoding.sh              # code + config (default, ABSOLUTE)
#   scripts/check_no_hardcoding.sh --all        # include docs/ and *.md
#   scripts/check_no_hardcoding.sh --json       # machine-readable
#   scripts/check_no_hardcoding.sh --since REV  # gate mode: fail only on THIS run's regression
#
# Exit 0 = clean, 1 = violation(s), 2 = the --since ref could not be read.

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$WORKSHOP" || { echo "check_no_hardcoding: workshop unreachable"; exit 1; }

INCLUDE_DOCS=0
AS_JSON=0
SINCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)     INCLUDE_DOCS=1 ;;
    --json)    AS_JSON=1 ;;
    --since)   SINCE="${2:-}"; shift ;;
    --since=*) SINCE="${1#--since=}" ;;
  esac
  shift
done

# This file names the things it looks for, and instance.env is untracked; both
# would otherwise be permanent self-hits.
EXCLUDES=(
  ":!scripts/check_no_hardcoding.sh"
  ":!scratch/**" ":!tmp/**"
  # .gitignore names the instance paths it excludes; that is its job.
  ":!.gitignore"
  # Vendored/third-party text: a dependency's own metadata is not our source.
  ":!**/package-lock.json" ":!**/*.lock" ":!**/yarn.lock"
)
if [[ $INCLUDE_DOCS -eq 0 ]]; then
  EXCLUDES+=(":!docs/**" ":!*.md")
fi

# --- who this machine is, asked rather than assumed --------------------------
# Computed ONCE, before any scan, and used for BOTH sides of a delta — see the
# header for why an extract cannot answer these questions about itself.
NEEDLES=()
add_needle() { [[ -n "${1:-}" && ${#1} -ge 4 ]] && NEEDLES+=("$1"); }

add_needle "$(id -un 2>/dev/null)"
add_needle "$(basename "$HOME")"
add_needle "$(git config user.name 2>/dev/null)"
EMAIL="$(git config user.email 2>/dev/null)"
add_needle "$EMAIL"
add_needle "${EMAIL%%@*}"

INSTANCE_ENV="${WORKSHOP_CONFIG_DIR:-$HOME/.config/workshop}/instance.env"
if [[ -f "$INSTANCE_ENV" ]]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    # Paths are covered by the home-path probe; a bare value here is an identity.
    case "$v" in /*) continue ;; esac
    v="${v#https://}"; v="${v#http://}"; v="${v%%/*}"
    IFS=',' read -ra parts <<< "$v"
    for part in "${parts[@]}"; do add_needle "${part// /}"; done
  done < "$INSTANCE_ENV"
fi

# De-duplicate.
if [[ ${#NEEDLES[@]} -gt 0 ]]; then
  mapfile -t NEEDLES < <(printf '%s\n' "${NEEDLES[@]}" | sort -u)
fi

# --- the four probes, over any tree ------------------------------------------
# ONE implementation, run against the working tree and (in delta mode) against
# the base extract. A second copy of "what counts as a hit" is how the two sides
# of a delta stop agreeing about what they are measuring.
#
# The extract has no .git, so it is searched with `git grep --no-index`, which
# takes the same pathspecs and the same regex engine as the tracked-tree scan.
# `--no-exclude-standard` keeps the two symmetric: an archive holds exactly the
# tracked files, so nothing in it should be skipped for matching a .gitignore.
_grep_in() { # $1=dir, rest = git-grep args
  local dir="$1"; shift
  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" grep "$@" -- . "${EXCLUDES[@]}" 2>/dev/null
  else
    git -C "$dir" grep --no-index --no-exclude-standard "$@" -- . "${EXCLUDES[@]}" 2>/dev/null
  fi
}

# Any of these is a path that exists on exactly one machine.
probe_homes() {
  _grep_in "$1" -nIE '(/home/[a-z_][a-z0-9_-]*|/Users/[A-Za-z][A-Za-z0-9_-]*)/' \
    | grep -v '@WORKSHOP_ROOT@' || true
}
# Every needle, concatenated: one category, one count. Which needle matched is
# in the printed lines; the gate only needs to know the category got worse.
probe_identity() {
  local dir="$1" needle
  for needle in "${NEEDLES[@]}"; do
    _grep_in "$dir" -nIF -e "$needle" || true
  done
}
probe_tailnet() { _grep_in "$1" -nIE '[a-z0-9-]+\.ts\.net' || true; }
# example/noreply/localhost addresses are placeholders and are meant to be here.
probe_mail() {
  _grep_in "$1" -nIE '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b' \
    | grep -viE '@(example|test|localhost|invalid)\.|\.local\b|noreply@|@(sentry|schemas?)\.' || true
}

count_lines() { local s="$1"; [[ -z "$s" ]] && { echo 0; return; }; printf '%s\n' "$s" | wc -l; }

CATS=(homes identity tailnet mail)
declare -A HITS NOW BASE
DESC_homes='absolute home path(s) in tracked source. Derive the root instead (backend: services/paths.py; shell: $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)).'
DESC_identity="reference(s) to this deployment's own identity in tracked source. It belongs in ~/.config/workshop/instance.env, read at runtime."
DESC_tailnet='tailnet hostname(s) in tracked source. Serve WORKSHOP_LOCAL_ORIGIN from instance.env instead.'
DESC_mail='email address(es) in tracked source. Use an env var, or a placeholder domain in docs.'

scan_tree() { # $1=dir  $2=NOW|BASE
  local dir="$1" target="$2" cat out
  for cat in "${CATS[@]}"; do
    out="$(probe_"$cat" "$dir")"
    if [[ "$target" == "NOW" ]]; then
      HITS[$cat]="$out"; NOW[$cat]=$(count_lines "$out")
    else
      BASE[$cat]=$(count_lines "$out")
    fi
  done
}

scan_tree "$WORKSHOP" NOW

# --- delta mode --------------------------------------------------------------
DELTA=0
if [[ -n "$SINCE" ]]; then
  BASE_DIR="$(mktemp -d -t hardcoding-base-XXXXXX)"
  # shellcheck disable=SC2064
  trap "rm -rf '$BASE_DIR'" EXIT
  # An unresolvable ref must never be mistaken for a clean base: an empty
  # extract would make every category look like a brand-new regression.
  if ! python3 "$WORKSHOP/scripts/_ratchet_delta.py" \
        --ref "$SINCE" --into "$BASE_DIR" --repo "$WORKSHOP"; then
    echo "check_no_hardcoding: cannot read ref '$SINCE'" >&2
    exit 2
  fi
  scan_tree "$BASE_DIR" BASE
  DELTA=1
fi

# --- judge -------------------------------------------------------------------
# ABSOLUTE mode: any hit at all is a violation. DELTA mode: only a category THIS
# RUN made worse. Inherited hits are still printed, just not gated on.
VIOL=()
INHERITED=()
for cat in "${CATS[@]}"; do
  n=${NOW[$cat]}
  desc_var="DESC_$cat"
  if [[ $DELTA -eq 1 ]]; then
    b=${BASE[$cat]}
    if (( n > b )); then
      VIOL+=("$((n - b)) NEW ${!desc_var} (was $b at $SINCE, now $n)")
    elif (( n > 0 )); then
      INHERITED+=("$cat: $n reference(s), $b of them already present at $SINCE")
    fi
  elif (( n > 0 )); then
    VIOL+=("$n ${!desc_var}")
  fi
done

# The offending lines themselves, for the categories that actually failed.
if [[ ${#VIOL[@]} -gt 0 && $AS_JSON -eq 0 ]]; then
  for cat in "${CATS[@]}"; do
    [[ -n "${HITS[$cat]}" ]] || continue
    if [[ $DELTA -eq 1 ]] && (( ${NOW[$cat]} <= ${BASE[$cat]} )); then continue; fi
    printf '%s\n' "${HITS[$cat]}" | head -20 | sed 's/^/    /'
  done
fi

# --- report ------------------------------------------------------------------
if [[ $AS_JSON -eq 1 ]]; then
  python3 - "$DELTA" "$SINCE" "${#VIOL[@]}" "${VIOL[@]:-}" <<'PYEOF'
import json, sys
delta, since, nviol = sys.argv[1], sys.argv[2], int(sys.argv[3])
viol = [v for v in sys.argv[4:4 + nviol] if v]
payload = {"hardcoding": "violation" if viol else "ok", "violations": viol}
if delta == "1":
    payload["since_ref"] = since
print(json.dumps(payload))
PYEOF
else
  if [[ ${#VIOL[@]} -eq 0 ]]; then
    if [[ $DELTA -eq 1 ]]; then
      echo "no-hardcoding: clean vs $SINCE (${#NEEDLES[@]} identity needles checked; this change added none)"
    else
      echo "no-hardcoding: clean (${#NEEDLES[@]} identity needles checked)"
    fi
  else
    echo
    for v in "${VIOL[@]}"; do echo "no-hardcoding: $v"; done
  fi
  if [[ ${#INHERITED[@]} -gt 0 ]]; then
    echo "  FYI ${#INHERITED[@]} category/categories carry references this change did not add (not gating):"
    for i in "${INHERITED[@]}"; do echo "      $i"; done
  fi
fi

[[ ${#VIOL[@]} -eq 0 ]] || exit 1
