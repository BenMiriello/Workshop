# Custom context/memory management for Workshop chat: a proposal

Status: proposal, revised 2026-08-20. Design only, no implementation. Builds on
`docs/platform-redesign/context-compaction-research-2026-08-20.md` and
`compaction-both-paths-plan-2026-08-20.md`.

Scope: a concrete architecture for a Workshop-owned context/memory management
layer that is cache-friendly on the production API path, provider-agnostic in
its core logic, and that does NOT build the memory-search engine itself, only
the interface that will call one.

---

## Correction note (why this revision exists)

The first version of this doc got the central cost model wrong, and the operator
caught it. It described the recent conversation as a "volatile tail" that is
"rebuilt every turn" and "never cached," then also said cache rewrites are the
expensive thing. Those two claims contradict each other, and the first one is
false.

The correct model, confirmed against Anthropic's own documentation
([How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching),
[Prompt caching API docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))
and the most-cited production writeup
([Manus: Context Engineering for AI Agents](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)):

- A conversation grows by **appending**. Each turn's request is the previous
  request plus the new exchange at the end. "On a normal turn, the prefix is the
  entire previous request and only the latest exchange is new"
  (Claude Code docs, verbatim). So the whole history except the newest exchange
  is read from cache at roughly 10% of the input rate. The conversation is **not
  rebuilt each turn**, and its size is not the per-turn cost driver.
- The genuinely expensive events are the ones that change the **prefix**: a
  model switch, an effort change, a tool-set change, editing the system prompt,
  and **compaction** (replacing old messages with a summary changes the
  conversation from that point, so the cached prefix no longer matches). Each of
  these forces one full uncached reprocess, then the new prefix is cached.
- Therefore the winning pattern, which every serious system converges on, is:
  **append cheaply for many turns, then reduce context in an infrequent, batched
  event at a natural boundary, and amortize that one-time cost over the many
  cheap turns that follow.** Continuously trimming or rewriting old context every
  turn is an anti-pattern: it pays the expensive rewrite over and over. Manus
  states it as a rule: keep context append-only, never modify prior actions or
  observations, because a single changed token "invalidate[s] the cache from that
  token onward."

So the operator's own instinct was right and mine was wrong. Removing or
compacting old content as a chat moves on is exactly correct; it just has to be a
periodic event, not a per-turn rebuild. This revision rebuilds the architecture
around that.

---

## a. Executive summary and recommendation

Workshop already has a working, if basic, retrieval substrate: a plaintext note
store (`knowledge.py`), a hybrid semantic+BM25 index over notes/docs/chats/tickets
(`semantic_index.py`), a scope tree governing reach, and an MCP tool
(`mcp__workshop__search`) that already exposes ranked, cited passages to a live
chat. It is embedding-based, not substring, and already wired into chats. The gap
is not "build retrieval," it is "decide what stays in the live prompt, what gets
evicted, and when," and do it so eviction and retrieval never needlessly rewrite
the cached prefix.

**Recommendation in one paragraph:** build a small, provider-agnostic **context
manager** around three rules taken directly from the state of the art. (1) The
conversation is **append-only and cached**; never modify past turns, and let the
provider cache do its job. (2) Content that should leave the live window is
removed in a **periodic compaction event at a turn boundary**, not per turn,
using **restorable compression**: replace the dropped span with a short summary
plus a **reference** (a `workshop:` ref) so the material is recoverable, never
destroyed. (3) When older or out-of-window material is needed again, it comes
back **through a retrieval tool call**, whose result appends to the conversation
and is therefore cached from then on, rather than being pre-injected fresh into
the prefix every turn (which would be the one genuinely cache-hostile move). On
the Claude API path these three rules map onto Anthropic's own documented
primitives (context editing / `clear_tool_uses`, the memory tool, and
`compact_20260112`), which Anthropic measures at +29% and +39% over baseline and
which are designed to preserve the expensive cache tiers. All provider specifics
live behind a `ProviderAdapter`; the manager never imports an Anthropic type.

