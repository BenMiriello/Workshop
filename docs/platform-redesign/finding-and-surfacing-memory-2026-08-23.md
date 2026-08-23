# Finding and surfacing memory (2026-08-23)

**Status:** CURRENT, design only, nothing here is built. Supersedes nothing. It
is the design behind N3 of
[`agents-and-memory-reassessment-2026-08-23.md`](agents-and-memory-reassessment-2026-08-23.md)
and the answer to the question that document left open: retrieval works now, so
what makes an agent *use* it.

Everything numeric below was measured on this box on 2026-08-23 against the live
index and a real `development` chat. Where a number disagrees with what was
believed, it is called out in section 8.

---

## 0. The brief, and the tension in it

The operator asked for two things that pull against each other.

> "we don't want to just be injecting some list of potential things that might be
> relevant that they could pull in, and then leading them to constantly pull in
> more things when they don't really need to. But we also don't want to just leave
> them to their own devices, forgetting to pull in context. There is some value to
> just making them aware that there IS existing context in some of these areas,
> otherwise they wouldn't even know to look."

Both halves are grounded in something this repo already did. The "leave them to
their own devices" half is what happened on 2026-08-23: an agent could not reach
the design corpus, so it rebuilt a designed-and-built subsystem from scratch inside
a conversation. The "injecting a list" half is what auto-recall did until
2026-08-12, and the argument against ever rebuilding it is written into
`backend/services/semantic_index.py:1-19`:

> THE POINT OF THIS MODULE IS THAT NOTHING IT PRODUCES IS INJECTED.

That contract holds in this design. Nothing below injects a passage, a body, or a
snippet. The resolution of the tension is a distinction the brief almost states
outright:

**Awareness is a property of the corpus. Relevance is a property of the query.**
Awareness is cheap, stable, cacheable, and belongs in the prompt. Relevance is
expensive, per-turn, and belongs in a tool call. The mistake auto-recall made was
not "injecting"; it was injecting the *relevance* half, computed from a guess, at
full body length, on every turn.

---

## 1. The measured injection budget

A `development` chat, operator-owned, tools active, `is_build_provider` true.
Token counts use the repo's own `_CHARS_PER_TOKEN = 4`
(`backend/routes/chat.py:1785`). There is no local Claude tokenizer, so treat
these as +/- 15%, which is well inside the decisions they inform.

**The fresh-session prompt** (`_build_prompt`, `backend/routes/chat.py:1508`),
which is what a new chat and the first turn after `/clear` or `/compact` send.
Assembly order is the source order:

- **turn contract** (`chat_context.turn_contract`, prepended by the caller at
  `chat.py:2745`): 2,219 chars, **~554 tokens**.
- **role preamble** (`chat_context.role_preamble`, `chat.py:2695`): 8,982 chars,
  **~2,245 tokens**. Contains the delegation block (4,295 chars, ~1,073 tokens)
  entirely inside itself, plus the ui-card, task-proposal, learning-note and note
  protocols and the worktree rule.
- **platform kernel L0** (`chat.py:1537`): 928 chars, **~232 tokens**.
- **runtime briefing** (`chat.py:1543`): 2,965 chars, **~741 tokens**.
- **orientation manifest L1** (`chat.py:1550`): 998 chars, **~249 tokens**. This
  is the only block in the prompt today that tells the model `search` exists.
- **persona** (`chat.py:1552`): 7,419 chars, **~1,854 tokens**.
- **capability note**: 0 chars for this provider.
- **learnings** (`chat.py:1583`): 4,810 chars, **~1,202 tokens**, rendered as two
  blocks, `LEARNINGS (workspace guardrails)` and `AGENT MEMORY (scope: development)`.
- **ambient digest** (`chat.py:1594`): 658 chars, **~164 tokens**.
- **working state** (`chat.py:1614`): 5,026 chars, **~1,256 tokens**. Bounded by
  `knowledge.WORKING_STATE_MAX_CHARS = 4800` (`backend/services/knowledge.py:1162`)
  and currently at its ceiling.
