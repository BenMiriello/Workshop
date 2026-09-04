# Reassessment: the agents that work tickets, and the memory that was supposed to remember them (2026-08-23)

Status: CURRENT. Supersedes nothing. Corrects the status banner of
[`primitive-roles-2026-07-18.md`](primitive-roles-2026-07-18.md) and the build log of
[`memory-consolidation-2026-08-16.md`](memory-consolidation-2026-08-16.md), both of which
describe their own state inaccurately.

---

## 0. Why this document exists

On 2026-08-23 the operator opened the Agents list, found nothing that looked like it
worked tickets, and asked what did. The agent answering him did not recall
`primitive-roles-2026-07-18.md` and built a new per-scope "maker" reconciler from
scratch inside the conversation, with an invented slug rule, on a 60-second loop. He
named the failure precisely: "custom untraceable agents despite the visibility being
central to the design", and then asked the question this document is mostly about:
we were going to build better memory recovery and search, did we do any of that?

**The cause was mechanical and it is now measured.** The design corpus was unreachable
from every chat. Searching *"is an app the same thing as a project"* with no scope
returned `app-is-the-entity-2026-08-20.md` first, at 0.787. The same query from a
`workshop` or `development` chat returned **zero documents**, for all 215 of them. The
MCP query server always passes `from_scope`, so that was what every agent got.

An agent that cannot retrieve a plan will rebuild it. Every time. The invented maker is
the symptom; unreachable memory is the disease. That ordering is the main finding here,
and it is why the memory section of this document is longer than the agent section.

---

## 1. The correction to the record

Two status banners are wrong and have misled at least one session each.

`primitive-roles-2026-07-18.md:12-16` says "The rest of R0-R4 (triage loop, template
catalog) has not been verified built or unbuilt by this pass." **R0 through R3 are
substantially built.** The role enum, the dispatch table that replaced the hardcoded
`role == "memory"` branch, the note-triager service and its runner, all three templates,
both groups, the three adoption routes, and a full adoption UI with all four list states
are in the tree today.

`memory-consolidation-2026-08-16.md` §9 reads as though T1 landed. **T1 is partly
built**: M1, M2, M4, M5 and M6 landed; M3's scheduler and M7 did not, and two new
regressions landed on top of it.

The lesson is the same in both cases and it is the one this system keeps relearning: a
mechanism that exists, is referenced, and never fires reads from the outside exactly
like a mechanism that was never built. **Built and unwired is indistinguishable from
absent, and should be reported as absent.**

---

## 2. The agents: designed, built, and never once used

### 2.1 What is true

The five primitive roles are law and are enforced in code:
`ROLES = ("maker","reviewer","triager","keeper","broker")` with a write-side validator
that raises rather than snapping. The doc's central claim holds and is worth restating
because it is what stops role inflation:

> **A role is not a job title. A role is the set of writes an agent is permitted to
> make.**

