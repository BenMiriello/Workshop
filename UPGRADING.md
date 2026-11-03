# Upgrading an existing install

For an installation that has been running for a while and is pulling a much
newer `main`. The routine update steps are in
[docs/deploying.md](docs/deploying.md#updating-an-existing-install); this page
covers the behaviour changes that an older install does not pick up by itself.

## Node 20

The repo now requires node 20 (`.nvmrc`, `engines` in `package.json`). On an
older node, `npm run build:ui`, `typecheck`, `test` and the frontend build stop
with a one-line error, and `scripts/preflight.sh` reports it as missing.

**By hand:** install node 20, then run `npm ci` at the repo root again. An
install made under node 18 silently skips optional packages (diagrams among
them), so reinstall rather than only switching node.

## The database

Tickets, chats and apps are served from Postgres. Migrating a new, empty
database is two commands, in this order, both with the owner connection
(`WORKSHOP_DATABASE_ADMIN_URL`) loaded:

```bash
cd backend
.venv/bin/python -m alembic upgrade head
WORKSHOP_DB_APP_PASSWORD=... .venv/bin/python -m db.bootstrap
```

`upgrade head` used to fail on a server that had never had the application
role; it now creates the role, and bootstrap gives it its login and grants.

**By hand, once:** import what the older install kept in files. All three print
a plan by default and write only with `--apply`:

```bash
.venv/bin/python -m db.backfill --apply        # org, users, tickets: first
.venv/bin/python -m db.chat_backfill --apply   # chat transcripts
.venv/bin/python -m db.app_backfill --apply    # apps, and chats linked to them
```

`db.backfill` no longer needs `--force` when the database holds no tickets yet;
it still refuses when it does, because the file store is then an old copy.
`db.chat_backfill` used to write with no flag at all; it now needs `--apply`
like the other two (`--plan` is still accepted and does nothing extra).

## Where chat turns run

Turns no longer run in the account's home directory. Every turn runs
confined to one directory, which the agent sees at `/workspace`: a project's
tree, this checkout for Development, and for a chat with no scope an empty
per-user directory under `.runtime/users/`.

**Automatic:** the Claude CLI files a conversation under the directory it ran
in, so every existing chat used to lose its conversation after the move. When
a chat resumes, its conversation is now copied from wherever it was filed into
the place the CLI now looks, and it continues as before.

**By hand:**

- General (unscoped) chats are text only: they have no file or shell tools.
  Bind a chat to a project, or to Development, to work on files.
- Confinement needs `bwrap` (bubblewrap) and working user namespaces. Without
  them every turn is refused rather than run unconfined. Install it if a chat
  reports that confinement could not be established.
- A confined turn sees only its own tree. Anything outside it that a project
  relied on is no longer visible to the agent unless the installation has a
  setting for extra paths.

## The Development scope

Development (the platform's own source, offered when `WORKSHOP_DEV_MODE=1`)
used to need a registry row marked `self: true` in `.runtime/projects.json`,
and nothing created it, so rescoping a chat to Development failed.

**Automatic:** without that row, Development now resolves to this checkout.
An existing row is still honoured.
