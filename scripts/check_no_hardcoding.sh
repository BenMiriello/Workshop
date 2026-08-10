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
#   scripts/check_no_hardcoding.sh            # code + config (default)
#   scripts/check_no_hardcoding.sh --all      # include docs/ and *.md
#   scripts/check_no_hardcoding.sh --json     # machine-readable, for reviewer.sh
#
# Exit 0 = clean, 1 = violation(s).

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$WORKSHOP" || { echo "check_no_hardcoding: workshop unreachable"; exit 1; }

INCLUDE_DOCS=0
AS_JSON=0
for arg in "$@"; do
  case "$arg" in
    --all)  INCLUDE_DOCS=1 ;;
    --json) AS_JSON=1 ;;
  esac
done

# This file names the things it looks for, and instance.env is untracked; both
# would otherwise be permanent self-hits.
EXCLUDES=(
  ":!scripts/check_no_hardcoding.sh"
  ":!scratch/**" ":!tmp/**"
  # Vendored/third-party text: a dependency's own metadata is not our source.
  ":!**/package-lock.json" ":!**/*.lock" ":!**/yarn.lock"
)
if [[ $INCLUDE_DOCS -eq 0 ]]; then
  EXCLUDES+=(":!docs/**" ":!*.md")
fi

VIOL=()
note() { VIOL+=("$1"); }

# --- 1. absolute home paths -------------------------------------------------
# Any of these is a path that exists on exactly one machine.
HOMES=$(git grep -nIE '(/home/[a-z_][a-z0-9_-]*|/Users/[A-Za-z][A-Za-z0-9_-]*)/' \
        -- . "${EXCLUDES[@]}" 2>/dev/null | grep -v '@WORKSHOP_ROOT@' || true)
if [[ -n "$HOMES" ]]; then
  n=$(printf '%s\n' "$HOMES" | wc -l)
  note "$n absolute home path(s) in tracked source. Derive the root instead (backend: services/paths.py; shell: \$(cd \"\$(dirname \"\${BASH_SOURCE[0]}\")/..\" && pwd))."
  printf '%s\n' "$HOMES" | head -20 | sed 's/^/    /'
fi

# --- 2. who this machine is, asked rather than assumed -----------------------
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
    # Paths are covered by check 1; a bare value here is an identity.
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

for needle in "${NEEDLES[@]}"; do
  HITS=$(git grep -nIF -- "$needle" -- . "${EXCLUDES[@]}" 2>/dev/null || true)
  if [[ -n "$HITS" ]]; then
    n=$(printf '%s\n' "$HITS" | wc -l)
    note "$n reference(s) to this deployment's own identity in tracked source. It belongs in ~/.config/workshop/instance.env, read at runtime."
    printf '%s\n' "$HITS" | head -10 | sed 's/^/    /'
  fi
done

# --- 3. one network's names --------------------------------------------------
TS=$(git grep -nIE '[a-z0-9-]+\.ts\.net' -- . "${EXCLUDES[@]}" 2>/dev/null || true)
if [[ -n "$TS" ]]; then
  n=$(printf '%s\n' "$TS" | wc -l)
  note "$n tailnet hostname(s) in tracked source. Serve WORKSHOP_LOCAL_ORIGIN from instance.env instead."
  printf '%s\n' "$TS" | head -10 | sed 's/^/    /'
fi

# --- 4. a real person's address ---------------------------------------------
# example/noreply/localhost addresses are placeholders and are meant to be here.
MAILS=$(git grep -nIE '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b' \
        -- . "${EXCLUDES[@]}" 2>/dev/null \
        | grep -viE '@(example|test|localhost|invalid)\.|\.local\b|noreply@|@(sentry|schemas?)\.' || true)
if [[ -n "$MAILS" ]]; then
  n=$(printf '%s\n' "$MAILS" | wc -l)
  note "$n email address(es) in tracked source. Use an env var, or an @example.com placeholder in docs."
  printf '%s\n' "$MAILS" | head -10 | sed 's/^/    /'
fi

# --- report -----------------------------------------------------------------
if [[ $AS_JSON -eq 1 ]]; then
  if [[ ${#VIOL[@]} -eq 0 ]]; then
    echo '{"hardcoding":"ok","violations":[]}'
  else
    printf '{"hardcoding":"violation","violations":['
    for i in "${!VIOL[@]}"; do
      [[ $i -gt 0 ]] && printf ','
      printf '%s' "${VIOL[$i]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'
    done
    printf ']}\n'
  fi
else
  if [[ ${#VIOL[@]} -eq 0 ]]; then
    echo "no-hardcoding: clean ($((${#NEEDLES[@]})) identity needles checked)"
  else
    echo
    for v in "${VIOL[@]}"; do echo "no-hardcoding: $v"; done
  fi
fi

[[ ${#VIOL[@]} -eq 0 ]] || exit 1
