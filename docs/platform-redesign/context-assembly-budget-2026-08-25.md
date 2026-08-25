# Context assembly: the missing budget layer

2026-08-25. Companion to
[`agent-context-and-toolkit-2026-08-20.md`](agent-context-and-toolkit-2026-08-20.md),
which this document amends. That doc defines the four-layer prompt contract and
is the model of record for **authority**. It says nothing about **cost**, and
this one supplies that half.

Everything numbered below was measured on this checkout on 2026-08-25 unless
marked as an estimate.

---

## 1. What went wrong

A brand-new `development` chat opens with a 114,192-char assembled prefix
(~28.5k tokens) before the operator has typed a character. `live_context()` is
77,265 of it, 68%. Inside that, `PROJECT.md` is 75,891 chars, and inside *that*,
the append-only `## Log` is 72,330 chars, 95.3% of the file, 30 dated entries
running 2026-06-22 to 2026-08-24. The brief above the Log is 3,561 chars.

The operator's question was: incomplete design, flawed design, or poor
implementation? **Incomplete.** The evidence supports that reading specifically,
and rules the other two out:

- It is **not poor implementation**, because every other author did the
  responsible thing unprompted. `_TODO_LIST_CAP = 8` (`chat_context.py:1821`),
  `knowledge.WORKING_STATE_MAX_CHARS = 4800` (`knowledge.py:1173`),
  `learnings._BLOCK_TOKEN_BUDGET = 900` per scope over two scopes
  (`learnings.py:99`), `DEFAULT_LAYERED_INDEX_TOKEN_BUDGET = 500`
  (`knowledge.py:496`), `workspace_digest.CAP_TASKS = 6`. Five independent
  authors, five explicit caps, each with a hand-written rationale beside it.
  That is not a codebase that forgot; that is a codebase where the individual
  discipline was uniformly present.
- It is **not a flawed design**, because the four-layer contract is correct
  about what it covers. L0 > L1 > L2 > L3, who authors each, what is
  confidential, what order they assemble in - all of that holds and none of it
  is what broke.
- It is **incomplete** in an exact way: the contract specifies authority,
  authorship, editability and order, and **never assigns a byte budget to a
  layer, and never names an owner for the total.** So each block author capped
  their own block and nobody owned the sum. The one block whose author did not
  cap it - `ProjectContextProvider.live_context()`, which does
  `self._read("PROJECT.md")` and appends the whole file - had nothing to fail
  against, and the layer above it had nothing to sum.

Two things turned that gap from a latent hole into a live cost:

1. **The persona instructs agents to keep growing the source.**
   `chat_context.py:1953` (and the near-identical copy at `:2073`,
   `build_agents.py:145`, `routes/projects.py:379`): "When the operator gives a
   non-trivial direction, append a one-line decision note to PROJECT.md." A
   prompt block that instructs agents to append to the store it inlines is a
   growth loop with the agent inside it. The mean Log entry is 2,411 chars, so
   the instruction costs roughly one median entry (1,942 chars) of permanent
   per-turn prefix each time it is obeyed.
2. **Nothing measures the total, and the one thing named "ceiling" cannot
   fire.** `PROMPT_CEILING_TOKENS = 600_000` is a fuse on conversation HISTORY.
   A 28.5k prefix will never trip it. `scripts/reviewer.sh` runs 19 `check_*`
   gates and not one of them looks at prompt bytes.

The only push-vs-pull statement anywhere in the tree is a code comment at
`routes/chat.py:2724` - "Pull, not push. Do not reintroduce an injection here"  - 
written narrowly about auto-recall and never generalized into doctrine. The
comment at `chat_context.py:1790-1812`, which justified cutting the TODO.md
block from 591,744 chars to 1,345 in commit `2b854321`, contains the whole
argument in miniature and was likewise never lifted out of the one call site it
sits in. This document is that generalization.

One structural note worth keeping: `routes/chat.py:2815 _resumed_prompt` sends
`runtime_briefing` INSTEAD of `live_context`, so the 76KB is a **fresh-chat and
post-compact** cost, not a per-turn one. That makes it cheaper than it looks in
aggregate and *worse* than it looks in the moment: it is paid exactly when a
chat has the least conversation to justify it, and again at every compaction,
which is when the window is already under pressure.

---

## 2. The doctrine

Written as rules a future author of a new prompt block can apply without
reading any of the above.

### R1. Push vs pull, with a test

A block belongs in the prefix only if it passes **both** halves:

> **(a)** The agent would be *wrong* without it, not merely slower. And
> **(b)** it cannot be known in advance which part of it matters, so the agent
> could not have asked for the right part.

