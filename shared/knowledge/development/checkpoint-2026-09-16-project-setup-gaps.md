<!-- author: self -->
# Checkpoint 2026-09-16: new-project setup gaps, confinement, GitHub

Written at the operator's request as a context handoff. This is state, not a
plan: every claim below was measured, and where it was not, it says so.

## What this session was

The operator started a project-scoped chat for a new project (Overview Effect,
a lightning/atmospheric globe app) and used it to discover what the platform
does and does not do when you start a project. He reported, across several
messages: a project agent working on absolute host paths and outside its
container; that same agent unable to install a system package; GitHub not
connectable; no project settings surface; chat toolbar wrapping; a chat that
filed a ticket and stopped; ticket and run states reading the same; his chat
missing from the project's Chats tab; and signing in again on every new tab.

## Landed and delivered (12 commits, bundle rebuilt, on the operator's origin)

- `0cc2f13a` background_runs confined. It was the ONLY unconfined claude spawn
  in the system (full sweep done: every other `scripts/` reference is a comment
  or a `command -v`). It ran with `bypassPermissions` and its cwd fell back to
  the PLATFORM checkout whenever the scope did not resolve, which fires in the
  ordinary case (a new project whose tree does not exist yet). Same fallback
  that was already removed from `MakerAgentContextProvider`.
- `b078962f` prompt layers. `instruction_contract` passed the raw host dir and
  `_task_cli()` interpolated the platform root into every project persona. New
  `agent_cwd()` returns `scoped_exec.MOUNTPOINT` when confinement is on.
  `check_prompt_layers.py` now scans 50 RENDERED blocks, not source literals,
  and was demonstrated failing with 18 offenses before the fix.
- `d7d1d9d2` GitHub connect: manifest registration + OAuth web flow + PKCE, no
  copy-paste. Design decision in `github-connect-flow-2026-09-16.md`.
- `7562a719` `chat_thread.app_id` written at creation and re-derived on sync.
- `56bde4fa` `jobs.finish()` idempotent on terminal status.
- `9e8cff0c` toolbar never wraps. `b417dddb` title event applied to client state.
- `a88c765d` Overview explainers deleted (floor 40 to 18). `cef5e4e0` kit
  Section primitive; full-width is now opt-in.

23 tickets filed. Two closed through the reviewer gate with mutation testing.

## OPEN AND URGENT: apps missing from the UI

Reported by the operator at the end of the session. Measured:

- `GET /api/apps` returns **HTTP 503**, "the relational store is not configured
  or not reachable".
- `services/apps._require_db()` raises `AppsUnavailable` when
  `tenancy.configured()` or `tenancy.available()` is false.
- **`tenancy.available()` caches its first answer in a module global for the
  whole process lifetime.** There is no re-probe and no way to clear it short of
  a restart.
- The backend restarted 2026-09-17T01:46Z. `/healthz` still reports schema
  `a1e4f7c02b93`, because that path does not go through `tenancy`. So the
  database is very likely fine and the cached probe is stale.
- `app_registry.launcher_cards()` wraps the DB half in a bare `try/except`, so
  the outage renders as a SHORTER LIST (7 catalog apps) rather than an error.
  That is why projects, including the new one, vanished rather than erroring.

A deferred restart was armed and a wake-up set for 19:20 PDT to verify. If that
fixes it, the real defects are the forever-cached probe and the swallowed
exception, not the database.

**Health is separate and pre-existing.** `apps/health/workshop.app.json`
declares `"pages": []` and no launcher card, so `launcher_cards()` skips it at
`if not spec.card: continue`. Installed, routes mounted, no way to open it.
Needs a product decision.

## OPEN AND NEEDS THE OPERATOR: the egress guard

Ticket `eb30f479`. This is what actually blocks the other project, not GitHub
auth: all three `overview-*` repos are PUBLIC and need no credential.

`~/.claude/hooks/egress-guard.py` decides "is this a builder" by testing whether
cwd is under a host build root. Inside bwrap the cwd is the mountpoint, so every
CONFINED agent fails that test and falls through to deny-all. Separately, its
sensitivity lookup reads a `projects.json` path that no longer exists (the
registry moved to `.runtime/`), so every UNCONFINED builder is treated as
trusted. Too strict where it matters, too loose where it does not.

Not touched: it is the operator's file, outside the repo, guarding financial
data. The stated constraint on the ticket is that an unidentifiable builder must
fall back to the SHAREABLE tier, never private.

## Corrections made to the record

- `shoot-sh-cannot-screenshot-a-chat-route` was stale. The driver was fixed
  (`domcontentloaded` plus a bounded settle); chat routes screenshot fine.
  Corrected in `6e0dbda0`.
- The other project believed `libeccodes-tools` was uninstallable. Measured
  false: `pip install ecmwflibs eccodes` decodes GRIB2 with no apt and no root.
  Note left in that project's own knowledge dir (scope boundary means it cannot
  see this one). Its INDEX is updated; the note is uncommitted in that tree
  because the classifier blocked an author-identity override.
- "Cannot install anything in the sandbox" is too strong generally. `apt` and
  `docker` are on PATH and cannot work; `pip` and `npm` do, and egress to
  allowed package hosts is open.

## Things that cost this session time

- `scripts/reviewer.sh` is invoked BY a unit test
  (`test_change_gates.py::test_every_check_the_reviewer_ran_becomes_a_gate...`),
  so the backend suite serialises every parallel agent behind it. Six concurrent
  agents meant six reviewer runs; pytest parents showed 7s CPU across 22 minutes
  and looked deadlocked. Ticket `a1ef4fdb`. Verify with targeted test files plus
  `python3 -c "import main"` instead.
- A bare `git commit` commits the whole INDEX, which swallowed another agent's
  staged work into one of my commits. Split back out; use
  `git commit -- <paths>` when the tree is shared.
- The direct DB connection from a sandboxed agent reaches a DIFFERENT Postgres
  than the backend (0 chat threads vs the real one). Any DB claim made from a
  sandbox is about the scratch database. Ask the running backend instead.
- The Playwright browser cache lives outside the persistent mount and
  re-downloaded ~100MB mid-session.

## Still open from the operator's list, ticketed, not started

Autonomy brake engaged 23 days (`Run now` bypasses it, which is why clicking
worked). Repo connection form shape and the homeless branch/ref field
(`e1835720`). Ticket-vs-run hierarchy and linking to the working thread
(`c3be59c2`). Waiting-vs-working indicator (`20b28f05`). Continue-rather-than-
stop on a non-blocking question (`b42f92e5`). Chat mode showing auto while the
session is in plan mode (`32837886`). New-tab sign-in, leading theory
`SameSite=Strict` on externally-initiated navigation (`4a3abacc`).
