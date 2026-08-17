# Ontology review: Chat, Agent, Ticket, Run, Memory (2026-08-17)

- Status: **proposal.** Nothing built. Supersedes no doc on its own authority; where it
  contradicts `entity-model-2026-07-18.md` or `agent-interaction-model-2026-07-31.md`
  the contradiction is called out inline and the operator decides.
- Method: read the live tree first, then researched twelve comparable systems, then
  reasoned. Every claim about current behaviour carries a `file:line` and was verified
  on 2026-08-17, not recalled.

---

## 0. The proposal in one page

**Three of the five top-level nouns sit at the wrong level of abstraction, and one of
them does not exist.**

1. **Run is not a peer of Chat. It is a turn inside one.** Workshop already has the
   correct implementation of a turn — `chat_store.begin_run/update_run/commit_run`
   (`backend/services/chat_store.py:994`) — and separately has a *second, degenerate*
   thing also called a run (`backend/services/jobs.py:52`) that is a fire-and-forget
   OS process with a log file instead of a transcript. The word covers both. That is
   the whole muddle, and it is a naming collision inside one repo, not a deep
   modelling problem.

2. **Chat and "agent session" are one object.** The instinct recorded on 2026-07-31 —
   *"it starts to look like just the chat again"* — is the answer, not a warning. Every
   platform that built both converged on a single durable container (LangGraph
   *Thread*, OpenAI *Conversation*, Linear *AgentSession*, A2A *contextId*, ACP
   *Session*). What distinguishes a chat from a background session is **one field: who
   holds the turn.** Waiting on you → it reads as a chat. Working → it reads as a
   session. Same table, same renderer, same search index.

3. **Memory is not a noun at this level.** It is three tiers with three different write
   paths, and Workshop currently has **six substrates** implementing them with no
   stated relationship between any two.

4. **The missing concept is the Thread's own state machine.** Ticket has one. Run has
   one. The conversation has none — which is precisely why an agent that can ask a
   question has nowhere to be *waiting*, and why the inbox had to be invented beside
   the chat rather than derived from it.

### The proposed spine

```
Workspace  (the installation)
 └── Container            ── Project | App | workshop | development
      ├── Ticket          ── durable intent. The unit of approval.
      ├── Thread          ── durable transcript. A chat OR an agent's work log.
      │    └── Turn       ── one execution of one agent over this thread. INTERNAL.
      ├── Note            ── curated, always-in-context knowledge
      ├── Artifact        ── a produced thing (diff, PR, deploy, report)
      └── Store           ── a named data surface with a tier and a host

Agent      ── workspace-level declaration, granted into Containers
Trigger    ── (schedule | event) → opens a Thread
Inbox      ── DERIVED VIEW: every Thread awaiting you. Not a table.
```

Five deletions, one addition:

| Change | Effect |
|---|---|
| **Delete Run** as a user-facing noun | A ticket attempt becomes a Thread anchored to that ticket. `/runs` becomes a filter over Threads, not a separate page. |
| **Delete `participants`** | It has no behaviour and one live button lies because of it (§2.3). Replaced by `responder` + mention routing. |
| **Delete `role:` as a scope kind** | A role is not a place. It is a memory *layer selector*. Same behaviour, honest name. |
| **Delete `build:` as a context kind** | It is the last load-bearing remnant of the retired builder/assistant axis. |
| **Delete the free-standing Inbox store** | Derive it from Thread state. One state machine, not two. |
| **Add `Thread.state`** | `idle \| working \| awaiting \| done \| failed`. The one new field that makes the model click. |

Everything else in `entity-model-2026-07-18.md` survives intact: Ticket, Scope, Grant,
Store, Latitude, Autonomy, the five roles. This is not a rewrite. It is a collapse of
three overlapping conversation-shaped things into the one that already works.

---

## 1. Why "run" is the word that broke

The concept is doing three unrelated jobs at once. Once separated, no user-facing noun
called "run" survives:

| Job | Where it lives today | Prior art | Where it should go |
|---|---|---|---|
| One execution of the agent loop: input → tools → output | `chat_store.begin_run` | LangGraph *Run*, OpenAI *Response*, ACP *prompt turn* | **Turn.** Internal. Already correct. |
| One autonomous attempt at a ticket | `jobs.py` | Linear *AgentSession*, GH Copilot *session* | **Thread**, anchored to the ticket. It produces a transcript, so the transcript is the record. |
| Crash-safe resumable execution | nowhere | Temporal *Workflow Execution* | Infrastructure. Not ontology. See §6.3. |

