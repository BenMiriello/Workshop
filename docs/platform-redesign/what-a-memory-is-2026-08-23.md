# What a memory is, what a note is, and what the learnings collector is

**Status:** audit and recommendation. No application code changed. Written 2026-08-23 from
a full read of the memory-ish stores in the live tree, with every count measured rather
than quoted.

**The question.** Operator, 2026-08-23:

> One thing I keep not understanding is learnings: the concept of learnings, the name of
> it, the role of it, and the functionality. It seems like you're migrating it into notes,
> or into memories. Are notes different from memories? They shouldn't necessarily be, I
> feel like that's the same thing. And what is the learnings collector? I don't understand
> what this is. Is it an agent? Or is it something else? I'm not sure if I like this, or
> the implementation and design of it.

Three questions. They are answered separately below, in sections 1, 2 and 3, before any
recommendation. The short version:

1. A learning is one sentence of text in a JSONL file that gets pasted verbatim into the
   top of every chat prompt in its scope. That is the whole mechanism. It deserves to
   exist as a *behaviour*. It does not deserve to be its own *store*, and the word is
   wrong.
2. No. "Note" and "memory" are the same object. The backend calls it a note, the URL and
   the page title call it memory, and the React component inside `Memory.tsx` is literally
   named `Notes`. This is a naming defect, not a distinction.
3. There is no learnings collector. There is a function named `prune_stale` that would be
   one if anything called it. Nothing does, and nothing ever has. Details in section 3.

---

## 1. What a learning actually is, mechanically

### 1.1 The shape

One row of JSON in `.runtime/learnings/<scope>.jsonl`. The whole store, measured today:

| scope file | rows |
|---|---|
| `workshop.jsonl` | 24 |
| `global.jsonl` | 12 |
| `project-development.jsonl` | 8 |
| `development.jsonl` | 4 |
| `project-workshop.jsonl` | 1 |
| **live total** | **49** |
| `archive/` (4 files, never read) | 21 |

49 rows, 14,558 characters of text in total, which at the repo's usual 4-chars-per-token
proxy is about 3,600 tokens. The entire corpus is smaller than one long chat turn. The
service that manages it is `backend/services/learnings.py`, **1,467 lines**. That is 30
lines of machinery per row.

Every row is one sentence. Median 286 characters, longest 655. By writer:

| `source` | rows | what it is |
|---|---|---|
| `chat` | 25 | a model decided, mid-turn, that the operator stated a durable preference |
| `ralph-reviewer` | 11 | the autonomous loop hit the same reviewer rejection twice |
| `legacy-md` | 10 | a one-off import on 2026-08-17 of rules that used to live in markdown |
| `ralph` | 2 | the loop's own failure handler |
| `memory-keeper` | 1 | written once, on 2026-07-18, and never again |

Zero rows are pinned (`set_pinned` at `learnings.py:771` has no caller outside its own CLI
and tests, so no row can ever be pinned). Zero rows came from the review endpoint.

### 1.2 What happens to a row

It is pasted into the prompt. In full. On every turn.

`chat_context.learnings_context` (`backend/services/chat_context.py:1271`) renders up to
two blocks per turn: `memory_block(GLOBAL)` at `:1281` labelled
`=== LEARNINGS (workspace guardrails) ===`, and the chat's own scope at `:1307` labelled
`=== OPERATOR PREFERENCES ===` or `=== AGENT MEMORY (scope: ...) ===`. Both are appended
by `backend/routes/chat.py:1583`. `memory_block` (`learnings.py:877`) caps at 60 entries
(`:87`) and 900 approximate tokens (`:99`), and `_block_line` (`:856`) emits the **full,
untruncated text**. An entry that does not fit is dropped entirely and summarised as one
`- +N more` line (`:938`).

That is the entire role of a learning: **full-text, unconditional, every-turn prompt
injection.** Nothing else reads the store. Nothing displays it. Nothing links to it.

### 1.3 Does it deserve to be its own concept?

The *behaviour* does. There is a real and useful difference between "a note whose title
you can see in an index and open if you want it" and "a rule that is in front of the model
whether it wants it or not". The second is what makes a correction stick.

The *store* does not. Everything that distinguishes learnings from notes is a property of
how the text is delivered to the prompt, not a property of the text. Compare the two
capture protocols the model is actually shown, which sit 21 lines apart in the same file:

