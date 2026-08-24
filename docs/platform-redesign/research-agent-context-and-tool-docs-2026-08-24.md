research (agent context and tool docs) - 2026-08-24

# Where ticket-state vocabulary should live, and how the industry actually solves "aware before calling, not reloaded every turn"

This is a research document. No code changed. It answers the operator's question in his
own words: he wants the ticket state vocabulary (what todo/doing/in_review/blocked/parked/
done/cancelled mean, and now whether "done" means merged-to-main or only committed-on-a-
branch) defined once and reachable by every agent and chat, without hardcoding it into
every system prompt, without re-loading it into context on every turn, while still making
the agent aware of it before it calls the relevant tool. He believes this is solved
elsewhere. It is - the pattern has a name and there is hard data on the caching question.

## 0. Repo measurements (grounding)

**`backend/services/tasks.py`** does define the vocabulary, in comments, once:

```
STATUSES = {"draft", "todo", "doing", "in_review", "blocked", "parked", "done",
            "cancelled"}
TERMINAL = {"done", "cancelled"}
```

with a short comment above it:

```
# draft = filed but not yet ready to run (Triage/Backlog in every other tracker).
# in_review = worker finished, operator is the gate; cancelled = dropped.
# parked = dead-letter: a poison-pill task that exhausted its attempt cap. Not
# terminal (the operator still sees it as open work), but next_ready never picks
# it, so it stops burning the budget every sweep.
```

and a second, coarser layer, `STATE_TYPES` / `_STATE_TYPE`, mapping the eight statuses onto
Linear's five categories (backlog/unstarted/started/completed/canceled), with a comment
citing Linear's docs as the source. This is source code, imported by `db/models.py` for its
CHECK constraint - it is the one real definition in the system. It is not exposed to a model
anywhere. No agent-facing surface reads it, quotes it, or links to it.

Critically: **`grep` for "merged into main", "not yet merged", "merged vs" across
`backend/services/*.py` and `docs/platform-redesign/*.md` returns nothing except one
unrelated hit** (`branches-worktrees-and-environments-2026-08-20.md`, "Data is not merged").
The distinction the operator says is "newly relevant" - done-as-in-committed versus
done-as-in-merged-to-main - does not exist anywhere in this codebase yet, in comments or in
code. This is a real gap, not a documentation-discoverability problem.

**`backend/mcp/workshop_query_server.py`** is where a chat's tools are described to the
model. Measured description lengths (concatenated string literal content, not full byte
count):

| Tool | Description length |
|---|---|
| `query` | 289 chars (~72 tokens) |
| `search` | 555 chars (~138 tokens) |
| `spawn_background` | 661 chars (~165 tokens) |
| `where_is` | 255 chars (~63 tokens) |
| `list_attachments` | 309 chars (~77 tokens) |
| `get_file` | 333 chars (~83 tokens) |

None of the six mentions the status vocabulary at all. `query`'s description says it
summarizes "open tasks" - it never says what "open" excludes, what `in_review` means, or
that `done` says nothing about whether the change reached main. An agent calling `query` or
reading its result has no path to that contract short of already having memorized it or
guessing.

**`backend/services/chat_context.py`** is 2,492 lines / 128,592 bytes, and its `PLATFORM_KERNEL`
constant - the L0 block, injected on every single turn regardless of scope, described in its
own docstring as "cache-stable" - is 928 characters (~232 tokens). It defines the layering
rule and confidentiality, nothing about tickets. Searching the whole file for `merged` and
ticket-status meaning turns up only *procedural* instructions (how to call the status-update
tool: `set-status <id> done`), never a definition of what `done` warrants. So today the
system already carries a non-trivial fixed prefix on every turn (L0 kernel + `role_preamble`
+ `delegation_block` + UI-card protocol + style rule, likely 1,500-3,000+ tokens depending on
branch) - and none of that budget is spent on the one vocabulary the operator says agents keep
misreading.

## 1. Tool descriptions as the carrier (MCP and equivalents)

MCP's `tools/list` result gives each tool a `name`, optional `title`, `description`,
`inputSchema` (full JSON Schema as of the 2026-07-28 spec, which lifted schemas to JSON
Schema 2020-12 with `oneOf`/`anyOf`/`allOf`/`$ref`), optional `outputSchema`, and optional
`annotations` (behavior hints like read-only/destructive; the spec says these **must** be
treated as untrusted unless from a trusted server). Separately, MCP defines **resources**
(read-only, application-controlled content the host decides whether to inject) and
**prompts** (user-controlled, invoked explicitly) as sibling primitives to tools
(model-controlled). A tool result can also carry a `resource_link` - a URI to a resource,
returned instead of inlining it, so the model can choose to fetch the full content later
rather than have it pushed into context up front.

