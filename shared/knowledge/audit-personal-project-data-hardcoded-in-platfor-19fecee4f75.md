Full-tree audit of hardcoded personal, machine, and app-specific values in the Workshop platform.

## Why the gate said clean
`scripts/check_pluggable_apps.sh` prints `pluggable-apps: clean` but inspects only 5 files (main.py, llm_router.py, chat_context.py, app_registry.py, Apps.tsx) against a HARDCODED app list, and greps for the literal string `is the operator`. Everything below passes it. The gate is itself a violation.

## Categories found

**Identity in core**
- backend/services/github.py:34 `OWNER = "<owner>"`, enforced at :163; :37-44 `reject_blocked()` called from tracker_bindings.py:121 and trackers/github.py:41
- backend/services/chat_context.py:262 GIT_WORKTREE_RULE injects "never touch the <blocked-org> org (personal <owner> repos only)" into every builder prompt
- backend/services/birds_analyze.py:502 admin seed default `<operator-email>`
- backend/services/commute/alerter.py:29 real Discord channel snowflake as default
- backend/services/memory_keeper.py:104 `the bridge agent` in core regex; agents_registry.py:545 NATIVE_FOREIGN
- frontend/src/pages/AgentDetail.tsx:202 real Discord snowflake as placeholder; ProjectDetail.tsx:1668 UI copy names <owner>

**Machine specifics**
- ~146 literal `$HOME` paths (60 backend, 60 scripts). Core loop scripts (ralph.sh, project_ralph.sh, reviewer.sh, queue_runner.sh, dispatch_triggers.sh, check_design.sh, cron_agent.sh) have NO env escape hatch. Node binary path pinned to a specific nvm version.
- backend/routes/health.py:39 + routes/owntracks.py:29 return <host>.<tailnet> in API responses
- frontend/src/pages/Settings.tsx:794 displays that host; :890 hardcodes a port map
- frontend/src/ui/Map.tsx:103 default center [-122.08, 37.39] in a core UI primitive
- mobile/native/config.ts:22 DEFAULT_SERVER = workshop.<your-domain>; app.json:11 bundle id; permission strings name Birds/Commute/Location
- infra/systemd/cloudflared.service tracked with real domains + tunnel id

**Core enumerating apps**
routes/events.py:35 KNOWN_SOURCES; services/stores.py:279-301 register_builtin_stores; services/knowledge.py:32 APP_CONTEXT_KEYS; routes/chat.py:56 PROVIDER_MODELS; routes/services.py:33 SERVICES; routes/agents.py:16 KNOWN_TOOLS; llm_router.py:273 strips PLAID_*; workshop_public_app.py:55 allowlists /birds/* and /location/owntracks

**Frontend shell**
App.tsx statically imports 16 app modules + hardcodes all routes; Birds RecorderProvider wraps the app root at App.tsx:97; appNav.tsx:35,318 breadcrumbs + app menu; Apps.tsx:524 switch over five apps; Settings.tsx:704 embeds the health app panel

**App content in platform dirs**
birdnet/ (40 tracked files, 2.2GB on disk, fits neither app contract); backend/springer_app.py + services/springer_auth.py; backend/mcp/plaid/ ("for the bridge agent (the service user tenant)"); backend/store-policies.json (tracked, personal account rules); shared/relay-corpus.jsonl (tracked runtime telemetry); frontend/src/components/PlaidLink.tsx + appSettings/*; ~180 lines of birds/budget CSS in src/styles.css; apps/commute/scripts/tick.sh, apps/commute/scripts/notes.sh (:87 puts the operator's name in a model prompt)

## Mechanisms that already exist for the fix
- AppSpec catalog (backend/services/app_registry.py) + install list (.runtime/installed-apps.json, WORKSHOP_INSTALLED_APPS)
- services/stores.py `register_from_manifests()` already converts manifest provide_types into declared Stores
- services/manifests.py SEED ships empty (correct)
- Correct path idiom already used in services/port_registry.py:56 and scripts/build_workshop_public.sh:17 (derive root from script location, env override)
- Frontend has a launcher manifest (GET /api/base-apps/launcher) that already carries `route`; missing piece is a frontend module registry mapping app id to lazy routes/providers/crumbs/icon

## Known limit
Git history still contains all of it; scrubbing is a separate coordinated force-push, out of scope per docs/apps-are-pluggable.md.