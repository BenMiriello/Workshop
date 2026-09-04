# Workshop

A self-hosted control panel for one person's agents, projects and apps.

Workshop is a **container and a toolkit**, not a bundle of applications. It
ships the shell — agents, chats, tickets, runs, memory, auth, the app launcher
and the runner — and **installs no apps of its own**. A fresh clone boots with
an empty catalog. Apps are separate: each one declares itself, lives in its own
tree, and is installed into an instance rather than committed into this
repository.

## Layout

```
workshop/
├── backend/               # FastAPI: agents, chats, tickets, runs, memory, auth
├── frontend/              # Vite + React + TS: the shell the browser loads
├── packages/ui/           # @workshop/ui — the design-system kit both consume
├── mobile/                # Expo + React Native client
├── shared/                # types shared by web and mobile
├── infra/                 # systemd units, compose files, config templates
├── scripts/               # bring-up, checks and the ratchets that gate a change
└── docs/                  # product documentation and design records
```

An installed app lives at `apps/<slug>/` — untracked here, because an app is not
part of the platform. It carries its own backend package, its own `ui/module.tsx`,
its own tests, and a `workshop.app.json` the platform reads. See
[`docs/apps-are-pluggable.md`](docs/apps-are-pluggable.md) and
[`docs/declaring-an-app-surface.md`](docs/declaring-an-app-surface.md).

## Bring-up

```bash
git clone <repo> workshop && cd workshop
bash scripts/bootstrap.sh      # first run creates config templates, then stops
# fill in ~/.config/workshop/auth.env (CLAUDE_CODE_OAUTH_TOKEN) and instance.env
bash scripts/bootstrap.sh      # second run installs, builds and starts
```

`bash scripts/preflight.sh` checks whether a machine is ready and lists
everything missing at once. It changes nothing.

Full deployment notes, including what each service is, how to update an existing
install, and why this is systemd rather than a container:
**[docs/deploying.md](docs/deploying.md)**.

Two things that catch everyone:

- `npm install` at the **repo root**, never inside `frontend/`. This is an npm
  workspace, and installing in `frontend/` leaves `@workshop/ui` unresolvable.
- `npm run build:ui` before any typecheck. vite reads the kit's sources, tsc
  reads its built `dist`, so a missing `dist` builds fine and fails typecheck.

## The API

The HTTP surface is generated, not listed here: a running instance serves
OpenAPI at `/openapi.json` and the typed client is
`frontend/src/api/schema.gen.ts`. The contract every route follows — error
bodies, the `Page[T]` envelope, status codes — is
[`docs/decisions/0023-the-http-contract.md`](docs/decisions/0023-the-http-contract.md).
`GET /healthz` is the liveness probe.

An installed app's routes are mounted from its own declaration and appear in the
same schema; the platform never lists them in source.

## Where the real documentation is

- [`CLAUDE.md`](CLAUDE.md) — repo conventions, and the gates a change must pass.
- [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — the design system, which is
  mandatory for anything under `frontend/` or `mobile/`.
- [`docs/applying-changes.md`](docs/applying-changes.md) — how a change reaches
  the running instance.
- [`docs/platform-redesign/entity-model-2026-07-18.md`](docs/platform-redesign/entity-model-2026-07-18.md)
  — the model the rest of the design set rides on.

There is deliberately no build-status list in this file. Status here went stale
for months while reading as current; what is true is what the checks in
`scripts/` report when you run them.
