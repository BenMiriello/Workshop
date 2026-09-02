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
REGISTRY="backend/services/app_registry.py"
CHATCTX="backend/services/chat_context.py"

# App route modules — these belong to installed apps and must mount from the
# catalog, never from main.py.
#
# Derived from the APPS THEMSELVES, never typed here: a guard with its own
# hardcoded app roster misses exactly the app someone just added, which is the
# only case that matters.
#
# READ FROM THE DECLARATIONS since 2026-09-01. This used to grep the registry
# for `from routes import ...`, which worked only while the registry held one
# hardcoded factory per app. It does not any more -- apps declare themselves in
# `services/<app>_app.py` and the registry discovers them -- so that grep came
# back empty and this guard reported the catalog as unreadable. The names moved;
# the guard follows them. Each declaration states its own route modules in
# `REQUIRES`, and its `from routes import` lines are the fallback.
APP_DECLS=(backend/services/*_app.py)
APP_MODULES="$(
  {
    grep -hoE '^REQUIRES[[:space:]]*=[[:space:]]*\(.*\)' "${APP_DECLS[@]}" 2>/dev/null \
      | sed 's/^REQUIRES[^(]*(//; s/)$//' | tr ',' '\n' | tr -d ' "'"'"''
    grep -hoE '^[[:space:]]*from routes import .*' "${APP_DECLS[@]}" "$REGISTRY" 2>/dev/null \
      | sed 's/.*from routes import //' | tr ',' '\n' | tr -d ' '
  } | grep -E '^[a-z_]+$' | sort -u | paste -sd'|' -
)"
if [[ -z "$APP_MODULES" ]]; then
  VIOL+=("no app declaration (backend/services/*_app.py) or $REGISTRY names any route module — the catalog cannot be read, so this guard cannot tell an app from platform code.")
  APP_MODULES="__none__"
fi
# App-specific startup loops. Historical names included deliberately: this is a
# regression guard, and the thing it guards against is these reappearing in
# platform-core, which is where they were before they moved onto the apps.
APP_LOOPS="_prewarm_finance_cache|_prewarm_cache|_prewarm_loop|_categorize_loop|_env_refresh_loop|_atlas_rebuild_loop"

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

# 1b) The catalog must not HARD-import app route modules at load time. Workshop is
#     a container; an instance may omit an app's module (slimmed/downstream build).
#     A top-level `from routes import <app>` crashes the whole platform when that
#     module is absent (base_apps/main/llm_router all import app_registry). Apps
#     must be built by factories that import their own modules tolerantly.
if [[ -f "$REGISTRY" ]]; then
  # Flag a top-level (column-0) import of any app route module from `routes`.
  if grep -nE "^(from routes import.*\b($APP_MODULES)\b|import routes\.($APP_MODULES)\b)" "$REGISTRY" >/dev/null; then
    VIOL+=("$REGISTRY hard-imports an app route module at load time. Import each app's module inside its factory (see _build_catalog); a missing module must degrade the catalog, not crash it.")
  fi
  if ! grep -q "_build_catalog" "$REGISTRY"; then
    VIOL+=("$REGISTRY no longer builds the catalog tolerantly via _build_catalog() — a slimmed instance (missing an app module) would crash on import.")
  fi
fi

# 1b-ii) Same rule, one level down: a DECLARATION is imported by the discovery
#        scan, so a top-level `from routes import <its own module>` makes a
#        slimmed instance lose the declaration entirely -- including its
#        REQUIRES, which is what tells the catalog to skip it cleanly. The
#        import belongs inside app_spec(), which only ever runs after the
#        module check has passed.
for decl in "${APP_DECLS[@]}"; do
  [[ -f "$decl" ]] || continue
  if grep -nE "^(from routes import|import routes\.)" "$decl" >/dev/null; then
    VIOL+=("$decl imports its route module at load time. Import it inside app_spec(); the discovery scan imports this file on every instance, including ones that do not ship the routes.")
  fi
done

# 1c) chat_context.py is platform chat plumbing — it must not hardcode app
#     personas. App personas live in services/app_personas.py and are registered
#     via each app's AppSpec (chat_provider/chat_snapshot), so a persona appears
#     only when its app is installed. Guard both the anti-pattern (app persona
#     classes back in chat_context) and the invariant (dispatch via provider_map).
if [[ -f "$CHATCTX" ]]; then
  if grep -nE "^class (Budget|Finance|Commute|Location)ContextProvider" "$CHATCTX" >/dev/null; then
    VIOL+=("$CHATCTX defines an app persona class. App personas belong in services/app_personas.py, registered via AppSpec.chat_provider — never hardcoded in chat plumbing.")
  fi
  # The operator's name is checked by scripts/check_no_hardcoding.sh, which
  # asks the machine who it is rather than naming anyone here. What this file
  # can assert is the mechanism: the name must come from operator_name().
  if ! grep -q "operator_name" "$CHATCTX"; then
    VIOL+=("$CHATCTX no longer resolves the operator via operator_name() (env-driven) — a fresh instance would be branded with whoever's name is in source.")
  fi
  if ! grep -q "installed_chat_providers\|provider_map" "$CHATCTX"; then
    VIOL+=("$CHATCTX no longer assembles providers from installed apps (provider_map/installed_chat_providers) — app personas would not be install-gated.")
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
  if grep -nE "to:\s*\"/apps/($APP_MODULES)\"" "$APPS_TSX" >/dev/null; then
    VIOL+=("$APPS_TSX hardcodes an app route (to: \"/apps/...\"). Launcher cards come from GET /base-apps/launcher.")
  fi
fi

# 4) Per-instance data / state must stay untracked (a fresh clone inherits none).
FORBIDDEN_TRACKED=(
  ".tasks.json"
  "TODO.md"
  "backend/store-policies.json"
  "shared/relay-corpus.jsonl"
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
