# Chat context and compaction: the two-path model, and what we stop injecting

Status: decision + in-progress, 2026-08-25. Supersedes the "keep our custom fold,
tune its retention unit" direction implied by
[compaction-both-paths-plan-2026-08-20.md](compaction-both-paths-plan-2026-08-20.md)
where the two conflict. Builds on the research in
[context-compaction-research-2026-08-20.md](context-compaction-research-2026-08-20.md)
and [custom-context-management-proposal-2026-08-20.md](custom-context-management-proposal-2026-08-20.md).

This doc exists because the operator asked, correctly, why a chat re-injects state
every turn, why we run our own compaction instead of the default, and how the whole
thing should actually be designed given two runtimes: `claude -p` in DEVELOPMENT
only, and the raw Claude API in PRODUCTION where `claude -p` must not exist in the
code, be reachable, or be a thing the system is even aware of.

---

## 1. How a chat turn actually runs today (the ground truth)

- A turn spawns the Claude Code CLI as a detached process, with `--resume
  <session_id>` when the thread already has a session, else `--session-id`
  (`build_streaming.py:2184`). **The CLI holds the real transcript on disk and
  accumulates it turn over turn.** The chat is NOT stateless and does have the
  conversation; the earlier claim that "the transcript is the small part" was
  wrong.
- First turn of a session: the full prompt is assembled once (persona, platform
  layers, working-state, PROJECT.md) and handed over. It lives in the resumed
  session after that; it is not re-sent every turn.
- Every later turn sends only a small delta (`_resumed_prompt`,
  `chat.py:2820`): the stop rule, the mode line, a contract refresh only when the
  contract digest moved, and a state snapshot that is deduplicated (a byte-identical
  snapshot is replaced by a one-line "unchanged" pointer).
- There is **no raw-API path today**. 100% of chat is on the CLI. `llm_router.call_cloud`
  also shells out to `claude -p` (for thread-naming, categorization, and the fold
  summarizer). The only provider seam that exists is `compaction_adapter.py`
  (`ClaudeCLIAdapter`, one implementation, declared but not yet load-bearing).

Empirically measured 2026-08-25 (small Haiku sessions, this box, CLI 2.1.143):

- Resume accumulates context: cache_read climbed 17k → 40k across four resume
  turns; the session carries the real history.
