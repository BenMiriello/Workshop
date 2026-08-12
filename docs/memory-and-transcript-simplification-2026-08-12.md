# Pare the memory and transcript machinery down to one thing that works

Status: **proposal, awaiting go-ahead.** 2026-08-12. Supersedes nothing; the
findings it rests on are in [`memory-stores-audit-2026-08-12.md`](memory-stores-audit-2026-08-12.md).

This absorbs the transcript-side removal plan drafted in a parallel chat. The two
were the same problem approached from opposite ends, and they meet at exactly one
function (`_schedule_distill`), which is why they are now one document with one
owner.

---

## The shape of it

Two mechanisms sit between a conversation and a model, and both are lossy.

- **The transcript path** truncates and summarises what was actually said.
- **The memory path** injects notes nobody asked for, ranked by a function that
  is really a length sort.

Neither can be fixed by tuning. The transcript path should not exist: the CLI
compacts its own window and `/clear` is a real, explicit, operator-driven cut.
The memory path has the direction wrong: it PUSHES guesses instead of letting an
agent PULL what it needs.

**The whole proposal is: delete both, and add one search tool.**

---

## 1. What is actually running today

| mechanism | state, measured 2026-08-12 |
|---|---|
| `MAX_HISTORY = 60` (`chat.py:103`, applied `:958`) | live. Silently drops the oldest messages from every fresh-session prompt |
| Server-side folding (`_compact_thread` and friends) | live on explicit `/compact` only (automatic folding was removed last week) |
| Haiku distillation (`_schedule_distill`) | live, and its ONLY trigger is a successful fold (one call site, `chat.py:1214`) |
| Auto-recall, top 3 notes per message | live in general and app chats. Ranking is `sum(hay.count(t))`: no stopwords, no IDF, no length normalisation |
| `mcp__workshop__query` | live. Its `topic` parameter is accepted and **discarded**; it returns note TITLES and never a body |
| `memory_keeper` (675 lines) | **dormant since 2026-07-18**, 25 days. Dispatcher off |
| `enforce_index_cap` archive path | code live, **zero archive dirs exist**, and an archived body would be unreachable if one did |
| `learnings` push, 2,524 chars every turn | live in every chat, including this one |
| `workspace_digest` push, 507 chars | live |

Measured prompt cost for a general chat before a word of conversation:
persona 7,517 + live context 715 + learnings 2,524 + digest 507 = **11,263 chars,
about 2,800 tokens**. Up to 2,000 more chars of auto-recall ride on every single
message.

### The finding that decides the direction

**An agent can write a note but cannot read one.** There is no MCP tool that
returns a note body. `routes/knowledge.py` has `get_note` and `search`, but those
are HTTP endpoints for the Notes UI and no agent holds credentials for them. The
one agent-facing tool lists titles and throws away its query.

So the only path from a note back into a model is auto-injection, and that
injection is ranked by length. Probed live against the real `workshop` scope, a
pure-stopword query ("the a of to and is in") returns **the same three notes** as
a real question, and those three are the three largest files in the scope.

Everything else in this document follows from that one sentence: the system
pushes what it guesses and refuses what is asked for.

---

## 2. What comes out

### Transcript half

- `MAX_HISTORY` and its slice at `:958`. This is the destructive one.
- `_compact_thread`, `_summarize_sync`, `_maybe_compact`, `_FOLDING`,
  `_COMPACT_KEEP_TAIL`, `_COMPACT_CALL_TIMEOUT_S`, `_fmt_turns`.
- `POST /threads/{id}/compact`.
- The `summary` block and parameter in `_build_prompt`, and the summary
  arithmetic in the context-meter fallback (which estimates from the real prompt
  instead, and is more accurate for it).
- `chat_store._set_boundary(..., "compact", summary)` and the `summary` write.
- Frontend: `chatApi.compactThread`, the `will_fold` / `live_session` branching
  in `useCompaction.compact()` and its two "Nothing to compact" no-ops, the
  `ctxNonce` bump on fold. `compact()` collapses to "send `/compact` as a turn".

About 260 lines in `chat.py` alone.

**Native `/compact` already works and is untouched.** `_is_raw_command`
(`chat.py:2418`) sends a bare `/compact` straight to the CLI, and `useCompaction`
already fires that turn. The server fold was running *alongside* it, not instead
of it.

**The boundary stays, narrowed.** `/clear` is built on `boundary_seq` and is a
real feature: it means "none of this has been said to you" and it drops the CLI
session too. Narrow the honouring to `mode == "clear"` only. There are 3 legacy
`compact` boundaries and 8 threads carrying a stored summary; making compact
boundaries inert gives those threads their full history back instead of
stranding the folded messages forever. One condition, no migration, and it
recovers context rather than destroying it.

Then the invariant, with a test, because nothing currently covers it:
**every committed message after the last `/clear` is in the prompt, verbatim.
Nothing else removes messages.**

Risk, measured: the largest transcript in the whole store is about 110k tokens,
11% of a 1M window. 47 of 60 threads were already under the old cap and are
unaffected. If a thread ever does approach the window, the CLI compacts itself.

