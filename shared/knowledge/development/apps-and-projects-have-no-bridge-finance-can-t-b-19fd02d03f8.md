Verified 2026-08-04.

**Two disconnected registries.**
- Apps: `backend/apps.json` (manifests) + `.runtime/installed-apps.json` (installed list). `finance` is there: private, api_prefixes `/finance` `/budget` `/plaid`, provides `finance.read`. `budget` is a SEPARATE app that `needs: [finance.read]`.
- Projects: `.runtime/projects.json`, each record pointing at a dir under `projects/`.
- Nothing links them. `CreateProjectIn` (backend/routes/projects.py:124) has no `app` field; there is no promote/adopt route.

**Note:** the `apps/` directory is NOT the app registry (it holds only `budget/knowledge`). Check `backend/apps.json` instead.

**Both project-creation paths refuse existing code.**
- `POST /projects/` (projects.py:351): 409s if the target dir exists, then scaffolds from a template and git-inits.
- `POST /projects/import` (projects.py:917): clones a URL into a NEW dir, also 409s on an existing one.

**Built-in apps live in the monorepo**, so there is nothing for a project to own: finance is `backend/routes/finance.py` (94 lines) + `frontend/src/pages/apps/Finance.tsx` (540 lines).

**Budget is the cautionary case.** A `budget` project was created 2026-06-22: `projects/budget/` has PROJECT.md, TODO.md, its own `.git`, and an EMPTY `src/`. The actual app code remains in the monorepo; its own PROJECT.md records "Whole feature is cross-boundary (workshop monorepo)". The project is a notes/tickets shell that does not own its app's code.

**Implication:** clicking New project for an existing in-tree app produces an empty shell. The fix is an app<->project link (adopt an existing path / carry an `app:` field), not extraction. If only a scope is needed, `app:<name>` is already a valid scope string.