The strongest external signal is that OpenAI **deleted** the object. The Assistants API
exposed `Assistant / Thread / Message / Run / Run Step`; it is being shut down on
2026-08-26 and the replacement keeps Conversations and Responses and drops Run entirely
— the polling loop, the `requires_action` state and the thread-locked-during-run
constraint were the stated costs. Linear never exposed a run at all: only sessions and
typed activities. A2A has a `Task` with a lifecycle but no run beneath it.

**The counter-signal, and it is real:** GitHub Actions, Airflow and Temporal all keep a
first-class run, and they are right to. The difference is that *their* runs are
executions of a **pre-authored definition** — a workflow file, a DAG. A run is
meaningful there because the definition is reusable and the run is the instance. A
Workshop ticket is not a reusable definition; it is one piece of intended work. So the
run/definition split buys nothing and costs a noun.

### What this changes concretely

- `Ticket has many Threads` replaces `Ticket has many Runs`. Attempt #2 opens a second
  thread on the same ticket. This matches A2A, where refinement after a terminal state
  starts a new Task under the same `contextId` rather than resurrecting the old one.
- `exec.attempts` / `max_attempts` / `claim_token` / `lease_until`
  (`backend/services/tasks.py:139`) stay exactly as they are. They are a lease, not a
  run record, and the lease is good.
- `jobs.jsonl` stays as the **execution ledger** — pid, exit status, wall clock. It
  stops being an entity and becomes telemetry hanging off a Thread. Nothing in
  `jobs.py:346 reconcile_stuck` needs to change.

---

## 2. Why chat and agent are already the same thing (with receipts)

### 2.1 The chat code already implements the correct model

`chat_store.begin_run(thread_id, run_id, model, agent)` opens a turn on a thread,
records `active_run_id`, streams a live draft, and commits. It takes an `agent`
parameter so a mention-turn is attributed while it streams
(`backend/services/chat_store.py:994-1030`). That is LangGraph's `Run(assistant,
thread)` exactly. It is correct and it should be the model for **all** agent
execution, not just chat.

### 2.2 The ticket path implements the degenerate version

`jobs.py` records `{id, trigger, status, started_at, pid, kind}` with
`STATUSES = queued|running|awaiting_input|done|failed|cancelled` (`jobs.py:52`) and a
`log_ref` pointing at a file. There is no thread, so there is nowhere for the operator
to type, nowhere for the agent to speak, and `awaiting_input` has no surface — which is
exactly the finding in `ticket-model-and-external-trackers-2026-08-07.md` §0: **zero of
179 tickets have ever carried an `awaiting` record.** The escalation path is unreachable
because the object it needs — a conversation the agent is blocked *in* — does not exist.

### 2.3 "Chat with this agent" currently has four entry points and three behaviours

This is the concrete form of the operator's confusion, and one of the four is wrong:

| Entry point | Where | What actually answers |
|---|---|---|
| `/agents/:slug/chat` | `AgentDetail.tsx:46` | `BuildAgentContextProvider` — the builder work pane, keyed on `build:<slug>` |
| "Start chat with agent" button | `AgentDetail.tsx:602` | **the general assistant.** It creates a `general`/`project:X` thread with `participants: ["agent:<slug>"]` — and `chat_context.get_provider` (`chat_context.py:1639`) never reads `participants`. The button is labelled with the agent's name and does not reach the agent. |
| `@mention` in any chat | `services/mentions.py` (shipped, `33016f6`) | that agent, correctly, for one turn |
| `/chat/agent:<slug>` (the fallback at `AgentDetail.tsx:620`) | `chat_context.py:1660` | that agent, via `AssistantAgentContextProvider` |

`participants` was audited on 2026-07-31 as having no behaviour anywhere. It still has
five mutators in `chat_store.py` (`:624-656`) and one button that depends on it. **This
is the duplication to delete first** — it is not a design question, it is a lie in the
UI.

### 2.4 The model that resolves it

A **Thread** has:

```
scope        the container it lives in
anchor       null | ticket:<id> | app:<name>     what it is about
responder    agent slug — who answers by default
state        idle | working | awaiting | done | failed
opened_by    operator | trigger | agent
```

Then, with no other machinery:

- **A chat** is a Thread with `anchor: null`, `responder: <the scope's assistant>`,
  `opened_by: operator`.