### Memory half

- **Auto-recall injection**, both sites (`chat.py:1673`, `:1806`) and the
  `recalled` parameter. The search tool replaces it.
- **Distillation** (`_distill_sync`, `_schedule_distill`, `_distill`). See the
  decision below.
- **`memory_keeper.py`**, 675 lines, dormant 25 days. Its ADD / UPDATE / RETIRE
  routing is an elaborate answer to "which of these notes is the same fact", and
  search answers that better by not needing to know.
- **The `archive/` demotion path** in `enforce_index_cap`. It moves note bodies
  out of the search corpus and tells the reader to "recall them by title when
  needed", which is not a thing any caller can do. Nothing has ever used it.
- **The dead `topic` parameter** on `mcp__workshop__query`, folded into the new
  tool.
- **Narrow the `learnings` push.** Read what it actually says: "DO NOT write
  `pass # stub`", "DO NOT mark `[x]` when the task still says needs-operator".
  Those are rules for an autonomous coding worker on the ralph loop, and they are
  being pushed into every interactive design conversation at 2,524 chars a turn.
  Keep them on the autonomous path; drop them from interactive chats.

### The decision the other chat flagged, answered

Distillation's only trigger is a successful fold, so removing folding stops it
silently. The options were: drop it, move it onto `/clear`, or re-home it later.
That chat leaned toward moving it to `/clear`.

**Recommendation: drop it.** The evidence was not available from the transcript
side:

1. What distillation writes is `chat-distilled.md`, and that note is **the
   largest file in the scope**, which is precisely why it wins the broken
   ranking. It is the single biggest polluter of auto-recall today.
2. In `development` and `project` scopes, auto-recall does not run at all, so
   every distilled fact written there is **write-only**. `shared/knowledge/development/chat-distilled.md`
   was written at 09:45 today, holds a good summary of this week, and nothing
   will ever read it back.
3. Under this proposal the **raw chat logs become directly searchable** (9.6 MB,
   63 threads). A lossy Haiku summary of a corpus you can search is strictly
   worse than the corpus, and it costs a model call per compact to produce.

Moving it to `/clear` would preserve a mechanism whose output has no reader.

---

## 3. What goes in: one tool

`mcp__workshop__search`: semantic search over an embedding index, returning
**ranked chunks with their bodies and a source reference.** That is the thing no
agent can get today.

**The capability is already on this box and Workshop does not use it.** Ollama
is running on :11434 with two embedding models, and `llm_router` already has a
hardened Ollama client (health probe, failure cooldown, timeouts):

| model | dim | one query | batched |
|---|---|---|---|
| `nomic-embed-text` | 768 | 39 ms | 6 ms/doc |
| `bge-m3` | 1024 | (cold start) | 18 ms/doc |

`nomic-embed-text` is the pick: local, no API cost, no per-query billing, and
39 ms is invisible inside a turn.

**Corpus and cost, measured:**

| source | files | size |
|---|---|---|
| knowledge notes | 23 | small |
| `docs/` | 141 | 2.4 MB |
| chat logs | 63 | 9.6 MB |
| **index total** | | **12.1 MB, about 8,000 chunks** |

Building the entire index from cold: **about 48 seconds, once.** Incremental
after that, on write.

**Storage: one JSONL per scope**, holding chunk text, source reference and
vector. Ranking is a numpy dot product over 8,000 rows, which is sub-millisecond.
`numpy` is already in the backend venv. **No vector database, no new service, no
new dependency.**

**Granted to every chat**, including `development` and `project`, which get no
recall whatsoever today and are where most of the work happens.

---

## 4. The net

| | |
|---|---|
| removed | roughly 1,400 lines (260 in `chat.py`, 675 `memory_keeper`, plus the recall, archive and frontend fold paths) |
| added | roughly 200 lines: an embed helper on the existing Ollama client, an index writer, and one MCP tool |
| pushed into every prompt | about 5,000 chars of memory machinery, gone |
| an agent can do | search notes, docs and past chats and read what it finds, which it cannot do at all today |

The direction inverts. Nothing is guessed at and inserted; the agent asks.

---

## 5. Sequencing

1. **Build the search tool and the index.** Remove nothing. It is additive, and
   the moment it exists everything below is easy to judge.
2. **Do the transcript removal in one pass**, as the other chat scoped it:
   deletions, the clear-only narrowing, the invariant test, restart.
3. **Turn off auto-recall** and the learnings push in interactive chats.
4. **Delete the dormant paths**: `memory_keeper`, the archive demotion, the dead
   `topic` parameter.

1 and 2 are independent and can run in either order.

---

## 6. The one thing that is the operator's call

**Does the Notes vault (`~/notes`, 965 files) go in the index?**

It is the only store with a clear human owner and a clear purpose, and it is
personal rather than platform. My recommendation is **not by default**: index it
as a separate, opt-in corpus the agent must name explicitly, so a search about
Workshop's routing code can never surface a personal note. Easy to add later,
unpleasant to walk back.