- `LEARNING_NOTE_PROTOCOL` (`chat_context.py:275`): "When the operator states a DURABLE
  preference or correction ... record it so it is not forgotten."
- `NOTE_PROTOCOL` (`chat_context.py:296`): "When you learn something durable and scoped
  that should outlive this chat (a fact, a finding, a gotcha, running context an agent
  will want later), record it as a note."

Both say "durable". Both say "scoped". Both take a `scope` field with the same vocabulary.
The model is being asked to sort text into two buckets on a distinction the prompt never
states, because the real distinction is not in the text at all: it is that bucket one gets
injected in full and bucket two gets injected as a title.

And the sorting does not work. Row one of `project-development.jsonl` reads:

> "Ralph keeps hitting `design_violations` on this task; the direct approach is not
> converging, try a different tactic: Add a by-chat usage view to the U..."

That is transient detail about one ticket, permanently injected as a standing rule. Eleven
of the 49 rows (22 percent) are that shape. The quality gate (`quality_refusal`,
`learnings.py:304`) refuses bare status tokens like `build failed`, and it does that job
correctly, but it cannot refuse a well-formed English sentence about a task that finished
three days ago. No mechanism can, because the judgement is about durability and the gate
only sees shape.

### 1.4 The word

"Learning" is the wrong word and the operator is right to say so.

It is a gerund used as a count noun, which is why it never reads naturally in the plural.
It describes the *provenance* of the text (something was learned) rather than what it is
or what it does. And it collides with the machine-learning sense of the word in a repo
full of LLM plumbing.

What these rows actually are, without exception, is **standing instructions**. "Always end
the final iteration with `<promise>COMPLETE</promise>`." "Decomposition happens in the main
chat thread, not inside the run." "DO NOT write UI without following the Workshop Design
System." Every single one is an imperative aimed at future behaviour. The store is not a
record of what was learned; it is a list of rules that are always on.

Naming candidates, in order of preference:

1. **Standing note** (or **standing instruction**). Says exactly what it is: a note that
   stands, meaning it is always in effect. Folds cleanly into the note vocabulary because
   it *is* a note with an adjective. This is the recommendation.
2. **Rule.** Short and accurate, and the existing `mint-rule` action already uses it
   (`backend/routes/tasks_all.py:1243`). Slightly overloaded in a codebase that also has
   design rules, lint rules and workflow rules.
3. **Guardrail.** Already the label in the prompt (`=== LEARNINGS (workspace guardrails)
   ===`, `chat_context.py:1285`), so the system is half-using it already. But it is
   defensive-sounding for something that is mostly preference, not safety.

Avoid "memory" as the name for this specific thing, for the reason in section 2: that word
is already spoken for.

---

## 2. Are notes and memories the same thing?

**Yes. Same object, three names, and the code says so out loud.**

Follow one note through the stack:

| layer | what it is called | citation |
|---|---|---|
| service module | `knowledge` | `backend/services/knowledge.py` |
| the function that writes it | `_put_note_sync` | `backend/services/knowledge.py:951` |
| HTTP route | `GET /knowledge/{scope_key}/notes` | `backend/routes/knowledge.py:123` |
| second HTTP route | `GET /notes` | `backend/routes/notes_all.py:252` |
| on disk | `shared/knowledge/<scope>/<slug>.md` | `backend/services/knowledge.py:106` |
| the per-scope index file | `MEMORY.md` | `backend/services/knowledge.py:259` |
| frontend route | `/memory` | `frontend/src/App.tsx:119` |
| the old frontend route | `/notes`, redirected to `/memory` | `frontend/src/App.tsx:203` |
| nav label | "Memory" | `frontend/src/components/appNav.tsx:49` |
| page title | "Memory" | `frontend/src/pages/Memory.tsx:250` |
| the React component | **`export default function Notes()`** | `frontend/src/pages/Memory.tsx:190` |

The last row is the finding. The default export of a file named `Memory.tsx`, rendering a
page titled "Memory", at the URL `/memory`, is a component named `Notes`. Somebody renamed
the surface and did not rename the thing, and the file has carried the contradiction ever
since. The page's own comment at `Memory.tsx:194` admits it: the note-addressed URL params
survive "from before Notes and Documents merged".

The scope index file being called `MEMORY.md` while every note in it is a note
(`knowledge.py:259` `_write_index`) is the same defect at the other end of the stack.