- **Chatting with an agent directly** is a Thread with `responder: <that agent>`. That
  is the operator's own preferred option (a) — and it is right, because it costs
  nothing: it is the same object with one field set.
- **An agent working a ticket** is a Thread with `anchor: ticket:<id>`,
  `responder: <the maker>`, `opened_by: trigger`. It starts in `working`.
- **Joining a running agent** is opening that thread and typing. There is no join
  ceremony to design. Your message becomes the next turn's input — which is precisely
  what Cursor and Copilot's agent modes do ("steer it with follow-up messages").
- **An agent asking you something** sets `state: awaiting` and posts an
  `elicitation` message. It does not need a separate inbox record.

**The UI names the thread by its state, not by its type.** Waiting on you → "Chat".
Working → "Session"/"Running". That single sentence is the mental model, and it is a
field rather than a type, so nothing has to be decided at creation time and nothing has
to be migrated when it changes.

### 2.5 Where I differ from the 07-31 model, and from the reviewing agent

Two disagreements worth stating, because both were argued the other way.

**(a) `Run` → `Session` (D11) is half the fix and the wrong half.** Renaming Run to
Session while keeping it a separate object from Chat leaves two message tables, two
renderers and two state machines. The rename is cosmetic; the collapse is the fix.
Recommend: **Thread** in the schema (LangGraph, OpenAI), **Turn** internally, and let
the UI say "chat" or "session" per `state`. "Session" is a poor schema name because it
means the container in Devin and ACP but the invocation in Linear — it is ambiguous
exactly where precision is needed.

**(b) One responder per *thread* is too strict — one responder per *turn* is right.**
The reviewing agent argued (correctly citing Linear and A2A) that a thread should have
one responder from birth and a second opinion means a second thread. I would not adopt
that here, for one reason specific to this system: **Linear's sessions are
agent-initiated, so the agent is the router. Workshop's chats are operator-initiated,
and the operator is the router.** The @mention path is already shipped, already
enforces first-tag-wins, already attributes the reply, and already reads the mentioned
agent's own memory union — and it is the single feature that delivers the operator's
stated goal of not juggling threads. Keeping it costs nothing in turn-taking ambiguity
because only one agent ever holds a turn. Keep D1/D2/D3 as built; add
`responder` as the *default*, not as an exclusive.

---

## 3. The entities

Each: what it is, what it can do, how it relates, a system that implements it, and the
delta from what is in the tree today.

### 3.1 Workspace

**Role.** The installation. One per box. Holds the agent registry, the model
credentials, the global brake.
**Relations.** has many Containers, Agents, Triggers.
**Source.** Every multi-tenant SaaS; Linear's workspace.
**Delta.** None. This is `workshop` scope today, minus the second meaning that ADR 0014
already stripped out (`task_scope.py:63` — `workshop` is the root queue,
`development` is the platform repo). That fix was correct and is the template for the
rest of this document: *one name, one meaning.*

### 3.2 Container (abstract) — Project | App | workshop | development

**Role.** The scope boundary. **Memory scope, access scope and runtime scope are the
same object.** If they ever diverge you own three overlapping permission systems.
**Relations.** belongs to Workspace; has many Tickets, Threads, Notes, Artifacts,
Stores; may have Agents granted into it.
**Source.** Claude Projects; Notion teamspaces; Devin's repo-scoped knowledge.
**Delta.** This is already true — `scope` is the first argument to every store
read/write. What is missing is the *word*. Today `project:`, `app:`, `workshop`,
`development`, `general` and `build:` are six string prefixes with no stated common
type, which is why `chat_context.get_provider` is a 40-line if-chain
(`chat_context.py:1639-1676`).

**Project vs App.** Keep them siblings, do not nest one in the other. The reviewing
agent proposed App-is-an-Artifact-of-a-Project; that is wrong for this tree, because
seven of the live apps (finance, budget, commute, location, birds, health, notes —
`app_registry.py:267-508`) are built-in and have no project. The honest rule:

> A **Project** is a source tree you build in. An **App** is a runnable surface you
> use. A generated app has a project behind it; a built-in app does not. Both own
> Stores, Tickets and Threads.

That also removes the awkwardness in `entity-model-2026-07-18.md` §8.3 where a Store
must set *at most one* of `host_app` / `host_project`: the field becomes one `host`
pointing at a Container.

**`build:` must go.** `BuildAgentContextProvider` (`chat_context.py:1657`) exists only
because builder agents predate the role enum. `_role_for_runtime` already resolves
builder → maker at the read boundary (P2, `d54c092`). The provider is the last place
the retired axis is still load-bearing, and it is one of the three "chat with an agent"
behaviours in §2.3.

### 3.3 Agent

**Role.** A declared contract: who it is, what it may write, where it may work, what
wakes it, what it may do unasked. **Defined by its declaration, never discovered by
running it** (the correction in `agent-definition-and-governance-2026-08-04.md` §0 —
keep it, it is the most important sentence in that doc).
**Relations.** belongs to Workspace; granted into Containers; has many Threads (as
responder); referenced by Triggers.
**Source.** A2A *Agent Card*; Claude Code subagent frontmatter; Letta agents.
**Delta.** Mostly built. Two changes:

- **Do not make roles into subtypes.** They are not today and must not become so. A
  role is `permitted writes + derived tools`, which is exactly the shape
  `role_may_write(role, target)` in the governance doc. That keeps the enum closed for
  authority purposes while letting a sixth *kind* of agent exist without a migration —
  it just declares a role and narrower tools.
- **The chat assistant should be an agent record.** Today the default responder in a
  general chat is a `ContextProvider`, not a roster agent, so there are two ways to be
  a thing that talks. `agents/apps/*.md` already proved the migration works for app
  personas (P1b). Finish it and "chat" and "chat with an agent" become the same code
  path — which is the deepest form of the answer to the operator's question.

### 3.4 Ticket

**Role.** Durable intent, and **the unit of approval.** Approving a ticket authorises
the reversible work inside its declared `touches` and nothing else — plan-level
approval, which the research identifies as the sweet spot and which this system already
had without naming it.
**Relations.** belongs to a Container; has many Threads (attempts); has many child
Tickets (depth 2); `depends_on` edges to other Tickets; produces Artifacts; links back
to the Thread it came from.
**Source.** Linear *Issue*; A2A *Task*; GitHub *Issue*.
**Delta.** The ticket is the healthiest concept in the system and needs almost nothing.
Two adjustments:

- **Split `assignee` into `owner` + `delegate`.** Today one field takes
  `operator | agent:<slug>` (`tasks.py:328`). Linear's split is the right one and the
  reasoning is exactly the operator's own concern that "it might not just be one agent
  doing the work": *an agent cannot be held accountable.* `owner` is always a human and
  never moves; `delegate` is the agent currently holding it and may change per attempt;
  **who actually did the work is derived from the Threads**, not from a field. That
  makes "one main agent, plus others" representable without inventing a co-assignee
  list.
- **`job_ids` → `thread_ids`.** Mechanical, follows from §1.

**One rule to write down on day one, or ticket count explodes:** *if a human might
reassign it, or it outlives the thread, it is a Ticket. Otherwise it is a plan step and
lives on the Thread.* Linear ships a session-level Plan for exactly this reason. The
tree has 179 tickets and zero with a `parent` — the sub-ticket tree has never been used,
which suggests the boundary is currently being drawn by instinct and holding. Write it
down before the ticket-maker starts decomposing at volume.

### 3.5 Thread ← the consolidation

**Role.** The durable transcript container for any interaction, attended or not. Holds
messages, holds the agent's activity, holds the state that says whether anyone is
waiting.
**Relations.** belongs to a Container; optionally anchored to a Ticket or App; has one
default responder; has many Turns; has many Messages; may produce Tickets and Artifacts.
**Source.** LangGraph *Thread*; OpenAI *Conversation*; Linear *AgentSession*; A2A
*contextId*; Zed ACP *Session*.
**Delta.** This is the new object, and it is 80% of the shipped `chat_store` thread plus
`anchor`, `responder` and `state`.

