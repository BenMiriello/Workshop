# Workshop platform redesign: core object model, data primitives, and surfaces

Status: proposal, ACCEPTED with locked decisions 2026-07-12. Authoritative build sequence: `implementation-plan.md`.
Companion doc: `task-management-exploration.md` (separate effort, not part of this implementation round)

## 0. Locked decisions (operator, 2026-07-12)

These override anything below that conflicts. `implementation-plan.md` is written to these.

1. **Naming: "Job", not "Run".** The bounded-execution binding object is a **Job**. "Store" stays. (Every "Run" in the sections below means "Job".)
2. **No chat-to-project promotion, at all.** The entire "chat then promote to project/builder" pattern is discarded, not just the blank builder. Chat and Project are independent, loosely-coupled nouns. A chat carries an optional context binding (general / project / app / agent). Creating a project is its own direct action. There is no conversion pipeline, no "promote" gesture, no blank-builder. Section 3 is superseded by this.
3. **Letta is on the way out, sequenced.** One Agent concept means one runtime as the end state. The concept is unified NOW (one registry, one surface, `runtime` as a field, no Assistants/Builders split). But the codebase inventory shows Letta is currently load-bearing for four entangled things: the knowledge semantic-search index (`knowledge.py`), the always-on heartbeat runtime (`supervisor.py`), global operator-context memory, and the the bridge agent per-channel Discord agents (`per_channel_agents.py`). Ripping it out in the same pass would destabilize the chat bridge (a hard no-derail area). So Letta stays as the memory engine behind the assistant runtime through this migration, and its full removal (Store-backed memory of type `Memory` + promoting the plaintext knowledge index to primary) is the dedicated next phase, tested against the bridge agent in isolation. Keep if useful now, remove cleanly next.
4. **No CRDTs by default.** Portable plain formats (files / JSON / SQLite) plus a mandatory export contract. A CRDT is used only if a specific Store genuinely needs multi-writer merge, chosen at that point. Do not overbuild interop.
5. **Agent definitions are plaintext/markdown** (frontmatter + body).
6. **No free/paid line is decided.** The business posture (Section 8) stays as loose design principles only. Nothing is committed or stated publicly.
7. **Interactive chat turns are not Jobs.** A Job is triggered or detached work. A synchronous chat turn is just a message.

## 1. Framing

Workshop has grown a working platform with an incoherent conceptual core. The symptoms, all live in the tree today:

- Two agent ontologies (Letta-backed assistants in `routes/agents.py`, Claude Code builders in `services/build_agents.py`) despite a locked "one Agent concept" goal.
- Two autonomy vocabularies: a 4-value enum on agents (off, scheduled, event-driven, continuous) and a 3-value enum on projects (off, manual, continuous), driven by different scripts.
- Two competing blank-project entry paths (`createBlankBuilder` and the `new-project` context provider), both built around a "build with chat" pipeline the operator has flagged as ill-conceived.
- Settings and config scattered across four homes with no model for which surface owns what.
- An Activity page that observes plumbing (runs, events, services) rather than any first-class concept.
- Cross-app data sharing that exists in skeleton form (grants.json, permissions.py, apps.json provides/needs) but no data primitive underneath it, which is why "should notes be built in?" has no answer yet.

Design goals for the redesign, in priority order:

1. **A small, closed set of nouns.** Every feature should be expressible as a relationship between them. If a feature needs a new noun, that is a design event, not a routine change.
2. **Portable, local-first.** The user's code and data outlive Workshop, in open formats, with a tested exit. The hub earns its keep through coordination, not custody.
3. **No forced domain concepts.** The platform ships no built-in Notes, no built-in anything at the domain layer. Apps own data; the platform owns the sharing mechanics.
4. **Single-user now, commercial later.** The capability model is designed so it does not need to be rebuilt for multi-tenancy, but nothing multi-tenant is built now. No allowlists, no audit journals, no confirmation gates.
5. **Deterministic contracts by default, agents only where judgment is the point.**