There is one real thing hiding under the confusion, and it is worth naming precisely
before renaming anything. The `/memory` page is broader than the note store: its tabs are
`library | chats | vault | files` (`Memory.tsx:153`). Only "library" is notes. "Chats" is
the transcript store, "vault" is the operator's own external Obsidian folder, "files" is
attachments. So:

- **Memory** is correct as the name of the *place* (a surface listing several kinds of
  durable material).
- **Note** is correct as the name of the *thing* (one addressable piece of durable text).
- What is wrong is that the component is named for the thing and the store's index file is
  named for the place, exactly backwards in both cases.

The fix is cheap and entirely cosmetic: rename `Memory.tsx`'s default export from `Notes`
to `MemoryPage`, and rename the per-scope `MEMORY.md` index to `INDEX.md` or `NOTES.md`.
Neither touches behaviour. The `MEMORY.md` rename is a one-constant change at
`knowledge.py:236` plus a migration of 8 live files, and is worth doing because that file
is a pure mirror that is never injected into any prompt (verified: `index_block` at
`knowledge.py:728` builds from `_ranked_notes` at `:672`, which reads the `.md` files
directly and never opens `MEMORY.md`), so a stale or misnamed index costs nothing but
confusion, which is precisely the thing being fixed.

---

## 3. What is the learnings collector?

**It is a Python function named `prune_stale` at `backend/services/learnings.py:1103`. It
is not an agent, not a cron job, not a service and not a process. It is 58 lines of code
that nothing calls, and it has never archived a single row in the store's entire history.**

That is the plain answer. Here is the whole of it.

**What it was supposed to do.** Delete rows that had gone stale. Its test is: archive an
entry unless it is recent (younger than `_AGEOUT_DAYS = 90`, `:112`), or reinforced (seen
from at least two independent occasions, `:113`), or actually used (retrieved at least
once, `:114`). Anything failing all three gets moved to `.runtime/learnings/archive/` with
the reason `"aged-out: stale and low-reinforcement"` (`:1150`).

**Whether it has ever run.** No, on three independent lines of evidence.

1. **No caller.** Its only invocations anywhere in the tree are `maintain` at
   `learnings.py:1285`, its own argparse CLI at `:1441`, and tests. `maintain` (`:1269`)
   in turn is called only by `maintain_all` (`:1357`), its own CLI at `:1453`, and tests.
   Nothing in `backend/routes/`, no service, no scheduler.
2. **No schedule.** `infra/cron/workshop.crontab` has three active lines
   (`daily_todo_digest.sh`, `autonomy_report.sh`, `frontends_watchdog.sh`) and none of
   them mentions memory or learnings. `crontab -l` on this host exits 1: there is no user
   crontab installed at all. `infra/systemd/` holds four units, all serving processes.
   There is no `backend/services/scheduler*.py`.
3. **No fingerprint in the data.** Every one of the 21 rows in `archive/` carries an
   `archived_reason`, and none of them is `prune_stale`'s. Nine say
   `"updated in place by a longer statement of the same rule"` (written by `record` at
   `:662`), eight say `"superseded by a revision"` (`record` at `:691`), three say
   `"refused by the quality gate"` (`sweep_quality` at `:1182`), and one says
   `"superseded at rest by a later revision"` (`reconcile_store` at `:1232`). Those last
   four share a single timestamp of `2026-08-17T06:41:20Z`, which is one manual CLI run,
   on one day, ever. Three further rows carry `"retired: false premise"`, a string that
   exists nowhere in the code, so somebody edited the file by hand.

**And it would archive nothing even if it were called today.** `_AGEOUT_DAYS` is 90 and
the oldest live row was created on 2026-07-18, 36 days ago. Every row passes the "recent"
clause. The collector cannot fire until October at the earliest.

**Worse, the injector was falsifying the collector's own precondition.** `memory_block`
rewrites the whole scope file on render (`_persist_render`, `:815`) to bump counters. It
used to stamp `last_retrieved` on every render, which meant every row was permanently
"recently retrieved" and the third clause could never fail. That was found and half-fixed:
`retrieval_count` is now bumped only when the token cap actually binds (`:846`). The
half-fix moved the defect rather than removing it. Today `global.jsonl` measures 881
approximate tokens against a 900-token budget, so the cap never binds, `retrieval_count`
stays at 0 forever for all 12 rows, and their `render_count` reads 52. The store's own
diagnostic knows: `scripts/verify_memory.py:356` (`c_learnings_gc_can_fire`) exists
specifically to catch this, its docstring at `:357` describes it exactly, and it **fails
against today's data** while having a blind spot (the comprehension at `:372` filters on
truthiness, so an all-zero store passes) that hides the `global` case entirely.