---

## b. The corrected architecture

Three tiers, distinguished by **how often each changes**, because change is what
costs cache, not size.

```
  TIER 1  STABLE PREFIX  (system prompt + tools + durable memory)
          changes: rarely (session start, a pin/edit, a compaction event)
          cache:   read at ~0.1x every turn; the big win, and size barely matters
          ── cache breakpoint here (API path) ──────────────────────────────────

  TIER 2  APPEND-ONLY CONVERSATION  (prior turns, tool results, appended recalls)
          changes: only by appending at the end
          cache:   the whole prior request is the cached prefix; only the newest
                   exchange is full price. NOT rebuilt per turn.
          reduced: only at a periodic COMPACTION EVENT (see below), which is the
                   one moment this tier's prefix is rewritten and re-cached.

  TIER 3  THIS TURN'S NEW INPUT  (the user's new message; any this-turn tool call)
          changes: every turn, by definition
          cache:   full price, but it is small
```

There is no "volatile tail rebuilt every turn." Tier 2 is cached and append-only.
The only per-turn full-price content is Tier 3, which is small. This is why an
agent can carry a very large context affordably: almost all of it is Tier 1 + the
already-cached part of Tier 2.

### The one cache-hostile move to avoid, and how

Pre-injecting **fresh retrieved passages into the prefix every turn** (classic
per-turn RAG) is the move that breaks this. Different passages each turn means the
prefix differs each turn, so nothing after the injection point caches. The fix
everyone uses: **retrieval is a tool, not a pre-injection.** When the model (or a
cheap pre-step) needs older context, it calls the recall tool; the result appends
to Tier 2 and is cached on every subsequent turn, exactly like a file read in
Claude Code ("reads append to the conversation," Claude Code docs). Any always-on
injected digest must be tiny and sit in Tier 1 where it is cached, not
re-assembled per turn.

### The compaction event (the rare, expensive, amortized moment)

When Tier 2 crosses a token threshold, at the **next turn boundary** (never
mid-tool-call), replace the oldest span with **summary + references**:

- Summarize the dropped span into a running summary (a model call).
- For anything with a durable address (a doc read, a large tool result, a
  retrieved note), keep its `workshop:` ref, not its body. This is Manus's
  restorable compression: "drop webpage content while preserving URLs," "omit
  document contents while retaining file paths," so nothing is irreversibly lost
  and the agent can pull it back through the retrieval tool.

This rewrites Tier 2's prefix once, costs one uncached reprocess of the now
shorter history, and then many cheap appended turns follow. Because Tier 1 sits
behind its own breakpoint, a compaction event never touches the system/tools
cache. And per Anthropic, prefer **truncation to an already-cached prefix**
(the `/rewind` shape) over compaction when the goal is to abandon a path:
truncating back to an earlier turn reuses a warm cache and costs nothing, while
compaction builds a new prefix.

### Who owns what

1. **Context Manager** (new, Workshop-owned, provider-agnostic). Owns the append
   discipline, the retrieval-trigger policy, and the compaction-event policy
   (when to fire, what to summarize, what to keep as a ref). Knows nothing about
   `cache_control` or any Anthropic type.
2. **Retrieval interface** (new, thin). One call, `fetch_context`; section (c).
   Backed today by `semantic_index.search`, swappable for a real memory-search
   service later without changing the contract.
3. **Provider Adapter** (new, one per model family). Turns the manager's abstract
   output into a real request: where the cache breakpoint goes, which native
   context-editing / compaction primitive to invoke. `AnthropicAdapter` first;
   `CLIAdapter` for the dev path (no cache control exposed, so it just accepts the
   custom fold already shipped); `CodexAdapter` later.

---

## c. The retrieval-request interface