- **knowledge index** (`chat.py:1628`): 2,056 chars, **~514 tokens**, rendering the
  `workshop` and `development` layers.
- **skills block**: absent, build providers are excluded (`chat.py:1645`).
- **custom instructions**: 0 chars.

Total assembled prompt with an empty history: 36,299 chars, **~9,074 tokens**.

**The resumed prompt** (`_resumed_prompt`, `backend/routes/chat.py:2815`), which is
what every other turn sends into an already-running CLI session. Steady state,
after the protocol digest has settled:

- turn contract: ~554 tokens.
- chat mode block: ~30 tokens.
- runtime briefing, **unconditionally, every turn** (`chat.py:2855`): ~741 tokens.
- `SHARED WORK UPDATED` pointer: 0 tokens unless `scope_version` moved.
- `PROTOCOL UPDATED`: 0 tokens unless the contract digest moved.
- the operator's message, plus an arrival notice when one applies.

So the recurring per-turn cost of a resumed development turn is about **1,325
tokens before the operator has said anything**, and 741 of those are a briefing
re-sent verbatim whether or not the environment moved.

**The memory-specific share of the fresh prompt is ~3,136 tokens** (learnings
1,202 + working state 1,256 + knowledge index 514 + ambient digest 164), which is
35% of the whole prompt. That is the budget any new surfacing mechanism competes
with, and it is already large.

---

## 2. The cache constraint, which decides most of this

There is a standing finding in this repo that a per-turn-varying block in the
cache-stable prefix invalidates the KV cache for everything after it. It is stated
three times, from three different angles:

- `docs/platform-redesign/research-findings-2026-07-15.md:10` and `:82`: a changing
  digest inside the stable prefix invalidates everything after it on every change,
  cache reads are roughly 10x cheaper than uncached input, and this is "likely the
  single largest cost lever in the design".
- `docs/platform-redesign/review-findings-2026-07-15.md:168-190`, finding F6: the
  ambient digest was placed early in the prefix and re-sorted on every sweep tick,
  so "the learnings block's careful oldest-first append design is defeated by the
  block right after it".
- `docs/platform-redesign/custom-context-management-proposal-2026-08-20.md:113-121`,
  which names the exact move to avoid: "Pre-injecting fresh retrieved passages into
  the prefix every turn (classic per-turn RAG) is the move that breaks this ...
  retrieval is a tool, not a pre-injection."

The response to F6 is visible in `backend/services/workspace_digest.py:10-18`: the
digest selects by priority but renders by creation order and drops the per-task
status tag, so a status flip no longer reshuffles it. It was made low-churn rather
than moved. It still sits in the prefix at `chat.py:1594`.

**Where the breakpoint actually is on this path.** Workshop does not send a system
prompt with `cache_control` markers. A turn spawns `claude -p --output-format
stream-json` (`backend/services/build_streaming.py:3`) and writes the assembled
prompt to stdin as one stream-json user message
(`build_streaming.py:2254-2258`), resuming the prior session with `--resume`
(`build_streaming.py:2138`). So:

- The **fresh-session prompt is the prefix.** Everything in section 1's first list
  becomes the opening of a session and is re-read, cached, on every subsequent
  turn of that session's life. A block here is paid once at full price and then at
  cache-read price for the rest of the chat. Size matters much less here than
  change does, and nothing in it changes at all until the session is dropped.
- The **resumed prompt is already after the breakpoint**, by construction. It is an
  appended user message. It is full price once, then cached forever like any other
  turn. Per-turn variation costs nothing in cache terms here, because this content
  is new bytes at the tail either way.

That is the whole answer to question 3, and it is unusually clean:

> **A corpus map goes in the fresh-session prefix. A relevance hint goes in the
> resumed per-turn message, at `chat.py:2912`, and nowhere else.**

`chat.py:2912` is not an arbitrary line. It is the comment that reads "The
per-message `=== RELEVANT NOTES ===` injection was here." That is the exact
position auto-recall occupied. Putting a hint back at that position is only
defensible if the hint is a different kind of thing than what was removed, and
section 4 is the argument that it is.

