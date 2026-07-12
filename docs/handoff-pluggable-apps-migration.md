# Handoff: migrate hardcoded apps/projects to the pluggable installed-app model

**Status: DONE on `main` (2026-07-12).** Implemented as a built-in app *catalog*
(`backend/services/app_registry.py`) driven by a per-instance *install list*
(`.runtime/installed-apps.json`, `services/installed_apps.py`), keeping all seven
apps working on clawd-a while making a fresh clone boot blank. Design +
per-instance data map: [`apps-are-pluggable.md`](apps-are-pluggable.md). The
original handoff follows for reference.

What was done:
- Router mounts, MCP specs, launcher cards, and app startup loops are all
  registry-driven; `main.py` / `llm_router.py` / `Apps.tsx` no longer hardcode any
  app. Adding/removing an app is an `installed-apps.json` edit + restart.
- Plaid MCP moved from `llm_router._MCP_SERVER_SPECS` into the finance/budget
  AppSpec; available to chat only when that app is installed.
- Personal data untracked and moved to `.runtime/`: `projects.json`,
  `build_agents.json`, `backend/apps.json`, and `commute/{schedule,legs,targets,
  sources}` → `.runtime/commute-config/`, notes → `.runtime/commute-notes/`.
  `commute/README.md` scrubbed of the real name/addresses/channel id. Paths
  de-hardcoded off `/home/clawd-a`.
- Verified: fresh install list → zero app routes/cards/MCP, shell intact;
  clawd-a's roster → all seven apps mount and serve (401-gated) as before.

Remaining (intentionally deferred, see the doc's "one deferred seam"):
integrated in-shell frontend route injection; and git-history scrubbing of the
already-committed personal data (a separate force-push operation).

---

_Original handoff (the flaw and the target model):_

Deferred during the 2026-07-12 upstream merge from `simonsays-local` (queue item
`f963c8e`, tagged "needs migration"). It is a fundamental architecture flaw, not a
cleanup nicety.

## The flaw

The Workshop platform hardcodes one operator's specific apps, projects, and
**real personal data** directly into the platform code and git-tracked files.
The shell is not app-agnostic and there is no supported "fresh/blank instance"
path. Consequences, confirmed by the 2026-07-12 incident
(`simonsays-local:docs/simonsays-local-instance.md`, Incident §2):

- A second operator who clones the repo inherits clawd-a's apps AND clawd-a's
  real data, including **home/work street addresses in plaintext**
  (`commute/schedule.json`), the actual commute route (`commute/legs.json`), and
  the real projects/build-agent registries.
- The platform code cannot host a different app set without editing core files.

## Where the hardcoding lives (inventory on `main`)

Frontend:
- `frontend/src/pages/Apps.tsx` — the launcher is a hardcoded `cards: AppCard[]`
  array (Finance, Budget, Birds, Location, Commute, Open Wearables), not a
  manifest read from installed apps.

Backend:
- `backend/main.py` — hardcoded `app.include_router(...)` for `finance`,
  `budget`, `commute`, `location`, `birds`, `owntracks`, `health` (~lines
  224-245), each with app-specific auth wiring.
- Per-app routers/services baked into the platform tree:
  `routes/{finance,budget,commute,birds,owntracks,health,environment,plaid_webhook}.py`,
  `services/{birds_analyze,birds_forecast,birds_store,budget_engine,budget_store,categorizer,finance_plaid,plaid_store,...}.py`.
- `backend/services/llm_router.py` `_MCP_SERVER_SPECS` — the plaid MCP server is
  hardcoded (main deliberately kept it during the merge; it should become
  app-registered).
- `backend/apps.json` — app registry (partly hardcoded).

Git-tracked personal data / instance state (should not ship in a cloneable platform):
- `projects.json`, `build_agents.json` — clawd-a's real projects + agent sessions.
- `commute/schedule.json` (home/work addresses), `commute/legs.json`,
  `commute/targets.json` — real personal commute data.

## Target model (what to build)

The `simonsays-local` redesign is the reference: a **manifest-driven launcher**
and an **app-agnostic shell**, where apps are *installed content* that register
themselves (routes, launcher card, any MCP servers, auth policy), not code baked
into the platform. See the design doc `simonsays-local:docs/apps-are-pluggable.md`
(it was deleted on `main` and must be restored/adapted), and commits
`simonsays-local` `f963c8e` (redesign) + `97e5e56` (removal of hardcoded lists).

## The critical constraint (why this is NOT a cherry-pick)

On `simonsays-local`, app-agnosticism was achieved by **deleting clawd-a's apps**
(`97e5e56` removed birds/budget/commute/finance/health/owntracks and their
services). That is correct for *that* instance but wrong for `main`: main must
**keep these apps working** while moving them into the installed-app model.

So do NOT `git cherry-pick f963c8e`/`97e5e56` onto main — that strips the apps'
wiring. Instead:

1. Adopt the pluggable mechanism (manifest-driven launcher + app-agnostic shell +
   an app-registration contract: routes, launcher card, MCP servers, auth).
2. **Migrate each existing app** (finance, budget, commute, location, birds,
   owntracks, health) into that model as an installed app, verifying each still
   works on clawd-a after conversion.
3. Move the plaid MCP spec from `llm_router._MCP_SERVER_SPECS` to the finance/
   budget app's registration.
4. **Untrack the personal data / instance state**: `projects.json`,
   `build_agents.json`, `commute/*.json` should be gitignored (or reduced to
   empty templates in-repo, with real state living under `.runtime/`), so a
   fresh clone is a blank slate. Provide a documented "new instance" bootstrap.

## Verification (definition of done)

- A fresh clone (no `.runtime/`, no secrets) boots with an **empty** app roster
  and **no** clawd-a data anywhere (no addresses, no projects).
- clawd-a's own instance still shows and runs all its apps exactly as before
  (finance/budget/commute/location/birds/owntracks/health), data intact.
- Installing/removing an app is a manifest/registration operation, no platform
  code edit.
- `grep` for clawd-a-specific hosts/paths/data in tracked platform code comes
  back clean (they live in env/`.runtime/` only).

## References

- `docs/instances-and-forking.md` (the multi-instance policy).
- `simonsays-local:docs/upstream-queue.md` (f963c8e "needs migration",
  58e60af registry note).
- `simonsays-local:docs/simonsays-local-instance.md` Incident §2 (the personal-
  data-in-git writeup) and §1 (the proxy leak, already fixed on main = a85e901).
- `simonsays-local:docs/apps-are-pluggable.md` (the design).
