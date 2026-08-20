<!-- author: self; chat: 2c44eaf71ef14a3f83c96d119787ba9f -->
Measured 2026-08-20. Five slugs (budget, birds, location, commute, notes-app) exist as BOTH an app and a project with nothing linking them: `routes/projects.py` has zero references to `app_registry`, and `app_registry.py` has zero references to `project_slug`. `CreateProjectIn` has no `app` field and there is no adopt route. The shared slug is a coincidence.

The consequence is a clean split of what an app needs:
- Apps got: launcher card, API prefix, chat persona, MCP grants, manifest.
- Projects got: repo, working tree, tickets, runs, deploys, agents, autonomy.
- The actual app code got: neither. It is in the platform monorepo.

So the dock lists apps, which is the half with no collections behind it. `chat_work._QUEUELESS_PREFIXES = ("app:",)` and `tasks_all._scope_roots()` never enumerates apps; runs and deploys are keyed by `project_slug`; `agents_registry` has no `app_id`; chats have no scope-filtered endpoint at all (only exact `context_key`, which is why `pages/Memory.tsx` filters client-side and says so in a comment).

And because the app code lives in platform source (25,600 tracked lines plus 42 backend modules named after his apps), any agent grepping the repo for an example finds birds.

Both symptoms have the same cause. Fixing either separately is wasted work. Model of record and the six-stage migration: `docs/platform-redesign/app-is-the-entity-2026-08-20.md`.

Also worth knowing: the database is already clean (zero app-specific tables/columns across all 17 migrations) and `main.py` names no app. The rot is in two registries and about a dozen special cases, not in the schema.