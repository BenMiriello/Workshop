# Notes, memory and tasks: what is actually stored, and what to do about it

Status: **discussion**. Nothing here is built. Ticket `165fc15a`, filed
2026-08-10 as "not yet", written up 2026-08-12.

The ticket said four overlapping stores. It is nine, and the interesting problem
turned out not to be the count.

---

## 1. The map

Nine places hold durable text. The column that matters is the last one: a store
that is PUSHED costs tokens on every single turn whether or not it is relevant,
and a store that is PULLED costs nothing until it is asked for.

| # | store | path | written by | reaches a turn by |
|---|---|---|---|---|
| 1 | Knowledge notes | `shared/knowledge/<scope>/*.md` + `MEMORY.md` | agents (fenced note block), memory-keeper | PULL, top 3 per message |
| 2 | Chat-distilled | `shared/knowledge/<scope>/chat-distilled.md` | chat compaction | same (it is note #1 in a trench coat) |
| 3 | Consolidated memory | `shared/knowledge/consolidated-memory.md` | memory-keeper | same |
| 4 | Learnings | `.runtime/learnings/<scope>.jsonl`, mirrored to `shared/learnings.md` | reviewer gate, ralph, operator corrections | **PUSH, every turn** |
| 5 | Tasks | `.tasks.json` per tree (13 of them) + `TODO.md` export | agents, operator | **PUSH**, via `workspace_digest`, capped |
| 6 | Notes app vault | `~/notes` (965 files) | the operator, in Obsidian | **never** |
| 7 | Repo CLAUDE.md | `CLAUDE.md`, `frontend/CLAUDE.md` | operator, agents | **PUSH, whole file** |
| 8 | Claude auto-memory | `~/.claude/projects/<enc>/memory/*.md` (30 files) | the CLI itself | **PUSH**, index every turn |
| 9 | docs/ | `docs/**` | agents, operator | **never**, unless someone reads it |

Stores 7 and 8 are not in the ticket and are the two the operator sees most
often, because they are quoted back in every system reminder.

**Tasks are in better shape than the rest and are close to fine.** The 13
`.tasks.json` files are one per scope, which is correct, and
`routes/tasks_all.py` already aggregates every scope into one feed. The only
real friction is that the two top-level scopes read as one inbox to a human and
are not: `development` is the platform repo (`./.tasks.json`, 16 open, almost
all the security audit) and `workshop`/`general` is `shared/queue/.tasks.json`
(9 open, everything reported in chat this week). Asking "what is open?" in a
chat answers for one of them. That is a naming and surfacing problem, not a
storage one.

---

## 2. The finding that changes the priority

Auto-recall (`routes/chat.py:1673`) searches the scope's notes with each message
and injects the top 3. That search is `knowledge._plaintext_search`, and it
scores a note as:

```python
score = sum(hay.count(t) for t in terms)
```

Raw term frequency. No stopword list, no IDF, no length normalisation. So common
words score, and a longer note contains more of every common word than a shorter
one. Run against the live `workshop` scope today:

```
Q: "how does the chat scope picker work"
   -> Distilled from chats
   -> Chat scope: one race caused every 'opens blank...' bug
   -> Transcript splitting: a delegation is atomic...

Q: "what should I know about the enter key in the composer"
   -> Distilled from chats
   -> Prior art survey: build/run/use in one system...
   -> Audit: personal/project data hardcoded in platform core

Q: "the a of to and is in"          <- pure stopwords, no topic at all
   -> Distilled from chats
   -> Prior art survey: build/run/use in one system...
   -> Audit: personal/project data hardcoded in platform core
```

The stopword query and the composer query return **the same three notes**, and
those three are exactly the three largest files in the scope (7402, 3269 and
3966 bytes). The first query only looks like it worked because the scope happens
to hold a note with "chat scope" in the title.

**In practice, auto-recall injects the three biggest notes on most turns.** It
is not retrieval, it is a length ranking wearing retrieval's clothes. Every
agent in this system has been reading the same three notes over and over,
regardless of what was asked, and paying for them every turn.

This is not an old bug that nobody noticed. The vector layer that used to rank
these was retired in the Stage 9 Letta teardown and the substring path, written
as a *fallback*, became the only path. Its own return value still says
`"debug": "fallback"`.

---

## 3. The other problems, in descending order of how much they cost

**P1. Retrieval is broken (section 2).** Cost: the wrong context on nearly every
turn, plus tokens for it.

**P2. There is no rule for which store a fact goes in.** A durable fact can
honestly be written to knowledge notes, learnings, chat-distilled, a CLAUDE.md,
or auto-memory, and the same fact regularly ends up in several. Measured by
grep across the stores: "design system" is stated in 6 of the 9, and 3 of those
6 are pushed into every prompt. "npx tsc is a no-op" is in 3. The existing
policy (`notes-and-docs-policy.md`, and the `notes-and-docs` skill) covers
scratch vs Notes vs docs, which is 3 of the 9, and says nothing about the
learnings / knowledge / CLAUDE.md / auto-memory choice, which is where the
duplication actually is.

**P3. Push and pull are assigned backwards.** Learnings, a 27-line append log of
past failure patterns, is injected unconditionally into every prompt. Knowledge
notes, which hold the actual architectural findings, are behind the broken
search from section 2. The store with the best content has the worst delivery.

**P4. Nothing retires.** `memory_keeper` has ADD / UPDATE / RETIRE routing and
`enforce_index_cap` bounds `MEMORY.md` at 200 lines, but the notes themselves
are unbounded and the index cap does not delete a note, only unlist it. Every
one of P1's symptoms gets worse monotonically as notes accumulate.

**P5. The operator's own vault is invisible.** 965 human-written markdown files
in `~/notes`, reachable only by the Notes app. Whether agents *should* read it
is a real question with a real answer either way, but right now it has not been
asked.

---

## 4. What I propose

**Two axes decide where a fact goes, and today the system has neither.**

- **Is it a RULE or a FINDING?** A rule is something you can violate without
  noticing, so it must be in front of you before you act: "rebuild the public
  origin", "never `npx tsc`". A finding is something you look up when it
  becomes relevant: "the edge's connection pool caused the send failures". Rules
  are pushed, few, and operator-owned. Findings are pulled, many, and
  agent-written.
- **Is it about the PLATFORM or the WORK?** Platform facts are true until the
  code changes. Work facts are true until the work moves.

Everything in section 1 maps onto that grid, and the merges fall out of it:
learnings, the CLAUDE.mds and auto-memory are all *rules* and should be one
store. Knowledge notes, chat-distilled and consolidated-memory are all
*findings* and are already one store wearing three names.

**But do not start with the merge.** Consolidating findings into one store while
retrieval is a length ranking produces one larger store that still cannot be
searched, and P4 means it degrades faster. Retrieval first.

Sequenced, cheapest first:

1. **Fix the ranking.** BM25 over 23 notes needs no dependency, no index, and
   no service: stopword drop, IDF, and length normalisation is about forty
   lines against `_plaintext_search`, and the fix is measurable against the
   probes in section 2 (a stopword-only query must return nothing). Do this
   before anything else, because every later decision is easier to judge once
   recall tells the truth.
2. **Name the rule store and empty the others into it.** One pushed store. The
   duplicate statements get deleted rather than moved. Concretely this is a
   decision about whether `CLAUDE.md` or `learnings` is the survivor; my
   recommendation is `CLAUDE.md`, because the CLI pushes it whether we like it
   or not, so anything elsewhere is strictly additional cost.
3. **Fold chat-distilled and consolidated-memory into ordinary notes.** They are
   already notes; they are special-cased only by filename, and their size is
   what makes them win P1's broken ranking.
4. **Give notes a retirement path.** `memory_keeper` already decides RETIRE; let
   it actually remove.
5. **Decide P5 deliberately.** My read: leave the vault to the human. It is the
   one store with a clear owner and a clear purpose, and merging it is the kind
   of change that is easy to make and very hard to undo.

Steps 1 and 3 are small enough to do in one sitting. Step 2 is a decision more
than a build. Steps 4 and 5 can wait.

---

## 5. What I did not do

No store was migrated, restructured or deleted. The ticket says discussion only
and this is the discussion. The one thing I would ask for a yes/no on before
anything else is step 1, which is a bug fix rather than a redesign and does not
really need the rest of this document to justify itself.