The precedent to copy is two blocks above it, at `chat.py:2896-2910`:
`scope_version.changed_since` is an integer compare that emits **no text at all**
in the common case, and when it does fire it spends its tokens on a pointer rather
than on content. That is the correct shape for everything in section 4.

---

## 3. The tool side

### 3.1 What exists today

`backend/mcp/workshop_query_server.py` serves six tools, plus any command tools the
scope declares:

- `query` (`:42`), a fixed digest of open tasks, jobs and note titles. Takes no
  arguments; its `topic` parameter was deleted rather than implemented, and the
  comment at `:56-62` explains why an advertised-but-ignored filter is worse than
  none.
- `search` (`:63`), the real one, onto `semantic_index.search`.
- `where_is` (`:140`), the orientation map, shared with the injected L1 manifest
  through `services/orientation.py`.
- `list_attachments` (`:151`) and `get_file` (`:162`), this chat's uploads only.
- `spawn_background` (`:110`), the one write tool.

`search`'s description (`:64-73`) is good and does the two things that matter: it
gives concrete trigger phrasings ("have we decided X before", "is there already a
ticket for this") and it states that an empty result is an answer. Its rendered
output (`_run_search`, `:263`) leads each hit with score, kind, title, ref, scope,
date and a `SUPERSEDED, historical` flag, and reports `searched N of M indexed
passages` so a scope-narrowed search does not read as a whole-corpus miss.

### 3.2 The gaps, in order of how much they cost

**Gap 1: there is no way to read a whole thing by ref.** `search` returns a
passage of at most `CHUNK_CHARS = 1200` (`semantic_index.py:71`), from at most two
chunks per document (`_MAX_PER_REF = 2`, `:205`). The model gets a paragraph and a
ref, and then has no tool that turns that ref into the document. `services/refs.py`
has `resolve_ref` (`:214`) but it returns `{kind, title, href, status}` and never a
body. A build chat can fall back to `Read` for a `doc` ref, which is why this gap
has gone unnoticed; a data chat cannot, and **no** chat can read a `chat` or
`ticket` ref that way. This is the biggest hole. Retrieval that can only ever show
you 2,400 characters of a 40,000-character design doc is retrieval that teaches
agents to reconstruct rather than read.

**Gap 2: nothing states the shape of the corpus.** `semantic_index.status()`
(`:1503`) computes exactly the right thing (`chunks`, `by_kind`, `model`,
`embedder_ok`) and has one non-test caller: the empty-result branch of
`_run_search`. So the model learns how much exists only in the moment it has
already failed to find something.

**Gap 3, which I am arguing is not a gap: "more like this".** It is `search` called
with the passage text as the query. Adding a tool for it buys a small quality
improvement over an existing composition and costs a permanent line in every
agent's tool budget. Reject it.

### 3.3 The recommended set

Net change: **one new tool, one extended tool, no renames.**

1. **Keep `search` exactly as it is.** Its contract, its floor, its supersession
   labelling and its honest coverage line are the parts of this system that were
   measured most recently and most carefully. Do not touch it.

2. **Add `open_ref(ref, part=None)`.** Takes a `workshop:` ref in the vocabulary
   `services/refs.py` already parses (`doc/<path>`, `note/<scope>/<id>`,
   `ticket/<id>`, `chat/<thread>#<message>`) and returns the body, bounded, with
   its title, date and supersession status attached, and with the same scope and
   `share` policy check `_keep_mask` applies (`semantic_index.py:1318`) so it
   cannot be used to walk around a scope boundary a search respected. For anything
   over the bound it returns the head plus a stated character offset and accepts
   `part` to continue. Refuse to invent a second addressing scheme: the ref format
   already exists, is already what `search` returns, and is already what the
   operator's chips render.

3. **Extend `where_is` to carry the corpus map.** It already exists, already takes
   no arguments, already returns the orientation text, and is already the tool the
   manifest tells the model to call when it is unsure. Adding counts there is
   strictly better than a seventh tool: no new schema, no new name to learn, and it
   answers "what is findable" in the same call as "where do I look". Contents are
   in section 4.2.

4. **Leave `query` alone.** It is honestly named for what it is (a digest) and
   renaming it now to free up the word "query" is churn.

### 3.4 How the agent learns what is findable

Three layers, and they should say different things rather than repeat one thing
three times:

- **The tool description** says what the tool is for and gives trigger phrasings.
  It already does this well. Add exactly one sentence, that `open_ref` reads the
  whole of anything `search` returns, since a passage-only mental model is the
  failure `open_ref` exists to fix.
- **The L1 orientation manifest** (`services/orientation.py`, injected at
  `chat.py:1550`, ~249 tokens) says which tool reaches which kind of thing. It
  already names `search` and states that an empty result means no prior art.
- **The corpus map** says how much there is and how old it is. This is the layer
  that does not exist, and it is the one the operator's "otherwise they wouldn't
  even know to look" points at.

---

## 4. The surfacing side

### 4.1 The four shapes, evaluated

**(a) Nothing, tool-only.** This is the status quo and it has a measured failure.
The 2026-08-23 episode is the case: the agent had the tool, had the manifest
telling it the tool existed, and still rebuilt a designed subsystem, because
nothing told it that a design corpus of 216 documents was sitting there. There is
a second, quieter version of the same failure in the prompt right now. The
knowledge index block ends with the line "(77 more notes in this scope; use the
search tool to reach them)" for a `workshop` chat: 55 of 132 notes are named and
the rest are a number. That line is the only quantitative statement about
unretrieved memory anywhere in the prompt, it covers one kind out of four, and
until 2026-08-23 it pointed at a tool that could not reach them at all
(reassessment §3.2 item 6). Reject (a).