**Should it exist at all?** No, not in this shape. A 49-row store does not need a garbage
collector; it needs a list the operator can look at. The thing `prune_stale` was reaching
for is real (rules go stale and nobody notices), but a background process deciding on its
own which of the operator's standing instructions to forget is the wrong answer to it, and
in a single-operator system it is the wrong shape besides. The right answer is that the
operator can see the list, sorted by how long since anything used it, and delete rows. See
section 4.

---

## 4. The full inventory

Everything in the tree that durably holds text an agent may later read. Counts measured
2026-08-23. Postgres counts are `pg_stat_user_tables.n_live_tup` because RLS is on.

### Reaches an LLM prompt

| # | Store | Where | Live | Injected as |
|---|---|---|---|---|
| 1 | **Knowledge notes** | `shared/knowledge/<scope>/*.md` + per-agent and per-project dirs | **127 notes** (121 under `shared/knowledge`) | **title + hook only**, 500 tokens (`knowledge.py:485`, `:503`, `:728`; emitted `routes/chat.py:1645`) |
| 2 | **Working-state note** | reserved note id inside #1, `shared/knowledge/development/working-state.md` | **1** | **full text**, 4800 chars (`knowledge.py:1162`, `:1235`, `:1268`; `chat_context.py:1346`; emitted `routes/chat.py:1614`) |
| 3 | **Learnings** | `.runtime/learnings/*.jsonl` | **49** | **full text**, 900 tokens / 60 entries (`learnings.py:877`; `chat_context.py:1271`; emitted `routes/chat.py:1583`) |
| 4 | **PROJECT.md / TODO.md** | project roots | 8 + 20 files | **full text**, project chats only (`chat_context.py:1853`, `:1855`) |
| 5 | **Ambient digest** | no store, recomputed per turn | n/a | full text, capped small (`workspace_digest.py:113`; `chat_context.py:1329`) |

### Persistent, not injected

| # | Store | Where | Live |
|---|---|---|---|
| 6 | Semantic index | `.runtime/users/6/semantic/` | 12,691 chunks (chat 8,015 / doc 4,046 / ticket 411 / note 219); pulled on demand by the MCP `search` tool (`semantic_index.py:1365`) |
| 7 | Chat transcripts, files | `.runtime/users/<owner>/chat/<thread>/log.jsonl` | 127 threads, 3,357 events |
| 8 | Chat transcripts, Postgres | `chat_message` / `chat_thread` / `chat_turn` / `chat_branch` | 2,234 / 121 / 874 / 125 |
| 9 | Tickets and events | `ticket`, `ticket_event` | 192 / 1,025 |
| 10 | `docs/` | `docs/**.md` | 216 files |
| 11 | Learnings archive | `.runtime/learnings/archive/` | 21 rows, **zero readers** |
| 12 | `MEMORY.md` scope indexes | one per knowledge scope dir | 8 live; a mirror of #1, never injected |
| 13 | `backlinks.json` | `.runtime/backlinks.json` | 20 refs, all `workshop:chat/...`, zero note or ticket sources |
| 14 | memory_keeper state | `.runtime/memory-keeper/*.watermark`, `.proposed.json` | watermark frozen at `2026-07-18T18:00:01Z`, 36 days stale |
| 15 | `memory-policy.json` | `.runtime/memory-policy.json` | 1 doc, 3 defaults, **0 items, 0 scopes** |
| 16 | Agent-scope TODOs | `.runtime/agent-scopes/*/TODO.md` + `.tasks.json` | 3 scopes, 0 tasks in every `.tasks.json` |
| 17 | Commute notes | `.runtime/commute-notes/*.md` | 64 files, **no backend writer exists** |
| 18 | Notes vault | `$WORKSHOP_NOTES_VAULT`, default `$WORKSHOP_NOTES_VAULT` | absent on this host; 1 stub file under `.runtime/users/4242/notes/` |
| 19 | Letta remnants | `.runtime/agents/*/knowledge/letta-*.md`, `.runtime/knowledge/archives.json` | 4 note files (live in agent scopes, so their titles ARE injected); `archives.json` has `"passages": {}` and zero readers |
| 20 | Custom instructions (L3) | `<owner runtime>/custom-instructions.json` | **0 files exist**; would be full text if any did (`custom_instructions.py:41`) |