Anything that fails (a) is a convenience and should be a tool call. Anything
that passes (a) but fails (b) - the agent *could* have named what it needed  - 
is a lookup, and pushing it is paying for 100% of a store to deliver the 2% that
this turn happens to need.

Worked: the ONGOING WORK digest passes both. An agent that does not know work is
in flight will duplicate it (a), and it cannot ask "what is in flight" before
knowing that anything is (b). A full ticket body passes (a) - you cannot do the
ticket without it - and fails (b) hard, because the agent knows exactly which
ticket it is working. That is the reasoning `2b854321` used, and it is the
general form.

Corollary: **a block that fails the test does not get a smaller cap, it gets
deleted and replaced by a pointer.** Halving something that should not be there
is not progress.

### R2. Every block that reads a growing store declares a cap AND what it drops

Two obligations, not one. A cap alone produces silent truncation, which is worse
than no cap because the agent cannot tell a store that is empty from a store
that was cut. So a capped block must, in the rendered text:

- name the cap it applied ("8 of 47 open"),
- name **what was dropped and on what rule** (oldest first, lowest priority
  first, non-blocking first),
- name **the tool that reaches the rest** ("search them with `search`").

If you cannot name the drop rule, you do not have a cap, you have a slice.

### R3. Something owns the SUM

The per-block caps are necessary and are not sufficient: five correct caps and
one uncapped block still produced 114,192 chars. So:

- The assembler (`routes/chat.py:_build_prompt`) owns a **total ceiling** and
  every block declares its cap to a single registry the assembler can sum
  *without executing it*.
- The gate (§5) checks `static prompt text + sum(declared caps) <= ceiling` at
  review time, statically. A budget you can only discover at runtime is a budget
  nobody checks.
- **The assembler never trims.** This matters more than it sounds: a blind
  tail-chop across layer boundaries would cut L3 (the operator's own custom
  instructions, lowest priority and assembled late) in order to fit an oversized
  L2, which silently inverts the authority order the contract exists to
  establish. Trimming is always block-local, done by the block's own author,
  under R2.

### R4. An unbounded-by-construction source may never be inlined whole

An append-only log, a chat transcript, an event stream, a generated export of a
growing store: these have no steady state. There is no cap you can choose today
that is still right in six months, because the *shape* of the source is
"monotonically increasing". They may appear in the prefix only as (i) a fixed
recency window with an explicit drop notice per R2, (ii) a digest whose size is
a function of the schema rather than of the history, or (iii) not at all, with a
tool pointer.

Test for whether a source is unbounded-by-construction: **is there any operation
in the system that removes an entry?** If the only writes are appends, it is
unbounded, and R4 applies no matter how small it is today. PROJECT.md's Log was
2KB once.

### R5. Prompt text is platform text (restatement, for completeness)

Already in `CLAUDE.md` and worth stating here because it interacts with R1: L0
to L2 ship to every installation, so a block cannot be justified by "this
checkout happens to need it". A per-checkout fact belongs in `AGENTS.md` or L3.

---

## 3. The PROJECT.md problem

PROJECT.md is two documents in one file:

| part | chars | shape |
|---|---|---|
| brief (goal, constraints, decisions, phase notes) | 3,561 | small, stable, edited in place |
| `## Log` | 72,330 | append-only, 30 entries, 2,411 mean / 1,942 median / 6,079 max |

The brief passes R1 cleanly: an agent without it does not know the goal or the
constraints (a), and could not have asked for a constraint it does not know
exists (b). It stays inline. It is 3,561 chars and it is the only part anyone
was ever arguing for.

The Log fails R1(b) and violates R4 outright. It is also genuinely valuable - it
is the decision history of the whole effort, and several entries are the only
written record of why something is the way it is. So the question is not whether
to keep it. It is where it lives.

### The deciding fact, verified

**PROJECT.md is not in the semantic index. Verified, not assumed.**

`semantic_index.PLATFORM_KINDS = ("note", "doc", "chat", "ticket")`.
`collect_docs()` walks `paths.workshop_root() / "docs"` and nothing else
(`semantic_index.py:479`), and `_doc_fingerprint()` fingerprints the same single
directory. PROJECT.md sits at the repo root, outside `docs/`. Reading the built
index directly (`.runtime/users/6/semantic/index.meta.jsonl`, 14,413 chunks:
5,059 doc, 8,308 chat, 827 ticket, 219 note) returns **zero chunks whose ref or
title contains `PROJECT`**.

