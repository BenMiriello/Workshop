# Conversation-context compaction and context management for Workshop chat

Status: research, 2026-08-20. Historical note (do not follow for "how to apply a change"):
see `docs/applying-changes.md` per repo convention.

Scope: how Claude Code compacts a conversation today, how Anthropic prompt caching
economics actually work and what breaks a cache, how other agent/chat frameworks manage
long conversations, whether Workshop's planned retrieval layer lets us beat native
compaction, and what unit of retention ("keep the last N ___") a good system should use.
Workshop's chat runs Claude two ways: today, headless Claude Code CLI
(`claude -p --input-format stream-json`, subscription auth); the target product, the raw
Claude API (per-token billing, server-side prompt caching Workshop controls directly).
Both are covered; the API path is weighted as the real product per the brief.

---

## Executive summary

1. **Claude Code's compaction is real but opaque, and not documented in the depth this
   report needed.** Anthropic does not publish the internals of Claude Code's `/compact`
   or auto-compact. Everything about triggers, the three-layer microcompaction/auto/manual
   design, and what specifically gets kept is community reverse-engineering (see Q1),
   not an Anthropic spec. Treat all Claude Code internals in this report as "best current
   understanding," not fact.
2. **The Claude API's compaction is a different, *documented* feature** (`compact_20260112`,
   beta header `compact-2026-01-12`), separate from whatever Claude Code does internally.
   It triggers at a configurable input-token threshold (default 150K), replaces *all*
   content before the compaction block with one LLM-generated summary, and is all-or-nothing:
   there is no way to compact only part of the history and keep an earlier stretch verbatim.
   Context editing (`clear_tool_uses_20250919`, `clear_thinking_20251015`) is a separate,
   more surgical primitive: no summarization call, just oldest-first clearing of tool
   results or thinking blocks down to a configurable "keep last N" floor.
3. **Prompt caching is a strict byte-prefix match; rewriting the prefix always cost real
   money.** Cache writes cost 1.25x base input price (5-minute TTL) or 2x (1-hour TTL);
   cache reads cost ~0.1x. Any compaction, summarization, or reordering that changes bytes
   at position N forces a full-price (or write-price) reprocessing of everything from N
   onward on the next request. There is no way around this for the content that actually
   changes; the only real lever is architecture: **keep the frozen part of the prompt
   physically separate from the part that changes**, so a compaction event only pays for
   what's new, not for re-writing the whole history.
4. **Anthropic's own docs already encode this mitigation.** Their compaction guide
   explicitly recommends a separate `cache_control` breakpoint at the end of the system
   prompt so the compaction summary (new, must be written) doesn't invalidate the
   system-prompt cache entry (old, should stay read-priced). This is the general pattern
   Workshop should copy: never let volatile content precede stable content in the rendered
   prefix.
5. **Every serious framework has converged on the same idea from a different angle:
   verbatim retrievable storage beats destructive summarization for accuracy.** A 2026
   controlled study (arXiv 2601.00821) found verbatim conversation chunks beat
   LLM-extracted "artifact" summaries by 16-22 accuracy points on long-conversation memory
   benchmarks; the fix wasn't better summarization, it was *not summarizing away the
   source*. MemGPT/Letta, LlamaIndex's `ChatSummaryMemoryBuffer`, and LangChain's
   LangGraph-store direction all separate "what's live in the prompt" from "what's
   durably stored and re-fetchable."
6. **Anthropic's own engineering guidance says compaction alone doesn't scale to very
   long multi-session work.** Their "effective harnesses for long-running agents" post
   is explicit that summarization repeatedly compounds information loss, and their fix
   for long-horizon coding agents was not better compaction but full context resets
   driven by structured handoff files (a progress log, a JSON feature list) that the next
   session reads instead of inheriting a summarized transcript.
7. **Workshop's planned retrieval layer changes the calculus completely**, and is the
   one lever native Claude Code and the native API primitives don't have: because nothing
   is destroyed (only evicted from the *live* prompt), Workshop can compact far more
   aggressively than either native mechanism dares to, and let the chat/agent re-pull
   specific detail on demand instead of hoping the one summary block guessed right about
   what would be needed later.
8. **Message count is the wrong retention unit.** A tool-heavy turn can be 50+ content
   blocks; a one-line reply is one. Good systems key on tokens (what caching, context
   windows, and billing actually measure), turn/exchange boundaries (a coherent unit of
   work), or research-validated "segment" granularity, not message count. There is also
   a hard technical unit Workshop must respect regardless of design: the **20-content-block
   cache lookback window**, which a message-count heuristic can silently blow past in
   tool-heavy turns.

