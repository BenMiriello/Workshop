#!/bin/bash
# check_pluggable_apps.sh — enforce the pluggable-app seam mechanically.
#
# Apps are installed content, not platform code (docs/apps-are-pluggable.md).
# This guard fails the build if a change re-welds an app into a platform-core
# file or re-tracks per-instance data. It is the mechanical backstop for the
# rule "adding/removing an app must not edit any platform-core file."
#
# Runs as a FULL invariant check (not added-lines only): the tree is clean now,
# so any reintroduction is a regression, full stop. Wired into reviewer.sh.
#
# Exit 0 = clean, 1 = violation(s).

set -u
WORKSHOP="${WORKSHOP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$WORKSHOP" || { echo "check_pluggable_apps: workshop unreachable"; exit 1; }

VIOL=()

MAIN="backend/main.py"
LLM="backend/services/llm_router.py"
APPS_TSX="frontend/src/pages/Apps.tsx"

# App route modules — these belong to installed apps and must mount from
# services/app_registry, never from main.py.
APP_MODULES="finance|budget|plaid_webhook|commute|location|owntracks|health|birds|environment"
# App-specific startup loops that were moved into app_registry.
APP_LOOPS="_prewarm_finance_cache|_prewarm_loop|_categorize_loop|_env_refresh_loop|_atlas_rebuild_loop"

# 1) main.py must not import or mount any app route module.
if [[ -f "$MAIN" ]]; then
  if grep -nE "^from routes import .*\b($APP_MODULES)\b" "$MAIN" >/dev/null; then
    VIOL+=("$MAIN imports an app route module (${APP_MODULES//|/, }). Apps mount from services/app_registry; do not import their routers in main.py.")
  fi
  if grep -nE "include_router\(\s*($APP_MODULES)\." "$MAIN" >/dev/null; then
    VIOL+=("$MAIN calls include_router() on an app router. Add the app to services/app_registry BUILTIN_APPS + .runtime/installed-apps.json instead.")
  fi
  if grep -nE "def\s+($APP_LOOPS)\b" "$MAIN" >/dev/null; then
    VIOL+=("$MAIN defines an app-specific startup loop ($APP_LOOPS). These live in services/app_registry as AppSpec.on_startup/background.")
  fi
  # The registry-driven mount loop must still be present.
  if ! grep -q "app_registry.installed_specs()" "$MAIN"; then
    VIOL+=("$MAIN no longer iterates app_registry.installed_specs() — the installed-app mount loop is gone.")
  fi
fi

# 2) llm_router.py must not hardcode MCP server specs; they come from installed apps.
if [[ -f "$LLM" ]]; then
  if grep -nE "^_MCP_SERVER_SPECS\s*[:=]" "$LLM" >/dev/null; then
    VIOL+=("$LLM hardcodes _MCP_SERVER_SPECS. Register MCP servers on the app's AppSpec (app_registry) and read app_registry.installed_mcp_servers().")
  fi
  if ! grep -q "installed_mcp_servers" "$LLM"; then
    VIOL+=("$LLM no longer sources MCP specs from app_registry.installed_mcp_servers().")
  fi
fi

# 3) Apps.tsx must render the manifest-driven launcher, not a hardcoded card set.
if [[ -f "$APPS_TSX" ]]; then
  if ! grep -q "/base-apps/launcher" "$APPS_TSX"; then
    VIOL+=("$APPS_TSX no longer fetches /base-apps/launcher — the launcher must be manifest-driven, not a hardcoded card array.")
  fi
  if grep -nE 'to:\s*"/apps/(finance|budget|birds|location|commute)"' "$APPS_TSX" >/dev/null; then
    VIOL+=("$APPS_TSX hardcodes an app route (to: \"/apps/...\"). Launcher cards come from GET /base-apps/launcher.")
  fi
fi

# 4) Per-instance data / state must stay untracked (a fresh clone inherits none).
FORBIDDEN_TRACKED=(
  "projects.json"
  "build_agents.json"
  "backend/apps.json"
  "commute/schedule.json"
  "commute/legs.json"
  "commute/targets.json"
  "commute/sources/registry.json"
)
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  tracked="$(git ls-files)"
  for f in "${FORBIDDEN_TRACKED[@]}"; do
    if grep -qxF "$f" <<<"$tracked"; then
      VIOL+=("$f is tracked in git — it is per-instance state and must live in .runtime/ (gitignored). See docs/apps-are-pluggable.md.")
    fi
  done
  if grep -qE "^commute/notes/" <<<"$tracked"; then
    VIOL+=("commute/notes/ is tracked — commute notes are per-operator data; they live in .runtime/commute-notes/.")
  fi
fi

if [[ ${#VIOL[@]} -gt 0 ]]; then
  echo "pluggable-apps: VIOLATIONS (see docs/apps-are-pluggable.md)"
  printf '  - %s\n' "${VIOL[@]}"
  exit 1
fi
echo "pluggable-apps: clean"
exit 0