**Message types.** Take Linear's activity vocabulary nearly verbatim, because it is the
thing that lets one renderer serve both a chat and a background session:

| Type | Author | Meaning |
|---|---|---|
| `prompt` | operator only | you said something |
| `thought` | agent | reasoning, collapsed by default |
| `action` | agent | a tool call, with its result |
| `elicitation` | agent | *asks you something* — sets `state: awaiting` |
| `response` | agent | the reply / the outcome |
| `error` | agent | it failed |

A chat is a thread that is mostly `prompt`/`response`. A background session is a thread
that is mostly `thought`/`action`. **Same renderer, different density** — and GitHub's
answer to density is the right one: collapse subagent and tool activity behind a
one-line heads-up, expandable on demand.

### 3.6 Turn (internal)

**Role.** One execution of one agent over one Thread: input → tool calls → output.
**Relations.** belongs to a Thread; names one Agent; produces Messages.
**Source.** LangGraph *Run*; OpenAI *Response*; ACP *prompt turn*.
**Delta.** Already built as `chat_store.begin_run/update_run/commit_run`. It needs a
rename (`run_id` → `turn_id` — note the live draft already calls it `turn_id` internally
at `chat_store.py:1015`) and it needs to be the mechanism the ticket dispatcher uses
instead of `Popen` + a log file.

### 3.7 Trigger

**Role.** `(schedule | event predicate) → open a Thread with agent A, instructions I,
anchored to X`. This is the autonomy dial, and the 07-31 doc is right that it *is* the
autonomy dial ("autonomy is really just scope and how often it's triggered").
**Relations.** belongs to a Container; names one Agent; opens Threads.
**Source.** Linear *Loops* — plain-language recurring instructions that run on a
schedule or an event, where each run re-reads its instructions and decides what should
happen, drawing on workspace context and previous runs. Their worked example maps onto
Workshop's triager exactly: *when an issue enters triage, review its context and the
codebase, recommend a next step, and start a coding session when the path is clear.*
**Delta.** `triggers.py` + `dispatch_triggers.sh` exist. What is missing is that a
trigger currently starts a **job**; it should open a **Thread**. Then a cron agent's
output is a readable transcript in the same place as everything else, rather than a
`.jsonl` line and a log path.

### 3.8 Inbox (derived)

**Role.** Every Thread in `awaiting`, plus every pending approval, across all
Containers. **The demo that makes the product click:** agents work in the background;
when they need you, they all show up in one place.
**Relations.** none. It is a query.
**Source.** LangChain *Agent Inbox* and the ambient-agent pattern; the notify /
question / review taxonomy.
**Delta.** `services/inbox.py` is a **store** with its own `KINDS`, `STATES`, dedupe
keys and reconciler (`inbox.py:38-46`, plus `run_inbox.py` which exists solely to
reconcile blocked tickets *into* it). Once Thread has a state, most of that becomes a
`WHERE state = 'awaiting'`. Keep the notify/question/review *typing* — it is genuinely
good, and typing items by **what they need from you** rather than by entity is what
makes one stack coherent. Drop the parallel storage and the reconciler.

This is one of the two clearest duplications in the system: the inbox exists because
threads could not be in a waiting state, and `run_inbox.py` exists because tickets and
inbox items had to be kept in sync. Give Thread a state and both problems evaporate.

### 3.9 Note / Learning / Artifact / Store

Covered in §4 and §5. Summary of relations:

| Entity | Belongs to | Source |
|---|---|---|
| **Note** — curated knowledge, always in context | Container (+ layers) | Letta *memory blocks*; CLAUDE.md; Linear agent guidance |
| **Learning** — a rule, promotable to a Grant | Container | the existing `learnings.py`; no clean external analogue |
| **Artifact** — a produced thing (diff, PR, deploy, report) | Container; produced by a Thread; may resolve a Ticket | A2A *Artifact*; Copilot's draft PR |
| **Store** — a named data surface with tier + host | Container | the existing `stores.py`; MCP resources |
| **Grant** — a scoped capability token | Agent × Store | macaroons; MCP elicitation + short-lived scoped tokens |

**Artifact is worth promoting to a real noun.** Today a run's product is
`output_summary` + `log_ref` — prose. GitHub Copilot's model is that delegation produces
**a reviewable artifact, not a conversation**, and that is what makes background work
trustworthy: you review a diff, not a transcript. A Thread anchored to a ticket should
end by naming what it produced.