The seam between "context management" (this proposal) and "memory search" (a
separate, later project). The manager asks one bounded question and does not care
whether the answer came from BM25, embeddings, or something smarter later.

```
fetch_context(request: ContextRequest) -> ContextResult

ContextRequest:
  scope: str             # the chat's context_key / scope_key
  query: str             # what is needed now (from the latest turn, or an
                         # explicit "what did we decide about X")
  kinds: list[str] | None  # note/doc/chat/ticket/vault (semantic_index.KINDS)
  budget_tokens: int     # hard cap on what comes back
  from_scope: str | None # caller's scope, for reach/policy checks

ContextResult:
  passages: list[Passage]
  mode: str              # "semantic" | "lexical" | "lexical (embedder down: ...)"
  truncated: bool        # more relevant material existed than the budget allowed
  backend: str           # "substrate-v1", later "workshop-memory-v2"

Passage:
  kind: str   # note | doc | chat | ticket | vault
  ref: str    # a workshop: ref (refs.py), resolvable
  title: str
  scope: str
  ts: str
  text: str   # verbatim retrievable text, NEVER a summary of it
  score: float | None
```

Design notes: `budget_tokens` is mandatory (the manager owns the turn's budget);
the result always states what it is (`mode`, `truncated`, `backend`), mirroring
`semantic_index.search` which already returns `mode`/`rebuilding` rather than
degrading silently; retrieval only reads (writes stay on `knowledge.put_note` /
`learnings.record_feedback`).

### v1 backing, using what already exists

`fetch_context` is a thin composition of shipped calls:

```
hits = semantic_index.search(query, kinds=..., scope=scope_tree.reach(scope),
                             from_scope=..., limit=derived_from(budget_tokens))
return ContextResult(passages=[Passage(...) for h in hits["hits"]],
                     mode=hits["mode"], truncated=..., backend="substrate-v1")
```

`semantic_index.search` is a real hybrid engine (embeddings via local
`nomic-embed-text` on Ollama, BM25 lexical fallback when the embedder is down, not
substring), already respects scope reach and per-item share policy, and is already
exposed to chats via `mcp__workshop__search`. The one new piece is the wrapper
plus token-budget trimming.

### What a chat can retrieve today

| Capability | Today? | Where | Substrate |
|---|---|---|---|
| Search notes/docs/chats/tickets by meaning | Yes | `semantic_index.search` | Embeddings + BM25 fallback, not substring |
| Scope-aware access control on results | Yes | `_keep_mask` + `memory_policy` | reach-tree + per-item share |
| Ambient always-loaded digest | Yes | `knowledge.layered_index_block` | token-capped (500 default) |
| On-demand search tool in chat | Yes | `mcp__workshop__search` | wraps the index directly |
| Ref-addressable citations | Yes | `services/refs.py` | `workshop:<kind>/<key>` |
| Cross-scope recall with governance | Yes | `knowledge.recall` / `_can_recall` | policy-gated |
| A purpose-built memory-search service | No | n/a | this proposal does not build it |

So "we probably already have the most basic version" is correct, and it is
slightly better than basic (embedding-based, scope-governed, already tool-exposed).

---

## d. Cache-cost analysis, corrected

Claude API path only; the CLI path exposes no cache control to the harness so this
analysis does not apply there (it keeps the custom fold from the compaction plan).

| Moving part | Tier | Cache behavior |
|---|---|---|
| System prompt + tool defs | 1 | Cached, read ~0.1x; rewritten only on a tool-set/system change |
| Governance/role digest (`layered_index_block`), pinned notes | 1 | Cached; rewritten only when a note in a read layer changes. Free win: already small and rarely-changing, just never put behind a breakpoint |
| Prior turns + prior tool results + appended recalls | 2 | Cached as the append-only prefix; only the newest exchange is full price. NOT rebuilt per turn |
| This turn's new user message + this-turn tool calls | 3 | Full price, but small |
| A retrieval **tool result**, once returned | 2 (after append) | Full price the turn it lands, cached every turn after |
| A retrieval passage **pre-injected into the prefix** every turn | would break Tier 2 | AVOIDED by design: this is the one cache-hostile move, so retrieval is a tool, not a pre-injection |
| Routine tool-output eviction | adapter | `clear_tool_uses_20250919`: clears oldest tool results, runs after cache lookup, preserves the Tier 1 cache; the Tier 2 re-cache is the amortized cost |
| Rare long-horizon compaction | adapter | `compact_20260112` at a turn boundary; Tier 1 behind its own breakpoint, so only the shorter new history is a cache write |
| Model / effort / tool-set switch | not done mid-conversation | Would invalidate everything; the manager never does this mid-thread |

Net: the cost driver is **how often the prefix changes**, not how big the context
is. The design minimizes prefix changes (append-only Tier 2, retrieval-as-tool)
and batches the unavoidable ones (compaction) into rare, amortized events.
Anthropic reports context editing alone at +29% and context editing + memory tool
at +39% over baseline
([context management announcement](https://www.anthropic.com/news/context-management)),
and states it manages context "without destroying prompt cache prefixes" for the
expensive tiers, which is the same principle expressed as a product feature.

---

## e. Rolling/automatic operation, and manual control

Goal: the operator never has to think about it, but keeps a manual lever.

Automatic (default):

1. Every turn, append-only assembly. Tier 1 is read from cache, Tier 2 is the
   cached prior request, Tier 3 is the new exchange. No rebuild.
2. Retrieval trigger (v1, deliberately simple): fire `fetch_context` as a tool
   call when the new message is question- or reference-shaped ("did we...", "why
   is...", "what did we decide about...") or a cheap keyword-overlap check says
   the recent window likely lacks the needed material. Conservative and
   observable, not learned; the research doc flags that "how aggressively to
   retrieve" has no off-the-shelf answer.
3. Routine tool-output eviction every turn via the adapter (oldest-first, keep a
   floor). Non-destructive to the retrieval story: tool output evicted this way is
   never a retrieval target.
4. Compaction event only when Tier 2 crosses a token threshold, at the next turn
   boundary: summary + references (restorable compression). Because Tier 1 is
   untouched and retrieval can re-hydrate, this can run more aggressively than
   native compaction dares.

Manual (layered on top):

- An optional "focus this compaction on X" hint on a manual compaction pass.
- A visible transcript marker when a compaction event runs (already shipped for
  the custom fold; same treatment here).
- "Pull this back in" is already possible conversationally via
  `mcp__workshop__search`; a dedicated UI control is out of scope here.

---

## f. Phased plan

**Phase 0 (today, no code):** chats already have `mcp__workshop__search` and the
ambient digest. Starting point is not zero.

**Phase 1 (v1 on the existing substrate, CLI path untouched):** build the
`ContextRequest`/`ContextResult` interface and the `fetch_context` v1 wrapper;
build the Context Manager's three-tier assembly as a data-shape exercise with the
append-only discipline and the token-budget window (replacing message-count
windowing); implement the retrieval-trigger heuristic. Provider-adapter-agnostic;
testable before any Anthropic-specific wiring, since the CLI exposes no cache
control anyway.

**Phase 2 (Anthropic adapter, needs the API migration):** `AnthropicAdapter`
places the `cache_control` breakpoint at the Tier 1 / Tier 2 boundary, wires
`clear_tool_uses_20250919` for routine eviction and `compact_20260112` for rare
compaction with a Workshop-authored instruction; adopt the memory tool if it fits
the durable-memory tier. Instrument real conversation gaps to settle the
5-minute vs 1-hour TTL choice before defaulting it. Cannot ship against the
headless CLI.

**Phase 3 (v2 retrieval backend, separate project):** Workshop's own
memory-search service replaces the v1 `fetch_context` implementation behind the
same contract; only `backend` in the result changes. Not scoped here.

**Phase 4 (more provider adapters):** `CodexAdapter` etc. against that provider's
own cache/compaction primitives, once chat traffic actually routes through it. The
`ProviderAdapter` interface is defined early so the manager is never written
against Anthropic types; a second implementation is written only when a second
provider is real (no committed stub).

---

## g. Open questions for the operator

1. Does Phase 2's cache wiring wait for the chat backend's API migration (it must,
   technically), and should Phase 1 proceed against the CLI path in the meantime
   to prove the manager and retrieval interface out?
2. What should the v1 retrieval trigger key on: a crude "retrieve on any question"
   rule, or the slightly richer keyword-overlap heuristic, until real usage data
   exists?
3. How aggressive should the append-only window be before a compaction event
   fires: generous (rare compaction, retrieval supplements) or tight (frequent
   compaction, retrieval does more)? A product choice, not just engineering.
4. Should the memory/durable tier adopt Anthropic's **memory tool** (model-driven
   create/read/update/delete of memory files) on the API path, or stay on
   Workshop's own note store behind the retrieval interface? The memory tool is
   what Anthropic pairs with context editing for the +39% number.
5. Is the Phase 3 memory-search service already on the roadmap, or should Phase 1
   assume the substrate-backed v1 is what Workshop runs on for a while?

---

## h. Section 3 (parked, not built): smarter selective compaction

Recorded as an idea per the operator, 2026-08-20. NOT in scope for the current
build. This is the third of three sections the operator named: (1) regular
compact working, (2) better ways of pulling up context, (3) a smarter custom
compact. We build 1 and 2; we document 3.

The idea, in the operator's framing: instead of compaction being a blind
"summarize everything older than the tail," make it able to SEE all the pieces
that currently make up the context (the documents, the chunks, the individual
bits), and then act on them selectively: remove some, compress some, keep others
verbatim, and pull some back in later. A precise, per-chunk editor of the live
window rather than one summarize-the-front operation.

Why it is genuinely more than what sections 1 and 2 deliver:

- Regular compaction (section 1) treats the foldable history as one opaque span
  and produces one summary. It has no notion of "this decision matters, keep it
  verbatim; this tool dump is noise, drop it to a reference; this doc is stale,
  evict it entirely."
- Section 2's retrieval can pull things back, but it does not decide what to
  evict or compress in the first place; it answers queries.
- Section 3 is the policy layer that sits between them: it reads the assembled
  context as a set of addressable chunks, scores each one (recency, relevance to
  the current goal, whether it is recoverable via a ref, whether it is a
  decision or a byproduct), and issues per-chunk keep / compress / evict-to-ref
  actions. The result is a window that stays small without losing the
  load-bearing detail a blind summary would flatten.

What it depends on, and why it cannot be built yet:

- **Per-chunk addressability.** Section 3 needs every piece of context to be an
  individually addressable, individually removable unit with metadata (source,
  kind, ref, age, a cheap importance signal). Workshop does not have this yet;
  it is exactly what the storage-and-structure work
  (`storage-and-structure-model-2026-08-19.md`) and the `refs.py` file-ref work
  are moving toward. Build section 3 on top of that, not before it.
- **The API context primitives.** Doing this cache-safely on the production path
  means expressing the per-chunk edits as append-only / oldest-first operations
  (see section d), or riding Anthropic's context-editing primitives where they
  fit. Premature per-chunk rewriting mid-history is the cache-thrash trap.
- **A scoring model.** "Which chunk matters" is the open research problem the
  compaction research doc flags as having no off-the-shelf answer. Section 3
  wants at least a cheap, explainable importance signal first; a learned one is
  further out.

So the sequence is: section 1 (works now), section 2 (retrieval + the
`fetch_context` seam), then per-chunk addressability from the storage work, and
only then section 3 as the selective-compaction policy on top. Parked here so the
idea is not lost and its prerequisites are named.
