# Apps are pluggable — never hardcoded into the platform

Workshop is a **container**. The platform ships the shell — agents, projects,
worktrees, chat, voice, auth, the launcher, the marketplace, the app Runner, and
the per-user primitives (location ingest is an app; `chat`/`voice`/`push` are
shell) that apps build on. Apps are *installed content*, not code welded into the
platform. **A fresh clone starts with an empty app roster and none of any
operator's data.**

This was violated on `main`: seven personal apps were compiled straight into the
platform (hardcoded router mounts, a hardcoded launcher card array, a hardcoded
MCP spec) and one operator's real data (home/work addresses, projects, agent
sessions) was tracked in git — so a second clone inherited both. See the incident
in `docs/handoff-pluggable-apps-migration.md`. This document describes the seam
that fixes it and keeps it from recurring.

## The rule

**Adding or removing an app must not require editing any platform-core file.**
None of these is ever touched to install or uninstall an app:

- `backend/main.py` (router mounts, startup loops)
- `backend/services/llm_router.py` (MCP server specs)
- `frontend/src/pages/Apps.tsx` (launcher cards)
- `backend/apps.json` / `backend/services/manifests.py` `SEED` (ship empty)

If installing an app makes you want to edit one of these, that is the bug.

## The two registries

Workshop keeps a **catalog** of built-in apps (code, tracked) and a per-instance
**install list** (state, gitignored). They are different things:

1. **`backend/services/app_registry.py`** — the built-in catalog. Each app is one
   `AppSpec` declaring everything the platform needs to plug it in:
   - `card` — the launcher card (title, tagline, icon, route) or `None` (headless).
   - `identity` — the identity-manifest fields (sensitivity, `api_prefixes`, caps).
   - `mounts` — the FastAPI routers + their auth (a `RouterMount` per router).
   - `mcp` — MCP servers the app registers for chat tool-use (e.g. `plaid`).
   - `on_startup` / `background` — lifespan hooks + loops (finance prewarm/
     categorize, birds env-refresh/atlas), started only when the app is installed.

   This is the platform's built-in *library*. Being in the catalog does **not**
   install an app.

2. **`.runtime/installed-apps.json`** (`services/installed_apps.py`) — the install
   list, this instance's state, gitignored. It is the source of truth for which
   apps are actually wired up:

   ```json
   { "installed": ["finance", "budget", "birds", "location", "commute", "health", "wearables"] }
   ```

   A missing/empty file means nothing is installed — the correct state for a fresh
   container. `WORKSHOP_INSTALLED_APPS` overrides the path.

## How wiring is driven

At startup `main.py` iterates `app_registry.installed_specs()` (catalog ∩ install
list) and mounts each app's routers with its declared auth; a fresh instance
installs nothing and mounts no app routes, only the shell. The lifespan starts
each installed app's `on_startup` hooks and `background` loops the same way.

- **Launcher** — `GET /base-apps/launcher` returns the installed apps' cards, in
  install order. `frontend/src/pages/Apps.tsx` renders one card per entry and
  shows its empty state when the list is empty. No app set is hardcoded in the
  frontend.
- **MCP** — `llm_router` reads `app_registry.installed_mcp_servers()`; the `plaid`
  server is available to chat tool-use only when `finance`/`budget` is installed.
- **Identity manifests** — `app_registry.ensure_identity_manifests()` seeds an
  `apps.json` entry for each installed app from the catalog on startup (preserving
  operator `ui` overrides such as the assistant toggle). `apps.json` is derived
  instance state, gitignored.

Because launcher, MCP, routes, and manifests all derive from the install list, an
installed app appears everywhere automatically and a removed one disappears — with
no shell edit.

## Installing / removing an app

- **Install**: add its id to `.runtime/installed-apps.json` (or call
  `installed_apps.install(id)`), then restart the backend. Its routes, launcher
  card, MCP, and loops come up; its identity manifest is seeded from the catalog.
- **Remove**: drop the id (or `installed_apps.remove(id)`) and restart.
- **A brand-new app** (not in the built-in catalog): build it as a Project with a
  `workshop.app.json` runtime manifest and deploy it through the Runner
  (`/base-apps/deploy/<slug>/…`), served from its own proxied origin. Register its
  identity manifest so discovery sees it. See `docs/app-platform-runner.md` and
  `docs/app-platform-plan.md`. No step edits a platform-core file.

## Per-instance data (never tracked)

Instance state lives under `.runtime/` (gitignored) so a clone inherits none of
it. De-hardcoded from `/home/clawd-a/...` to home-relative defaults, overridable:

| Data | Location | Override |
|---|---|---|
| Install list | `.runtime/installed-apps.json` | `WORKSHOP_INSTALLED_APPS` |
| Identity manifests | `backend/apps.json` (gitignored, reseeded) | `WORKSHOP_APPS_MANIFEST` |
| Projects registry | `.runtime/projects.json` | `WORKSHOP_PROJECTS_REGISTRY` |
| Build-agents registry | `.runtime/build_agents.json` | `WORKSHOP_BUILD_AGENTS_JSON` |
| Commute config (addresses, sources) | `.runtime/commute-config/` | `WORKSHOP_COMMUTE_ROOT` |
| Commute notes | `.runtime/commute-notes/` | `WORKSHOP_COMMUTE_NOTES_DIR` |

The tracked `commute/` dir holds only generic structural docs (`README.md`) and
an empty `state/` dir — no real addresses.

> **History note.** Untracking stops future clones from receiving this data, but
> the pre-migration commits still contain it. Scrubbing git history on `main` is a
> separate, deliberate operation (force-push, coordinate first) and is out of this
> migration's scope; it is noted in the incident writeup.

## Bootstrapping a fresh instance

1. Clone the repo (no `.runtime/`, no secrets). The backend boots with an **empty**
   app roster — the launcher shows "No apps installed."
2. Register the operator account (login wall) — see
   `project_workshop_product_auth`.
3. Install the built-in apps you want: create `.runtime/installed-apps.json` with
   their ids and restart. Supply each app's config under `.runtime/` (e.g. link
   banks for finance, add `commute-config/` for commute) and any secrets in
   `~/.config/workshop/secrets.env`.
4. Or build your own app as a Project + deployment and register it.

## The one deferred seam

Running an app **integrated in-shell** — its React pages mounted inside the
Workshop SPA at `/apps/<id>` rather than served from a proxied origin — still
needs dynamic frontend route/bundle injection (`docs/workshop-v1/06-…`). The
built-in apps keep their route components compiled into the SPA for now; what is
manifest-driven is their **visibility and backend wiring** (launcher card, routes,
MCP, loops, data), which is the security-critical part. A not-installed app has no
card and no backend, so its compiled-in route is unreachable and leaks nothing.
Fully dynamic route injection is intentionally left for later rather than
special-cased per app.
