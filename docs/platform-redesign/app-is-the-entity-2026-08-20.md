# App is the entity: retiring the app/project split

- Date: 2026-08-20
- Status: **model decided, migration proposed and not yet built**
- Supersedes the App-vs-Project split asserted in
  [`entity-model-2026-07-18.md`](entity-model-2026-07-18.md) §2 and §9.4, and in
  `CLAUDE.md`. Ratifies and extends
  [`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md) §5-6.
- Companion to [`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md),
  which is the shell this model unblocks.

---

## 1. The operator's direction, 2026-08-20

Recorded close to verbatim, because the reasoning matters as much as the answer.

> "In the process of developing this, we sort of redefined the term app a
> little bit. Even though I think it still kind of retains the original main or
> existing meaning. But app is I think a better term for the overarching term,
> but also the term for the actual running app. And then I guess project. I
> don't know if we need the term project anymore. I mean maybe we do, but
> basically project is like if you're editing an app. So I'm a little bit
> undecided, I'll admit, but so I guess it's an app."

> "We might want to rework the actual backend code or even the schema so that we
> don't have this unnecessary distinction, or that we group these things
> correctly now and going forward."

> "One of the other things that we did in this redesign is we redefined the term
> scope into being an app."

> "Let's make sure to define these things in a RESTful way with normal tables and
> API endpoints and backend services. And they should not be tied into the front
> end in whatever configuration the front end is. We want the backend to be some
> way that we could just redesign the front end however we want, and the services
> will be what they are."

> "When we have this deployed in production, and any other user is using our
> system, there should be absolutely zero retention or hint or inclusion of
> anything of my specific apps, like budget, finance, birds, etc. in the code.
> Every time I ask another agent to look at the codebase and give me examples,
> it tends to reference my specific apps. That makes me think we must have some
> reference of these in the code. And that's not acceptable."

And on the shell as built:

> "When I open an app, I notice there's only the app page. You never actually
> show the agents, tickets, deploys, memory, or chats."

---

## 2. The diagnosis, measured

The operator raised those as separate complaints. **They are one defect**, and
this is the central finding of this pass.

### 2.1 One thing, three records, none of them whole

Five names exist as an app AND as a project, with nothing linking them:
`budget`, `birds`, `location`, `commute`, `notes-app`.

Take Budget. It exists as:

1. **An app** — `.runtime/installed-apps.json` plus an `AppSpec` factory in
   `backend/services/app_registry.py`. Gives it a launcher card, an API prefix,
   a chat persona, an MCP grant. Owns no repo, no tickets, no runs, no deploys.
2. **A project** — `.runtime/projects.json`, `state: "sketch"`, `repo: absent`,
   pointing at `projects/budget/`. Gives it a ticket store (12 tickets), a
   knowledge directory, a working tree, autonomy settings. `projects/budget/src/`
   is EMPTY.
3. **Actual code** — in neither. It is in the platform monorepo, at
   `frontend/src/pages/apps/Budget.tsx` and `backend/services/budget_*.py`.

Nothing joins them. Verified: `routes/projects.py` contains zero references to
`app_registry`, `installed_apps` or `app_id`; `app_registry.py` contains zero
references to `project_slug` or the projects registry. `CreateProjectIn` has no
`app` field and there is no adopt route. The shared slug is a coincidence.

This was already known and written down and never acted on. See the note
`apps-and-projects-have-no-bridge...` (2026-08-04), which calls Budget "the
cautionary case" and predicts precisely this empty shell.

### 2.2 Why the app page column is empty

Because the dock lists **apps**, and every collection worth a page hangs off
**projects**:

| Collection | Keyed by | An app has one? |
|---|---|---|
| Notes / Memory | scope key | yes, and `reach()` works today |
| Chats | `context_key` | yes in the model, but no scope-filtered endpoint exists |
| Tickets | scope key | **no** — `chat_work._QUEUELESS_PREFIXES = ("app:",)`, and `tasks_all._scope_roots()` enumerates dev/workshop/projects/agents and never apps |
| Runs | `project_slug` or `agent_id` | no |
| Deploys | `project_slug` | no |
| Agents | `project_slug` | no — `agents_registry` has no `app_id` |
| Repo / worktrees | project `dir` + `repo` | no field anywhere |