**(b) A corpus map, static-ish, no per-turn variation.** Cheap, cache-stable,
answers exactly the "they wouldn't even know to look" half, and cannot induce a
retrieval spiral because it names no topics. Its weakness is that it is
undirected: it tells the model there are 216 documents but not that three of them
are about the thing being asked. Strong, incomplete.

**(c) A relevance hint, titles and scores only, above a high floor.** Directed, and
therefore the shape that could actually change behaviour on the turn that matters.
It is also the shape that is one refactor away from being auto-recall again, and
the measurements below show it is far more dangerous than it looks.

**(d) Hybrid: map always, hint only on a strong signal.** Recommended.

### 4.2 What I measured before recommending, and why it changes the design

I sampled 120 real operator messages from `development` chats (589 available), ran
each as a query against the live index with the same reach and `from_scope` a
development chat would use, and recorded the top hit.

- With no floor, **100% of messages return a hit at 0.65 or above**, and the
  median top score is 0.908. A hint with a floor anywhere near the search floor
  fires on essentially every turn. That alone kills the naive form of (c).
- **In 96 of 120 cases (80%), the top hit is a chunk of the very thread the message
  was sent in.** The chat corpus is 8,015 chunks of 12,691, and it contains the
  conversation the hint would be computed inside. A hint built the obvious way
  would spend most of its firings telling the model that the chat it is in is
  relevant to the chat it is in. This is the single most important measurement in
  this document and I have not seen it stated anywhere in the tree.
- Excluding the current thread, the top scores fall to: 92% at or above 0.70, 67%
  at 0.80, 32% at 0.85, **4% at 0.90**. Median 0.827.
- Excluding chat entirely, so notes, docs and tickets only: 77% at 0.70, 42% at
  0.80, **14% at 0.85**, 1% at 0.90. Median 0.792.

Reading the 0.85+ note/doc/ticket hits by hand, they are genuinely on topic: a
question about the file tree and file viewer design returns "The file tree, viewer
and editor" at 0.914; a message pointing at a handoff doc returns that handoff at
0.875. The 0.80 to 0.85 band is noticeably weaker and includes a general handoff
doc matching four unrelated messages about chat bugs, which is exactly the
topically-related-distractor failure the context-rot finding warns about
(`research-findings-2026-07-15.md:71-82`).

Two design conclusions fall straight out:

1. A hint **must exclude the current thread**, and I would go further and exclude
   `kind=chat` altogether. Chat chunks are the largest and most self-similar part
   of the corpus, and "a past conversation touched this" is the weakest possible
   reason to interrupt. `search` still reaches chats when the model asks; the hint
   does not advertise them.