**Recommendation in one paragraph:** don't try to out-engineer Claude Code's own
compaction on the CLI path; it isn't yours to change beyond one env var, and Workshop's
own tests already show `/compact` detaches the CLI session and forces a context rebuild,
i.e. it already cache-thrashes today. On the Claude API path (the real product), build a
deliberately boring two-tier prompt: a **stable, rarely-rewritten cached core** (system
prompt + durable memory, one cache breakpoint) and a **volatile tail** assembled per turn
from recent messages plus whatever Workshop's retrieval layer pulls back on demand,
placed after the breakpoint so it never costs a cache write. Use the API's
`clear_tool_uses_20250919` for routine tool-output eviction (cheap, surgical, no LLM call)
and reserve full `compact_20260112` summarization for genuine long-horizon compaction
events, run infrequently and always at a turn boundary. See "Recommendation for Workshop"
for three concrete options and their cache-cost tradeoffs.

---

## 1. Native Claude Code compaction: how sophisticated is it actually?

**Caveat up front, because it matters for how to read this section:** Anthropic has not
published a spec for Claude Code's internal compaction behavior comparable to the Claude
API's compaction docs. Every claim below that isn't explicitly marked "Anthropic API doc"
comes from community reverse-engineering: blog posts, a widely-cited comparison gist, and
a site (decodeclaude.com) that reads the CLI's own source/behavior. Treat confidence
accordingly.

### What `/compact` and auto-compaction do

