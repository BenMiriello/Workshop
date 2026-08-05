# 0011. The model manages the conversation; we manage what enters it

- Status: accepted
- Date: 2026-08-04
- Depends on: ADR 0008 (autonomy override semantics)
- Supersedes: nothing. Records a direction previously held only as an operator wish.

## Context

The operator's standing wish was that Workshop manage its own context and memory:
cull old context, run its own compaction, decide what to retain. Stated as a
wish, it predates knowing how the transport actually behaves. The reality is
more constrained, and getting it wrong is expensive in exactly the way the wish
was meant to avoid.

Three facts settle it, all verified rather than assumed:

1. **Every chat turn is a `claude -p` subprocess, not an API call.** All cloud
   calls route through `llm_router.call_cloud`. Each thread holds one persistent
   CLI session (`--session-id` on the first turn, `--resume` after), so history
   is never replayed by us.
2. **The CLI already compacts.** At roughly 90-95% of the window it summarises
   the conversation and continues from the summary. Anything we built would
   duplicate or fight it.
3. **Cache invalidation runs `tools -> system -> messages`.** A change at any
   level invalidates that level and everything after it, and Anthropic's
   guidance is explicit that dynamic values must not be interpolated into the
   system prompt. Appending to the end of the messages is the cheap direction.

The problem we actually had was none of these. Volatile blocks (the live app
snapshot, the turn contract, retrieved notes) were appended every turn and never
retired, so a long conversation accumulated many copies and the stale ones stayed
readable. That is context bloat plus a correctness hazard, not a caching fault.

## Decision

**Pattern B: working context plus an external retrieval store.** The model owns
the conversation; we own what enters it and what persists outside it.

Concretely, four rules:

1. **Do not build compaction, summarisation, or eviction.** The CLI does it. If
   we later add an API transport, revisit — do not pre-build for it.
2. **Volatile context is superseding and deduplicated, never accumulating.** A
   re-sent block states that it replaces earlier copies; an unchanged block is
   not re-sent at all.
3. **Durable memory is notes and tickets on disk, scoped.** This is our
   equivalent of a file-backed memory tool, and it already exists. Chats read it
   on demand rather than carrying it.
4. **Knowing something changed must not cost a semantic search.** Writers bump a
   per-scope integer (`services/scope_version`); readers compare one integer.
   Embeddings answer "which note is relevant", never "did anything happen".

**Sub-agent work stays as tickets and runs.** Delegating to a fresh context and
receiving a short report back is the same pattern the ecosystem converged on;
ours adds a durable receipt, a reviewer gate, and a UI. A finished run posts a
condensed summary into the chat that filed it, and never its transcript: the
value of delegation is that the delegated context does not come back.

## Consequences

- Long conversations stay the CLI's problem, which is where the effort already is.
- Cross-chat freshness costs an integer compare per turn instead of a search, so
  it can be on by default without a token bill.
- The live CLI session is never injected into from outside. A run report lands in
  the transcript for the operator; the model learns via the scope counter. These
  are deliberately different channels.
- Provider portability is bounded by *session semantics*, not by model names:
  `claude_session` / `session_id` / `--resume` appear in `chat_store`,
  `build_agents`, `build_streaming` and `routes/chat`. An API transport replays
  messages instead. The refactor that unblocks it is a `Conversation` port with
  CLI-session and API-replay implementations, storing an opaque handle. Not built
  yet; recorded so it is not designed around.

## Rejected

- **Our own compaction / summarisation loop.** Duplicates the CLI, and
  irreversible summarisation eventually drops something needed.
- **Embedding everything and polling for topic changes.** Pays retrieval on every
  turn in every chat to learn, almost always, that nothing happened. Event-driven
  invalidation gets the same property for an integer compare. Embeddings remain
  in retrieval, where they earn their cost.
- **Injecting run results into the live session.** Corrupts a running
  conversation. The transcript plus the scope counter cover it.