2. The hint floor is **0.85**, not 0.70 and not the search floor of 0.65
   (`semantic_index.py:176`). It fires on roughly one turn in seven.

### 4.3 The recommendation

**Ship (d), and ship the two halves in that order, because they are independently
useful and the first is nearly free.**

**Half one: the corpus map, in the fresh-session prefix.** Computed at prompt
assembly from `semantic_index` meta filtered by the chat's own reach, placed
immediately after the L1 orientation manifest at `chat.py:1550` so awareness of
the corpus sits next to awareness of the tool. Measured on the real index for a
`development` chat, this renders as 339 characters, **~84 tokens**:

```
=== WHAT IS IN THE WORKSHOP'S MEMORY (reachable from this chat) ===
216 documents under docs/, 132 knowledge notes, 65 past chats, 254 tickets.
Written 2026-06-09 to 2026-08-23. 140 document passages are marked superseded
and are demoted, not hidden.
None of it is in this prompt. `mcp__workshop__search` is the only way to read
any of it.
```

Counts are **distinct sources, not chunks**. 216 documents is a number a model can
reason about; 4,046 chunks is not, and would also make the block churn on every
edit to any doc. Same text is returned by `where_is` so the two never drift.

The supersession sentence is load-bearing and its wording is constrained by the
code. Superseded chunks are demoted by `_SUPERSEDED_PENALTY = 0.03`
(`semantic_index.py:200`) in the ranking only, while admission still tests the raw
score (`search`, the `masked[i]` / `adjusted[i]` split around `:1465`), and the
penalty was resized down from 0.05 on 2026-08-22 precisely because burying history
answers historical questions wrongly. So the map may say "demoted, not hidden". It
may **not** say "search returns only current material", which would be false and
would stop an agent asking a history question it is entitled to ask.

**Half two: the relevance hint, in the resumed per-turn message.** At
`chat.py:2912`, after the `SHARED WORK UPDATED` pointer and before the operator
message, shaped like `scope_version.changed_since`: silent in the common case.

- **Trigger:** run `semantic_index.search(user_message, limit=3,
  kinds=("note","doc","ticket"), scope=<chat reach>, from_scope=<chat scope>,
  min_score=0.85)`. Emit nothing unless at least one hit survives. Additionally
  suppress when the same ref was already hinted in this thread (one mark in
  `chat_store.set_turn_marks`, the mechanism used at `chat.py:2938`), because a
  hint repeated every turn is a nag and a nag is ignored.
- **Cap:** at most **2** titles. Not 5. The point is to name the thing, and a
  two-item list cannot read as a menu.
- **Content:** ref, title, date, score, and the supersession flag when set.
  **No passage, no snippet, no hook.** One instruction line: read them with
  `open_ref` if this turn depends on what was decided there, and ignore this
  otherwise.
- **Cost:** about 60 tokens when it fires, times a measured 14% firing rate, so
  roughly **9 tokens per turn amortised**. Compute cost is one embed plus one
  masked dot product over the corpus: measured **0.41s** end to end on this box
  (0.12s load, 0.36s `_keep_mask`, the rest embed and rank), which is real and
  must not sit on the request path unguarded. The mask is cacheable per
  (scope, index generation) and should be, since it is 88% of that time.
- **What it displaces:** nothing, and that is deliberate. At 9 tokens amortised
  there is nothing to trade against. If a trade is ever wanted, the honest one is
  in the resumed prompt and it is the **runtime briefing at ~741 tokens re-sent
  verbatim every turn** (`chat.py:2855`), which already has a digest-and-suppress
  twin sitting right beside it in the `LIVE CONTEXT` branch (`chat.py:2874-2889`)
  and does not use it. Giving the briefing the same "unchanged since last turn"
  treatment would free 80 times what this hint costs. That is a separate ticket
  and I would file it regardless of this design.

### 4.4 The failure mode, and how the operator would see it

