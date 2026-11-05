# Operations and boundaries: what an agent can do, and where it can write

Status: design of record, 2026-11-04. Supersedes the "operations" half of
`tools-are-declared-2026-08-23.md` §3 (preview stays as specified there, built
here as one verb among the rest) and settles the access-boundary question that
`app-is-the-entity-2026-08-20.md` §10 left open.

Principle, stated by the operator on 2026-11-04: **make it easy to do it right,
and hard or impossible to do it wrong**, for this installation and for anyone
who clones Workshop, in every scope (development, home, apps, agents).

## 1. What was measured

- 116 top-level scripts in `scripts/`, 45 more in one app. 59 were added in
  August; almost none are ever deleted. 9.4% of all agent shell calls in three
  weeks ran one, plus ~2,300 throwaway scripts written into `tmp/`, 168 `pkill`
  and 138 hand-started servers.
- The scripts are where the defects were: restarts that reported success they
  did not have (`6baeaef7`, `880eaa00`), a port hardcoded for twelve weeks
  after ports moved (`6560be07`), checks that checked nothing (`npx tsc`), a
  restart that loaded another session's half-written file and failed 26
  renders (2026-10-19).
- The Workshop tool server is a stdio child launched from the backend's own
  interpreter and source, which a sandboxed app turn cannot see
  (`app_registry.mcp_server_reachable`). **Every app chat has no Workshop tools
  at all**: no search, no wake_me, no operations. Agents in those chats were
  told to run platform scripts that are also outside their view.
- The declared-tool registry (`services/tool_registry.py`) works and holds no
  operations. Its `command` kind runs inside the CALLER's sandbox, so it cannot
  restart, build or apply: those need the platform's authority.
- Write boundaries: one bwrap view per turn, but every confined turn also gets
  the systemd user socket and bus (a way out of the sandbox), read-write
  `~/.claude` (global hooks and instructions), and the development scope can
  write every app's source. Advisory layers (`permissions.py`, `scope_tree.py`)
  say of themselves that they are not walls.
- Placement: two app formats in two folders with no rule for picking one, six
  registries that disagree, three ways of consuming the UI kit, and apps
  carrying copies of platform capabilities (speech setup, restart, test env)
  because the platform did not offer them.

## 2. The model

### 2.1 Three kinds of place

| place | what it holds | who writes it |
|---|---|---|
| **The platform** | `backend/`, `frontend/`, `packages/`, `scripts/`, `docs/`: what every installation runs | the development scope only |
| **An app** | one folder, one repo, one `workshop.app.json`: everything particular to that app | that app's own scope only |
| **Installation state** | `.runtime/`, `~/.config/workshop/`, the database | the platform, through verbs and routes; never an agent's file write |

Plus each scope's own scratch (`tmp/`, `scratch/`), which is where throwaway
work goes and is never named in instructions.

A rule of thumb that decides every case: **if a second installation would want
it, it is platform; if only this app wants it, it is the app's; if it is a
fact about this machine or this person, it is installation state.**

### 2.2 Operations are platform verbs; particulars are declarations

The platform offers a fixed set of verbs as Workshop tools. The thing being
acted on declares its own particulars in its manifest. An agent never needs a
path, a port, a credential or a script name to operate anything.

| verb | what it does | what the app declares |
|---|---|---|
| `app_status` | services, health, ports, pids, last error, what is building | nothing new |
| `app_logs` | one service's recent log | nothing new |
| `app_control` | start, stop or restart the app or one service; restart waits for health and returns the old and new pid, or the error and the log tail | per service: `reload` (how to swap in new code without a stop, e.g. a signal to its launcher) and `preflight` (a command that must pass before a restart) |
| `app_setup` | install dependencies (venv, packages) | `setup` per service, split from `build` |
| `app_build` | run the declared build of one or every service | existing `build` |
| `app_apply` | make the running app match its source: build what changed, migrate, restart what changed, verify | `migrate`; optional whole-app `apply` (the platform has one) |
| `app_env` | read and set the configuration and secrets the app declares | existing `env_requirements` |
| `app_test` | run the declared tests, under a memory cap, with a time limit | `test`: command, optional memory |
| `app_check` | run the declared checks, pass or fail for each | `checks`: named commands |
| `app_preview` | screenshot a page as the operator's browser renders it | `preview` (as in `tools-are-declared` §3) |

