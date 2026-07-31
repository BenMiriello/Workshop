# Agent interaction model + stale-surface audit (2026-07-31)

> **STATUS: DESIGN + AUDIT. NOTHING BUILT.** Operator has approved no
> implementation. This doc records decisions made in conversation on
> 2026-07-30/31, the research behind them, the open questions, and a
> factual inventory of surfaces that contradict the current model.
>
> Builds on `entity-model-2026-07-18.md` (authoritative), and supersedes
> §4.1 of `docs/chat/chat-header-restructure-2026-07-30.md` (the three
> candidate meanings of "add an agent to a chat" — resolved below).

---

## 1. The finding that reframed this

`participants` — the field behind the chat's add-agent button — **has no
behavior**. It is read in exactly two files (`chat_store.py`,
`routes/chat.py`). In `chat_context.py`, which builds the prompt, selects
tools and picks the model, the word appears once, in a comment.

Adding an agent to a chat writes a slug into a list and renders a tag. It does
not change what the assistant knows, what it can call, or who answers. The
control was a data model shipped without a behavior, which is why it could not
be placed well in the UI.

Corollary: there is nothing to preserve. The interaction model below can be
designed freely.

---

## 2. Decisions (operator, 2026-07-31)

| # | Decision |
|---|---|
| D1 | **@-mention makes an agent respond in the chat.** The reply lands in the transcript, clearly attributed to that agent. Mention it again to get another response. |
| D2 | **One agent at a time.** Only the FIRST mention in a message is parsed. Additional tags in the same message are not recognized and not routed. |
| D3 | **The main assistant does not answer a message addressed to an agent.** It can see agent exchanges in the transcript as context, but it does not reply to them. |
| D4 | **Agent-initiated messages do not land in the transcript.** They surface at the top of the chat as a dismissible item. From there you can pop it into the chat to address it, or push it away into the agents drawer to handle later. |
| D5 | **Agent→chat messaging is scope-bounded.** An agent may message a chat whose scope is its own scope or above it, AND which is connected or relevant to it. A top-level chat must NOT receive pings from every agent beneath it. |
| D6 | **The inbox is global, with a per-chat filter.** |
| D7 | **An agent's page lists the chats it is connected to.** You can enter one, or start a new chat with that agent already included. |
| D8 | **A Ticket comes out of a Chat and links back to it. It never replaces the chat as the record.** |
| D9 | **Chat scope is operator-changeable.** Practically, if the operator wants to move a conversation to another scope, that is allowed. Do not design a limitation here. |
| D10 | **No ambient access to secrets or capabilities.** An agent must ask. Grants are once / this session / always. Every grant is visible, editable, revocable. What an agent can do must be explicit and legible before it runs. |
| D11 | **"Run" is the wrong noun.** A thing that can ask questions, recover, and report insufficient information is a *session*, not a job execution. |
| D12 | **Run/session context = ticket + a pointer to the originating chat + a tool to read that chat.** No pre-computed summary (summarizing costs a model call and loses information). The session pulls what it needs and raises a question if it comes up short. |

### Resolved from the earlier open list

- *Does an Agent own a Scope?* — No, not necessarily. But an agent needs **its
  own workspace to work in**: a defined bubble with a defined capability set,
  isolated from everything else, communicating outward through the chat it is
  connected to. Some agents get a narrow set, some much broader. (Design work
  outstanding — see §5.)
- *Is a Ticket a promotion or a projection of a Chat?* — Projection (D8).
- *Is chat scope fixed at creation?* — No (D9).

---

## 3. Research: everyone who has built this split it into three surfaces

The reason the current design feels incomplete is that one surface (the chat
header) was being asked to do three unrelated jobs. Every prior art splits them.

### 3.1 Linear — delegation ≠ assignment, and mention ≠ delegation