**The count is 20.** Collapsing #2 into #1, #11 into #3, #8 into #7 and dropping #5 (which
has no store) gives 16 physical stores. Five reach a prompt. **Exactly two carry full text
unconditionally into every chat: learnings and working state.**

The earlier claim of "eleven memory substrates"
(`docs/platform-redesign/memory-consolidation-2026-08-16.md:42`, table at `:100-115`) is
no longer true, and it was never a like-for-like count. Three of its eleven are genuinely
gone: `memory_tree.py`, `memory_promotion.py` and the learnings promotion lobe's
`promoted.jsonl` have no files and no code. The Letta *server* is gone. But the count went
up rather than down, because that audit lumped "chat + ticket stores" into one row, did not
count `MEMORY.md`, `agent-scopes`, `commute-notes`, custom instructions or the
working-state note, and `memory-policy.json` did not exist yet. Counts have moved a lot:
notes 110 to 127, learnings 29 to 49 live plus 21 archived, index 4,600 to 12,691 chunks,
docs 170 to 216, backlinks 7 to 20, vault 965 files to none.

---

## 5. Recommendation: one store, and the axis is injection, not kind

Three options were on the table.

**(a) One store with a `kind` field, where `learning` is a kind.** Rejected. This preserves
the exact confusion being complained about: the model still has to sort text into buckets
at write time on a distinction nobody can state, and the operator still cannot tell why
one sentence went one way and another went the other. A `kind` enum with `learning` in it
is the current design with a smaller diff.

**(b) One store where the axis is injection policy.** Recommended. See below.

**(c) Keep them separate.** Rejected, but for a specific reason worth stating: the argument
for separation was that learnings has machinery notes lack. Measured, that is backwards in
the direction that matters. Notes have a *working* retirement path (`enforce_index_cap`,
`knowledge.py:282`, reachable from `_put_note_sync` at `:993`, demoting to `archive/` at
200 index lines) while learnings' retirement has never fired. Learnings has *working*
write-time dedup and supersession (`_resolve`, `learnings.py:528`, called from `record` at
`:615`, and the archive proves it fires: 17 of 21 archived rows were archived by it) while
notes have none in `knowledge.py` at all, only in `memory_keeper.py:73-90`, which never
runs. Each store has exactly the half the other is missing, and the fold is what gives
both halves to both.

### The recommended shape

**One store: notes. One additional field on a note: `reach`.**

```
reach: "index"   the default. The title and hook appear in the scope's
                 knowledge index. The body is searchable and openable.
                 This is what every note does today.

reach: "always"  the body is injected verbatim into every prompt in the
                 scope, subject to a scope-wide token budget. This is what
                 a learning does today.

reach: "search"  not in the index, not in the prompt. Searchable only.
                 The archive tier, and the destination for a demoted note.
```

Three properties make this the right axis.

**It is the only real difference.** Sections 1 and 2 established that the text is the same
kind of text, written by the same writers, into the same scopes, and that the only thing
that differs is delivery. Naming the axis for the actual difference means the write-time
decision is finally answerable: not "is this a learning or a note", which nobody can
answer, but "does this need to be in front of the model every time, or is it enough that
it can be found". That is a question a model, and an operator, can actually answer.

**It is what the operator asked for.** His complaint is that he cannot see it, and that is
literally true: `grep -ri learning frontend/src mobile/` finds one lucide icon
(`MessageList.tsx:137`), a receipt that `receipts.ts:39` deliberately refuses to make
clickable, and generated OpenAPI types nothing consumes. There is no list, no page, no
route (`backend/routes/` has no learnings endpoint of any kind; the only HTTP surface that
touches the store is write-only). Folding into notes means every one of the 49 rows shows
up on the `/memory` page tomorrow, in its scope, with a title, openable, editable,
deletable, through routes that already exist and a page that already renders markdown.
**That is the entire fix for "I cannot see it", and it costs no new UI.**

**It makes the prompt budget visible as one number.** Today two stores independently claim
full-text injection (learnings 900 tokens, working state 1,200) and a third claims
title-space (knowledge index 500), and no code anywhere adds them up. Under one store with
a `reach` field, "what is always in my prompt" is a query, and the operator can sort a
scope by reach and see his standing instructions as a list with a total at the bottom.

### What changes for the operator