---

## 4. Memory, restructured

### 4.1 The problem, counted

Six substrates, no stated relationship between any two:

| # | Substrate | What it holds |
|---|---|---|
| 1 | `knowledge.py` | scoped notes, layered read union, index blocks |
| 2 | `learnings.py` | rules/corrections at `global` + scope keys |
| 3 | `notes_vault.py` + the Notes app | the operator's own notes |
| 4 | `memory_tree.py` | a YAML inheritance tree of Letta blocks + archival namespaces |
| 5 | `stores.py` | the generic named-data-surface registry |
| 6 | Letta | agent-side core/archival memory |

Plus chat transcripts and `jobs.jsonl`, which are the actual record of what happened and
are **in no index at all**. That last fact is the important one, because it is the
direct blocker on the operator's stated goal.

### 4.2 The three tiers

The field has converged on this split, and the tiers differ in mutability and in whether
a human should ever see them:

| Tier | What | Mutable | Operator-visible | Prior art |
|---|---|---|---|---|
| **Episode** | immutable record of anything ingested: a message, a tool result, a commit, a doc revision | no | only as provenance | Zep/Graphiti *episode subgraph*; Letta recall memory |
| **Fact** | derived: entities and relations, each edge carrying a validity window | invalidated, never deleted | no — only through search results | Graphiti's bi-temporal model |
| **Context block** | small curated text always in the prompt: conventions, persona, current objective | yes | **yes — this is the tier people curate** | Letta *memory blocks*; CLAUDE.md |

Mapping onto the tree: tier 3 is `knowledge.py` + `learnings.py` and is in decent shape.
Tier 1 exists as files but is not addressable. Tier 2 does not exist.

### 4.3 Recommendations

1. **Do not build the knowledge graph yet.** Tier 2 is where the temptation is and it is
   the wrong first move. Zep beats Mem0 by ~15 points on LongMemEval on the strength of
   temporal knowledge graphs, so the ceiling is real — but the operator's actual ask
   ("in any chat, pull up other chats and docs about this topic") is served by tier 1
   plus decent retrieval. Build tier 1 first; the graph is an implementation detail of
   retrieval quality, and no operator should ever be shown a node-link view of their own
   memory unless they ask for one.

2. **The single highest-value change in this whole document: put threads and tickets in
   the index.** `knowledge.recall()` (`knowledge.py:515`) already implements
   cross-scope retrieval with a permission check. It searches notes only. The goal —
   *"in any chat about a topic, pull up the other chats about it, whether or not it
   started there"* — is one write path away: every message, ticket and artifact lands as
   an Episode in its container, and `recall` returns mixed-type hits. That is the
   feature the operator described, and it needs no new entity.

3. **`role:<role>` is not a scope.** It is a *layer selector*. `read_layers` already
   treats it correctly (`knowledge.py:319`) — the bug is only that
   `scope_dir("role:reviewer")` makes it look like a place. Rename the axis: a note has a
   **place** (container) and optionally a **facet** (role / agent / global). Same
   directories, same union read, no category error. Roles are classes, not locations,
   and every other part of the system treats scope as an address.

4. **Rename the operator-facing tier.** "Memories" sounds like something that happens to
   you. **Notes** is what people curate. The `/memory` route (P1) is fine as the *page*;
   the entity should be a Note.

5. **Every derived fact must link back to its Episode.** Non-negotiable whenever tier 2
   is built. An agent asserting something with no traceable source is unfalsifiable, and
   this system's whole value proposition is that its context is inspectable.

6. **`memory_tree.py` and Letta blocks need a decision, not a merge.** A YAML
   inheritance tree of memory blocks is a *fourth* scoping mechanism next to scope,
   layers and grants. Either it becomes the implementation of layered reads for
   Letta-backed agents, or it retires. It should not remain a parallel opinion about
   what an agent can see.

---

## 5. The three questions, answered directly

### 5.1 "What is a run, and is it the right thing?"

No. Delete it as a noun. A ticket attempt is a **Thread**; one loop iteration is a
**Turn**. Keep `jobs.jsonl` as execution telemetry hanging off a Thread — pid, exit,
wall clock — not as an entity. §1.

