# Developer mode and the `development` scope

- Status: **proposed** (not implemented; operator decisions needed — §8)
- Date: 2026-08-09
- Relates to: `entity-model-2026-07-18.md` §2 (scope addressing), ADR 0013
  (public auto-deploy is pre-launch), tickets [SEC-A1] (execution identity),
  [SEC-A2] (stop representing scope as a security boundary)

---

## 1. The problem

`workshop` scope means two unrelated things.

**Meaning A — the root scope of the installation.** Notes land in
`shared/knowledge/`, tickets in the root queue, and per
`entity-model-2026-07-18.md:67-69` an entity in `workshop` is visible to every
scope beneath it. This is correct, it is what "global" should mean, and every
user needs it.

**Meaning B — permission to run an autonomous agent against the Workshop's own
source.** Not designed; an accident of naming. `backend/routes/tasks_all.py:840`:

```python
elif scope == "workshop":
    start_kwargs["project_slug"] = "workshop"
    cmd_tail = ["--dir", str(root)]          # root == $WORKSHOP
```

A ticket in the root scope launches a sweep that rewrites `frontend/`,
`backend/`, `scripts/` — the running application's own code.

Everyone needs A. Only the developer should have B.

## 2. The finding that determines the design

**The platform repo is not reachable under one name. It is reachable under at
least five, through four different subsystems**, and a survey found no guard
anywhere that prevents a run from modifying the Workshop's own source.

| # | Door | Where |
| --- | --- | --- |
| 1 | `scope == "workshop"` ticket run | `backend/routes/tasks_all.py:840-842` |
| 2 | `general` chat — same task root as `workshop` | `backend/services/task_scope.py:52-58` |
| 3 | **`project:workshop`** — a normal registry entry with `dir: $WORKSHOP`, `self: true`; resolves through the identical code path as any other project | `.runtime/projects.json`; `build_agents._project_dir` at `build_agents.py:71-79` |
| 4 | **`build:workshop-builder{,-2,-3,-4}`** — four build agents with `cwd: $WORKSHOP`. Marked `retired`, but `get_agent()` (`build_agents.py:90`) does not filter on state | `.runtime/build_agents.json` |
| 5 | `agent:<slug>` for a `maker` whose build-agent record has the self cwd | `dispatch_triggers.sh:434-464` |
| 6 | `POST /api/projects/workshop/autonomy/run` | `backend/routes/projects.py:794-819` |
| 7 | `dispatch_triggers.sh` project loop — treats the `self: true` project as an ordinary loop candidate, gated only by its own `autonomy.mode` | `scripts/dispatch_triggers.sh:298-380` |
| 8 | **`ralph.sh` with no `--dir` — the platform repo is the DEFAULT** | `scripts/ralph.sh:60` |
| 9 | **`queue_runner.sh` — same, `DIR="$WORKSHOP"`**, resident, restarted by cron every 5 min | `scripts/queue_runner.sh:40-41`; `queue_runner_watchdog.sh:19` |

Chat turns in doors 3–5 spawn `claude` with `--permission-mode bypassPermissions`
(`build_streaming.py:649`) and the full agentic toolset, cwd = the repo root.

**Therefore: renaming the scope closes door 1 and nothing else.** A plan built on
"make `workshop` non-runnable" would produce a boundary that reads as real and is
not — precisely what [SEC-A2] exists to unwind. The scope is the *name*; it
cannot be the *gate*.

## 3. The gate is the resolved directory — and the predicate already exists

The one thing every door has in common is that it ends up with a working
directory equal to the platform repo. So that is where the check belongs.

**`app_runner.is_self_dir()` and `is_self_project()` already exist**
(`backend/services/app_runner.py:57`, `:75`) and are exactly the right predicate.
They are used today only to refuse *starting the self project as an app* and to
*skip auto-checkpoint commits* (`build_streaming.py:1091`). They are never
consulted on any write or run-launch path.

So developer mode is definable in one sentence:

> **Developer mode is whether `is_self_dir(cwd)` is permitted as a run target.**

Nothing new needs inventing. Four call sites cover every door in §2:

| # | Chokepoint | Covers |
| --- | --- | --- |
| 1 | `task_scope.root_for_context` (`task_scope.py:48`) — refuse any scope whose root resolves to `WORKSHOP_ROOT` | doors 1, 2, 3, 5, 6 (upstream of the launcher, the chat providers and the jobs router) |
| 2 | `launch_task_run` (`tasks_all.py:810`) — the single Popen for ticket runs | door 1, belt-and-braces |
| 3 | `build_streaming.start_turn` (`build_streaming.py:572`) — the one funnel every chat/build turn passes through before the `:703` spawn | doors 3, 4, 5 |
| 4 | `ralph.sh:60` and `queue_runner.sh:41` — make the platform repo an explicit request instead of the DEFAULT | doors 7, 8, 9 |

Chokepoint 4 is the one most easily forgotten and the most consequential: today a
bare `ralph.sh "some task"` edits the platform repo because that is what `DIR`
defaults to.

## 4. The `development` scope

With the gate on the directory, the scope becomes what it should be: the honest
name for "work that targets the platform repo."

- Address: **`development`** — top-level, a singleton like `workshop`, not
  `dev:<x>`.
- Label "Development", icon `CodeXml` (verified present in lucide-react 1.16.0).
- Sorted **above** Workshop/Home in the scope picker.
- Present only when developer mode is on.

**And it becomes the ONE name for the platform repo.** The other four aliases
(§2 doors 3, 4, 5, and the `general`→root aliasing) are collapsed into it or
removed. This is the part that makes the gate checkable rather than
whack-a-mole: today "am I about to edit the platform?" has five different
answers depending on which subsystem is asking.

## 4.1 What `workshop` scope becomes — the definition

Three scopes, three different questions:

| scope | the question it answers | working tree for a run |
| --- | --- | --- |
| `development` | work **on** the Workshop — its source, and this installation's host | the platform repo (dev mode only) |
| `workshop` | work **with** the Workshop — the root organising layer | **none** |
| `project:<slug>` / `app:<name>` / `agent:<slug>` | work on one specific thing | that thing's tree |

**`workshop` is the root organising scope: where you think, plan, note and
triage. It is not where anything is built.**

It KEEPS everything it does today except one thing:

- the **global notes layer** (`shared/knowledge/`) — the widest read layer,
  inherited by every scope beneath it (`entity-model-2026-07-18.md:67-69`);
- the **root ticket queue**, now in its true role: the inbox and orchestration
  surface where work is filed, triaged, decomposed and routed to the scope that
  owns it;
- **chats** — the general/assistant chat lives here. This is already how it
  behaves: the workshop chat is a *data* provider with `cwd` = `~` and no
  filesystem tools (`chat.py:1946-1948`), not a build provider;
- **being the universal parent** — an entity here is visible to every scope
  below. This is "universal scope including subscopes", unchanged.

It LOSES exactly one thing:

- **resolving to the platform repo as a working tree.** `WORKSHOP_ROOT` stops
  being what `workshop` means for execution.

### The distinction that made the earlier framing confusing

A scope's **store location** and a run's **working tree** are different things,
and only the second is gated. Workshop-scope data still physically lives inside
the platform repo on disk (`.tasks.json`, `shared/knowledge/`). That is storage.
It does not make the repo a work target, and nothing about this change moves
those files.

### So what does "not runnable" mean here

Only that **no builder sweep is dispatched against `workshop`** — there is no
code tree for one to run in. Workshop tickets are still worked: by the assistant,
in chat, and by assistant-style runs that have no repo and no code tools. A
ticket that turns out to need code is re-scoped to whatever owns that code
(`development` for the platform, `project:x` for a project). That routing is the
triage step, and it is the root scope's actual job.

`workshop` therefore means only what the entity model always said it meant — the
root scope that inherits downward.

## 5. Where developer mode must NOT live

Three standing constraints rule out the obvious approaches.

**Not a per-user permission.** `CLAUDE.md:7` forbids multi-tenant guardrails in a
single-operator system, and `permissions.py:335-347` is default-permissive for
the operator by design. A role check here is the exact cruft the repo rejects.