Anthropic's own guidance ("Writing tools for agents," referenced via its engineering blog)
is explicit about *how much* to put in a description: write it "as you would explain to a
tool to a new hire on your team" - make implicit context explicit, define specialized
terminology, name resource relationships. It also names the failure mode of *not* doing
this and the failure mode of doing too much of it in the wrong place: small refinements to
tool descriptions measurably change agent behavior, so vocabulary that matters (like what a
status value warrants) belongs in the description or a linked resource, not assumed. For
output shape, the same guidance recommends a `response_format` enum (concise vs detailed) so
a tool can return a short answer by default and a fuller one on request - the tool-level
analogue of progressive disclosure.

What happens when descriptions get long: nothing graceful, by default - a long description
is just more tokens in the fixed `tools` block on every request that has this tool bound.
That is exactly why the ecosystem built the pattern in §2.

## 2. Progressive disclosure - the direct answer to the three-way tension

This is the actual mechanism, with two converging real implementations.

**Agent Skills (Anthropic, opened as agentskills.io, adopted by 26+ platforms as of 2026)**
formalizes a three-tier load:
1. **Discovery**: only `name` + `description` from each skill's frontmatter load at
   startup - roughly 30-50 tokens per skill.
2. **Activation**: when a task matches, the agent reads the full `SKILL.md` body into
   context.
3. **Execution**: reference files and scripts inside the skill folder load only if the
   instructions in step 2 point the agent at them.