- `/compact` is a manual slash command; there is also an automatic trigger when context
  usage crosses a threshold. Multiple independent write-ups converge on the auto-trigger
  being in the 83-95% range of the effective context window, but they **do not agree on
  the exact number**, and neither is an Anthropic-published figure:
  - One source (BSWEN, TurboAI) says the default is ~83% and that `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
    cannot raise it above that ceiling; a value of 95 is a no-op. [BSWEN](https://docs.bswen.com/blog/2026-03-21-claude-code-auto-compact-settings/), [TurboAI](https://www.turboai.dev/blog/claude-autocompact-pct-override-guide)
  - A separate comparison write-up puts the effective auto-trigger around 95% and
    describes it as "late," recommending manual compaction earlier. [badlogic gist](https://gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f)
  - Given this disagreement between two independent reverse-engineering efforts, **do not
    hard-code either number into Workshop's own logic**; if Workshop needs to reason
    about "how close is Claude Code to compacting," measure it empirically against the
    live product rather than trusting either published figure.
- When compaction runs, it replaces the conversation history with a generated summary.
  Community sources describe the summary as structured to capture: user intent, what
  changed, technical/architectural decisions, files touched, errors and their resolutions,
  and pending next steps. [decodeclaude.com](https://decodeclaude.com/compaction-deep-dive/), [howdoiuseai.com](https://www.howdoiuseai.com/blog/2026-04-16-what-does-compact-do-in-claude-code-context-management)
- After compaction, decodeclaude.com's write-up describes a "rehydration" step: the CLI
  re-reads the 5 most recently accessed files, restores the todo list and plan state, and
  injects a continuation instruction telling Claude to proceed without asking further
  questions. This is a specific, checkable claim from a single source; it is plausible
  given how Claude Code's file/todo tools work, but is not independently corroborated
  here.
- Manual `/compact` accepts a custom focus hint (e.g. "Focus on the database schema
  changes"); several sources note this gives the user more control than automatic
  compaction, where "Claude's model makes that call without your input, and it often gets
  it wrong, discarding architectural decisions and keeping mundane output." [howdoiuseai.com](https://www.howdoiuseai.com/blog/2026-04-16-what-does-compact-do-in-claude-code-context-management)

### "Microcompaction"

This term does exist in community write-ups (claudelog.com, decodeclaude.com) and
describes something distinct from full `/compact`:

- decodeclaude.com describes a **three-layer design**: microcompaction (cheapest,
  runs first), auto-compaction (full summarization at a headroom threshold), and manual
  `/compact` (user-triggered, at task boundaries). [decodeclaude.com](https://decodeclaude.com/compaction-deep-dive/)
- Microcompaction is described as operating per tool call: it keeps a "hot tail" of
  recent tool results (read, bash, grep, glob, web search, edit) visible to the model
  while moving older tool output to disk, rather than summarizing anything. This is
  presented as non-destructive and much cheaper than a full summarization pass.
- A second description (from the badlogic gist's broader survey, describing OpenCode
  rather than Claude Code specifically) frames the equivalent mechanism as pruning: scan
  backward through tool calls, protect the last ~40K tokens of tool output, and remove
  older tool output beyond a ~20K-token threshold. This is OpenCode's implementation, not
  Claude Code's; it's included here because it is a documented, source-visible
  implementation of the same idea Claude Code is reported to do opaquely, and gives a
  concrete sense of scale (tens of thousands of tokens, not the whole transcript). [badlogic gist](https://gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f)
- One source claims a cache-aware detail: "cached microcompaction" (warm cache) uses a
  server-side cache-edit path to drop old tool results without changing local message
  state or the cache key, while "time-based microcompaction" (cold cache, roughly
  matching the ~60-minute server cache TTL window described elsewhere) clears tool
  content inline instead. This is a single, unverified source; it is directionally
  consistent with how Anthropic's own `clear_tool_uses_20250919` context-editing primitive
  behaves (see Q2/Q4), which suggests Claude Code may be built on top of the same or a
  similar underlying API mechanism, but this report found no source that confirms that
  link explicitly.

### Tool outputs: handled specially, but not officially documented for Claude Code

Every reverse-engineering source agrees tool outputs get different treatment than
conversational text: they're the first thing evicted (microcompaction), and they're
explicitly called out as the safest thing to clear in Anthropic's own general context-
engineering guidance (see Q3). No source claims tool outputs get *summarized*, only
evicted/truncated/offloaded to disk.

### Cross-tool comparison (reverse-engineered, one source)

The badlogic gist is the single most useful source found for this question: it directly
compares Claude Code, OpenAI Codex CLI, sst/opencode, and Sourcegraph's Amp on this exact
axis. [badlogic gist](https://gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f)

| Tool | Trigger | Microcompaction / pruning | Notes |
|---|---|---|---|
| Claude Code | manual `/compact` or auto (~83-95%, disputed) | reported (see above) | summary structured around decisions/files/next-steps |
| Codex CLI | manual or `model_auto_compact_token_limit` (~180-244K tokens, ~95% of window) | not clearly separate from compaction | keeps last ~20K tokens of recent user messages verbatim alongside summary; docs warn accuracy degrades with repeated compaction |
| OpenCode | manual or `isOverflow()` (context_limit minus output_limit) | explicit: protects last 40K tokens of tool output, prunes older output past 20K tokens | only tool in the comparison with source-visible pruning logic; `OPENCODE_DISABLE_AUTOCOMPACT` env var |
| Amp | manual "Handoff" only, no auto-compaction | none; relies on Fork/Edit/Restore and manual thread references | philosophy is "keep conversations short," not "compact automatically" |

The comparison's own conclusion is worth stating directly: users of all three
auto-compacting tools report **quality degradation compounding across repeated
compactions**, and the general community recommendation is to compact manually, earlier,
and with a focus hint, rather than trust the automatic trigger.

### What this means for Workshop's CLI path today

Workshop already has direct, empirical evidence that Claude Code's `/compact` is a
cache-hostile operation on the CLI path: `backend/tests/test_chat_compact.py` documents
(in a comment, measured 2026-08-12 on a real thread) that `/compact` drops the underlying
CLI session, and the following turn has to reassemble/reconnect. That is exactly the
failure mode this report was commissioned to investigate: a compaction event forcing a
non-cached, full-price rebuild. This is not a hypothesis; it is something Workshop
observed. It confirms the general finding above (any compaction rewrites the prefix) is
not just a theoretical API-pricing concern, it is already happening on the subscription
CLI path today, just paid for in subscription usage rather than metered tokens.

---

## 2. Anthropic prompt caching economics

This section is sourced primarily from Anthropic's own documentation
(`platform.claude.com/docs/en/build-with-claude/prompt-caching`), fetched directly for
this report, cross-checked against the bundled `claude-api` skill reference.

### The mechanism

- **Prompt caching is a strict prefix match.** The system hashes the exact bytes of the
  rendered request up to a `cache_control` breakpoint. A cache write stores that hash; a
  cache read looks for a matching hash at the same position. **Any byte difference
  anywhere in the prefix invalidates the cache for everything at or after that point.**
- **Render order is fixed: `tools` → `system` → `messages`.** A breakpoint placed on the
  last system block caches tools and system together; it says nothing about invalidation
  caused by changes further down in `messages`.
- **Breakpoints, not the whole prompt, are what's cached.** Up to 4 `cache_control`
  breakpoints per request. Each one caches a chunk from the previous breakpoint through
  itself.
- **20-block lookback window.** A read at a breakpoint walks backward at most 20 content
  blocks looking for a matching prior write. In a tool-heavy agentic turn (many
  `tool_use`/`tool_result` pairs), a single turn can exceed 20 blocks, in which case the
  next request's breakpoint silently misses the previous cache entirely, no error, just
  a full-price write. Fix: place an intermediate breakpoint roughly every 15 blocks in
  long turns.
- **Minimum cacheable prefix is model-dependent and not monotonic across generations**:
  512 tokens (Opus 5 / Fable 5 / Mythos 5), 1,024 tokens (Opus 4.8, Sonnet 5, Sonnet 4.6,
  Sonnet 4.5, Opus 4.1, Opus 4), 2,048 tokens (Mythos Preview, Opus 4.7, Haiku 3.5), 4,096
  tokens (Opus 4.6, Opus 4.5, Haiku 4.5). Below the minimum, caching silently doesn't
  happen at all (`cache_creation_input_tokens: 0`, no error).

### TTL: 5-minute vs 1-hour

- Default (`{"type": "ephemeral"}`) is a 5-minute TTL, **measured from the start of the
  request that writes or reads it**, not from when the response finishes streaming; a
  slow multi-minute response eats into that window.
- Every cache *read* automatically refreshes the TTL at no extra cost, so continuous
  traffic (gaps shorter than the TTL) keeps a cache warm indefinitely for free.
- 1-hour TTL (`{"type": "ephemeral", "ttl": "1h"}`) exists for prompts reused less often
  than every 5 minutes but more than every hour, at double the write cost (below).

### Cost multipliers (exact, from Anthropic's docs)

| Category | Multiplier vs base input price |
|---|---|
| 5-minute cache write | **1.25x** |
| 1-hour cache write | **2x** |
| Cache read (hit or TTL refresh) | **~0.1x** |
| Uncached input | 1x |
| Output | separate, much higher, rate (unaffected by caching) |

Break-even math, worked from these multipliers:

- **5-minute TTL**: write once (1.25x) then read once (0.1x) = 1.35x total for two uses,
  versus 2.0x for paying full price twice. Breaks even after the *second* request that
  reuses the prefix.
- **1-hour TTL**: write once (2x) then read once (0.1x) = 2.2x for two uses, versus 2.0x
  uncached, i.e. **still worse than not caching after only two uses**. It needs a third
  read (2x + 0.2x = 2.2x vs 3.0x uncached) to pay off. The 1-hour TTL trades a higher
  up-front cost for surviving longer gaps between requests; it is not simply "the safe
  default," it needs enough reuse to be worth it.

This directly bears on chat UX: a human chatting with an agent has real gaps (reading,
typing) that can exceed 5 minutes. If Workshop's chat traffic pattern has gaps in that
range, the 5-minute TTL will silently miss and re-write at full 1.25x cost repeatedly; the
1-hour TTL avoids that but only pays off with enough turns per conversation. This is an
empirical question about Workshop's actual usage pattern (see "Open questions").

### What invalidates a cache: the operator's core concern, precisely stated

The invalidation hierarchy has three tiers (`tools` → `system` → `messages`); a change
only invalidates its own tier and everything after it, not tiers before it:

| Change | Invalidates tools cache | Invalidates system cache | Invalidates messages cache |
|---|:---:|:---:|:---:|
| Tool definitions (add/remove/reorder) | yes | yes | yes |
| Model switch | yes | yes | yes |
| `speed`, web-search toggle, citations toggle | yes | no | no |
| System prompt content | yes | no | no |
| `tool_choice`, images, thinking enable/disable | yes | yes | no |
| Message content (this is what compaction touches) | yes | yes | no |

Two important readings of this table for compaction specifically:

1. **Compacting or rewriting the message history invalidates the messages cache tier and
   nothing above it**, *if* the tools and system prompt are otherwise unchanged and
   physically precede the messages in the render order. This is the load-bearing fact:
   a well-designed compaction only costs a rewrite of the messages tier, not the whole
   request, provided the system/tools prefix stays byte-identical.
2. **If the system prompt is what gets edited to reflect state** (a common pattern:
   interpolating "current mode: X" or "current date: Y" into the system prompt), that
   invalidates the system tier and forces the *entire* downstream conversation history to
   be reprocessed uncached on the next request, which is far worse than compacting
   messages. Anthropic's docs explicitly recommend the mid-conversation `role: "system"`
   message channel (appended to `messages[]`, after the cached prefix) instead of editing
   top-level `system`, specifically to avoid this. Available on Opus 5, Opus 4.8, Fable 5,
   Mythos 5; **not** on Sonnet 5 (Sonnet 5 must fall back to a text block inside a user
   turn, which is spoofable and has no cache advantage over ordinary message content, but
   at least doesn't touch the system tier).

### How Anthropic's own compaction feature avoids thrashing the cache

This is documented, not inferred: Anthropic's compaction guide explicitly recommends
placing a `cache_control` breakpoint on the **end of the system prompt**, separate from
any breakpoint in the messages. Their stated reasoning: "When compaction occurs, the
summary becomes new content requiring cache write. Without this breakpoint, the cached
system prompt would be invalidated. With it: system prompt cache remains valid (read from
cache); only the compaction summary needs to be written as a new cache entry." This is
the single clearest piece of prescriptive guidance found in this research and should be
treated as the template for any of Workshop's own compaction logic: **isolate the frozen
prefix behind its own breakpoint, so paying to rewrite the volatile part never also pays
to rewrite the stable part.**

### Practical design rules that follow from all of the above

1. Never interpolate anything that changes per-request (timestamps, UUIDs, unsorted JSON
   keys) into the system prompt or tool definitions; it silently kills caching for the
   entire downstream prefix, and there is no error, just `cache_read_input_tokens: 0`.
2. Give the frozen system prompt its own breakpoint, separate from the messages
   breakpoint.
3. Only compact at a stable point (a turn boundary, not mid-tool-call), and place the
   compaction/summary content, once generated, before any further volatile per-turn
   content so subsequent turns can read it from cache rather than rewriting it again.
4. Don't compact every turn. Each compaction event is itself a fresh, uncached write of
   at least the new summary text; compacting on every single message defeats caching
   entirely and is strictly worse than not compacting until actually necessary.
5. Watch the 20-block lookback window on tool-heavy turns.
6. Never change tools or switch models mid-conversation if avoidable; both invalidate
   everything, with no cache-preserving escape hatch for a model switch (route cheaper
   sub-tasks to a subagent on a different model instead of switching the main loop's
   model).

Sources: [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Anthropic compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction), [Anthropic context windows docs](https://platform.claude.com/docs/en/build-with-claude/context-windows), [Anthropic context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing).

---

## 3. How others do it: a survey of real approaches

### Rolling / sliding-window + summary (the baseline pattern)

LlamaIndex's `ChatSummaryMemoryBuffer` is a clean, documented example: it keeps the most
recent messages verbatim up to a token limit, and **iteratively summarizes everything
older** using the configured LLM, rather than doing a single one-shot summary of the
whole history. `ChatMemoryBuffer` (no summarization) is the pure sliding-window baseline:
last N messages that fit a token limit, nothing else. [LlamaIndex memory docs](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/), [Chat Summary Memory Buffer](https://docs.llamaindex.ai/en/stable/examples/agent/memory/summary_memory_buffer/)

Tradeoff: cheap and simple, but every summarization pass is destructive (the original
text is gone) and, per the accuracy research below, measurably worse for later recall
than keeping the source text retrievable.

### Hierarchical / recursive summarization

Referenced in the literature search (e.g. "Recursively Summarizing Enables Long-Term
Dialogue Memory in LLMs," arXiv 2308.15022) as summarizing summaries: periodically
re-summarize an existing summary plus new turns into a new, still-bounded summary, so the
summary itself doesn't grow unboundedly across a very long conversation. This is the same
idea Anthropic's Claude API compaction implements structurally: each new compaction
supersedes the last, and re-applying a previous compaction block costs nothing extra.
Tradeoff: bounds size well, but compounds information loss with every additional
summarization pass, exactly the degradation multiple Claude Code / Codex CLI users
reported in Q1.

### RAG / retrieval over past turns

LlamaIndex's `VectorMemory` stores chat messages in a vector database and returns the
messages most semantically similar to the latest user message (no ordering guarantee);
`SimpleComposableMemory` composes a `VectorMemory` with a `ChatMemoryBuffer` or
`ChatSummaryMemoryBuffer` so a conversation gets both a recency window and topically
relevant older material. [LlamaIndex memory docs](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/)

The broader research literature (arXiv 2502.05589, "On Memory Construction and Retrieval
for Personalized Conversational Agents") finds that **retrieval granularity matters as
much as the retrieval mechanism itself**: turn-level retrieval is "too fine-grained,
leading to fragmentary and incomplete context," session-level is "too coarse-grained,"
and segment-level (topically coherent multi-turn chunks) is the reported sweet spot. This
is directly relevant to Q5 (the unit problem) below.

Tradeoff: nothing is destroyed, and recall of specific old facts is much better than pure
summarization, but retrieval has its own failure mode: naive vector similarity "return[s]
broad evidence sets once the memory store grows," i.e. retrieval quality degrades with
scale too, just differently than summarization does.

### Memory extraction to a structured store: MemGPT / Letta

MemGPT (now Letta) is the most fully worked-out "OS-inspired" architecture found in this
survey: a two-tier memory model with **core memory** (small, size-limited, always
in-context, embedded directly in the system prompt, and *self-edited by the agent via
tool calls*) and **archival/recall memory** (an external store, in Letta's case
Postgres+pgvector, paged in and out via explicit function calls the model itself issues).
[Letta docs](https://docs.letta.com/guides/legacy/memgpt_agents_legacy), [Leonie Monigatti's MemGPT write-up](https://www.leoniemonigatti.com/blog/memgpt.html)

The key design idea, distinct from both pure summarization and pure RAG: **the model
decides what's important enough for core memory and what to relegate to archival
storage**, rather than a fixed heuristic doing it. This is effectively "structured
note-taking," which Anthropic's own engineering guidance (Q4 below) independently arrives
at and has since formalized as a first-class "memory tool" on the Claude API.

Tradeoff: gives the agent real agency over its own memory, which can be powerful, but
means memory quality is bounded by the model's own judgment about what's worth keeping,
and it's another tool-call round trip (latency, tokens) every time the agent wants to
read or write memory.

### Tool-output eviction / truncation

Universal across every agent framework surveyed: tool results are the first and safest
thing to evict, because once a tool result has been read and acted on, the raw bytes
rarely need to stay in context. This is what Claude Code's microcompaction, OpenCode's
pruning, and Anthropic's own `clear_tool_uses_20250919` context-editing primitive all do,
independently converging on the same conclusion. Anthropic's context-engineering blog
post states this directly: clearing tool call results is "one of the safest, lightest
touch forms" of context management.

### Importance-based retention

Less standardized across frameworks than the other approaches; mostly shows up as a
research direction (arXiv results in this survey, e.g. "HiGMem," "Control-Plane Placement
Shapes Forgetting") rather than a shipped, documented product feature. The practical
version that *is* shipped is `exclude_tools` in Anthropic's context editing (protect
specific tools' results from ever being cleared, e.g. never clear `web_search`), which is
importance-based retention at the tool-type granularity rather than per-message.

### "Rolling automatic compaction so the user never thinks about it"

This is what Claude Code, Codex CLI, and OpenCode all do by default (Amp deliberately does
not, betting on user discipline instead). The near-universal community complaint in every
source surveyed is the same: automatic compaction, run by the model with no human
oversight of what's kept, degrades quality across repeated compactions and users
learn to trigger it manually, earlier, with an explicit focus hint, specifically to
retain control over what's discarded. This is a strong signal that "fully automatic, no
visibility" is not actually what sophisticated users want, even though it is the
lowest-friction default.

---

## 4. Can Workshop do better than native, given its retrieval layer?

Workshop is also building smart search/discovery over a user's chats, memory, and files
within a scope, with tools that let chats/agents pull relevant context in on demand. This
changes what's possible in a way neither Claude Code's `/compact` nor the raw API's
`compact_20260112` can offer, because both of those are **destructive**: the API's
compaction block replaces *everything before it* with one generated summary, all-or-
nothing, and Claude Code's `/compact` does the same thing opaquely. Neither can compact
just one section while leaving another verbatim, and neither can un-compact on demand.

With a retrieval layer that indexes the full, uncompacted history, memory, and files
independently of what's live in the model's prompt, four things become possible that
native compaction cannot do:

### 4a. Compact aggressively, re-hydrate specific detail on demand

Because the source of truth lives in Workshop's index, not only in the live prompt, the
live prompt can be compacted far more aggressively than Claude Code or the API's
compaction would dare (both are tuned conservatively, to avoid losing something the model
might need later, precisely because they *can't* get it back). Workshop can instead give
the chat/agent a retrieval tool and let it re-pull the original text of a compacted
section when it turns out to matter. This directly matches the empirical finding in Q3:
verbatim chunks retrieved on demand outperform extracted/summarized artifacts by a wide
margin (16-22 points on LoCoMo/LongMemEval, arXiv 2601.00821); the fix wasn't better
summarization, it was not summarizing away the retrievable source.

**Cache-cost interaction:** favorable. The compacted, in-prompt summary stays small and
stable (good for caching, few tokens to rewrite on the rare occasions it changes); the
retrieved detail is fetched per-turn as ordinary volatile tail content that was never
going to be cached anyway (it differs every time by construction). Nothing is lost by
putting it after the cache breakpoint, because it wasn't cacheable in the first place.

**Feasibility:** high. This is a straight application of Workshop's planned retrieval
tool plus a `clear_tool_uses_20250919`-style eviction policy or a Workshop-authored
compaction pass; no new Anthropic primitive is required.

### 4b. Compact specific sections, not a blind tail

Native compaction (both Claude Code's and the API's) operates on a single boundary: keep
everything after position N, replace everything before it. It has no concept of "this
early architectural decision still matters, compact the tool-noise from that abandoned
debugging detour in the middle instead." Workshop's chat log already models the
conversation as more than a flat list (per `backend/services/chat_branches.py` and the
chat-log boundary/cut-mode machinery referenced in `backend/tests/test_chat_compact.py`),
which is a reasonable substrate for topic- or task-aware compaction: identify a completed
sub-task or a tool-heavy digression by its turn boundaries, collapse only that span, and
leave surrounding history alone.

**Cache-cost interaction:** this is the one place native's naive discipline (single
boundary, invalidate everything after it) is actually *cheaper* per operation than
section-targeted compaction. Rewriting a section in the *middle* of the prefix
invalidates the cache for everything *after* that section too, not just the section
itself (prefix matching is positional, not content-addressed by section). Section-targeted
compaction only pays off if the collapsed section is late enough in the conversation, or
frequent enough to amortize, that the win from a smaller/cleaner prompt outweighs the
extra cache-write cost of the tail that gets pulled along with it. This needs to be
designed deliberately, not assumed to be free.

**Feasibility:** medium. Requires Workshop-side logic to identify section boundaries
(the chat-log's existing branch/boundary model is a start) and to actually perform the
rewrite; no blocking Anthropic-side gap, but real engineering.

### 4c. Stable cached core + retrieved volatile tail

This is the architecture implied directly by the cache economics in Q2: put the frozen
system prompt and a durable, rarely-changing "core" (Letta-style persistent facts/memory,
or Workshop's own long-lived project/agent context) behind one cache breakpoint that is
almost never rewritten, and assemble everything else per-turn from recent messages plus
retrieval results, placed after that breakpoint. The core stays cheap to re-read
(~0.1x) turn after turn; the tail is priced at ordinary input-token rates because it
was never going to be cacheable anyway.

**Cache-cost interaction:** the best of the options assessed here. It is the direct
translation of Anthropic's own "isolate the frozen prefix behind its own breakpoint"
guidance (Q2), extended with retrieval standing in for "whatever used to be summarized
inline."

**Feasibility:** high on the Claude API path; **not available on the Claude Code CLI
path**, because Claude Code doesn't expose `cache_control` placement or
`context_management` to the calling harness. This is one of the clearest concrete reasons
the API path is the one worth building this on.

### 4d. Per-section add/remove of context

The mid-conversation `role: "system"` message (Opus 5, Opus 4.8, Fable 5, Mythos 5; not
Sonnet 5) and the `tool_addition`/`tool_removal` block mechanism (Opus 5 onward, beta)
both let Workshop inject or withdraw a specific piece of context *after* the cached
prefix without touching the cached part at all. Combined with retrieval, this gives
Workshop a "pull in relevant memory/file/chat context for this turn only, then let it
fall out of the live prompt on the next turn" pattern, which is a closer match to how a
human actually keeps a workspace in mind (relevant things on the desk while working on
them, filed away after) than either "keep everything" or "summarize everything."

**Cache-cost interaction:** neutral to favorable, same reasoning as 4a: content that's
added after the breakpoint and known to be turn-specific was never cacheable, so there's
no cache cost being given up by using this instead of baking it into the summary.

**Feasibility:** high, but model-gated (excludes Sonnet 5 for the system-message channel
specifically; the underlying "put volatile content after the breakpoint" pattern works on
any model, it's only the operator-authority system-message channel that's restricted).

---

## 5. The unit problem: what should Workshop actually count against?

"Keep the last 6 messages" is a bad heuristic for reasons this research surfaces from
multiple independent angles:

- **Messages vary enormously in size and shape.** A single agentic turn can be one
  `assistant` message containing dozens of `tool_use`/`tool_result` content blocks; a
  plain reply is one text block. Counting messages says nothing about tokens, which is
  what context windows, cache minimums, and billing all actually measure.
- **The 20-block cache lookback window is a real, silent constraint that a message-count
  heuristic can violate without any error being raised.** If "the last 6 messages" happen
  to contain more than 20 content blocks (trivial in a tool-heavy exchange), the next
  request's cache breakpoint can't find the prior write and silently pays full price.
  This is not a hypothetical: it is exactly the kind of bug that produces "why is
  `cache_read_input_tokens` zero" with no error message anywhere.
- **Segment/turn granularity beats both finer and coarser units for retrieval accuracy.**
  The research surveyed in Q3 (arXiv 2502.05589) found turn-level retrieval too
  fragmentary and session-level too coarse; topically coherent multi-turn segments were
  the reported sweet spot. This argues for retention/retrieval units aligned to
  *semantic* turn boundaries (a user request through its full resolution, including all
  tool activity), not raw message counts.
- **Token budget is what the API itself surfaces and reasons about.** Claude's own
  "context awareness" feature (Sonnet 5, Sonnet 4.6, Sonnet 4.5, Haiku 4.5) injects a
  running `<budget:token_budget>` and post-tool-call `<system_warning>` of remaining
  tokens directly into the model's context, i.e. Anthropic itself treats token budget,
  not message count, as the unit the model should reason about its own remaining space in.
  Workshop's own retention/compaction triggers should key on the same unit the model is
  already being told to think in.
- **What survived the "verbatim chunks beat artifacts" study (arXiv 2601.00821) wasn't a
  unit choice at all, it was a storage-format choice**: raw chunks of source text, not
  extracted structured facts. This is a useful complement to the unit question: even once
  Workshop picks the right *granularity* (turn/segment, sized by token budget), it should
  store that unit's *original text*, not a lossy extraction of it, if it wants retrieval
  to actually help later.

**Recommendation for the retention unit:** key retention/eviction decisions on **token
budget within turn/exchange boundaries**, i.e. never split a turn's tool-call sequence
mid-way, but decide how many whole turns/segments to keep live by summing their tokens
against a target budget, not by counting how many turns that is. Index at the same
turn/segment granularity for retrieval, storing the original text, not a summary, as the
retrievable unit.

---

## Recommendation for Workshop

Three concrete options, in increasing order of engineering investment and payoff.

### Option A: Native-CC-only (status quo)

Keep relying on Claude Code's built-in three-layer compaction on the headless CLI path;
do nothing bespoke.

- **Cache-cost profile:** entirely outside Workshop's control. Whatever Claude Code's
  internal harness does, including the empirically-observed session-drop on `/compact`
  (Q1), happens regardless of anything Workshop builds.
- **Pros:** zero engineering cost; inherits Anthropic's ongoing improvements to Claude
  Code for free; the only lever Workshop has (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) is
  already available with no code changes.
- **Cons:** opaque and undocumented internals (Q1); no section-targeting, no retrieval
  integration, no cache-breakpoint control; confirmed (not hypothesized) to already
  cache-thrash on the CLI path today; and structurally unavailable on the API path, since
  Claude Code's compaction is a CLI/Agent-SDK-internal behavior, not the same mechanism
  as the API's `compact_20260112`. Building nothing here does not carry forward to the
  real product.

### Option B: Our-own-rolling-compaction (Workshop-owned, API-native)

Move the chat path to the raw Claude API and implement compaction directly against
Anthropic's documented primitives: `context_management.edits` with
`clear_tool_uses_20250919` for routine, cheap, non-destructive-to-conversation-flow tool-
output eviction, and `compact_20260112` (with a custom `instructions` prompt tuned for
Workshop's needs) for genuine long-horizon summarization, run infrequently and only at
turn boundaries. Follow Anthropic's own cache-preserving pattern: isolate the system
prompt behind its own `cache_control` breakpoint, separate from the messages breakpoint,
so a compaction event only pays to rewrite the new summary, never the frozen system
prompt.

- **Cache-cost profile:** clean and Workshop-controlled. Reads of the stable system
  prompt stay cheap indefinitely; writes are paid only for what's actually new
  (the compaction summary, or the cleared-tool-result placeholder text), scaling with
  compaction *frequency*, not conversation length.
- **Pros:** documented, supported primitives; full visibility into what's kept/dropped;
  works the same way regardless of conversation length; sets up the eventual retrieval
  integration (Option C) as an incremental addition rather than a rewrite.
- **Cons:** meaningful engineering lift; still shares the fundamental limitation of
  native compaction, an all-or-nothing summary boundary with no section-targeting and no
  way to recover detail once compacted away; not available on the CLI path at all
  (`context_management` and `cache_control` placement are not exposed by the Claude Code
  CLI to the calling harness), so this option effectively requires committing to the API
  migration for chat now rather than later.

### Option C: Hybrid with retrieval (recommended)

Everything in Option B, plus wiring Workshop's planned search/discovery layer in as the
system of record: index the full, uncompacted transcript/memory/files independently of
what's live in the prompt; keep a small, stable, cache-friendly "core" (system prompt +
durable memory) behind one long-lived breakpoint; assemble the volatile tail per turn
from recent messages plus whatever the retrieval tool pulls back; and give the
chat/agent a retrieval tool so it can re-hydrate specific detail on demand instead of
depending on one generated summary having guessed correctly what would matter later.

- **Cache-cost profile:** best of the three. The stable core amortizes its cache-read
  discount over the whole conversation; retrieved/volatile tail content sits after the
  breakpoint and was never going to be cacheable regardless of how it's sourced, so
  routing it through retrieval instead of baking it into a summary costs nothing extra in
  cache terms while recovering fidelity that summarization would have destroyed.
- **Pros:** the only option that gets section-targeted, non-destructive compaction (4a-4d
  above); directly backed by the strongest single piece of empirical evidence in this
  research (verbatim retrievable chunks beating extracted summaries by 16-22 points,
  arXiv 2601.00821); matches Anthropic's own stated conclusion that summarization alone
  doesn't scale to long-horizon work and needs a durable, structured external store
  alongside it.
- **Cons:** highest engineering cost of the three; depends on the retrieval layer
  actually shipping and being fast/accurate enough to trust for on-demand rehydration
  mid-conversation, not just for offline search; needs real design work on triggering
  (when does the agent decide to retrieve vs. trust the compacted summary) that has no
  off-the-shelf answer in any framework surveyed here.

---

## Open questions for the operator

1. **Does this force the chat backend off headless Claude Code CLI onto the raw API now,
   rather than later?** Options B and C both require primitives (`context_management`,
   `cache_control` placement) the CLI doesn't expose. If the API migration for chat isn't
   imminent, is it worth doing any bespoke compaction work at all before then, versus
   accepting Claude Code's opaque behavior (Option A) as an interim state?
2. **How aggressively should the live prompt be compacted, given retrieval can re-hydrate
   detail?** This is a real design knob, not a solved question in any framework surveyed:
   too aggressive risks the agent not knowing *that* something exists to retrieve at all
   (it can't ask for what it doesn't know it's missing); too conservative forfeits the
   cost/quality win retrieval is supposed to buy. Needs an explicit decision, likely
   informed by watching real Workshop chats rather than guessed up front.
3. **5-minute vs 1-hour cache TTL for the stable core:** this depends on actual gaps
   between messages in real Workshop conversations (reading/typing time), which this
   report did not have access to measure. Worth instrumenting before picking, since the
   1-hour TTL only pays off with three or more reads and is a real cost if guessed wrong.
4. **Should the "core memory" be user-visible/editable (Letta's core-memory pattern, or
   Workshop's Edit-mode) or fully agent-managed?** This is a product/trust decision, not
   a technical one, but it determines whether Option C's "core" needs UI.
5. **Is the CLI-path `/compact` session-drop (confirmed in `backend/tests/test_chat_compact.py`)
   something that needs a near-term fix or workaround regardless of the longer-term API
   migration**, since it is a live cache-thrashing issue on the product as it ships today,
   not just a future-state design concern?