Hosting, versioning, and deploy of projects are treated as a seam (Section 8) and deliberately not designed here.

## 2. The core object model

Eight nouns. Everything else is a view or a relationship.

| Noun | One-line definition | Lives where |
|---|---|---|
| **Chat** | A message thread. Agent-optional. Never the home of durable state. | `.runtime/users/<owner>/chat/` |
| **Agent** | A named, declarative configuration: persona, runtime, tools, triggers, limits. Not a conversation, not a process. | On-disk definition files (user scope or project scope) |
| **Run** | One bounded execution of an Agent: the binding object carrying `agent_id`, optional `project_id`, optional `chat_id`, trigger provenance, lifecycle state, budget. | Run ledger (evolves `agent-runs.jsonl`) |
| **Project** | A git working tree under construction: source, knowledge, tasks, project config. The home of durable maker state. | `projects/<slug>/` + `projects.json` |
| **App** | Installed, running software: mounted routers, launcher card, provider interfaces, app settings. Built as a Project, deployed via the Runner. | `app_registry.py` catalog + `installed-apps.json` |
| **Store** | An app-owned, URI-addressed container of data with a type descriptor and an export contract. The platform never interprets its contents. | Owned by the app, registered with the hub |
| **Grant** | A scoped capability: (store URI or subtree) x (verb set) x (caveats), held by an app or agent, attenuable, revocable. | Evolves `grants.json` + `permissions.py` |
| **Task** | A durable work item scoped to a Project. Design deferred to the companion doc. | Per-project task store |

### Key decisions and rationale

**Agent is config, not conversation.** This is the pattern the platform layer has converged on everywhere (OpenAI Assistant/Thread/Run, Claude Code subagent definitions). An agent definition is a declarative file you can read, diff, version, and copy between machines. Conversations, schedules, and events are all just ways of instantiating it. This also makes agents portable artifacts: an agent definition inside a project ships with the project.

**Run is the binding object, and scope lives on the Run, never on the Agent.** This resolves the "should an agent be scoped 1:1 to a project?" question by dissolving it. An Agent definition has no `project_id`. A Run does. "This project has a dedicated agent" becomes a convention (an agent definition stored inside the project tree, whose runs default to binding there), not a schema constraint. "One agent working across many projects" is just many Runs with different `project_id`s. The cost we accept: Run is a new object with a lifecycle (`queued | running | awaiting_input | done | failed | cancelled`) that must be built and observed, and the question "which project context did this scheduled run see?" now has to be answered explicitly on every Run record. That is a feature: today the answer is implicit and unrecoverable.