- `/memory` gains the 49 rows. They are notes, in their scopes, with titles.
- A note has a control that says whether it is always in the prompt. Following the
  existing pattern for low-stakes settings, this is a commit-on-change toggle, not an
  Edit-then-Save cycle.
- Deleting a standing instruction is deleting a note. No collector, no aging policy, no
  background process deciding what he forgets.
- The word "learning" disappears from the product. What is left is "a note that is always
  in the prompt", which needs no glossary.

### Naming, restated

Do not name the fold "learnings become a note kind". Name it what it is: **a note can be
standing.** In UI copy, "Always in context" or "Standing" as the toggle label. In code,
`reach` as the field. Reserve "memory" for the page, since that is what it already means
there, and let the thing on the page be a note, since that is what it already is
everywhere else.

---

## 6. What would be lost, and where each piece goes

`learnings.py` is 1,467 lines. Most of it is not worth keeping at this corpus size, but
some of it is load-bearing and would be silently dropped by a naive fold. Item by item.

| Behaviour | Where it is now | Does it fire? | Verdict |
|---|---|---|---|
| **Write-time dedup and supersession** (`_resolve` returns add / update / supersede / noop / refused) | `learnings.py:528`, from `record` `:615` | **Yes.** 17 of 21 archived rows were archived by it. | **KEEP, and give it to notes.** This is the single most valuable thing in the file and notes have nothing like it: `knowledge.py` has zero dedup, so ten notes about one fact are ten notes. Move `_resolve`, `_canonical` (`:210`), `_jaccard` (`:230`) and `_containment` (`:239`) into the note write path. `memory_keeper.py:73` already imports `learnings._NEAR_DUP_JACCARD` to do this for notes, in a keeper that never runs; this makes that intent real. |
| **Quality gate refusing bare status tokens** (`quality_refusal`, `_is_machine_status`) | `learnings.py:304`, `:287` | Yes, 3 archived rows carry its reason. | **KEEP, narrow it.** It correctly refuses `build failed` and `exit 1`. It cannot refuse a well-formed sentence about a finished ticket, which is 22 percent of the store. Apply it to `reach: "always"` writes specifically, where the cost of a bad row is highest, and do not pretend it solves durability. |
| **Scope layering** (global plus the chat's own scope) | `chat_context.py:1281`, `:1307` | Yes. | **ALREADY IN NOTES, better.** `knowledge.read_layers` (`:432`) and `layered_index_block` (`:503`) do richer layering (workshop plus role plus place plus private) with a governance floor (`:500`). Learnings' two-block version is the weaker implementation of the same idea. Drop it and use the note one. |
| **Provenance weighting and salience** (`_entry_value` `:409`, `_is_salient` `:397`, `_cap_provenance` `:385`) | `learnings.py` | Partly. Provenance accumulates; ranking runs on render. | **KEEP the count, DROP the formula.** "This was said five times on five occasions" is genuinely useful and notes cannot express it, so keep `provenance` as a list on the note. The 30-line weighted-value ranking function exists to choose which of 49 rows fit in 900 tokens; at this corpus size, order by scope and recency and let the operator pin. |
| **Render and retrieval counters** (`_persist_render` `:815`) | `learnings.py` | Yes, and it is the source of the collector defect. | **KEEP `last_used`, DROP the rest.** One timestamp per note answering "when did this last go into a prompt" is exactly what the operator needs to prune by hand. `render_count`, `retrieval_count`, `last_rendered`, `last_retrieved` and the hourly write throttle (`:105`) are four counters and a lock built to feed a collector that never ran. Note also that rendering currently rewrites the whole scope file on a read path, wrapped in a bare `except: pass` (`:852`); a single `last_used` stamp is cheaper and honest. |
| **`_BLOCK_TOKEN_BUDGET` / `_MAX_BLOCK_ENTRIES`** | `learnings.py:99`, `:87` | Yes. | **KEEP, as the `reach: "always"` budget.** The concept is right and stays; it just becomes one number the operator can see rather than a private constant. |
| **Pinning** (`set_pinned` `:771`) | `learnings.py` | **No.** Zero callers, zero pinned rows. | **DROP the learnings one; notes already have it.** `knowledge.note_pinned` (`:865`) works and is used (the working-state note is pinned at `:1264`). |
| **The maintenance suite** (`maintain` `:1269`, `prune_stale` `:1103`, `merge_duplicates` `:1052`, `sweep_quality` `:1166`, `reconcile_store` `:1194`, `cap_provenance` `:1250`, `maintain_all`, `tidy_store_dir` `:1307`) | `learnings.py`, roughly 300 lines | **No.** One manual CLI run, 2026-08-17, ever. | **DROP.** Everything it does at rest, `_resolve` already does at write. A batch reconciler for a store that reconciles on write is a second implementation of the same rule that can disagree with the first. |
| **`distill_skill`** (`:947`) | `learnings.py` | **No.** Only its own CLI. `memory_keeper.py:560` explicitly declines to call it. | **DROP.** |
| **`promote_when_stable`** (`:753`) | `learnings.py` | **No.** Its only non-test caller is `tasks_all.py:1257`, on a code path that never executes (see 7.1). | **DROP, and re-derive later if wanted.** Promotion counts only `review:`-prefixed provenance (`_BLOCK_ORIGIN` `:729`), and there are zero rows with a `review:` source, so it can never return true. |
| **JSONL append-only storage** | `.runtime/learnings/*.jsonl` | Yes. | **DROP in favour of one note per row.** 49 rows becoming 49 markdown files is the point: a file has a URL, a title, and a delete button, and a line in a JSONL file has none of those. |

Net: roughly 1,100 of 1,467 lines are deletable, about 250 lines of dedup and quality
logic move into `knowledge.py`, and `knowledge.py` comes out with the write-time
reconciliation it has always lacked.

---

## 7. Other mechanisms in this area that exist, are referenced, and never fire

The recurring defect in this repo is a mechanism that is written, wired, cited in a doc,
and never executed. Every one found during this audit, with a citation.

**7.1 The `mint-rule` review action has no client.** `backend/routes/tasks_all.py:1243`
branches on `action == "mint-rule"` and calls `learnings.record` at `:1252` and
`promote_when_stable` at `:1257`. Nothing sends it. The three frontend callers of that
endpoint send only approve, deny, dismiss, accept and send-back
(`frontend/src/components/TaskPanel.tsx:303`, `frontend/src/pages/TicketDetail.tsx:258`,
`frontend/src/pages/ChangeReview.tsx:139`), and `TaskPanel.tsx:568` defers mint-rule to a
"Work.tsx Review tab" that does not exist. Confirmed in the data: zero rows in any scope
file carry a `review:` source. **The one write path the ground-truth brief expected to be
live is dead**, and the live writers are the chat fence (`task_scope.py:553`) and the
autonomous loop (`scripts/ralph.sh:1085`).

**7.2 `prune_stale` has never archived a row.** `backend/services/learnings.py:1103`. No
caller, no schedule, no fingerprint in the archive. Full argument in section 3.

**7.3 `set_pinned` has no caller, so no row can be pinned.**
`backend/services/learnings.py:771`. Zero pinned rows across all 49.

**7.4 `learnings.maintain` and its whole suite have run once, manually, ever.**
`backend/services/learnings.py:1269`. The four archived rows attributable to it all share
`archived_at: 2026-08-17T06:41:20Z`.

**7.5 `verify_memory.py`'s GC check both fails and has a blind spot.**
`scripts/verify_memory.py:356`. Against today's data it fails, correctly. But the
comprehension at `:372` filters on truthiness, so a store whose `retrieval_count` is
uniformly zero yields an empty set and **passes**. That is exactly `global.jsonl`'s state
(12 rows, all zero, 881 tokens against a 900-token budget so the cap never binds). The
check that exists to catch this defect is blind to its worst instance.

**7.6 The memory keeper agent has no scheduler.** `backend/services/memory_keeper.py:628`
`run_consolidation` is reached from `scripts/memory_keeper_run.sh:57`, dispatched by
`scripts/dispatch_triggers.sh:467` for the `keeper` role agent declared at
`agents/memory-keeper.md` with `cron: 0 */6 * * *`. `dispatch_triggers.sh` appears in no
crontab, and this host has no user crontab at all. Its watermark has been frozen at
`2026-07-18T18:00:01Z` for 36 days, and it has written exactly one learning, ever. This
means **notes get no dedup and no supersession in practice**, because that logic lives
here (`memory_keeper.py:73-90`) and nowhere else.

**7.7 `enforce_index_cap` is reachable but has never fired.**
`backend/services/knowledge.py:282`, called from `_put_note_sync` at `:993`. It demotes
notes to an `archive/` subdir past 200 index lines. No `archive/` directory exists under
any knowledge scope and no `memory-archive.md` exists anywhere. `shared/knowledge/MEMORY.md`
is at 97 lines, so the cap is real, wired and simply not yet reached. Unlike the others in
this list it will fire on its own; it is here so that a fold does not delete it by accident.

**7.8 `shared/types.ts:14` declares `memory_blocks: string[]`.** The only occurrence of
that identifier in the entire tree. A leftover of the Letta agent-memory model with no
backend, no writer and no reader.

**7.9 `.runtime/knowledge/archives.json` has zero readers and zero contents.** Its payload
is `{"workshop": {"archive_id": "archive-28660ae1-...", "passages": {}}}`. `grep -rn
archives.json --include=*.py` returns nothing.

**7.10 `.runtime/commute-notes/` has 64 files and no backend writer.** Referenced only from
`docs/apps-are-pluggable.md:128` and `scripts/check_pluggable_apps.sh:137`. Whatever wrote
these last wrote on 2026-08-21 and is not in `backend/`.

**7.11 `memory-policy.json` gates nothing.** `.runtime/memory-policy.json` holds three
defaults, `"items": {}` and `"scopes": {}`. It is consulted at `knowledge.py:1343` and
`semantic_index.py:1306`, so the mechanism works; it simply has no data, which means the
sharing controls on `Memory.tsx:1024` and `Settings.tsx` have never been used.

**7.12 The learning receipt is deliberately unclickable.**
`frontend/src/components/chat/receipts.ts:41` returns `null` for any receipt whose kind is
not `note`, asserted by `receipts.test.ts:59`. So when a chat writes a learning, the
operator sees a lightbulb (`MessageList.tsx:137`) and a line of text that goes nowhere.
That is not a bug in `receipts.ts`: it is correct, because there is nowhere to go. It is
the clearest single expression of the problem. Folding into notes fixes it by making the
destination exist.

**7.13 Four tickets that address exactly this complaint are cancelled.** In `.tasks.json`:
`8c3ab8ae` "Typed operator-preference record", `692ee17b` **"Preference review UI: see,
correct, and delete what the system learned"**, `b3240eba` "Let repeated chat preferences
earn durability", `75d7d996` "Design the note/learning capture model". The second of those
is word-for-word the operator's present complaint, filed and cancelled. Its objective line
reads: "A mis-learned rule that silently distorts behavior forever is worse than no
learning."

**7.14 The fold is not a ticket.** The instruction the brief expected to find as a ticket
is a paragraph inside a TODO item: `shared/queue/TODO.md:21`, which states plainly that
`shared/knowledge/<scope>/` and `.runtime/learnings/<scope>.jsonl` "differ only by a `kind`
tag" and "should almost certainly be one store". That TODO is `[ ]` and unstarted.

**7.15 Ticket `d5895cc2` "Execute P5 and P6 (memory layers)" is `todo` but P6 is done.**
`docs/platform-redesign/continuation-2026-07-31.md:25` records P6 as built in commits
`9002956` and `ca6f973`, and `role:` scopes are live (`shared/knowledge/roles/reviewer/`
exists with one note). The ticket has not been closed.

---

## 8. What this recommends doing, in order

Not a plan to execute now; the operator asked a question, and this is the answer plus what
follows from it. In dependency order, smallest first:

1. **Rename, no behaviour change.** `Memory.tsx`'s default export `Notes` becomes
   `MemoryPage`. Per-scope `MEMORY.md` becomes `INDEX.md` (`knowledge.py:236`, 8 files).
   Settles section 2 for the price of a rename.
2. **Add `reach` to a note**, defaulting to `"index"`. Nothing changes yet.
3. **Move `_resolve` and `quality_refusal` into the note write path.** Notes get dedup and
   supersession for the first time. This is the piece with real value and it is worth
   doing whether or not the fold follows.
4. **Migrate the 49 rows to notes with `reach: "always"`**, one file each, carrying
   `provenance` and a single `last_used`. Point `chat_context.learnings_context` at the
   note store, keeping the same token budget.
5. **Delete `learnings.py`,** including the entire maintenance suite, `distill_skill`,
   `promote_when_stable`, `set_pinned`, and the counter machinery. Delete
   `shared/types.ts:14` and `.runtime/knowledge/archives.json` on the way past.
6. **Give the operator the toggle and the total.** A note shows whether it is standing; a
   scope shows how many tokens its standing notes cost. That is the collector, replaced by
   a person who can see.

Step 6 is the one that answers the actual complaint. Steps 1 through 5 exist to make it
possible.