[Agent Interaction SDK](https://linear.app/now/our-approach-to-building-the-agent-interaction-sdk) ·
[docs](https://linear.app/docs/agents-in-linear) ·
[changelog](https://linear.app/changelog/2025-05-20-linear-for-agents)

- Humans and agents are peers in the workspace, but issues can **only be
  assigned to humans, and only delegated to agents** — "an agent cannot be held
  accountable." Delegation does not transfer responsibility.
- Interaction is **two separate opt-in scopes**: `app:assignable` (can be given
  work) and `app:mentionable` (can be talked to). An agent may hold either,
  both, or neither.
- The unit is an **agent session**, triggered by delegation or mention, with
  state `waiting | working | complete | errored`.
- Sessions emit **structured activity types**: tool calls, thoughts, and
  **elicitations** (the agent asking the human something).

This is the single most directly applicable source. D1/D2 and the
mentionable/delegatable split below come from it.

### 3.2 GitHub Copilot coding agent — delegation produces an artifact

[assigning issues](https://github.blog/ai-and-ml/github-copilot/assigning-and-completing-issues-with-coding-agent-in-github-copilot/) ·
[session visibility](https://github.blog/changelog/2026-03-19-more-visibility-into-copilot-coding-agent-sessions/)

- Assign an issue → background session in an isolated cloud environment →
  output is a **draft PR**. The deliverable is a reviewable artifact, not a
  conversation.
- You can watch the session live or read its logs afterwards.
- Subagent activity is **collapsed by default** behind a heads-up line showing
  what it is working on, expandable on demand. Directly relevant to how much of
  a session's interior belongs in the chat.

### 3.3 LangChain ambient agents + Agent Inbox — the pattern behind D4/D6

[Introducing ambient agents](https://www.langchain.com/blog/introducing-ambient-agents) ·
[agent-inbox repo](https://github.com/langchain-ai/agent-inbox)

Yes — this is an existing, named pattern with a reference implementation.

- **Ambient agents** listen to an *event stream* rather than to prompts, and can
  act on several events at once. Chat handles one conversation at a time; this
  is the structural reason a chat cannot be the agent surface.
- The **Agent Inbox** is modelled on "an email inbox plus a customer support
  ticketing system" — a dedicated place showing every open line of
  communication. Their stated reason for not using chat/Slack: **notification
  fatigue and scattered conversations**. Currently sorted chronologically;
  priority sorting is planned.
- Three human-in-the-loop patterns, and this is the useful taxonomy:
  - **notify** — FYI, agent had no authority to act
  - **question** — agent is uncertain and asks rather than hallucinating
  - **review** — agent proposes an action for approve / edit / reject
- A persistence layer saves agent state between actions, which is what lets a
  session pause and wait for a human at all.
- **Long-term memory is a namespaced key-value store with semantic search**, and
  human responses feed it — the answers become the alignment signal over time.

**What we can take beyond the inbox itself:** the notify/question/review
taxonomy dissolves the "do we mix agents and jobs at the top of the chat?"
worry. Items are not typed by entity. They are typed by **what they need from
you**. A finished session, a blocked agent, and a ticket awaiting review are all
just items with a verb. Nothing has to decide whether an entry is an agent or a
job.

The second takeaway is the learning loop: the operator's answers to *question*
and *review* items are the highest-quality preference data the system will ever
get. Writing them back into scoped notes is nearly free and closes the loop the
`chat-distilled` note already asks for ("task filing should learn from the
operator's patterns").

### 3.4 Slack — noise is the top concern

[Agent design](https://docs.slack.dev/concepts/agent-design/): in channels, keep
agent output minimal and threaded; DMs can be conversational. Be audience-aware:
an agent may hold context from elsewhere that is not appropriate to surface
here. Two deployment shapes — dedicated agent channels (low noise, easy to
audit) vs inline in working channels (high presence, needs careful config).

Supports D5: unrestricted agent→chat messaging is the failure mode, not the
feature.

### 3.5 Sandboxing and workspaces

[worktree isolation survey](https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development/) ·
[sandbox strategies](https://northflank.com/blog/how-to-sandbox-ai-agents) ·
[agent execution sandboxes](https://www.augmentcode.com/guides/agent-execution-sandbox)

- **Worktree per agent is the emerging norm.** Claude Code ships `--worktree`
  for exactly this. The hybrid production pattern is worktree-per-agent plus one
  lightweight container per worktree (isolated DB + dev server) once more than
  ~4 agents run concurrently.
- Least privilege / zero trust: actions **explicitly allowed, not implicitly
  permitted**.
- The finding most relevant to this installation: **long-lived secrets in an
  agent's environment are the biggest blind spot** — prompt injection
  exfiltrates them trivially. The recommended shape is a proxy *outside* the
  sandbox holding the real credentials; the agent carries a synthetic
  credential meaningful only to the proxy, which attaches the real token at the
  network layer.

Note for this system: full microVM/gVisor isolation is over-engineering for a
single-operator installation and cuts against the no-defensive-cruft rule. The
valuable part is not the sandbox boundary, it is the **legible workspace**: a
declared directory plus a declared capability set, so blast radius is knowable
before the agent runs. That is the `touches` field — already in the schema,
enforced nowhere (defect D3 in the entity model).

### 3.6 MCP elicitation — the mechanism for D10

[MCP permissions overview](https://www.cerbos.dev/blog/mcp-permissions-securing-ai-agent-access-to-tools) ·
[agent auth guide](https://www.arcade.dev/blog/ai-agent-authentication-authorization/)

MCP has a standardized **elicitation** flow: a tool pauses execution, asks the
client for user input, and resumes when the answer arrives. The URL-elicitation
SEP (Arcade + Anthropic) standardizes delivering a granular, context-specific
consent URL mid-task. When an agent attempts a restricted operation the server
halts and issues a "scope challenge"; on approval a **short-lived, single-use
token** is minted for that one operation.

This is exactly D10's shape, and it means "ask for access, granted once / for
this session / always" is a protocol-level pattern rather than something to
invent. Claude Code's own permission prompt is the same model.

---

## 4. The model this converges on

```
chat        you and one assistant, in a scope. @mention routes ONE agent
            (the first tag) into the conversation; its reply is attributed
            and inline. The assistant does not answer for it. Scope is
            operator-changeable.

inbox       global, per-chat filter. Items are notify / question / review.
            NOT "agents" and NOT "jobs" — typed by what they need from you.
            Surfaces at the top of the chat when relevant to that chat;
            dismiss into the drawer, or pop into the transcript to answer.

ticket      comes out of a chat, links back to it, never replaces it.
            Delegation lives here, not in the chat header.

session     replaces Run. State (waiting/working/blocked/done), structured
            activities (thoughts, tool calls, elicitations), can ask.
            Reads the originating chat on demand rather than being handed
            a lossy summary.

workspace   per-session (not per-agent) isolated working area + a declared
            capability set. Secrets are never ambient.
```

### 4.1 Agent configuration

The minimum honest agent record, matching the operator's own framing that
"autonomy is really just scope and how often it's triggered and under what
conditions":

| Field | Meaning |
|---|---|
| `scope` | where it works and what corpus it can see |
| `trigger` | cron / event / manual — **this is the autonomy dial** |
| `mentionable` | can be @'d in a chat and reply inline (D1) |
| `delegatable` | can be handed a ticket |
| `grants` | explicit capability set; nothing ambient (D10) |
| `touches` | declared path bounds for its workspace |

Most agents are neither mentionable nor delegatable — they are triggered, they
work, they report through the inbox. Making every agent chattable is precisely
what would turn this into mush. A notes-reviewing keeper should be neither.

### 4.2 Attribution and routing rules

- One agent per message; first tag wins; later tags are inert (D2).
- Agent replies are transcript messages with an agent author, visually distinct
  from the assistant.
- The assistant sees agent exchanges as context but never answers them (D3).
- Agent-initiated content is inbox-first, transcript only on operator action
  (D4).
- Delivery predicate: `chat.scope == agent.scope` or `chat.scope` is an
  ancestor, AND a connection/relevance link exists (D5).

---

## 5. Open questions

1. **Workspace granularity.** Per-agent or per-session? Per-session lets two
   sessions of one agent run in parallel without collision, which the worktree
   pattern supports naturally. Leaning per-session; needs a decision.
2. **Merge path.** If a session works in a worktree, what brings it back —
   auto-merge on clean, PR-style review, or operator-confirmed fast-forward?
   GitHub's answer (draft PR) is the review-artifact model and fits D8.
3. **Secrets proxy.** Worth building the credential-proxy shape, or is
   "grants gate which MCP servers spawn" sufficient for a single operator?
   The current exposure is real: agents inherit an environment that can reach
   `~/.config/clawdbot/` (Plaid tokens, Google OAuth, the auth token).
4. **Inbox learning loop.** Do operator answers to question/review items get
   written back as scoped notes automatically?
5. **Does the assistant get told an agent replied?** D3 says it does not answer,
   but it presumably should not be blind to the exchange either.

---

## 6. Stale-surface audit

Factual inventory as of 2026-07-31. Every item verified against the tree, not
inferred.

### 6.1 The agent UI never migrated off `runtime`

`frontend/src/pages/Agents.tsx` keys agents on `a.runtime === "builder"`
(lines 501-575) and ships a **"New builder"** modal (`NewBuilderModal`, line 93;
`deriveBuilderName`, line 86) describing "a Claude Code session scoped to a
project."

The backend already moved: `agents_registry.py` has the closed `role` enum with
`_coerce_role` (line 157) and `_validate_role` (line 171), which raises on an
unknown role. `entity-model-2026-07-18.md` §6.1 states agents are keyed by
**role**, not runtime. The frontend is a generation behind. This is the "builder
agent interface" the operator flagged.

### 6.2 Fully orphaned page files

| File | Status |
|---|---|
| `frontend/src/pages/Events.tsx` | **zero imports.** `/events` redirects to `/settings` (App.tsx:173) |
| `frontend/src/pages/Services.tsx` | **zero imports.** `/services` redirects to `/settings` (App.tsx:172) |

Dead code. Safe to delete.

### 6.3 Two Notes surfaces, both live, both called "Notes"

- `/notes` → `pages/Notes.tsx` (imported as `NotesPage`, App.tsx:37/123). **In
  the main nav** (`SECTIONS`, appNav.tsx).
- `/apps/notes` → `pages/apps/notes/Notes.tsx` (App.tsx:43/143). Breadcrumb
  label also "Notes" (appNav.tsx:47).

Two different pages with the same name and overlapping purpose.

### 6.4 Surfaces that exist but are unreachable from the nav

`SECTIONS` in `appNav.tsx` is: Home, Chats, Projects, Apps, Agents, Work,
Deploys, Notes, Usage, Settings.

Not in it, but routed and breadcrumbed:

| Route | Note |
|---|---|
| `/jobs`, `/jobs/:id` | `Jobs.tsx` is an 8-line wrapper over `JobsFeed` — **the same component the Work page's Runs tab renders.** One surface, two addresses. |
| `/stores`, `/stores/:app/:name` | `entity-model-2026-07-18.md` §8.3 explicitly says there should be **no separate `/stores` page**; storage belongs inside App/Project detail via `stores.by_host()`. |
| `/marketplace` | reachable only by deep link. |

### 6.5 Vocabulary drift in the UI

The entity model fixes one word per concept: **Chat / Ticket / Run / Agent**.
The UI says:

- Tickets live at `/work` (list) but `/tickets/:scope/:id` (detail) — the noun
  changes between list and detail.
- Runs are called **Jobs** in the nav, the page, and the route. `/runs`
  redirects to `/jobs` (App.tsx:175), i.e. the canonical word redirects to the
  legacy one.

### 6.6 Three renderings of the same ticket data

| Surface | Lines |
|---|---|
| `pages/Work.tsx` | 1153 |
| `components/TaskPanel.tsx` | 727 |
| `pages/TicketDetail.tsx` | 558 |

Not necessarily wrong — list / panel / detail are legitimately different — but
2438 lines across three implementations is where the "mixture of old concepts
and interfaces" the operator describes would live. Needs a read before any
consolidation claim is made.

### 6.7 Proposed trim (NOT executed)

| Action | Confidence |
|---|---|
| Delete `pages/Events.tsx`, `pages/Services.tsx` | high — provably dead |
| Retire `/jobs` in favour of Work's Runs tab, keep the route as a redirect for existing deep links | high |
| Migrate `Agents.tsx` from `runtime` to `role`; retire the "New builder" modal | high — backend is already there |
| Resolve the two Notes pages into one | medium — needs a call on which survives |
| Fold `/stores` into App/Project detail per entity model §8.3 | medium — the model says so; the work is real |
| Rename Jobs → Runs across the UI | medium — cosmetic but it is the stated vocabulary |

---

## 7. What is NOT decided

Nothing in §4 or §6.7 is approved. No code has been written against this
document.