The map's failure mode is **drift into a lie**. It states counts derived from the
index, and the index is a snapshot: it self-heals through a stale marker plus a
doc-tree fingerprint (`semantic_index.py:761-932`) and a 300-second maintenance
loop (`:1020`). If the loop stops, the map keeps confidently reporting the count
it last saw. The failure looks like an agent reasoning correctly against a corpus
that has moved. It is low-severity but silent, which is the bad combination, and
the mitigation is that the map renders the index's own build timestamp and any
consumer of `status()` shows it. Note that this document's own ground-truth brief
carried counts (12,652 chunks, 217 notes, 7,995 chats, 394 tickets) that were
already stale by the time I measured (12,691 / 219 / 8,015 / 411, section 8). That
is the failure mode demonstrating itself inside the design document for it.

The hint's failure mode is **the shopping list, and it is loud**. It looks like:
the hint firing on most turns rather than one in seven; the same two titles
appearing under unrelated questions; a general handoff doc becoming the answer to
everything; and the tell that matters most, the model opening the hinted refs and
then not using them. The operator notices it as chats that start every reply by
reading a document before answering a question that did not need one. He should
not have to notice it by feel, which is section 6.

There is a second, subtler hint failure: **anchoring**. Two titles at 0.87 that
are near-misses can steer a turn toward the wrong prior decision more effectively
than no hint at all, because a title carries authority without carrying its
argument. This is the strongest argument for the cap of 2 and the floor of 0.85,
and it is why the hint states its score. A score the reader can see is a claim the
reader can dismiss, which is the same reasoning `semantic_index.py:165-169` uses
to justify erring toward a readable failure over an invisible one.

### 4.5 What I am explicitly rejecting

- **Injecting any passage text.** Not a snippet, not a hook, not the first line.
  The moment the hint carries body text it is auto-recall with a better ranker, and
  `semantic_index.py:3-19` is the standing argument against exactly that.
- **A hint on the fresh-session path.** It would sit in the cached prefix, vary by
  first message, and buy one firing per session.
- **Hinting chats.** Section 4.2. The corpus is 63% chat by chunk count and the
  hint would be 80% self-reference.
- **A "relevance" score in the corpus map.** The map is about the corpus, not the
  turn. Mixing them is how (b) turns into (c) by accident.

---

## 5. The operator side

He cannot search his own workshop. Verified: no file under `backend/routes/`
imports `semantic_index` except `docs_index.py:190`, which only calls
`mark_stale`. Its only non-test caller is `backend/mcp/workshop_query_server.py`.
The one component that indexes 4,046 doc chunks, 8,015 chat chunks and 411 ticket
chunks is reachable by agents and by nobody else.

What he has instead, traced end to end: the Memory page's search box calls
`GET /api/notes?q=` (`frontend/src/pages/Memory.tsx:670`), which lands in
`routes/notes_all.list_all` (`:251`), which calls `knowledge.search`
(`services/knowledge.py:1290`), which is `_plaintext_search` (`:1297`), which
scores a note as `sum(hay.count(t))` with no stopwords, no IDF and no length
normalisation. That is the scorer `semantic_index.py:9-16` was written to replace
and whose failure is already measured there: a query of pure stopwords returned
the three largest files in the scope. Documents on that page are not searched at
all server-side; their titles and paths are filtered client-side against the tree
index the page already holds (`Memory.tsx:643-645`).

### 5.1 The minimum

**One route.** `GET /api/memory/search?q=&kinds=&scope=&limit=`, onto
`semantic_index.search`, returning the hit list as-is: score, kind, ref, title,
scope, ts, text, status, plus the envelope's `mode`, `rebuilding`, `searched` and
`corpus`.

**Scope it honestly: pass no `scope` and no `from_scope` by default.** The
operator searching his own workshop is not a scoped agent. `from_scope` is what
engages the `share` policy layer (`semantic_index.py:1330-1336`), and omitting it
skips that layer entirely, which is the correct behaviour for the person who owns
every scope. This also makes the route the diagnostic surface for the class of bug
that erased the document corpus: a hit the operator can see and an agent cannot is
now a visible, reproducible difference rather than an invisible one.