So building one honest row per app was the correct call **given the model**, and
the fix is not to render the rows anyway. It is to make apps the thing the
collections hang off. The page column then falls out of the data model instead
of being decorated onto it.

### 2.3 Why every agent talks about birds and budget

Because the operator's apps genuinely are the platform's source code. Tracked
lines in this repo:

- `frontend/src/pages/apps/` — 61 files, 14,613 lines
- `apps/` (the BirdNET rig, Pi setup, public site) — 43 files, 8,752 lines
- `backend/mcp/plaid/` — 6 files, 2,193 lines
- `agents/apps/{budget,commute,finance,location}.md` — 4 files
- 42 modules under `backend/services/` and `backend/routes/` named after his apps,
  sitting in the flat platform directories

Any agent asked for an example of this codebase greps it and finds birds. It is
not inferring his apps from a stray comment; it is reading them. His instinct
was right and the cause is structural.

Two of those modules are worse than the count suggests because their names hide
their owner: `backend/services/environment.py` is the **birds** weather spine
(and hardcodes `~/birdnet/data/environment.db`), and `backend/services/categorizer.py`
is budget/finance-only. Both read as platform code.

**The same split causes both symptoms.** Apps got the launcher and the manifest.
Projects got the repo, the tickets, the runs and the deploys. The code got left
in the platform. No half owns an app, so the platform holds the pieces.

### 2.4 What is genuinely already right

Worth stating so the migration does not "fix" it:

- **The database is clean.** Zero app-specific tables, columns or enum values,
  across `db/models.py` and all 17 migrations.
- **`main.py` does not name a single app.** Router mounting is one generic loop
  over `installed_specs()`, and `scripts/check_pluggable_apps.sh` fails the build
  if that regresses.
- **Install is data, not code** — `.runtime/installed-apps.json`, gitignored.
- **The scope model is coherent and has one owner**, `services/scope_tree.py`.
  The five disagreeing `context_key -> scope` mappings recorded in `CLAUDE.md`
  were consolidated on 2026-08-16 and all five callers now delegate.

A 2026-07-12 migration (`docs/apps-are-pluggable.md`) did real work here and its
one explicitly deferred seam is still the blocker: an app's React pages are
compiled into the platform SPA because dynamic bundle injection was never built.

---

## 3. The model

### 3.1 App is the only container the user names

**App.** The primary entity. It has a name, an icon, an optional repo, a place
in the tree, and a set of collections. Every app can be *used* and every app can
be *worked on*; which one you are doing is a page you are on, not a different
object.

**"Project" is retired as an entity, and as a word in the UI.** It was never a
peer of App; it was the build view of one. Its data (repo, working tree, tickets,
runs, autonomy) becomes App's data.

This answers the operator's hesitation directly: he does not need "project" for
the editing view, because that is a page.

### 3.2 App does not have to mean two things

His second hesitation, that "app" also names the running instance, is resolved by
a word that already exists in the model rather than by a new one:

- **App** is the entity. It is what you name, and what owns things.
- **Deployment** is a running instance of an app, at a **ref**, in an
  **environment**. Already a record (`Deploy`), already the right shape.

So "the running app" is a Deployment. App never means two things.

### 3.3 The rest of the vocabulary

- **Agent.** Its own kind, not folded into App: its page list genuinely differs
  (Runs, Schedule) and it can be bound *under* an app.
- **Home.** The root. Not "the workshop scope."
- **Page.** What an app's page column lists. (The earlier handoff said "section".)
- **Scope.** *Internal only.* The addressing and permission boundary of an app.
  Never appears in the UI, exactly as Kubernetes says namespace internally while
  OpenShift says project to users. `services/scope_tree.py` keeps its name, and
  a scope key becomes a **derived address for an app**, not a parallel concept.
- **Ref.** Internal. The UI says "branch" or names the version.
- Unchanged and already right: **Chat, Ticket, Run, Note, Deploy, Memory.**

### 3.4 Where "Work" came from

The design agent named the ticket destination "Work". Two causes, both ours:

1. `storage-and-structure-model-2026-08-19.md` §6 lists the pages as "Chats,
   Files, Memory, **Work**, Agents, Deploys, Settings". It read our own doc.
2. The running UI said Work in more places than it said Tickets: the route was
   `/work`, `pages/Work.tsx` rendered an eyebrow reading **Work** directly above
   an `<h1>` reading **Tickets**, the chat toolbar's Tickets button opened a panel
   headed **Work**, and the agent page listed the same records under **Tasks**.

That was ADR 0016 working as specified: it chose Tickets and deliberately stopped
at the label. The cost of the half-rename is now measured, so
[ADR 0028](../decisions/0028-tickets-is-the-name-everywhere-the-operator-can-see.md)
finishes it. **Fixed in this pass** (see §7).

---

## 4. The data model

Principles, in the operator's words: normal tables, RESTful endpoints, real
backend services, and **nothing shaped by what the frontend currently looks
like**. The resource layout below is justified by the entity model; the page
column happens to fall out of it, and that is the correct direction of causation.

### 4.1 One `app` table

```
app
  id            uuid          surrogate, immutable
  slug          text          MUTABLE label, unique per owner
  name          text
  icon          text          token or uploaded asset ref (see §8.3)
  kind          text          'app' | 'agent'
  parent_id     uuid null     containment; null = root
  repo_id       uuid null     an app need not have a repo
  archived_at   timestamptz null
  + the tenancy/RLS columns the Ticket model already carries
```

**A slug is a label, never an identity** — `target-architecture-2026-08-17.md`
§2.3, and it is load-bearing here. Today a project rename is a fork: it orphans
the working tree, the ticket store, the knowledge layer, `chat_thread.scope`, the
usage and job records, and the AAD a project's secrets are encrypted under.
Merging two slug-keyed registries without a surrogate key first would multiply
that bug, not inherit it.

### 4.2 Collections are FKs to `app_id`

`chat_thread`, `ticket`, `run`, `deploy`, `note`, `agent` each carry `app_id`.
That is the whole change. Each then has a real, indexed, queryable owner, and
"everything belonging to this app" stops being a directory walk.

Notes/memory move from directory-shaped to row-shaped last, because they are the
one collection that works correctly today (including `reach()`), so they are the
least urgent and the most disruptive to move.

### 4.3 Scope becomes derived

`scope_tree.py` stays and keeps its name, but a scope key becomes a **rendering
of an app's identity for addressing and permissions**, not an independent axis.
`app:budget`, `project:budget` and a bare `budget` collapse to one app row. The
six live scope shapes reduce to: the root, an app, an agent, and `role:` (which
is already a second axis and not a tree node).

`RELATIONAL_SCOPES = ("development", "workshop")` disappears with it: every app
is relational once there is a table.

### 4.4 The migration is a merge, not a copy

Two file registries become one table, and **five slugs collide and must merge to
one row each**, not to two. The five: budget, birds, location, commute, notes-app.
Conservation counts before and after, on tickets, chats, notes and runs, in the
same commit. `db/ticket_sync.py` is the worked precedent for a diff-and-apply
writer.

---

## 5. The HTTP surface

Today's is not REST. It is resource-shaped at the top and RPC underneath, and
several routes are shaped by a frontend need. The worst, all verified:

- `POST /projects/{slug}/deploys/{id}:deploy`, `:rollback`, `:tick` — colon-verbs.
- `POST /stores/{id}/read` — a POST body to perform a read.
- `GET /tasks/{scope}/{id}` — `{scope}` is a compound key containing a colon, so
  real URLs read `/tasks/project:birds/t_123/run`.
- `GET /chat/threads?context=<context_key>` — filters on the alias namespace, not
  on the modelled scope, so **the one resource the entity model says is scoped
  cannot be queried by scope**. `pages/Memory.tsx` compensates by fetching
  everything and filtering client-side, and says so in a comment.
- `POST /chat/threads/{id}/card-state` — React component state persisted through
  an endpoint. This is the clearest instance of the coupling the operator wants
  gone.