This is a working answer to "aware before calling, but not reloaded every turn": the *name
and one-line description* are the cheap, always-present layer (satisfies "aware before
calling"); the full contract is a **separate document that is fetched, not injected**, and
only when relevant (satisfies "not re-read every turn" in the sense that it isn't paid for
on turns that don't need it). It is a real, shipping pattern, not a proposal.

**Tool Search Tool (Anthropic, Claude Developer Platform, announced 2026)** applies the same
idea to MCP tool catalogs. Tools are declared with `defer_loading: true`; their full
definitions (description + input schema) are excluded from the initial prompt and are
fetched by a `tool_search` call (BM25 or regex) only when the agent decides it might need
them. Anthropic's own measurement, with a five-server/58-tool setup: **without** Tool
Search, ~55,000 tokens of tool definitions are loaded before any conversation starts, and
overall context usage is 122,800 tokens versus 191,300 tokens preserved **with** it - an
85% reduction. Accuracy on MCP evals *improved*, not just token count: Opus 4 went from 49%
to 74%, Opus 4.5 from 79.5% to 88.1%, because a smaller, relevant tool set is easier to
select correctly against than a large one with everything crammed into every prompt.

**Code execution with MCP** (a related Anthropic pattern) pushes disclosure further: instead
of tool results flowing through the model's context at all, the agent writes code that calls
tools and filters/aggregates the results in an execution sandbox, returning only what the
model actually needs to see. Anthropic's worked example (Google Drive to Salesforce) drops
150,000 tokens of naive tool-call traffic to about 2,000 - a 98.7% reduction - because
intermediate data never enters the model's context at all, and tool *definitions* likewise
only materialize as the agent's code imports them.

**Returning documentation in the tool result, not the prompt.** This repo already has the
mechanism half-built: `workshop_query_server.py`'s `where_is` tool returns a computed
orientation manifest as a *tool result string*, not prompt text - it exists precisely so an
agent can ask "where do I reach X" on demand instead of that map being baked into every
system prompt. The same shape (a tiny always-visible tool description plus a fuller answer
returned only when called) is the natural home for "what does `in_review` mean and what does
`done` claim" - a `ticket_contract` style call, or an addition to the existing digest/where_is
surface, rather than a second copy of the vocabulary pasted into every prompt-building
function.

**Tradeoffs of progressive disclosure, honestly:** it trades a fixed cost (always-loaded,
never wrong, but paid every turn) for a variable one (an extra round trip when the agent does
need the detail, and a dependency on the short description being good enough that the model
reliably decides to fetch the long form when it matters - Anthropic's own guidance stresses
description quality is what makes this decision reliable rather than a coin flip). It is not
free discovery: a bad short description means the model never asks for the long one, and the
contract is effectively invisible again.

## 3. Prompt caching as the economic answer - and where it cuts against the "never reload" instinct

The numbers (Claude API, current as of 2026):

- Cache write, 5-minute TTL: **1.25x** base input token price.
- Cache write, 1-hour TTL: **2x** base input token price.
- Cache read (hit): **0.1x** base input token price - a 90% discount versus paying full
  input price for the same tokens again.
- Minimum cacheable block: 512-4,096 tokens depending on model (1,024 for Sonnet 5).
- Cache hierarchy: `tools` → `system` → `messages`, strictly ordered. A change to any block
  invalidates that block *and every block after it* in the ordering - so a stable block
  placed early (tool definitions, then system prompt, then only the actual conversation
  varying) rides the cache for free on every subsequent turn; the same block placed after
  something that changes every turn gets evicted every turn regardless of its own stability.
- Default TTL in 2026 is 5 minutes (this was quietly cut from 1 hour earlier in the year,
  which inflated bills 17-26% for workloads that assumed the old TTL - a caution that TTL
  assumptions need to be revisited, not a caution against caching itself).

What this means for the operator's stated constraint ("must NOT be re-read and re-loaded
into context on every turn"): **if the vocabulary block is placed in a stable position in
the prompt (early, unchanging, same across chats/agents), reincluding it on every turn is
not "reloading" in the expensive sense he is worried about.** After the first turn in a
session, every subsequent turn pays 0.1x input price for it, not 1x - and the *quality* cost
(more tokens for the model to attend over) is the same whether it is cached or not, since
caching is a pricing/latency optimization, not a context-window optimization. A 300-token
vocabulary block sitting in the L0 kernel, alongside the other ~230-token cache-stable block
already there, costs about 30 extra cached tokens per turn at 0.1x price after the first
turn of a session - negligible against a chat turn that is already spending thousands of
tokens on tool results and conversation.

**So the evidence says his instinct is right for a different reason than "cost."** The
economic argument for keeping it out of every prompt is weak - caching makes repetition
nearly free. The argument that *does* hold is the one progressive disclosure targets: a
model attending over more fixed, mostly-irrelevant text on every turn is worse at using the
tools that matter *this* turn (Anthropic's own 49%→74% / 79.5%→88.1% accuracy jump when
irrelevant tool definitions were removed from context, not just made cheaper). The
"entangles things" complaint the operator raises is really a maintainability and
attention-budget argument, not a cost argument, and prompt caching does not touch it: caching
makes an unnecessary block cheap, not necessary. If the vocabulary is something *every* turn
genuinely needs (e.g., a chat that manages tickets in almost every message), caching makes
paying for it every turn a non-issue and it should probably just live in the stable prefix
plainly. If it's needed by *some* tool calls and not others (the more likely case here -
most chat turns never touch ticket status), progressive disclosure is still the better fit,
not because of price, but because of relevance and attention.

## 4. Where shared vocabulary lives - practitioner consensus

Across the sources reviewed, the rough hierarchy practitioners converge on:

1. **The data carries its own explanation, when the data is what triggers the question.**
   A returned `status: "in_review"` is best paired, in the same response, with what that
   status means right now for this record - not a hope that the model memorized a system
   prompt paragraph from three tool calls ago. This is the "self-describing API" move (§5)
   applied to agent tool results.
2. **The tool description carries the *shape* of the contract** - enough that the model
   knows the tool exists, roughly what it returns, and (per Anthropic's guidance) any
   specialized terminology it needs to phrase a good call - but not the full state-machine
   documentation inline, because that description is paid on every turn the tool is bound,
   whether or not the turn touches status at all.
3. **A retrieved/companion document (resource, skill file, or tool-result payload) carries
   the full contract**, fetched on demand. This is where "what does each status mean, and
   what does `done` warrant about main" belongs: one document, one place, referenced by name
   from the short tool description and from the human-facing ticket-writing doc alike.
4. **The system prompt carries almost nothing domain-specific** - it is reserved for
   identity, authority layering, and safety framing (this matches what `PLATFORM_KERNEL` in
   this repo already does correctly: confidentiality and layering, not ticket semantics).

What fails, per the same sources: duplicating the vocabulary in more than one of these
layers. The moment "what `in_review` means" is written once in a system-prompt paragraph and
again in a tool description and again in a doc, the three drift, and an agent that reads only
one of them is reading a stale or partial version - which is arguably worse than reading
nothing, because it reads as authoritative. The repo's own `tasks.py` docstring makes this
argument in a different context ("a second live ticket store that diverged from the first")
and the same logic applies to documentation, not just data.

## 5. Self-describing APIs - what transfers, what doesn't

- **HATEOAS/hypermedia**: the promise was that a client needs to know nothing but an entry
  point; every subsequent affordance and its meaning arrives embedded in the response as
  links plus their semantics. Human REST clients mostly never adopted this in practice
  (writing a generic hypermedia-following client was more work than hardcoding endpoints).
  For an LLM agent it transfers *cleanly*, because "read an embedded description and decide
  what to do" is exactly what an LLM is good at and a hardcoded HTTP client is not. The
  concrete transfer for Workshop: a ticket object returned by a tool can carry, alongside
  `status: "in_review"`, an inline one-line gloss of what that status currently means and
  what would move it forward - cheap per-call, always current because it is generated from
  the same source of truth as the status itself (`services/tasks.py`'s `STATUSES` comments),
  and impossible to drift from the data since it's derived at read time, not hand-written
  per caller.
- **JSON Schema / OpenAPI `description` fields**: the same content-plus-metadata idea, but
  static and English-only by convention; nothing enforces that a `description` stays
  accurate as the API evolves, which is the same drift risk as system-prompt duplication.
  What transfers: put the field-level description on the *schema*, not in a wiki, so it is
  next to the thing it describes.
- **GraphQL introspection**: closest existing analogue to what MCP does with
  `tools/list` + tool `description` - a schema that documents itself and that a client (or
  tooling like GraphiQL) can query directly, with descriptions rendered as CommonMark. What
  doesn't transfer: GraphQL descriptions are typically written once and read by humans
  building a client; they were never optimized for "the reader decides whether to spend a
  turn fetching the long version," which is the exact decision progressive disclosure is
  built around. MCP's addition of `resource_link` in tool results is the missing piece
  GraphQL never needed, because GraphQL's client wasn't token-metered.

## 6. State-machine documentation as prior art - closed vs merged vs released

This is the most directly on-point prior art for the operator's specific problem, because
GitHub already had to solve "one lifecycle word is being asked to mean two different things"
and the fix is instructive.

- **Issues**: `state` is `open` or `closed`. Closed is closed; there is no further
  distinction inside the issue object itself.
- **Pull requests**: `state` is also only `open` or `closed` - closing and merging are *not*
  distinguished by `state`. GitHub's REST API instead exposes a **separate, orthogonal
  field**, `merged` (boolean) and `merged_at` (timestamp, `null` if never merged). A closed
  PR with `merged: false` was abandoned; a closed PR with `merged: true` shipped its diff to
  the target branch. There is no `state=merged` filter in the REST API at all - callers are
  expected to fetch `state=closed` and then partition on `merged_at`. (GraphQL is more
  ergonomic here and lets you query/sort by `mergedAt` directly, which is a genuine papercut
  in the REST surface that the community has asked GitHub to fix.)
- **Releases**: merging to the default branch and cutting a **release** (a named, tagged,
  distributable version) are two more separate facts again. A repository can carry months of
  merged, unreleased commits; "released" is its own object (a git tag plus a GitHub Release
  entry), not a state on the commit, the PR, or the issue.

The structural lesson: GitHub does **not** try to make one `status` enum carry
"finished/abandoned" and "integrated into the mainline" and "shipped to users" simultaneously.
It keeps the coarse state (open/closed) as one small enum, and represents the finer,
genuinely orthogonal facts (`merged`, `merged_at`, and separately the release/tag graph) as
their own fields that a client checks in combination. This maps directly onto the operator's
new requirement: `done` in this system's ticket store answers "is the ticketed work finished,"
which is a different axis from "did the resulting diff land on main," which is itself
different from "is that change live/deployed." Trying to overload `done` to mean all three is
the same mistake a `state=merged` REST filter that doesn't exist is a workaround for. The fix
suggested by this prior art is not a ninth status value - it's a second, orthogonal field
(something like `merged: bool` / `merged_ref: str|None`, populated from the same place a
future git-integration layer would know it, mirroring how `sync`/`external_refs` already
exist in `tasks.py` as separate, addable facts rather than folded into `status`) plus a
one-line gloss surfaced wherever `status` is surfaced, exactly as GitHub's own API docs gloss
`merged_at` next to `state`.

Linear and Jira reinforce the same shape from the other direction: Linear defines a small,
closed set of **categories** (backlog/unstarted/started/completed/canceled, plus triage) that
every custom status maps into, documented once in its product docs and exposed via API as
`type` on the workflow state object - which is exactly the pattern `tasks.py`'s own
`STATE_TYPES` already imitates, citing Linear by name in a comment. Jira's status categories
(To Do / In Progress / Done) are the same idea. None of the three trackers puts the
authoritative definition of what a status means in a client's system prompt; all three put it
in one schema-adjacent place (the workflow/status API object) that any client, human or
otherwise, reads directly rather than being told about secondhand.

## What this implies for Workshop

Six options, in the shape the operator's three constraints actually cut across (C1 = each
surface carries its own explainer or reference; C2 = not re-read/re-loaded into context every
turn; C3 = agent aware of the tool and its meaning before calling it):

**A. Write the vocabulary into a shared constant in `services/tasks.py` (already exists,
barely) and stop there.**
Satisfies: nothing new, this is the status quo. C1 fails (no surface references it), C3 fails
(nothing tells an agent it exists). Free, and insufficient - this is where the repo already
is, and it's the gap the operator is describing.

**B. Paste the full vocabulary into every relevant tool description
(`query`, `search`, a ticket-update tool, etc.), verbatim, repeated in each.**
Satisfies C3 fully (always visible before the call). Violates C1 in spirit (it's not "its own
explainer," it's N copies of the same explainer, which is the drift risk named in §4) and,
per §3, does **not** meaningfully violate C2 economically once cached - but it does cost real
context-window attention on every turn a tool with this description is bound, whether or not
the turn touches tickets, which is the accuracy argument from Tool Search's 49%→74% jump
working in reverse. Cheap in dollars, not free in quality, and a maintenance liability (N
places to update if the vocabulary ever changes, exactly the multi-store problem `tasks.py`'s
own docstring warns against elsewhere).

**C. One short gloss per relevant tool description (a sentence, not the full contract) plus
a companion call/resource for the full contract - the Agent Skills / Tool Search pattern.**
Concretely: extend `query`'s and any ticket-read/write tool's description with one sentence
("statuses: todo/doing/in_review/blocked/parked/done/cancelled; see the `where_is`/a new
`ticket_contract` result for what each means and what `done` does and doesn't claim about
main"), and put the actual contract behind a cheap on-demand call - either folded into the
existing `where_is` orientation manifest (it already exists, is already read-on-demand, and
already documents "which tool reaches what") or a new tiny result string alongside it.
Satisfies all three: C1 (the reference lives with the tool, one line), C2 (the full text is
paid only on the turns that fetch it, and per §3 even that cost is 0.1x after first fetch if
kept in a stable spot), C3 (the model sees enough in the short description to know to ask).
This is the closest fit to what he described and is exactly the pattern MCP tool
descriptions + resource links, and Agent Skills' discovery/activation split, both implement.
Cost: one new short field/call to design and keep in sync with `tasks.py`'s real STATUSES
(should be *generated from* the comments already there, not hand-duplicated, to avoid the
drift problem).

**D. Return the gloss inline with the data itself - a `status_meaning` string alongside every
`status` field a tool returns (the HATEOAS/GitHub-`merged_at` move from §5-6).**
Satisfies C1 (lives with the data, generated from the one source of truth), C2 well (a few
words per ticket returned, not a document, and only appears when tickets are actually
returned), C3 partially (it teaches the model *after* it has already decided to call the
tool, which covers "misreading a result" but not "not knowing to call the tool at all" - so
it complements C but doesn't replace it). This is the most direct fix for the operator's
"newly relevant" complaint specifically (agents misreading what a *returned* status means)
and is cheap to build: derive it from the same `_STATE_TYPE`/comment table in `tasks.py`
rather than writing new prose, and add the merged/committed distinction as a second,
GitHub-shaped field (`merged: bool|None`, not a ninth status) once that data exists, so
`done` stops being asked to mean two things.

**E. Put it in the system prompt / L0 kernel, unconditionally, every turn.**
Satisfies C3 trivially and, per the caching numbers in §3, does **not** meaningfully violate
C2 on cost grounds after the first turn of a session (roughly 30 extra cached tokens/turn for
a ~300-token block). It does violate C1 (one copy, centrally injected, is the opposite of
"each surface carries its own") and it pays the attention-quality cost from §3 on every turn
system-wide, including the majority of turns that never touch a ticket. This is the option
the operator is right to resist, but the evidence says the reason to resist it is relevance
and prompt hygiene, not the dollar cost he may be assuming.

**F. A dedicated "ticket contract" MCP **resource** (not a tool), fetched via the `resources`
primitive rather than returned from a tool call.**
Technically the most spec-faithful MCP answer (resources are exactly "read-only content the
host decides whether to inject," a closer semantic fit than overloading a tool result), but
this server implements tools only, by hand, over stdio (`workshop_query_server.py`'s own
docstring: "implemented by hand so it needs no MCP SDK") with no `resources` capability
declared. Adding one is more protocol surface than extending `where_is`'s existing pattern
(option C) for the same effect, so it's worth naming as the "correct" answer but not the
cheapest one here.

**Recommendation implied by the evidence, not a decision:** C and D together are the load-
bearing pair - one short reference sentence in each ticket-touching tool's description
pointing at a single generated contract doc/call (satisfies "aware before calling" without
hardcoding), plus a short generated gloss returned alongside every `status` value in tool
results (fixes the specific "misreading a returned status" failure, including the
merged-vs-committed gap, at the point of use). Both should be *generated* from the one
existing source of truth in `tasks.py` rather than hand-written prose, for the same reason
the file's own docstring gives for keeping `owner`/`assignee` as one record with two
projections: two hand-maintained copies of a vocabulary always drift, and the repo has
already paid for that mistake once (the store/database divergence documented in
`tickets-postgres-cutover-2026-08-16.md`). Option E (system prompt) is not needed on cost
grounds - caching makes it nearly free - but is still the wrong layer on relevance grounds,
which is a stronger and more durable argument than the cost one he may have started from.

## Sources

- [The 2026-07-28 MCP Specification Release Candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [MCP specification - Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [What is MCP Tool Search? (Claude Code context pollution guide)](https://www.atcyrus.com/stories/mcp-tool-search-claude-code-context-pollution-guide)
- [Introducing advanced tool use on the Claude Developer Platform - Anthropic](https://www.anthropic.com/engineering/advanced-tool-use)
- [Code execution with MCP: building more efficient AI agents - Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Writing tools for agents - Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic Prompt Caching pricing and mechanics](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Cache TTL regression discussion, anthropics/claude-code #46829](https://github.com/anthropics/claude-code/issues/46829)
- [Anthropic Prompt Cache TTL + Cost Mechanics - Brandon Wie](https://brandonwie.dev/posts/anthropic-prompt-cache-ttl)
- [What Is Progressive Disclosure in AI Agent Design? - MindStudio](https://www.mindstudio.ai/blog/progressive-disclosure-ai-agent-skill-design)
- [Agent Skills: Progressive Disclosure as a System Design Pattern - SwirlAI](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)
- [Agent Skills Overview - agentskills.io](https://agentskills.io/home)
- [Agent Skills Explained: How SKILL.md Files Work - Firecrawl](https://www.firecrawl.dev/blog/agent-skills)
- [HATEOAS: The API Design Style That Was Waiting for AI - Nordic APIs](https://nordicapis.com/hateoas-the-api-design-style-that-was-waiting-for-ai/)
- [Context engineering is sleeping on the humble hyperlink - mbleigh.dev](https://mbleigh.dev/posts/context-engineering-with-links/)
- [GraphQL Introspection - graphql.org](https://graphql.org/learn/introspection/)
- [graphql-spec, Section 4 - Introspection](https://github.com/graphql/graphql-spec/blob/main/spec/Section%204%20--%20Introspection.md)
- [Linking a pull request to an issue - GitHub Docs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
- [REST API endpoints for pull requests - GitHub Docs](https://docs.github.com/en/rest/pulls/pulls)
- [Support state=merged in the pull request listing endpoint - GitHub community discussion #174481](https://github.com/orgs/community/discussions/174481)
- [Deploying code - GitHub Docs](https://docs.github.com/en/pull-requests/concepts/deploying-code)
- [Issue status - Linear Docs](https://linear.app/docs/configuring-workflows)
- [Triage - Linear Docs](https://linear.app/docs/triage)

Repo files measured directly (no citation needed, read in this session):
`backend/services/tasks.py` (STATUSES, STATE_TYPES, comments, lines 1-1226 of 2806),
`backend/mcp/workshop_query_server.py` (full file, 641 lines),
`backend/services/chat_context.py` (PLATFORM_KERNEL and surrounding, lines 543-729 of 2492).