**Chat stands alone by being agent-optional.** A chat with no `agent_id` is the general assistant (today's `general` provider). Attaching an agent changes who answers. "Calling in an agent" from a chat means creating a Run that references the current `chat_id`; the agent works detached and results reattach to the thread. The chat is one input channel among several, not the container of the work.

**Triggers are peers of chat, not children.** An Agent definition carries zero or more triggers: `manual` (chat or on-call), `cron`, `event` (webhook, file-watch, platform event), and `loop` (continuous, with budget and completion gate; the Ralph pattern). Every trigger firing creates a Run. A chat message that dispatches work is just one more trigger type. This single vocabulary replaces both existing autonomy enums (Section 4).

**Durable state lives in the Project or a Store, never in the Chat.** The Replit lesson: checkpoints, plans, and decisions belong to the project; a new chat re-derives context from files and knowledge scopes. Chats are transcripts. The existing knowledge-scope and compaction/distillation machinery already points this way; the redesign makes it the rule.

### Relationship diagram

```
                 trigger fires
  Chat ----+     (cron/event/manual/loop)
 (agent-   |            |
  optional)|            v
           +-------->  RUN  <-- binds --> Agent (declarative config,
                        |                  user-scope or project-scope,
        chat_id? -------+                  owns triggers + limits)
        project_id? ----+
                        |
                        v
                    Project (git tree: source, knowledge,
                        |    Tasks, project config)
                        |
                 deploy via Runner
                        |
                        v
                      App --owns--> Store(s) (typed, URI-addressed,
                        |                     exportable)
                        +--holds--> Grants ---> other apps' / agents'
                                               Stores (scoped verbs)
```

## 3. Chat, and the death of the blank-project builder

The current "start with chat" flow hardwires a single narrow outcome (a freestanding blank builder agent) into the act of starting a conversation. That inverts the natural order. The redesign:

**Any chat stands alone.** Starting a chat requires no destination decision. General chats, app chats, project chats, and agent chats are all the same object with different context keys; the composer and thread store are already unified, so this is mostly a framing change plus discovery.

**Chats are discoverable.** A Chats surface lists and searches all threads across contexts (title, context, agent, recency). Today threads exist on disk per context key but there is no cross-context browse. This is a straightforward index over `chat_store`.

**Project creation from a chat is an explicit promotion gesture.** Any chat gets a "Create project from this chat" action. Promotion does three things: scaffolds a project from a template (the existing `projects/_templates` machinery), runs the existing distillation path to seed `projects/<slug>/knowledge/` and PROJECT.md from the conversation, and links the chat to the project (`context_key` migrates to `project:<slug>`, thread history preserved). Nothing about the chat changes until the user promotes. This mirrors the ephemeral-to-durable promotion principle in the task doc: conversation is working memory, promotion is the act of making something durable.

**Remove both blank-project paths.**
- Delete `lib/blankBuilder.ts` / `createBlankBuilder()` / `POST /api/agents/build/blank` / `build_agents.create_blank()`. The "Blank project N" freestanding builder is replaced by: chat first, promote when ready, and the project's default agent picks up from the seeded knowledge.
- Delete the `new-project` context provider (`NewProjectContextProvider`). Its planning role is subsumed by any general chat plus the promotion gesture.
- `ProjectNew.tsx` keeps a direct "create empty project" form for people who know what they want; the chat path is no longer a special pipeline, just a chat.

## 4. Agents, autonomy, and delegation

### One Agent concept

Assistant and Builder collapse into one Agent definition with a `runtime` field:

```yaml
# projects/<slug>/agents/reviewer.md  (or ~/workshop/agents/<slug>.md for user scope)
name: reviewer
runtime: builder            # builder = Claude Code execution; assistant = memory-backed conversational
purpose: >
  Reviews diffs on this project against acceptance criteria.
capabilities:               # grant references, not ambient authority
  - store: workshop://store/<app>/...  verbs: [read]
triggers:
  - type: manual
  - type: cron
    expr: "0 6 * * *"
    binds: { project: this }
limits:
  max_iterations: 20
  budget: default
memory: letta               # assistant runtime only; builder runtime uses project knowledge + session
```

Two runtimes remain two execution paths internally (Letta-backed conversational loop vs `claude -p` agentic loop), but there is one registry, one definition format, one config surface, and one Runs ledger. The definition file format follows the Claude Code subagent model (markdown with frontmatter): human-readable, diffable, and it gives user-scope vs project-scope for free by storage location. Project-scoped agent definitions travel with the project when it is forked, ejected, or published.

### One autonomy vocabulary

Both existing enums are deleted. Autonomy is not a mode an agent or project is "in"; it is the set of triggers attached to agent definitions, plus the limits on each trigger. Concretely:

- Agent `autonomy_mode: off` becomes "no triggers except manual".
- Agent `scheduled` / `event-driven` become cron / event triggers.
- Agent `continuous` and project `continuous` become a `loop` trigger with `max_iterations`, a budget, and a completion gate (the Ralph loop, now expressed in the same vocabulary as everything else).
- Project autonomy (`routes/projects.py` autonomy endpoints, `AutonomyPanel.tsx`, `project_ralph.sh` wiring) becomes a view: "the triggers, across all agent definitions, whose runs bind to this project". The panel stays, the separate vocabulary and storage go.

Iteration caps and budgets on loop triggers are correctness and cost bounds on an unattended process, not multi-user guardrails. They stay.

### Delegation: the rule

From the handoff research, adopted as a platform rule:

**Contracts in the middle, agents at the edges.** When component A needs a capability of component B, the default is a typed, deterministic interface: an MCP tool or plain API exposed by B, called with a scoped grant. This is roughly 1x cost, reproducible, securable, and debuggable. Agent-to-agent handoff (creating a Run for another agent and transferring control) is the expensive exception, justified only when all three hold:

1. The sub-problem decomposition cannot be enumerated in advance (judgment is the point).
2. The work is genuinely parallelizable or requires a context window the caller cannot spare.
3. The delegate contributes intelligence, not unsupervised writes. All side-effecting writes stay single-threaded through one owner (the Devin/Cognition rule).

Multi-agent research systems pay 10 to 15x tokens and lose reproducibility; most coding tasks are explicitly a poor fit. So: no free-floating agent negotiation, no A2A inside a single Workshop instance. A2A-style protocols only ever matter at a real trust boundary between independently operated systems, which is a future concern. When delegation is justified, it is expressed as the coordinator creating Runs (pattern: orchestrator plus workers, each worker a bounded Run with an attenuated grant set), never as peers discovering each other.

### The Agents surface, redesigned

The current Agents page (segmented All/Assistants/Builders, separate config pages, autonomy scattered) is replaced by:

- **Roster**: all agent definitions, user-scope and project-scope, one card each: runtime, purpose, trigger summary ("cron daily, on-call"), last run, next scheduled run. No assistant/builder segmentation as the primary axis; runtime is a badge.
- **Agent detail**: the definition (editable), its triggers and limits, its grant set, and its Run history filtered to this agent. This replaces both `AgentConfig.tsx` and the config half of `AgentBuild.tsx`.
- **Run detail**: one Run's transcript, evidence, binding (which project, which chat, which trigger), and outcome. Linked from everywhere a Run is mentioned.

Autonomy is set in exactly one place: the trigger list on the agent definition (with the project panel as a filtered view of the same records).

## 5. Data ownership: the Store and Grant model

This is the root primitive the operator intuited: some apps own data and choose which apps and agents can access it. The research names it: object-capability security, with Android's Storage Access Framework as the closest mainstream template. The primitive set:

1. **Store**: an opaque, app-owned container of bytes or records, addressed by URI (`workshop://store/<app>/<name>`, subtree-addressable). The platform never interprets domain meaning. Backed by a portable, self-describing on-disk format (plain files, JSON, SQLite; CRDT where multi-writer merge is actually needed) so the data survives the app.
2. **Type descriptor**: a declared `@type` on a Store (schema.org-style, e.g. `Note`, `Transaction`, `Detection`). Consumers discover stores by kind, not by app name. Untyped stores are allowed; a shared ontology is never a prerequisite for storing data (the Solid failure to avoid).
3. **Provider interface**: the typed operation surface the owning app exposes over a Store: MCP-style Resources for reads, Tools for writes, JSON-Schema'd. This is the only sharing boundary; no other app ever touches the raw files.
4. **Grant**: an unforgeable capability record: (store URI or subtree) x (verb set: read, write, append) x (caveats: expiry, field or row filter). Attenuable (narrow before handing on) and revocable (delete the record, no ACL walk).
5. **Grant broker / picker**: user-mediated minting. When an app or agent needs access to "a Store of type Note", the broker presents the user's matching stores and the user picks; the grant is scoped to exactly what was picked. Authority originates from the user, not from one app asking another.
6. **Export contract**: every provider must implement a full portable dump of each Store in its self-describing format. First-class operation, part of the provider manifest, tested.

### How this kills built-in Notes

Workshop ships no Notes app and no notes table. "Notes capability" means: any app may declare a Store of type `Note` and a provider interface over it. The user can install one notes app, three, or none. An agent that needs notes asks for "read+append on a Store of type Note"; the broker mediates; the agent receives a task-scoped grant bound to the specific store the user picked. Multiple notes apps coexist because consumers bind to the type and the grant, never to an app identity. The same pattern covers every would-be built-in: tasks-adjacent data, bookmarks, journals, media libraries.

### Agents are ordinary capability holders

An agent never has ambient authority. Its Run receives the grant set from its definition plus anything attenuated down from the caller. The grant token is bound to each tool invocation so the provider checks the capability, not the caller's identity; this structurally defeats the confused-deputy problem that plain MCP wiring has today. Sub-runs get attenuated copies.

### Evolution, not rewrite

This extends what exists:
- `apps.json` provides/needs becomes the Store registry plus type descriptors (an app's "provides" entries become declared Stores with types; "needs" become grant requests resolved through the broker).
- `grants.json` + `permissions.py` already model directional default-deny grants; they gain resource URIs, verb sets, and caveats instead of app-level capability strings. `capability_for()` becomes the check point that validates a grant token per invocation.
- Per-app MCP servers already exist as the provider surface; they get formalized as the provider interface with export as a required operation.
- Knowledge scopes (`knowledge.py`) become Stores owned by the platform's knowledge app, accessed by the same grant mechanics, closing the "knowledge.read/write grants" TODO.

### Why this is not defensive cruft

The no-cruft rule bans multi-user guardrails on a single-user system. The Store/Grant model is not that: it is the interop mechanism itself. Even with one human, the system has many mutually untrusting principals: apps written at different times, agents running unattended on cron, sub-runs spawned with partial context. Grants are how a birds app shares detections with a dashboard without handing it Plaid data, and how an unattended loop run is structurally unable to touch stores it was never granted. It is the same reason `permissions.py` already exists. What stays banned: per-action confirmation prompts, audit journals for their own sake, and any UI friction that assumes an adversarial second user.

## 6. Interop philosophy: contracts in the middle, agents at the edges

The decision framework, stated once and applied everywhere:

| Situation | Mechanism |
|---|---|
| App A needs a capability of app B (query, mutation, computation) | Typed provider interface (MCP tool / API) called with a scoped grant. Always. |
| A chat or agent needs data an app owns | Grant via broker, then deterministic reads/writes through the provider interface. |
| An agent needs judgment applied to an open-ended sub-problem it cannot enumerate | Orchestrator creates a bounded worker Run with attenuated grants; writes single-threaded through one owner. |
| Two independently operated systems (real org boundary) | Out of scope now; the seam where A2A-class protocols would live. |

The hub's role in interop is broker and registry, not message bus: it holds the Store registry, mints grants, and gives consumers type-based discovery. Apps then talk through their provider interfaces directly. Workshop coordinating interop must never become Workshop intermediating every call; that would be both a performance tax and a lock-in vector.

### The media-creation app example, answered

The operator's example: an app that teaches itself and teaches workflows for AI media creation, with different agents in different roles, and the question of whether Workshop should provide an orchestrator template the app can fully own and spin off.

Yes, and the object model makes it cheap. Workshop provides an **orchestrator template** as a project template: one coordinator agent definition plus N role agent definitions (all declarative files in the project tree), a manifest of the typed capabilities the roles expose to each other, and the loop/trigger wiring. Because agent definitions are project-scoped files and capabilities are typed contracts, the whole assembly is owned by the project: fork it, eject it, run it standalone. Outside Workshop it loses the broker, the Run ledger UI, and hosted scheduling, and it stubs the hub API (Section 8), but the orchestration logic and agent definitions are portable artifacts. Inside the app, the roles interoperate the platform way: the coordinator delegates open-ended creative sub-problems as Runs, while every concrete operation (render, edit, file write, publish) is a typed tool. No agent-to-agent negotiation.

This template is also a business asset: it is exactly the kind of thing a marketplace distributes later, and it demonstrates the portability promise concretely.

## 7. Top-level UI, pages, and surfaces

The proposed surface set:

- **Home**: the hub. Recent chats, active and recent Runs, pinned apps and projects, items needing attention (a Run in `awaiting_input`, a task in review). Workshop's identity as a workspace you operate from, not a menu.
- **Chats**: the discoverable thread list (Section 3). Search, filter by context/agent, promote-to-project action.
- **Projects**: maker surface. List plus project detail. Project detail is where **project config** lives: state, sensitivity, project-scoped agent definitions and their triggers (the autonomy view), grants the project's agents hold, tasks (per the companion doc), knowledge, and the deploy seam.
- **Apps**: the launcher (existing). Each app carries its own **app settings** surface inside the app: anything a user needs, defined by the app, even when the maker is the user. The platform provides a standard settings slot in the app frame so apps do not hand-roll placement, consistent with the shared-header rule.
- **Agents**: roster, agent detail, run detail (Section 4).
- **Settings** (platform/user only): account, appearance, devices, integration credentials, installed-app management, the grants overview (the broker's management view; day-to-day grant minting happens contextually via the picker), and a System panel.

**Activity is retired as a top-level concept.** Its three tabs disperse to where they are now coherent:
- Runs becomes the global Runs view under Agents (and filtered views on project detail and agent detail). The Run object finally gives this page a first-class subject.
- Events and Services move to a System panel under Settings: platform plumbing status, not daily-driver material.

**The settings split, stated as a rule:** project config is the developer/maker surface and lives on project detail; app settings are anything a user of the running app needs and live in the app; platform settings are about the user and the instance and live in Settings. A datum that seems to belong in two places is a sign the noun is wrong; escalate rather than duplicate.

## 8. Portability and business posture

Design constraints, not a business plan. Drawn from the local-first and open-core research and stated here so every implementation decision can be checked against them:

1. **Code and data are the user's, in formats that outlive Workshop.** Plain files, JSON, SQLite; no feature may require data to live only in a Workshop-proprietary store. The Logseq DB-format backlash is the cautionary tale.
2. **Apps run standalone, degraded not dead.** An app built in Workshop boots and does its core job with no hub. The hub adds sync, cross-app grants, scheduling, hosted inference.
3. **One documented, versioned app-to-hub API.** The small coordination surface an app calls to get more powerful inside Workshop (broker, knowledge, model access, run dispatch). Versioned so an ejected app can stub or re-point it. This is the Tailscale control-plane/data-plane split applied to Workshop.
4. **Eject is reversible, and export is mandatory.** "Eject to standalone repo" produces a runnable repo that still speaks the hub API, so leave, run standalone, re-attach are all possible. Every Store has a tested export. Exit is a feature with tests, not a compliance checkbox.
5. **The moat is coordination, never custody.** Revenue comes from the operated hub (sync, remote access, one-click deploy, backups), hosted inference as the zero-config default, and later a marketplace. Never from gatekeeping the user's code or data.
6. **BYOK is first-class.** Hosted inference is the frictionless default; BYOK users pay a smaller hub subscription for coordination services. Both paths are honest revenue; neither is punished (the anti-Cursor stance, and the trust asset).
7. **Draw the free/paid line once, publicly, and never move it inward.** Free: full local single-user Workshop, unlimited apps, BYOK. Paid: hosted inference credits, or the hub subscription, later team features. The relicensing wave (Grafana, Sentry, Directus) shows the reputational cost of moving the line after users commit.

Hosting, versioning, releases, and deploy of projects remain a seam: the Runner and marketplace registry exist, the hub API reserves the surface, and the deep design happens in a later round. Multi-tenant commercial architecture is likewise a deliberately sequenced future phase (open-core, then multi-tenant cloud, then marketplace); nothing in this redesign builds for it early except the grant model, which is needed regardless.

## 9. Migration and phasing

Sequenced to unify concepts before adding machinery, and to delete before building where possible.

**Phase 1: Run object and one autonomy vocabulary.**
Introduce the Run record and ledger (migrate `agent-runs.jsonl`), with lifecycle states and explicit bindings. Re-express both autonomy enums as triggers on definitions; delete the project-autonomy storage and the agent `autonomy_mode` field; rewire `project_ralph.sh` / `cron_agent.sh` dispatch to create Runs from loop and cron triggers. `AutonomyPanel` becomes the filtered trigger view. This phase touches the most duplicated logic and unblocks everything else.

**Phase 2: One Agent registry.**
Declarative agent definition files (markdown plus frontmatter), one registry replacing `build_agents.json` plus Letta-side config; `runtime` field selects the execution path. Migrate existing assistants and builders. Rebuild the Agents surface (roster, agent detail, run detail).

**Phase 3: Chat discovery and promotion; delete the blank-builder.**
Chats surface with cross-context browse and search. Promote-to-project gesture (scaffold, distill, relink). Remove `createBlankBuilder`, `POST /agents/build/blank`, `create_blank()`, and the `new-project` provider.

**Phase 4: Store and Grant evolution.**
Store registry from apps.json provides/needs; type descriptors; grants gain URIs, verbs, caveats; broker/picker UI; grant tokens checked per invocation in `capability_for()`; export contract required in provider manifests; knowledge scopes migrate onto Stores.

**Phase 5: Surfaces.**
Home hub. Activity retired (Runs to Agents, Events/Services to Settings > System). Settings split enforced; app-settings slot in the app frame.

**Explicitly deferred:** task-management implementation (companion doc, own effort), hosting/versioning/deploy design, anything multi-tenant or commercial, marketplace mechanics.

Each phase is independently shippable and leaves the system coherent; Phases 1 and 2 are the load-bearing unifications and should not be split further or interleaved with feature work.

## 10. Open questions for the operator

1. **Are interactive chat turns Runs?** Recommendation: no. A Run is detached or triggered work; a synchronous chat turn is just a message. This keeps the ledger meaningful and cheap. But it means chat turns and Runs are observed differently; confirm you accept that split.
2. **Letta's future.** One Agent concept still leaves two runtimes. Does the assistant runtime stay Letta-backed long-term, or converge on Claude Code sessions plus knowledge-scope memory? Recommendation: keep Letta for now (it works, and memory quality matters), revisit after Phase 2 when both runtimes share one definition format and the switching cost is visible.
3. **CRDT commitment.** Adopt portable formats plus mandatory export now, CRDTs only where a Store genuinely needs multi-writer merge (recommendation), or standardize on Automerge-class storage from the start? The latter is a bigger bet with real DX cost.
4. **Agent definition format.** Markdown with frontmatter (recommended, matches Claude Code, human-first) vs JSON registry entries (machine-first, easier validation). Pick one; supporting both is worse than either.
5. **Promotion mechanics.** When a chat is promoted, does the thread move into the project context or does the project link back to a chat that stays general? Recommendation: move it (one home per thread), but this changes URLs and habits.
6. **The free/paid line wording.** Section 8 point 7 proposes the line. Since the principle is "state it once, never move it inward", the exact wording deserves your sign-off before it appears anywhere public.
7. **Naming.** "Run" vs "Session" vs "Job"; "Store" vs "Source" vs "Collection". Naming is cheap now and expensive later; flag any of these that read wrong.

## References

- Grounding report on the current tree (docs/workshop-v1/, docs/apps-are-pluggable.md, docs/agent-chat-redesign/plan.md, docs/auth-redesign/)
- Anthropic, Building Effective Agents; Anthropic multi-agent research system postmortem
- Cognition, Don't Build Multi-Agents; MAST failure taxonomy (arXiv 2503.13657)
- Ink & Switch, Local-first software; Solid project retrospectives
- Android Storage Access Framework and content-provider permission model; MCP specification
- OpenAI Assistants API object model; Claude Code subagent definitions; Linear agent/delegate model
- Open-core and pricing prior art: Obsidian, Nabu Casa/Home Assistant, Tailscale/Headscale, Vercel, Supabase, GitLab, n8n, Cursor