**Where it appears.** The Memory page's existing search box, repointed. Not a new
page and not a second box. Results render as a flat ranked list with score, kind
chip, title, scope, date and the `SUPERSEDED` marker, each row linking through the
ref to the existing viewer (`refs.resolve_ref` already returns the `href`,
`services/refs.py:214`). All four list states per the design system. Add the
index's `status()` (chunk count, model, freshness, embedder health) as a small
footer on that page, which closes reassessment §3.2 item 5 for the cost of one
existing function call.

### 5.2 Repointed or replaced

**Replaced, for the search path. Repointed is not available.** The current path is
a filter, not a ranker: `list_all` calls `knowledge.search(skey, query,
limit=1000)` purely to build a set of matching ids and then renders notes in
directory order (`routes/notes_all.py:277-286`). There is no ranking to repoint.
The new route is a different shape (ranked, cross-kind, chunk-level) and gets its
own component.

`knowledge.search` and `_plaintext_search` **stay**, unchanged, and stop being
reachable from a search box. They are still the correct implementation of "which
notes contain this string", which is what `?q=` on a note list means, and they are
still what `recall` sits on.

`POST /api/knowledge/recall` (`backend/routes/knowledge.py:168`) has zero frontend
callers. **Do not delete it in this change and do not leave it silently.** It is
the only HTTP expression of the cross-scope reach rule in `_can_recall`
(`knowledge.py:1322`), which is real, tested and used by the policy layer. But it
is a store with no reader, which is precisely the class of defect reassessment §3.3
says should be a gate failure. The honest move is to mark it deprecated in its
docstring with a pointer to the new route, and to file it as the first candidate
for N7's inert-mechanism detector so it is removed by a rule rather than by
somebody's judgement. `POST /api/knowledge/{scope}/search` (`:162`) is in the same
position and gets the same treatment.

---

## 6. Measurement, which is the part that makes this reversible

The reassessment's own rule is that a mechanism that exists, is referenced and
never fires is indistinguishable from one that was never built. A mechanism that
fires and cannot be evaluated is worse: it is indistinguishable from one that
works.

### 6.1 What is logged

One JSONL line per hint decision, in the owner's runtime dir, next to the index:

- turn id, thread id, scope, timestamp.
- whether the hint fired, and if not, the top score it saw. **Log the near misses.**
  A floor with no record of what it rejected cannot ever be retuned, which is the
  exact hole the 0.58 floor sat in for three days (`semantic_index.py:139-146`).
- the refs and scores hinted.
- **whether any hinted ref was opened by `open_ref` or `search` later in that
  turn.** This is the only outcome signal that exists without asking the operator,
  and getting it requires `open_ref` to record the turn id it was called under.
  That is the real reason `open_ref` is in the minimum tool set rather than being
  deferred: without it, the surfacing mechanism has no observable effect at all.

`search` should log the same way, hint or no hint: query, kinds, scope,
`searched`/`corpus`, top score, whether it returned empty. This costs one line and
turns "does anyone use search" from an argument into a count.

### 6.2 The success signal

**Follow-through rate: of hints that fired, the fraction where the model opened at
least one hinted ref in that same turn.** One number, computed from the log alone,
no judgement required.

Interpretation, and both directions are informative:

- Below about 25%: the hint is noise. Either the floor is too low or titles alone
  are not enough signal to act on. Raise the floor to 0.90 (which fires on 1% of
  turns by section 4.2's measurements, effectively off) and if follow-through does
  not rise, the hint has no value at this corpus size.
- Above about 70%: suspect the failure the operator named. A hint that is opened
  almost every time it appears is either genuinely well targeted or is inducing
  retrieval by its presence. Distinguish them with the second signal.

**Second signal, retrieval without a hint.** The rate of `search` calls on turns
where no hint fired. This is the pre-existing baseline and it should be measured
for at least two weeks **before** the hint ships. If it is already high, the model
finds things on its own and the hint is solving a problem that was fixed by the
corpus map alone. That ordering is why half one ships first.

The corpus map needs no per-turn signal. It is 84 tokens and does not vary. It is
evaluated once, by asking whether searches happen at all in fresh sessions after
it lands compared with before.

### 6.3 What would justify removal

Any one of these, and they should be written into the ticket that ships the hint
so that removal is a pre-agreed outcome rather than a defeat:

- Follow-through below 25% over 100 fired hints.
- Firing rate above 30% of turns, measured over a week. That is more than double
  the 14% this design predicts, and it means either the floor drifted or the corpus
  changed shape underneath it. Either way the mechanism is no longer the one that
  was approved.
- The operator reporting, once, that chats open documents before answering
  questions that did not need them. His judgement outranks the counter, and this
  is the failure he described in advance.

Removal is one `if` and one block. Keep it that way. The corpus map is not part of
the same decision and does not go with it.

---

## 7. Sequence

1. **Corpus map** in the fresh prefix, plus the same text on `where_is`. ~84
   tokens, no new tool, no cache cost, no per-turn work. Independently useful.
2. **`open_ref`**. Closes the largest tool gap, and is a precondition for measuring
   anything in section 6.
3. **Operator search route and the Memory page repoint.** This is N3 of the
   reassessment and it does not depend on 1 or 2.
4. **Baseline logging** on `search`, for at least two weeks, with no hint shipped.
5. **The relevance hint**, only if the baseline shows agents are not searching on
   their own, with the removal criteria in the ticket before it lands.

Steps 1 through 3 are worth doing whatever the answer to step 4 turns out to be.
Step 5 is the only part of this design that could make things worse, and it is
last on purpose.

---

## 8. Where the ground truth was wrong, or has moved

Recorded because a design brief that carries stale numbers is the corpus map's own
failure mode, and it is worth having the example.

- **Chunk counts have moved.** The brief said 12,652 chunks: note 217, doc 4,046,
  chat 7,995, ticket 394. `semantic_index.status()` on 2026-08-23 returns
  **12,691**: note **219**, doc 4,046, chat **8,015**, ticket **411**. The index is
  self-healing (`semantic_index.py:761`, `:1020`), so any absolute count in a
  document is a snapshot with a short half life. Model and dimension are as stated:
  `local:BAAI/bge-small-en-v1.5`, 384.
- **`MIN_SCORE` and the supersession mechanics are exactly as described.** 0.65 at
  `:176`, re-measured 2026-08-23 over 48 queries. Penalty 0.03 at `:200`, applied
  to ranking only while admission tests the raw score. `_MAX_PER_REF = 2` at
  `:205`. Status is parsed per block from the first 20 lines
  (`doc_status`, `:455`) with an explicit not-superseded guard for "partly",
  "section" and the template's own field label.
- **"`semantic_index.search` has zero HTTP routes" is correct**, with one nuance
  worth stating: `routes/docs_index.py:190` does import the module, but only to
  call `mark_stale`. No route reaches `search` or `status`.
- **The search signature is as given**, and `from_scope` is worth restating
  precisely because it is easy to read as a filter and it is not one. `scope` is
  the set of scopes that may be reached and is a filter. `from_scope` is the
  caller's own identity, and its only effect is to engage the `share` policy layer
  in `_keep_mask` (`:1330-1336`); omitting it skips that layer entirely. Neither
  argument touches ranking. It is the argument whose mishandling erased the whole
  document corpus for scoped callers on 2026-08-23.
- **One thing the brief did not name and the design turns on.** The chat corpus
  contains the conversation any hint would be computed inside, so 80% of naive
  hints would surface the current thread. Section 4.2.
- **The ambient digest was never actually moved out of the cache-stable prefix.**
  `research-findings-2026-07-15.md:126` recommended moving it and
  `autonomy-turn-on-2026-08-12.md:667-670` says in the past tense that it "had to
  be moved out of the cache-stable prefix and capped". It was capped and made
  low-churn (`workspace_digest.py:10-18`, `CAP_TASKS = 6`), which was the more
  important half, but it still sits in the prefix at `chat.py:1594`. The claim
  should be corrected where it is repeated, because the next person to reason from
  it will believe a slot was already vacated that was not.