- `GET /base-apps/` returns git repos under `$HOME`, `/base-apps/launcher`
  returns apps, `/base-apps/manifests` returns a third thing. Three senses of
  "app" under one prefix, and `POST /base-apps/run/{slug}/start` takes a
  **project** slug on a router named `base_apps`.
- `/agents/build/{slug}/…` — 20 routes shadowing `/agents/{id}`, including a
  third way to create a ticket, under a `build:` spelling the scope model deleted.

### 5.1 Target

```
GET    /apps                       ?kind=&archived=
POST   /apps
GET    /apps/{id}                  DELETE /apps/{id}       PATCH /apps/{id}
GET    /apps/{id}/chats
GET    /apps/{id}/tickets
GET    /apps/{id}/runs
GET    /apps/{id}/deploys
GET    /apps/{id}/notes
GET    /apps/{id}/agents
```

Each collection is also addressable flat with a filter
(`GET /tickets?app=<id>`), because "everything across every app" is a real
question and a nested-only API cannot answer it.

`{id}` is the surrogate, with slug resolution at the edge, so a rename is a
`PATCH` and not a migration.

---

## 6. No app-specific code in the platform. Ever.

The operator stated this as a blanket rule and it is adopted as one.

**Rule.** No platform source file may name a specific app. Not a registry entry,
not an `if app_id ==`, not an icon import, not a status probe, not a route, not a
persona, not a test fixture, not a permission string in a mobile manifest.

An app declares what it is; the platform reads the declaration. Anything the
platform needs to know about an app is a field on the app record or in its
manifest, never a branch in platform code.

### 6.1 What violates it today

- **Two enumerating registries.** `backend/services/app_registry.py:497`
  (`_CATALOG_FACTORIES`, 8 ids + 8 factories + app-specific startup loops) and
  `frontend/src/apps/registry.tsx:148` (the same 8, with icons, groups, lazy page
  imports and three status probes that fetch `/api/finance/cache`,
  `/api/birds/summary`, `/api/location/latest`). The frontend one also imports
  `BirdsShell` and `RecorderProvider` **eagerly**.
- **Special cases inside generic code.** `stores.py` ships a `FinanceAdapter`
  and declares the notes vault unconditionally; `permissions.py` holds
  `NOTES_VAULT_ROOT`; `semantic_index.collect_vault()` imports the notes app
  directly; `routes/events.py` hardcodes `VERIFIERS = {"plaid": ..., "github": ...}`;
  `event_dispatch.py` has a literal `if source == "plaid"`; `App.tsx` carries a
  commute-only redirect; `agentTriggers.ts` and `AutonomyPanel.tsx` each hold a
  duplicate hardcoded event-source list.
- **The operator's own content, tracked in git.** `apps/birds/**` (his Pi rig and
  public site), `agents/apps/*.md`, `backend/mcp/plaid/**`,
  `infra/cron/workshop.crontab:37-55` (**his actual commute times and home/work
  windows**), and `mobile/app.json:14-34`, whose iOS permission strings ship to
  the App Store reading "Workshop uses your location for the Location and Commute
  apps".
- **The guards themselves.** `check_pluggable_apps.sh:41,81` hardcodes an app
  roster to check for app hardcoding, and `security_audit.sh:82-152` is a
  finance-specific audit in the platform.

### 6.2 The gate

A ratchet, in the shape this repo already uses (`check_one_definition.py`,
`check_no_raw_paths.py`): `scripts/check_no_app_names.py`, wired into
`reviewer.sh`, failing when a platform source file names a known app. The app
list comes from the app table, so **the gate does not hardcode a roster** — which
is the mistake the current guard makes. Pin to the measured baseline, ratchet to
zero as extraction proceeds, and prove it catches a planted regression.

Without the gate this decays, because it already has once.

---

## 7. Shipped in this pass

Small, and deliberately only what needed no decision.

1. **The page column no longer slides when you switch place.** Raised repeatedly
   across the design sessions. The rule was right in `selectPlace`, but the flag
   was cleared in a `useEffect`, which runs *after* the browser has painted the
   frame it was meant to suppress: navigating from a place whose column is open
   (Home) to one whose column is closed (any app) rendered once with
   `is-animating` set and the width going 240px to 0, which starts the CSS
   transition. Now a pure value in `shellState.ts` (`grantMotion`/`settleMotion`),
   read during render, so there is no frame in which the class is wrong. Five
   tests, including the case a bare path comparison gets wrong: toggle at A,
   leave, come back to A, must not animate.