App-only operations (an app's eval, import or setup) are declared under the
manifest's `tools` list and arrive as that app's own tools, through the
registry that already exists. Nothing an app declares can name another app.

The platform is operated like an app: the development scope has a declaration
(in platform code, not a catalog entry, so a fresh clone still boots with an
empty catalog) whose `apply` is the existing apply procedure, whose `checks`
are the gates, and whose `test` is the backend and frontend suites. One code
path, so a fix to one is a fix to all.

### 2.3 Who may operate what

Operations are offered to BUILD turns (a maker: the development chat, an app's
build chat, a maker agent), never to a data chat: an app's in-app assistant
runs on a small local model whose tool list must stay short, and it has no
business restarting anything.

- An app's build chat may operate its own app.
- The development scope may operate any app and the platform.
- The tool refuses any other target with the reason, rather than hiding it.

The authority comes from the turn's signed claims (§2.4), not from anything
the agent says.

### 2.4 How the verbs reach every turn

The Workshop tool server is served by the backend over HTTP at
`/mcp/workshop`, statelessly: no session id, every request carries its own
signed claims (owner, scope, scope root, chat, turn, session, tool limits,
page level, expiry) in its `Authorization` header, minted with a key kept on
disk so a turn adopted across a restart keeps working. Measured 2026-11-04
against the CLI: a stateless HTTP server restarted between two tool calls of
one turn answered the second call with no new handshake.

Every turn reaches it over loopback from inside any sandbox, so app build
chats get the same Workshop tools as the development chat, which they never
had (`app_registry.mcp_server_reachable` dropped it: ticket `1e2b7f87`).

The tool code is unchanged except that it reads its context through one
accessor that prefers the request's claims to the process environment. Calls
run on a small dedicated thread pool, never on the event loop. Command tools
still run in the CALLER's sandbox, built from the claims' scope root and
session. The stdio launch stays as a transport for one purpose: a ticket
worker testing a worktree's own copy of the server (`ralph.sh`), and as a
fallback switch (`WORKSHOP_MCP_TRANSPORT=stdio`).

The verbs themselves run in the backend, not in the agent's sandbox:
restarting and applying need authority a sandbox must not have, and
verification lives in one place instead of in every agent's habits.

A verb that would restart what serves the calling turn (the platform's
backend for any turn; an app's service that the calling chat's model runs on)
is DEFERRED to the end of the turn and says so; it never cuts off its caller.
Long verbs (an app restart that swaps gracefully, a test suite) run inside the
call with the server's timeout raised to 30 minutes.

### 2.5 Where each scope can write, enforced by what it can see

Enforcement is the mount table, so a wrong write is impossible, not detected.

| scope | writable | not present |
|---|---|---|
| development | the platform tree | app folders |
| an app's build chat | its own folder, plus external folders the app declares | the platform tree, other apps |
| data chats | nothing on disk (they act through tools) | everything |

This is the END STATE and it has prerequisites, each its own stage (§3):
the UI kit consumed as a package rather than by a relative path into the
platform; per-app external folders declared in the manifest instead of one
global bind list; memory caps applied by the platform to every turn instead
of by agents through `systemd-run`; and a proven hand-off, `app_agent`, that
starts or continues the app's own build chat with the work and brings its
report back into the calling chat. Until those land, no folder is hidden: a
boundary that breaks the operator's real work is the wrong boundary.

### 2.6 How agents talk about it

Replies say what was done in the operator's terms ("restarted the app's api,
healthy in 4 s"), never "ran the restart script". Instructions name verbs,
never script paths.

## 3. Order of work

Each stage carries a runnable assertion in `scripts/plan_landed.json`.

1. **Tools over HTTP.** `/mcp/workshop`, signed claims, every turn's config
   switched to it. Check: a sandboxed app build turn lists `mcp__workshop__*`
   tools and `where_is` answers.
2. **Runner verbs.** `app_status`, `app_logs`, `app_control`; the authority
   rule; `reload` and `preflight` per service; restart refuses to load
   another session's uncommitted edits. Check: an app build turn restarts its
   own app and the reply carries the pid change; a foreign target is refused.
3. **Setup, build, apply, test, check, env.** `setup` (dependencies) split
   from `build`; `migrate` per app; `test` and `checks`; `app_env` to read and
   set declared configuration and secrets. The platform's own declaration
   lives in platform code, not as a catalog app. Check: the development chat
   applies a platform change through `app_apply`.
4. **Preview.** `app_preview` on the existing screenshot driver.
5. **Clarity.** Script names out of prompt text, `CLAUDE.md`, `AGENTS.md` and
   memory; one "where things go" section in the generated orientation; dead
   scripts deleted; per-app restart and test scripts replaced by
   declarations.
6. **Prerequisites for the boundary.** Memory caps per turn; per-app external
   folders; kit as a package for every app; `app_agent`.
7. **The boundary.** App folders absent from the development scope; the
   systemd socket and bus withdrawn from turns that no longer need them;
   installed apps (`apps/*`) get a build scope of their own.
8. **Placement.** One app home and one rule for choosing an app's format; the
   registries reduced to the `app` table plus the manifest; the app-name gate
   deleted once the boundary makes it redundant.

## Status (2026-11-04, end of the first build)

`python3 scripts/check_plan_landed.py` is the authority; this is what it said.

Landed: stages 1 to 5 and 6a, 6b, 6d. Every turn, an app's build chat included,
reaches the workshop tools over HTTP; build chats have `app_status`, `app_logs`,
`app_control`, `app_apply`, `app_build`, `app_test`, `app_check`, `app_env`,
`app_preview`, `run_capped` and (development) `app_agent`; the platform is the
app `workshop`; an app declares its graceful `reload`, `test` and check; the
orientation map, the app-chat briefing and `CLAUDE.md` name the verbs; thirteen
dead or replaced scripts are deleted. Verified on real sandboxed turns against
a second backend: an app chat's tools, `app_check` running an app's declared
check, a cross-app call refused with its reason, and `run_capped` confined to
the caller's view and killed at its ceiling.

Not yet, pinned in `scripts/plan_landed_baseline.json`:

- **6c, the kit as a package.** Four runner apps compile the kit from a
  relative path into the platform tree. Moving them needs a decision on how
  the kit is distributed: published to a public npm registry under its own
  name (portable, the standard answer), or a tarball each app vendors (the one
  app that does this today needs a refresh script to keep it current).
- **7, the boundary.** Waits on 6c. It also changes how app work is done from
  the development chat: once app folders are gone from that scope, app work
  happens in the app's own build chat (the chat beside the app's page) or is
  handed over with `app_agent`.
- **8, placement.** Waits on 7.

## 4. What this does not change

- Installation chores (bootstrap, installing services, mobile and Mac builds,
  git hooks) stay scripts: an operator runs them on a machine, not an agent on
  an app.
- Code the backend schedules for itself stays where it is.
- The ticket and reviewer flow keeps its gate; stage 3 makes the gate's checks
  a declaration the verb runs rather than a hardcoded list in `reviewer.sh`.