**Not an origin check.** `workshop_public_app.py:44-47` is explicit that platform
routers are deliberately *not* blocked by origin, because that would be "the
network-position anti-pattern this whole redesign removed". It would also be
wrong on the facts — the operator uses the public origin as their daily UI
(ADR 0013).

**Not another claimed security boundary.** [SEC-A2] is open to stop representing
scope/touches/latitude/grants as security boundaries, and
`agent_authority.py:19-27` already warns that its role model is "coordination,
not confinement."

## 6. Where it does live: the build and the deployment

One flag per side, read in one place.

| side | flag | default | when off |
| --- | --- | --- | --- |
| backend | `WORKSHOP_DEV_MODE=1` (env) | off | `development` does not resolve; absent from `GET /api/tasks/scopes`; the §3 chokepoints refuse `is_self_dir` targets |
| frontend | `VITE_DEV_MODE` (build-time) | off | the Development scope and dev-only UI are **not in the bundle** |

Both patterns already exist here: `VITE_PUBLIC` in `BirdsShell.tsx:7`, and
`WORKSHOP_ENABLE_DOCS` conditionally mounting a router in `main.py:76-78`.

The property that matters: **in a normal build the capability is not shipped, and
in a normal deployment the path is not reachable.** Absent code needs no
permission system — which is how this satisfies "regular users cannot edit the
Workshop" without adding a guardrail of the kind the repo forbids.

## 7. What this is, honestly

A **product capability boundary and a safety interlock** — not a security
boundary against a hostile user. Anyone with a shell on the box can still edit
the source; real confinement is a separate execution identity, which is [SEC-A1]
and still open. Nothing in the UI or docs should imply otherwise.

What it does buy:

- a normal build cannot be *used* to modify the app it is running;
- the always-on loops cannot silently rewrite the platform in a normal install;
- "edit the Workshop" becomes a named, visible mode instead of an emergent
  property of the root scope and four forgotten registry rows.

Write that framing at the flag and at each chokepoint, so it does not become the
next thing an audit has to unwind.

## 8. Decisions (operator, 2026-08-09)

1. **`workshop` is not a build target.** Defined in §4.1. No builder sweep is
   dispatched against it; it has no code tree. It keeps its notes, its queue, its
   chats and its universal-parent role.
2. **The always-on loops stay always on — scoped.** Nothing is switched off. The
   only change is that the platform repo stops being the *default* target:
   `ralph.sh:60` and `queue_runner.sh:41` currently read `DIR="$WORKSHOP"`, so a
   loop started with no argument works the platform tree. A target must be named.
   The dispatcher keeps iterating scopes that have work — projects always,
   `development` only when dev mode is on.
3. **Redirect, don't multiply.** The `project:workshop` registry row becomes
   `development`; the four retired `workshop-builder*` records are deleted. Exactly
   one name resolves to the platform repo.
4. **Migrate wholesale — the combing is done, and the result is decisive.** See
   §8.1.
5. **One scope**, not several. Per-ticket `touches` provides granularity.

### 8.1 Result of combing the root queue

93 tickets: 16 open, 61 done, 16 cancelled. **Every one of them is platform or
installation work.** Not a single personal-ops ticket exists in the root queue.

The 16 open ones fall into two groups, both of which belong to `development`:

- **platform source** — "Add a by-chat usage view to the Usage page", "Audit the
  toolbar and chat-adjacent pages", "Execute P5 (Run → Session rename) and P6",
  `[SEC-A2]`, `[SEC-A5]`, `[SEC-A6]`, `[SEC-G1]`;
- **this installation's host** — `[SEC-C3]` IPv6 firewall, `[SEC-H3]` NOPASSWD
  sudo, `[SEC-H4]` ComfyUI bind, `[SEC-H7]` Ollama bind, `[SEC-M1]` Letta
  password, `[SEC-M2]` open-wearables secrets, `[SEC-M3]` sshd, `[SEC-L2]` Vite
  dev server, `[SEC-L5]` Samba.

The closed history is the same in character: the ralph worker loop, the reviewer
gate, task leases, the entity model, the orchestration surface, the autonomy
model.