### 5.2 "Does the agent join a chat, or do you chat with the agent?"

**You chat with the agent, and that is the same object as any other chat.** A Thread has
a default `responder`; setting it to an agent *is* "a chat with that agent". No wrapper,
no ceremony, no participants list.

Keep @mention on top of that as the *operator's routing tool*, not as a second model:
it hands one turn to another agent, attributed, and hands it back. That is how a chat
about a topic can reach the reviewer without you leaving the conversation — which is the
whole point of the system.

And "viewing a running agent in detail" is not a separate surface: it is that agent's
thread, which you can open and type into. The reason it "starts to look like the chat
again" is that it **is** the chat. That was never the problem; treating it as one was.

### 5.3 "Pipeline or graph?"

**Neither. Do not build an authoring surface for control flow.** Three different things
are wearing these two words:

1. **Control flow inside one agent** — a loop, not a graph. Claude Code and Codex
   demonstrate that prompt → act → observe → verify → repeat handles the overwhelming
   majority of real engineering work. Keep the loop. Add structure when you observe a
   failure, not in anticipation of one.
2. **Orchestration across agents** — this is where graphs earn their place, and it is a
   *team scaling* problem. For one operator with a handful of agents, Trigger + ticket
   `depends_on` edges + the fixed triager→maker→reviewer lifecycle cover what a DAG
   would, at a fraction of the cost. Linear reached "self-driving project management"
   with Loops, which is a scheduler over a loop, not a graph engine.
3. **The graph that actually matters here is the data graph, not the control graph** —
   ticket dependencies and memory relations. Both are already modelled
   (`depends_on` + `_would_cycle` at `tasks.py:218`). Neither needs a canvas.

The 2026 consensus is a macro/micro split — durable orchestration outside (Temporal),
dynamic agent loop inside (LangGraph) — and Workshop's macro layer is **the ticket DAG**,
which is already operator-legible in a way a flowchart is not. A builder does not want to
author a graph; they want to say "this before that", which is an edge on a ticket.

If a pipeline is ever genuinely needed, the extension point is a **ticket template that
files a fixed set of dependent sub-tickets** — a recipe, not a runtime graph. That
preserves one execution model.

---

## 6. Alternatives considered

### 6.1 Keep Chat and Session as separate types (Linear's actual model)

Linear does keep them apart and it works — but Linear has no general chat product, so
they pay for one surface, not two. Workshop has both. Separating them costs two message
tables, two renderers, two search indexes and two state machines, which is the situation
today and is what produced the four-entry-points-three-behaviours problem in §2.3. Take
their **event vocabulary**, not their table split.

### 6.2 Immutable threads (A2A's model)

A2A: once a task is terminal it cannot restart; refinement starts a new task under the
same `contextId`. Better provenance, worse ergonomics — people want to reply to a
finished thread. Recommend **mutable Threads with immutable Turns**, which keeps most of
the audit benefit. If autonomy is turned up far enough that airtight provenance matters
more than ergonomics, flip this.

### 6.3 Adopt a durable-execution engine (Temporal)

Rejected as a runtime, consistent with the 08-04 framework decision, but **two patterns
should be stolen now** because they are cheap and expensive to retrofit:

- **Generate an idempotency key before an interruption and persist it in thread state**,
  so a resumed action runs exactly once.
- **Store a hash of the proposed action at the moment of interrupt** and verify it at
  execution time, so an approval granted an hour ago is refused if the underlying data
  drifted. This is the one that is easy to skip and costly to skip — it is the
  difference between an approval that means something and an approval that means
  "someone clicked yes at some point".

Both matter more once `awaiting` is a durable state rather than a held process, which is
exactly what §2.4 proposes.

### 6.4 Ticket as the only container, threads as comments (Jira/GitHub model)

Doesn't survive contact with chats that aren't about a ticket, which already exist and
are the primary surface. Rejected.

### 6.5 Name the container "Session" rather than "Thread"

Genuinely close, and D11 argued for it. "Session" reads better for the agent case and
worse for the chat case, and is ambiguous across the prior art — container in ACP and
Devin, invocation in Linear. Recommend **Thread** in the schema and let the UI say
"chat" or "session" per state (§2.4). The thing the operator actually feels is *who
holds the turn*, and that is a field.