This inverts the premise the split was going to rest on. PROJECT.md is
*readable* by path - `refs._clean_doc_path` and `docs_index._resolve_within_root`
both deliberately resolve `workshop:doc/<rel>` to any `.md` in the tree, and
their docstrings name PROJECT.md as the motivating example - but it is
**not searchable**. So moving the Log behind the search tools is **not free**:
it would move 72KB of decision history from "always present" to "reachable only
if you already know to open it by path", which is a real regression in
retrievability and exactly the kind of thing that makes an agent redo a settled
decision.

**Indexing PROJECT.md is therefore a hard prerequisite, not a nice-to-have.**
It is also small: the Log's `- YYYY-MM-DD - ...` entries chunk cleanly at
`CHUNK_CHARS = 1200` (median entry 1,942 chars → one to two chunks), and adding
project roots to the `doc` collector adds roughly 30 chunks per project against
a 14,413-chunk corpus.

### The three options

**(A) Brief inline, Log entirely behind search.**
live_context drops to 3,561 + the 1,345-char TODO roll-up = 4,906 chars, a 94%
cut. Maximum saving. Cost: the most recent decision - very often the one that
matters, since a fresh chat usually continues yesterday's work - is now a tool
call away, and an agent that does not know a decision was made will not search
for it. This is R1(b) failing in the other direction: recency is the one
dimension where the agent genuinely cannot ask, because not-knowing is the
condition.

**(B) Recency window of the last N entries inline, the rest searchable.**
Last 3 entries = 7,449 chars; last 5 = 14,860; last 8 = 28,919. Cost: N is a
guess, the window's size varies 3x with how verbose recent entries happened to
be (max entry 6,079 chars), and it is a cap on *count* protecting a budget
denominated in *chars* - the exact mismatch that `_TODO_LIST_CAP = 8` gets right
by also truncating titles. Fixable by capping chars and letting N float.

**(C) A regenerated "what has been decided" digest.**
Best information density in principle. Cost: a write-time LLM call on every Log
append, a second artifact that can drift from its source, and a lossy summary
standing in front of a verbatim record - which is precisely the mechanism
`semantic_index.py`'s own header rejects ("This is the corpus that makes
distillation unnecessary: the thing a summary was standing in for is right here,
verbatim and attributable"). Building a distiller in the same repo that argues
against distillation needs a stronger reason than saving 20KB.

### Recommendation: (B), char-capped, on top of a mandatory (A) prerequisite

Order matters:

1. **Index it first.** Extend the `doc` collector to include markdown at the
   repo root and at each project root, so the Log is searchable *before*
   anything stops inlining it. Nothing about the prefix changes in this step,
   and it is independently worth doing regardless of the rest.
2. **Then split the render.** `live_context()` emits the brief in full, plus the
   most recent Log entries up to a **char** budget, newest first, stopping at
   the last entry that fits whole (never mid-entry - a half-decision is worse
   than no decision). Then, per R2, a literal line naming the count dropped and
   the tool that reaches them: `(22 older Log entries not shown - search them
   with the `search` tool, or open workshop:doc/PROJECT.md)`.

(B) over (A) because the drop notice plus a working index is what makes the
recency window a convenience rather than a load-bearing guess: an agent that
misses something in the window is *told* the rest exists and *can* find it. (B)
over (C) because a verbatim window costs one slice operation and cannot drift,
and the corpus already argues against summarization as a memory strategy.

**Structural fix, same ticket family:** the brief/Log boundary is currently a
`## Log` heading convention that nothing enforces. A project whose Log is
spelled `## History` gets its whole file inlined again. Either the split is
enforced by the scaffolds that create PROJECT.md
(`routes/projects.py:372`, `build_agents.py:139`) and read by an explicit
parser, or the Log moves to its own file (`DECISIONS.md`) and the ambiguity
disappears with it. The separate-file version is cleaner and makes R4 structural
instead of textual; it costs a migration of the existing 30 entries and an
update to the four persona strings that say "append to PROJECT.md".

---

## 4. The budget

Measured today: total prefix 114,192 chars, of which live_context is 77,265, so
**everything-but-live_context is 36,927 chars (~9.2k tokens)**. That residual is
the honest floor of what exists with every author's own cap already applied:
19,420 chars of it is module-level prompt-text constants in `chat_context.py`
(17,847) and `routes/chat.py` (1,573); the remainder is per-scope personas built
as f-strings plus the five capped store blocks (learnings 2 x 900 tok, working
state 4,800 chars, layered index 500 tok, digest 6 tasks, TODO roll-up 1,345).