Built: the role enum (R0), the `ROLE_HANDLERS` dispatch table (R1, structurally
different from the spec's `ROLE_RUNNERS` but functionally equivalent), the note-triager
service and runner (R2), and the templates, groups and adoption path (R3).

### 2.2 What is not, and it is the whole of the value

**Nothing has ever been adopted, and the triage ant has never fired once.**

- All live agents carry `template: None`. The adoption path has never been exercised on
  this instance.
- `find .runtime -iname '*triage*'` returns nothing. `.runtime/trigger-state.json`
  carries zero `triage:` keys. There is no `.runtime/triager/` directory.
- The only `role: triager` on the instance is `<bridge-agent>`, whose trigger is disabled with
  `disabled_reason: "discord trigger has no channels"`.
- `memory-keeper` runs from a crontab line that is commented out, and there is no
  crontab installed on this machine at all. Its watermark reads `2026-07-18T18:00:01Z`:
  **36 days frozen**, up from the 29 days the 08-16 audit measured.

So the note-driven work loop, which is the thing the operator asked for ("leave a note,
it gets picked up, an ant makes tickets or escalates"), is complete code that has never
executed. That is the gap, and it is small: a trigger, an adoption, and two surfaces.

### 2.3 The divergence that was introduced, and what was kept

`makers.py` (commit `1c6d869`) conflicted with §6 in one specific way: it auto-created a
definition per runnable scope every 60 seconds. §6 says "The default for a new project
is zero agents. The project loop is not an agent; projects work without any definition
file", and `agent-roles-research-2026-07-16.md:1068` rejects auto-provisioning by name
as "a machine for generating noise and cost".

Reshaped in `23c3b05`, keeping what was load-bearing and removing what was invented:

- **Kept**: the module owns no state of its own (arm level *is* `scope_execution`,
  residency *is* `work_driver`, activity *is* `presence`, runs *are* the job store);
  `maker_for_scope` as a search rather than a slug lookup; `ensure()` additive and never
  rewriting; the persona reaching `ralph.sh`, which is the only thing that makes a
  definition do something instead of being a display row.
- **Removed**: the reconcile loop; the invented `<slug>-maker` rule (a test now pins that
  the suffix appears nowhere in the module); a docstring claiming an enforcement the code
  did not perform; and the dead `_handler_for` branch testing the retired `runtime`
  field, which was permanently false and made every roleless agent report
  `skip-assistant` instead of R1's `skip-role`.

Two things the reshape found that the audit had not: `POST /agents/build` wrote a
registry row and **no definition**, so a maker the operator created by hand never reached
his roster; and a trigger-less maker logged "registered but undispatchable", which reads
as though nothing was working its tickets while the resident runner was in fact working
them.

### 2.4 The rule this episode establishes

> A definition file is created by someone asking for one. Never by a loop.

The corollary, which is the operator's actual requirement and was not written down
anywhere before now:

> **Anything that acts on the operator's behalf must be visible in the Agents list, and
> anything visible there must be manageable from there.** A process that works tickets
> and cannot be seen, named, paused, or edited is not finished, however well it works.

---

## 3. Memory: what was inert, what is fixed, and what is still missing

### 3.1 Fixed today, all measured before and after (`cf565ee`)

- **Documents are reachable again.** `private` on a placeless kind resolved as
  `from_scope == item_scope` against an empty string: always false. That is not a
  restriction, it is an erasure, and no setting should be able to express
  findable-from-nowhere by accident. The resolution is now explicit; the Settings copy
  that said the control "only matters once you scope one yourself" is corrected, because
  it mattered retroactively to every document that exists.
- **81% of the chat corpus was never indexed.** `collect_chats` was the only collector
  not using `_split`: on a flush it truncated the next message to 1,200 characters and
  discarded the tail. 7,094,963 characters on disk, 1,357,375 indexed. The messages it
  ate were the long ones, which are the designs and the operator's direction, while
  compaction was simultaneously telling the model they were "still searchable". Chat
  chunks went 1,466 to 7,974.
- **The relevance floor was tuned for a replaced model.** bge scored 15 of 18 nonsense
  queries above the nomic-era 0.58, including "banana helicopter tuesday" at 0.647.
  Re-measured over 48 queries: 0.65. The superseded penalty had to *shrink* to 0.03,
  because bge's competitive band is narrower and 0.05 buried history rather than
  demoting it.
- **The reason none of this was caught**: `verify_memory.py` asserted "search reaches
  docs" *without* `from_scope`, the one path that skips the policy entirely. It passed
  while every real search returned nothing, and it was wired into no gate. It now tests
  the path agents take and runs inside `reviewer.sh`.

Outcome, through the real tool path: from a `development` chat, the model of record is
now **rank 1**.

### 3.2 Still inert, in priority order

1. **The learnings garbage collector has never fired.** `prune_stale` and `maintain`
   have zero callers outside their own CLI. Zero archive rows in the store's whole life
   carry an aged-out reason. The 08-16 fix (splitting `render_count` from
   `retrieval_count`) was real and correct and made the precondition satisfiable;
   nothing was ever scheduled to use it. Cost today: `project:development` holds 7 rows
   and **7 of 7** are the machine template "Ralph keeps hitting X, try a different
   tactic". They pass the quality gate because the template contains instruction words.
   They are injected into that scope's prompt every turn and nothing will ever remove
   them.
2. **`memory_keeper` was never repointed and has not run in 36 days.** D10/M7 said
   repoint it to merge, supersede, prune and re-index, then schedule it. It is still
   verbatim the consolidation organ D10 condemned, and its scheduler chain terminates in
   a commented-out crontab line.
3. **The operator cannot search anything.** Zero HTTP routes reach
   `semantic_index.search`; its only non-test caller is the MCP server. The Memory
   page's search is plaintext substring over note bodies, scored by raw term count with
   no stopwords and no length normalisation, so it ranks by file size. Documents are
   searched by title and path only. Chats are searched by title plus 120 characters of
   one message. **The one component that indexes chat transcripts, doc bodies and
   tickets is agent-only.** "Where was this decided" is answerable, badly, by an agent,
   and not at all by the operator.
4. **Learnings have no UI, read or write.** They are the only always-injected store the
   operator cannot see, edit or prune from the app. The 08-16 doc said exactly this and
   it is unchanged. `set_pinned` has no caller, so no learning can ever be pinned.
5. **No index status surface.** `semantic_index.status()` has one non-test caller and it
   is the MCP server. Nobody can see whether the index is fresh, how large it is, or
   when it last built.
6. **Note titles are the only thing surfaced, and the budget is 500 tokens.** The
   `workshop` scope holds 109 notes; a plain workshop chat sees about 15 titles and the
   line "(103 more notes in this scope; use the search tool to reach them)". Until
   today that line pointed at a tool that could not reach them.
7. **The operator's own preferences are half-invisible.** The `workshop` learnings block
   renders 12 of 24 rows at 889 tokens against an 900-token cap, with "+12 more". Half
   of what he has told the system never reaches the prompt, and the cut is by
   provenance weight, not by importance.
8. **T2 is built and unused.** `agent:` chat keys are reachable and tested; 0 of 101
   live threads carry one. "Chat with an agent" works and nothing has ever done it.

### 3.3 The rule this establishes

> The ratchet covers enums, app names and field occupancy. It does not cover memory,
> which is why memory rotted under a green gate for a week.

`verify_memory.py` is now in `reviewer.sh`, which closes this for retrieval. The
remaining half is that **an inert mechanism should be a gate failure**: a scheduled job
whose watermark has not moved, a collector with no caller, a store with no reader. Those
are all mechanically detectable and none of them is detected.

---

## 4. What to do next, in order

The ordering principle: **restore recall first, then wire the loop that was built, then
give the operator the surfaces.** Recall is first because everything else is
re-derivable from a plan an agent can find, and nothing is re-derivable from a plan it
cannot.

**N1. Schedule the learnings collector.** `maintain`/`prune_stale` on the same lifespan
reconciler pattern as the index maintenance loop, with the salience floor already
written. Acceptance: an aged-out archive row exists, and the seven templated Ralph rows
in `project:development` are gone from the injected block.

**N2. Repoint and schedule `memory_keeper`.** Merge, supersede, prune, re-index, per
D10. Do not restore the consolidation organ. Acceptance: the watermark moves, and it
moves without a crontab, because there is no crontab on this machine.

**N3. Give the operator search.** One route onto `semantic_index.search`, and the Memory
page's search box pointed at it instead of substring matching. Scope it honestly: the
operator searching his own workshop is not a scoped agent and should see everything.
Acceptance: searching "why did we stop using nomic" from the Memory page returns the
commit and the doc, not three notes ranked by file size.

**N4. Make learnings visible.** A read surface first, with provenance and the two
counters, then delete and pin. This is the store that shapes every prompt and it is the
only one with no window into it.

**N5. Give the note-triager a live trigger and adopt `tended` on one scope.** The code
is written, tested and has never run. Start with one scope, watch one pass, then widen.
Acceptance: write a note, one ticket appears with `provenance=note:<scope>:<id>`; edit
the note, it links rather than duplicating; a bare URL note decides `none`.

**N6. Ship R4's two surfaces.** The per-note triage chip and the "from notes" filter on
the ticket list. Both target files were renamed out from under the design doc
(`Notes.tsx` is now `Memory.tsx`, `Work.tsx` is now `Tickets.tsx`), which is part of why
they were never built. Without these, a triage decision is invisible and the ant cannot
be trusted.

**N7. Detect inert mechanisms in the ratchet.** A scheduled job whose watermark has not
advanced in N days, a public function with no non-test caller, a store with no reader.
This is the general form of the defect that produced everything in §3.2.

**Deliberately not planned here**: renaming the roster's retired "builder" vocabulary
(the operator's call, and a rename churns slugs that are identities elsewhere); and
enabling `auto_when_green_and_low_risk`, which remains the open question recorded in
`changes-and-review-2026-08-22.md` §8.

---

## 5. What this changes about how these plans get written

Three habits, each earned by a specific failure in this episode:

1. **Search before designing.** The agent that invented `makers.py` had a search tool and
   did not use it, and it would not have helped if it had. Both halves are now fixed:
   the corpus is reachable, and this is written down.
2. **A status banner is a claim and decays like any other.** Two of them were wrong in
   opposite directions. When a phase doc says "not verified", verify it before building
   against it, and update it in the same commit.
3. **Report built-and-unwired as absent.** Every item in §3.2 was "built". None of it
   ran. The operator experiences the difference as nothing, and he is right to.

---

## 6. Addendum, same day: working state, and the end of "learnings" as a concept

The operator read §3, then asked two things: patch the most basic level so memory
is not lost when we compact and continue, and give us handoff information we can
read and continue from later, so the intended system gets built up and the
shortcuts taken along the way get fixed. He also said, of learnings: "not sure
this is a first order concept."

### 6.1 What was built (commit `16c09d4`)

Both asks land on one seam, which is worth stating because they did not look like
one problem. Compaction folded the transcript through a model call whose prompt
asked for "a running summary", and **the result lived only inside that thread**.
So what survived a compaction was whatever the summarizer felt like writing, and
a new chat in the same scope inherited nothing at all. Nothing durable was ever
written at the exact moment memory was about to be lost.

The compaction summary now has six fixed sections, generated from one constant so
the prompt, the trimmer and the tests cannot drift apart:

**Objective · Decisions and why · Done · Not done · Shortcuts taken and what they
owe · Open questions**

Two of those are load-bearing and neither is obvious:

- **Decisions and why.** A decision without its reason gets re-litigated or
  silently reversed. Most of the wasted work in this repo's history is one of
  those two.
- **Shortcuts taken and what they owe.** A shortcut nobody wrote down is
  indistinguishable from a design decision. That is precisely how the parallel
  systems this effort is removing came to exist, and it is why the operator asked
  for it by name.

It is persisted as **one note per scope in the store that already exists**, not a
twelfth substrate, and injected into any chat in that scope. That injection is
the half that makes it worth building: the old summary only ever helped the
thread that produced it. Overlap with the ambient digest is settled rather than
left to collide: the digest owns ticket and job **status**, the working state owns
**reasoning**, because no registry has a field for why.

Proven on a real fold with a live model call rather than asserted. The structured
prompt produced structured output, and the note it wrote surfaced, unprompted,
"keep learnings.py or delete it?" as an open question owned by the operator.

### 6.2 Learnings is not a first-order concept. It is a kind of memory.

The operator's instinct is right and the measurement is blunt about it:

> **1,467 lines of machinery, for 48 rows, in 5 scope files, that the operator
> cannot see, whose garbage collector has never once run.**

That is not a substrate. That is a feature of memory that grew its own house.
Everything that makes it feel separate is either something notes already do, or
something notes should do:

| What learnings has | Where it belongs |
| --- | --- |
| scope and layering | notes already have this, identically |
| update-in-place and supersede | notes already update in place; the dedup and supersede logic is the genuinely valuable part and must survive |
| provenance | notes carry an author and markers |
| the quality gate (refuses a bare status token) | worth keeping, and worth applying to more than learnings |
| render and retrieval counters | only earn their keep if a GC is scheduled; see N1 |
| injected in FULL, not as a title | this is the one real difference, and §6.1 just built the precedent for it |

That last row is the whole design. A note's **kind** should decide whether it is
injected as a title or in full. The working-state note is injected in full and is
a note; a learning is injected in full and should be a note too.

**Decision: fold `learnings` into the knowledge store as a note kind, and delete
the separate store.** One store, one injector, one garbage collector, one UI, and
the operator gets to see and prune them, which is item 4 of §3.2 solved for free
rather than built twice.

The capability the operator explicitly blessed survives untouched: a system that
learns specific things about him and keeps them for itself or for its agents.
That is what it does now; it just stops being its own country to do it.

### 6.3 The order of the rest, revised

Recall first, still. The revision is that §6.1 landed ahead of everything and
changes what N1 through N7 cost.

1. **N1, schedule the collector** — now also decides whether the counters survive
   the fold in §6.2, so it comes before it rather than beside it.
2. **§6.2, fold learnings into notes** — the migration is 48 rows. It will never
   be cheaper than it is now, and every week it waits, more code is written
   against a store that is leaving.
3. **N3, give the operator search** — with §6.1 landed, the working-state notes
   become searchable objects, which is most of the value of searching at all.
4. **N2, repoint and schedule the keeper** — it is the thing that would maintain
   the merged store, so it follows the merge rather than preceding it.
5. **N5 and N6, the triager's live trigger and its two surfaces.**
6. **N7, teach the ratchet to detect an inert mechanism** — the general form of
   every defect in §3.2, and the thing that stops this document being written
   again in six weeks.

N4 (make learnings visible) is **struck** as a separate item: §6.2 delivers it,
because a note is already visible.