---

## 7. Sequence

Ordered so each step is coherent alone and nothing is left half-migrated. The first two
are strictly deletions and can ship immediately.

| # | Step | Why here |
|---|---|---|
| **S1** | Delete `participants` end to end; point "Start chat with agent" at a thread whose responder **is** that agent. | It is a lie in the UI today (§2.3). No design decision required. |
| **S2** | Retire `build:` as a context kind; `BuildAgentContextProvider` folds into the agent provider. | Last load-bearing remnant of the retired runtime axis. |
| **S3** | Add `Thread.state` + `anchor` + `responder`. Rename `run_id` → `turn_id` inside `chat_store`. | The one new field. Everything downstream depends on it. |
| **S4** | Derive the Inbox from Thread state; delete `run_inbox.py` and the parallel item store; keep notify/question/review typing. | Removes the second state machine and its reconciler. |
| **S5** | Ticket dispatch opens a Thread instead of a `Popen` + log file. `job_ids` → `thread_ids`. `jobs.jsonl` becomes telemetry. | This is the collapse. Everything before it is preparation. |
| **S6** | Split `assignee` into `owner` + `delegate`. | Small, and it is the accountability model. |
| **S7** | Index threads, tickets and artifacts as Episodes; `recall` returns mixed-type hits. | The operator's actual feature. Needs S3/S5 to have threads worth indexing. |
| **S8** | Rename the `role:` axis from scope to facet; make the chat assistant a roster agent. | Cleanup. Neither blocks anything. |

S5 is the one that pays for the rest: it is what makes an agent's background work a
readable, joinable, searchable conversation instead of a log path — and it is what makes
the escalation path reachable for the first time (zero uses in 179 tickets, §2.2).

---

## 8. Sources

**Conversation and run models** ·
[LangGraph threads and runs](https://deepwiki.com/langchain-ai/langgraph/7.2-threads-and-state-management) ·
[OpenAI Assistants migration guide](https://developers.openai.com/api/docs/assistants/migration) ·
[OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) ·
[A2A: Life of a Task](https://a2a-protocol.org/latest/topics/life-of-a-task/) ·
[A2A specification](https://a2a-protocol.org/latest/specification/)

**Agent interaction and delegation** ·
[Linear: developing the agent interaction](https://linear.app/developers/agent-interaction) ·
[Linear: agent best practices](https://linear.app/developers/agent-best-practices) ·
[Linear: our approach to the Agent Interaction SDK](https://linear.app/now/our-approach-to-building-the-agent-interaction-sdk) ·
[GitHub Copilot coding agent](https://github.blog/ai-and-ml/github-copilot/assigning-and-completing-issues-with-coding-agent-in-github-copilot/) ·
[Background agents compared, 2026](https://www.builder.io/blog/best-ai-background-agents-for-developers-2026)

**Ambient agents and the inbox** ·
[LangChain: introducing ambient agents](https://www.langchain.com/blog/introducing-ambient-agents) ·
[Human-in-the-loop implementation patterns](https://www.buildmvpfast.com/blog/human-in-the-loop-ai-agents-implementation-patterns-2026)

**Memory** ·
[Letta memory blocks](https://www.letta.com/blog/memory-blocks/) ·
[Letta shared memory](https://docs.letta.com/guides/agents/multi-agent-shared-memory) ·
[Mem0 vs Zep vs Letta, 2026](https://datapace.ai/blog/ai-agent-memory-tools-2026) ·
[Mem0 vs Zep (Graphiti)](https://vectorize.io/articles/mem0-vs-zep) ·
[CoALA](https://arxiv.org/pdf/2309.02427)

**Orchestration, graphs and durability** ·
[LangGraph vs Temporal](https://www.langchain.com/resources/langgraph-vs-temporal) ·
[Durable agent execution in production, 2026](https://agentmarketcap.ai/blog/2026/04/10/durable-agent-execution-production-temporal-modal-event-sourced) ·
[Graph-based agent workflow orchestration, 2026](https://zylos.ai/research/2026-04-14-graph-based-agent-workflow-orchestration-production/)

**Work hierarchies** ·
[Linear vs Notion data models](https://t0ggles.com/alternatives/linear-vs-notion)