**Proposed: `PREFIX_CEILING_CHARS = 46_000` (~11.5k tokens), with
`LIVE_CONTEXT_CAP_CHARS = 8_000`.**

Derivation, so the number is arguable rather than asserted: 36,927 measured
residual + 8,000 for the one block that never had a cap = 44,927, rounded up to
46,000 to leave ~1,000 chars of headroom in the blocks that are small and
uncapped rather than pretending they are frozen. The 8,000 is itself derived
from the case it has to serve: PROJECT.md's brief (3,561) + the TODO roll-up
(1,345) = 4,906 of irreducible content, leaving ~3,100 for the recency window,
which is the last one to two Log entries at the observed median. It is not a
round number chosen first and justified after.

This takes a fresh `development` chat from 114,192 to roughly 45,000 chars  - 
28.5k tokens down to ~11.2k, a 61% cut - and, more importantly, makes the number
*bounded*: today's figure is a coincidence of how much has been written, and
after this it is a property of the code.

The residual 36,927 is deliberately **not** attacked in this pass. It is not
obviously wrong, no single block in it dominates, and cutting platform prompt
text is a different argument (about what agents need to be told) than capping
store reads (about what fits). Pinning it is what makes it visible; if it drifts
upward the gate says so.

### What happens when it is exceeded

Three different answers in three different places, and the split is the point:

- **Block level, at runtime: trim, visibly.** The block's own author truncates
  to its own declared cap and says so per R2. This is the only place trimming
  happens.
- **Assembly level, at runtime: log, never fail.** If the sum exceeds the
  ceiling anyway, `_build_prompt` emits a single WARN with the per-block char
  breakdown and assembles the prompt regardless. A user's turn is never failed
  over a budget: a fuse that fires on the operator is a fuse that gets disabled,
  and then the budget is gone along with the measurement.
- **Review level: FAIL.** `scripts/check_prompt_size.py` blocks the change.
  Review is the only point at which an over-budget prefix can actually be
  *fixed*, so it is the only point where blocking is useful rather than
  destructive.

---

## 5. The gate

`scripts/check_prompt_size.py`, following `check_no_app_names.py` and
`check_ratchets_not_loosened.py` exactly: a measured count, a pinned baseline
JSON under `scripts/`, ratchet direction down, `--json`, `--write-baseline`,
`--because` required for any upward pin, and `check_ratchets_not_loosened.py`
picking up the new baseline automatically (it asserts the repo-wide "numbers
count bad things, so they only go down" rule over every `*_baseline.json` that
is not in its `_ADDITIVE` set - this one is not additive, so nothing needs
adding there).

**It measures statically, not by running the assembler.** This is a deliberate
divergence in mechanism from the runtime thing it protects, for one concrete
reason: the assembled size depends on instance-local store CONTENT
(`.runtime/`, PROJECT.md, the knowledge store), none of which is tracked. A gate
whose number moves when the operator writes a note is a gate that gets re-pinned
until it means nothing. So it measures what the CODE commits to:

- **`static_chars`** - module-level prompt-text string constants in an
  enumerated file set (`backend/services/chat_context.py`,
  `backend/services/orientation.py`, `backend/services/custom_instructions.py`,
  `backend/routes/chat.py`), found by AST-walking module-level string
  assignments over 200 chars. **Measures 19,420 today** (chat_context 17,847,
  chat.py 1,573).
- **`declared_budget_chars`** - the sum of every cap in a new `PROMPT_BLOCKS`
  registry in `chat_context.py`, one row per prefix block: name, source store,
  `cap_chars`, and the drop rule from R2. Token-denominated caps convert at 4
  chars/token so one unit is summable.
- **`uncapped_blocks`** - rows in `PROMPT_BLOCKS` with `cap_chars is None`.
  **Pins at 1 today** (the project brief), target 0. This is the count that
  would have caught PROJECT.md on the day it was written, and it is the whole
  reason the registry exists rather than a comment.

Gate conditions, all three ratcheting down, plus one absolute:

    static_chars            <= baseline
    declared_budget_chars   <= baseline
    uncapped_blocks         <= baseline          (target 0)
    static_chars + declared_budget_chars <= ceiling_chars   # enforced once uncapped_blocks == 0

The ceiling is not enforced while any block is uncapped, because an uncapped
block has no number to sum and a ceiling computed over an incomplete sum reads
as passing when it is not measuring. `uncapped_blocks` going to 0 is what arms
it, and that ordering is stated in the baseline `_comment` so it cannot be
mistaken for the gate being lenient.

`scripts/prompt_size_baseline.json`:

```json
{
  "_comment": "Ratchet for check_prompt_size.py. These counts only go DOWN. ceiling_chars is enforced only once uncapped_blocks reaches 0, because a sum over an incomplete registry is not a sum. See docs/platform-redesign/context-assembly-budget-2026-08-25.md section 4.",
  "ceiling_chars": 46000,
  "live_context_cap_chars": 8000,
  "static_chars": 19420,
  "declared_budget_chars": null,
  "uncapped_blocks": 1
}
```

`declared_budget_chars` pins on the first run after `PROMPT_BLOCKS` exists;
`static_chars` is pinned from a **committed** tree (`check_no_app_names.py`'s
header records what happens otherwise: a baseline written mid-edit reads the
index, not the tree, and then fails the next run for no reason).

**Delta mode is not offered.** The `--since` pattern exists so a run is not
blamed for inherited debt, and it works by scanning a `git archive` extract. All
three counts here are pure functions of tracked source, so a regression is
always attributable to the diff under review and there is no inherited-debt
problem to solve. Adding `--since` would be ceremony. State that in the header
so the next person does not add it out of symmetry.

Wiring in `scripts/reviewer.sh`, alongside the other change gates:

```bash
PROMPT_SIZE_CHECK="skipped"
if printf '%s\n' "$CHANGED_ALL" | grep -qE '^backend/(services/(chat_context|orientation|custom_instructions|knowledge|learnings)|routes/chat)\.py$'; then
  if python3 "$WORKSHOP/scripts/check_prompt_size.py" >/dev/null 2>&1; then
    PROMPT_SIZE_CHECK="ok"
  else
    PROMPT_SIZE_CHECK="fail"
  fi
fi
```

Not `_delta_check`, since that helper's whole job is passing `--since`.

**An advisory second mode, `--measure-live`,** assembles the real prefix for
each scope with an empty conversation and prints the per-block char breakdown.
Never gated, because of the store-content problem above; it is how a person
answers "what is it actually now", and it is what produced every number in this
document.

---

## 6. What this does not cover

The operator measured a fresh chat consuming **over 30% of a 1M window**  - 
upwards of 300k tokens. The prefix this document governs is 28,500. Everything
in here addresses under 10% of what was observed, and saying otherwise would be
the more comfortable and less useful claim.

The rest is the Claude Code CLI's own context, which this repo assembles nothing
of: the CLI system prompt, its built-in tool schemas, the `CLAUDE.md` files it
loads (user-scope + project + `frontend/CLAUDE.md`), skill listings, and MCP
tool schemas. What can be quantified from here:

- **The `workshop` MCP server exposes 6 tools** (`query`, `search`,
  `spawn_background`, `where_is`, `list_attachments`, `get_file`), whose name +
  description + schema source is **5,917 chars**, so on the order of 1.5k
  tokens. That is small, and it is not the problem.
- **`CLAUDE.md` files**: the project file plus `frontend/CLAUDE.md` plus the
  user-scope file. In this checkout's own terms these are L3-adjacent and are
  the operator's to size, but they are worth measuring before assuming they are
  free.
- **Everything else is opaque from inside this repo.** The CLI's system prompt
  and built-in tool schemas are not ours, are not measurable from here without
  instrumenting the CLI, and are not something a design doc in this repo can
  budget.

The honest division:

| inside our control | outside it |
|---|---|
| the assembled prefix (§4 ceiling) | the CLI system prompt |
| how many MCP tools the server exposes, and their schema verbosity | built-in tool schema size |
| whether tools load eagerly or deferred | the deferral mechanism itself |
| `CLAUDE.md` size in this repo | user-scope `CLAUDE.md` |

Two follow-ups the measurement suggests, both outside this doc's scope and
neither ticketed here as a fix:

1. **Count the tools actually served per turn**, not just ours. If the CLI is
   loading many MCP servers' schemas eagerly, the schema block can exceed the
   entire prompt prefix, and the lever is *which servers are configured*, not
   how the prefix is assembled. Deferred tool loading, where available, is the
   direct fix and is a CLI-configuration change rather than a code change.
2. **Get a real breakdown before optimizing further.** The next honest step
   after this design lands is a measurement of the whole window at turn 1 with
   the prefix already cut, so the remaining ~270k has an owner. Cutting 70k from
   a number nobody has decomposed is how the next 30% goes unexamined.

This document is worth doing on its own terms - an unbounded block that grows
every time an agent obeys its own persona is a defect regardless of what
fraction of the window it currently occupies, and R4 exists so the next one is
caught at review instead of at 76KB. It is just not the whole answer to the
question that prompted it.