2. **Tickets is the only user-facing noun**, per ADR 0028. `/tickets` is the
   route, `/work` redirects **keeping its query string**, and every "Work"-as-a-
   ticket and "Tasks" string is gone. A ticket record now keeps the Tickets row
   lit, which was impossible while the list and the record had different
   prefixes.

---

## 8. Deferred, with the reason, so none of it is quietly dropped

- **Dashboard.** Operator: "that's something we should get into later, it's a lot
  of work on its own."
- **The ref / branch pill.** Operator: "don't build it, but don't forget about
  it, it'll require its own discussion about branches and worktrees."
- **The file browser and editor.** Later, and explicitly not hand-built: an
  existing package.
- **Search.** Deferred by the operator this pass. `services/semantic_index.py` is
  a complete working hybrid search; the palette is a thin wrapper over it.
- **App icons.** Operator: an app's icon "should be something the user or the
  agent can set for any app, and if it's already set it should show". So: a field
  on the app record, settable by either, with a letter fallback. Not designed
  here. What matters is only that it is **data, never an import** — today it is
  `import { Bird } from "lucide-react"` in platform source.
- **Per-app Files and per-app Memory-as-a-directory**, which land with the file
  work.

---

## 9. Plan

Ordered so each stage is shippable and none wires a dead control.

**A. Model and schema.** The `app` table with a surrogate key; `app_id` on
chats, tickets, runs, deploys, agents; merge the two registries with the five
colliding slugs resolved to one row each and conservation counts reported. Scope
becomes derived. Nothing moves in the UI.

**B. The REST surface.** `/apps` and its nested collections, plus flat
equivalents with an `app` filter. Retire the colon-verbs and `POST /stores/{id}/read`.
Give chats the scope-filtered listing they have never had, and delete the
client-side compensation in `Memory.tsx`.

**C. The app page column becomes real.** Chats, Tickets, Memory, Agents, Deploys
per app, each row backed by a stage-B endpoint. This is the operator's "you never
show the agents, tickets, deploys, memory or chats", and it is the first stage
where it is honest to render them.

**D. The purity gate.** `check_no_app_names.py` at the measured baseline, wired
into `reviewer.sh`. Do this BEFORE extraction so extraction is measured, not
asserted.

**E. Extraction.** Move the operator's apps out of platform source. Kill the two
enumerating registries, hoist the special cases onto the app record, remove his
content from git (including the crontab commute windows and the mobile permission
strings), and de-hardcode the guards. Ratchet the gate to zero.

**F. The identifier rename**, `task` -> `ticket`, in one migration. Deferred from
ADR 0028 on purpose: it touches the mobile client, `scripts/project_tasks.py` and
a generated OpenAPI file. The Postgres tables already say `ticket`, so it is
half-done from the storage end.

Stage E has a real decision in it, and it is the operator's: see §10.

---

## 10. The one open question

**How do the operator's apps leave the platform repo?**

Three options, with the tradeoff that actually distinguishes them:

- **Separate repos, one per app.** Cleanest end state and the one the model
  implies (an app owns a repo). Costs the most: seven repos to create, and the
  in-shell mount stays blocked until dynamic bundle injection exists, because an
  app's React pages are still compiled into the SPA.
- **One untracked `apps/` tree in this checkout.** Cheapest, immediately removes
  every app name from the tracked platform, keeps the current dev loop intact.
  But an untracked working tree has no history and no backup, which for the birds
  Pi rig means losing real provenance.
- **A private overlay repo** mounted into `apps/`, tracked separately. Middle
  cost, keeps history, one extra clone in the bootstrap.

Recommendation: **the overlay**, then split individual apps out of it later as
each earns its own repo. It gets the platform to zero app names in one move,
which is the actual requirement, without paying for seven repos or losing the
birds rig's history.

Either way, **dynamic frontend bundle injection is the real blocker** for an app
being fully external, and it should be scoped as its own piece of work rather
than assumed along the way.
