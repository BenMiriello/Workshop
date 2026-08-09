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
    cmd_tail = ["--dir", str(root)]          # root == /home/clawd-a/workshop
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
| 3 | **`project:workshop`** — a normal registry entry with `dir: /home/clawd-a/workshop`, `self: true`; resolves through the identical code path as any other project | `.runtime/projects.json`; `build_agents._project_dir` at `build_agents.py:71-79` |
| 4 | **`build:workshop-builder{,-2,-3,-4}`** — four build agents with `cwd: /home/clawd-a/workshop`. Marked `retired`, but `get_agent()` (`build_agents.py:90`) does not filter on state | `.runtime/build_agents.json` |
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

`workshop` then means only what the entity model already says — the root scope
that inherits downward. That matches the operator's "global scope just means
universal scope including subscopes."

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

## 8. Open questions — operator input needed

1. **Does `workshop` stay runnable at all?** Recommendation: no. It becomes a
   pure organising/notes/tickets scope, and anything that runs does so in
   `development`, a project, or an agent scope.
2. **What do the always-on loops do in a normal install?** `queue_runner.sh`
   currently defaults to the platform tree and is kept alive by cron. Under this
   model it should target `development` only in dev mode. In a non-dev install,
   is there an always-on loop at all, or only per-project runs?
3. **The four `workshop-builder*` build agents and the `project:workshop`
   registry row** — delete, or redirect to `development`? Recommendation:
   redirect the self row, delete the retired builders, so exactly one name
   resolves to the platform repo.
4. **Migration of the existing root queue.** `.tasks.json` (287 KB) mixes
   platform work (the `SEC-*` and UI tickets) with personal ops. Recommendation:
   produce a listing for the operator to sort by hand rather than guessing.
5. **Is `development` one scope or several?** Recommendation: one. Per-ticket
   `touches` gives granularity; sub-scopes would multiply the §9 parser cost.

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
| 5 | Migrate the root queue; point the loops at `development` | data follows behaviour |
| 6 | Verification: assert dev surfaces absent from a non-dev bundle, and that a non-dev backend refuses every door in §2 | proves the claim instead of asserting it |

Phases 3–5 must not ship separately from 4. Between them the system would claim
a boundary it does not have, which is worse than the honest status quo.