Two things follow. First, the migration is a **move, not a sort** — no judgement
call per ticket. Second, and more usefully: **`workshop` scope has never once been
used for the purpose this redesign gives it.** In practice "workshop scope" has
only ever meant "edit the Workshop", which is exactly why the two meanings fused.
The redesign is therefore closer to a rename plus a gate than to a split.

This also settles a question §4.1 raises implicitly: host/infra work belongs to
`development`, because `development` means *work on this installation* — its
source and the box it runs on. A normal user of a hosted Workshop has no box to
configure and no source to edit; that is the whole boundary.

## 9. Known cost: there is no canonical scope enum

Scope is a bare string parsed independently in four places. Adding a value means
touching all of them:

| where | what |
| --- | --- |
| `backend/services/knowledge.py:88-96` | `_parse_scope` allowlist (notes directory) |
| `backend/services/task_scope.py:48-71`, `:74-101` | `root_for_context` / `key_for_root` |
| `frontend/src/components/chat/chatScope.ts:42-55`, `:57-65`, `:105-111` | `scopeKind`, `scopeLabel`, `SCOPE_GROUPS` |
| `frontend/src/lib/tasks.ts:280-287`, `:311-317` | `scopeLabel`, `scopeHref` |

Plus `ScopeIcon` (`ChatToolbar.tsx:17-22`) for the `CodeXml` icon and the two
orderings so Development sorts above Workshop.

`chatScope.ts:36-40` already documents this duplication and notes that unifying
it "is a migration, not a rename." This plan does not require that migration, but
it is the second consumer to pay the tax — worth deciding whether a small
canonical enum gets introduced here and paid for once.

## 10. What `touches` is not

Worth recording so it is not mistaken for part of this boundary. `touches`
(`tasks.py:720-850`, sole call site `project_ralph.sh:895-915`) is opt-in
(`[]` = unrestricted, and `[]` is the default at creation, `tasks.py:1395`),
evaluated **after** the model has finished and committed, and computed from the
*committed git diff* — so uncommitted writes and anything outside git
(`.runtime/`, other trees) are invisible to it. An unknown task id returns
"allowed". It is a ticket declaring what it is about, not a repo boundary, and it
must not be cited as one.

## 11. Proposed sequencing

| phase | work | why this order |
| --- | --- | --- |
| 0 | ADR recording the decision | settle the shape before code |
| 1 | Both flags introduced, no behaviour change | isolates the plumbing |
| 2 | `development` added through the four parsers + UI (icon, label, ordering) | the scope exists but nothing routes to it yet |
| 3 | Collapse the aliases (§4): self registry row, retired builders, `general`→root | one name for the platform repo |
| 4 | The four chokepoints (§3) call `is_self_dir` and refuse when dev mode is off | the load-bearing change |
| 5 | Move the root queue wholesale to `development` (§8.1 — no per-ticket sort needed); name the loops' targets so the platform repo stops being their default | data follows behaviour |
| 6 | Verification: assert dev surfaces absent from a non-dev bundle, and that a non-dev backend refuses every door in §2 | proves the claim instead of asserting it |

Phases 3–5 must not ship separately from 4. Between them the system would claim
a boundary it does not have, which is worse than the honest status quo.

### Where phase 6 lives

- **Backend, one test per door**: `backend/tests/test_dev_mode_gate.py`.
- **Cron half of the flag, and door 7**: `backend/tests/test_dev_mode_cron_and_notes.py`.
  Door 7 was closed only by the self row's stored `autonomy.mode`, and
  `WORKSHOP_DEV_MODE` reached the API through a systemd `EnvironmentFile=` that
  cron does not have, so the flag read ON for the backend and OFF for every
  sweep cron started.
- **Bundle**: `bash scripts/check_dev_bundle.sh`. Not in `npm test`, because it
  has to strip `VITE_DEV_MODE` from `frontend/.env` and a suite that rewrites
  deployment config is one nobody can run twice. It builds
  `frontend/src/lib/devMode.bundleProbe.ts` through the real Vite pipeline with
  the flag absent, runs the emitted module, and asserts every gate reads off at
  its DEFAULT argument, then repeats with the flag set as a positive control.
  Grepping the built bundle for `development` cannot work and is not what this
  does: the string is present either way, because `orderScopes` has to compare
  against it in order to filter it out.