- **Native compaction does not fire on the headless path.** Manual `/compact` was
  already proven dead in `-p` (2026-08-12: it parses to an unexecuted local-command
  block). Automatic compaction: with the window forced to 5000 tokens
  (`CLAUDE_CODE_AUTO_COMPACT_WINDOW=5000`) and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25`,
  four resume turns drove context to ~40k with **zero `compact_boundary` events**
  and monotonically growing cache_read (a real compaction would collapse it). The
  env knobs have no observable effect in `-p`.

**Consequence:** on the dev/CLI path there is no native compaction to fall back on,
manual or automatic. If we delete our own fold on that path, an unbounded chat grows
until it hits the window and errors. So a minimal Workshop-owned compaction has to
survive on the CLI path. On the API path, the opposite is true: real compaction is a
documented, supported primitive and our fold should not exist there at all.

---

## 2. State injection: what it was, why it was wrong, what replaces it

### What was happening

Two separate things, not "the whole context every turn":

1. **The working-state block** (`working_state_block`, `chat_context.py`): a small
   (~1200-token) handoff written at each compaction so a new chat in the same scope
   inherits "what was I doing" instead of nothing. Injected once in the first-turn
   prefix, not re-appended on resume. This is legitimate memory and stays.

2. **`live_context()` for project/dev/maker chats**: PROJECT.md in full, plus a
   ticket ROLL-UP (`todo_summary_block`: status counts over the whole scope's task
   store plus up to 8 ticket titles). Appended in the volatile tail, and on resume
   re-appended whenever its digest changed. In a busy scope the task store changes
   under the chat constantly (other agents filing/closing tickets), so the roll-up's
   digest moved most turns, so a fresh copy of the scope's ticket counts was pushed
   into most turns. Nobody asked for that, and polling the whole ticket list into a
   conversation is the wrong shape regardless of how small the list is.

History note, for honesty: the roll-up (commit `2b854321`) was itself damage
control. It replaced an even worse prior behavior that inlined the ENTIRE 148k-token
TODO.md export into every turn's prefix. So it was a reduction, not a fresh mistake,
but the shape was still wrong.

### The rule

State does not get polled into a conversation. State changes are the natural
evolution of the chat:

- The user types. The system appends a message when a **real event actually
  happens** (a run finishes, a ticket the chat is about closes, an agent reports
  back), delivered ONCE at that moment as an appended system message.
- Anything the agent needs to KNOW rather than be TOLD, it pulls on demand. Ticket
  state is already reachable through the `query`/`search` MCP tools and
  `project_tasks.py list-json`/`next-ready`; the persona already points there. There
  is no reason to push the list.

This also matches the cache economics (section 4): appending a real event is cheap;
re-pushing a changing snapshot every turn is pure waste even when it dedupes, and on
the API path a snapshot placed anywhere but the very end would invalidate the cached
prefix.

### Done in this pass (2026-08-25)

- Removed the ticket roll-up from `ProjectContextProvider.live_context` and
  `MakerAgentContextProvider.live_context`. `live_context` now returns PROJECT.md
  orientation only (the brief; it changes rarely and dedupes on resume). Deleted the
  now-unused `todo_summary_block` and its helpers. Tests green.
- PROJECT.md is kept as orientation deliberately, not by omission. If we want pure
  event-driven with zero standing state, it moves into the one-time first-turn
  assembly instead of `live_context`; flagged as an open choice below.

### Still to build (the event channel)

A single sanctioned way for another system to append a one-time message into a chat
when something happens ("run 8fe2 finished: 3 files changed", "ticket X you filed is
in review"). This is an append, never a poll, and never a periodic list. Shape to
confirm with the operator before building: does it target a chat by scope, by
explicit subscription, or only chats that spawned the work?

---

## 3. Compaction: default on the API, minimal fold only in dev, behind the seam

Decision: **prefer the platform's own compaction, do not out-engineer it.** Concretely:

- **Production / API path:** use the Claude API's documented `compact_20260112`
  (summarize at a token threshold, at a turn boundary), plus `clear_tool_uses`
  context-editing for tool-output eviction, both behind the API adapter. Our own
  summarizer fold is NOT part of this path and is not referenced by it.
- **Development / CLI path:** the CLI has no native compaction (section 1), so keep
  a MINIMAL Workshop fold as the `ClaudeCLIAdapter`'s implementation only. It exists
  because the dev runtime cannot do otherwise, it is dev-only, and production neither
  imports nor is aware of it.

The seam that makes production `claude -p`-free is section 5.

### Cache-preserving rules (why the default is also the cheap option)

From Anthropic's own docs and the Manus writeup (researched 2026-08-25):

- The cache is a strict byte-prefix match. **Any edit to content at position N
  invalidates the cache from N to the end.** There is no per-segment caching. So you
  cannot trim or compact anything OLDER than the newest content while keeping that
  older region warm; the honest answer to "can we compact without resetting the
  cache" is no, for the region you touch.
- What you CAN do, and what every serious system does: append cheaply for many turns
  (each turn re-reads the whole prior request at ~10% and pays full price only for
  the new exchange), and make reduction a RARE, batched event at a turn boundary, so
  its one-time re-cache amortizes over the cheap turns after it. Never compact every
  turn.
- Isolate the frozen prefix (system prompt + tools) behind its own `cache_control`
  breakpoint, so a compaction rewrites only the conversation region, never the system
  prompt. This is Anthropic's own documented compaction pattern.
- `clear_tool_uses_20250919` is documented as cache-INVALIDATING (from the earliest
  cleared block onward); its `clear_at_least` parameter exists precisely because each
  clear is a paid re-cache to be batched. The 2026-08-20 docs slightly understate it
  as "surgical/cheap"; correct that when acting on them.
- Prefer expressing reduction as APPENDED compressed references (Manus/memory-tool
  style: keep a path or a `workshop:` ref, drop the body) over rewriting history,
  because an append keeps more of the prefix byte-identical.

### Ideas from our custom compaction worth keeping

- **The working-state handoff note.** A per-scope "what was I doing / what's decided"
  summary written at compaction and inherited by the next chat is genuinely useful
  and is not something native compaction gives us. Keep it as scope memory
  independent of which runtime compacts.
- **Retrieval rehydration.** Because Workshop indexes the full uncompacted history,
  an aggressive compaction can be paired with a retrieval tool that re-pulls verbatim
  detail on demand (the strongest empirical result in the research: verbatim chunks
  beat summaries by 16-22 points). This is the one place we can beat native, and it
  belongs on the API path.

Everything else in the custom fold (the summarizer prompt, the keep-tail token
budget) is a dev-path necessity, not an advantage.

---

## 4. The seam: production must have no trace of `claude -p`

The compaction adapter (`compaction_adapter.py`) is the seed; it needs to widen from
"compaction" to the full set of things a turn asks of a model backend, and the CLI
implementation must be the only place any CLI detail lives.

Operations a turn needs from the backend (all currently wired straight to the CLI):

- spawn + stream a turn; resume a session; deliver mid-turn input; approve a tool
  use; compact; report context/usage; stop a turn.

CLI-specific today, must move behind the adapter: the `claude` argv and stream-json
wire format (`build_streaming._build_cmd`, `_spawn_and_drive`), the
control_request/`--permission-prompt-tool stdio` protocol, `session_alive`'s on-disk
session probe, `run_context_report` scraping `claude -p /context`, and
`stop_turn`'s process-group signal. Session identity is a **CLI session UUID on
disk** (`chat_store.claude_session`); the CLI owns the conversation and we only hold
the handle.

The load-bearing consequence for production: **the API path has to own the
conversation itself** (a Workshop-side message store), because there is no CLI
on-disk session to `--resume`. That store is the real prerequisite for the API
adapter, and it is what lets compaction, resume, and context reporting all become
adapter methods with two honest implementations. Building it is the first API-path
task; nothing here should be written against the CLI's session store as if it were
permanent.

---

## 5. Open choices for the operator

1. **PROJECT.md in `live_context` (deduped orientation) vs move it to first-turn-only**
   so a resumed chat carries zero standing state and only appended events. Leaning
   toward keeping it as orientation; cheap to change.
2. **Event-channel targeting:** by scope, by explicit subscription, or only chats
   that spawned the work. Needed before building the append channel.
3. **API-path sequencing:** build the Workshop-owned conversation store + API adapter
   now (unblocks native compaction, cache breakpoints, retrieval rehydration), or
   after the current dev-path cleanup settles.
