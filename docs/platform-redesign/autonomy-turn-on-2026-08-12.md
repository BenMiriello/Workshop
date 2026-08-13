# The automation loop: what exists, what is dead, and the plan to turn it on

**Status: PLAN. Written 2026-08-12 against the live tree, not against the docs.**
This says which parts of the existing design are real, which are decoration,
and the shortest honest path from here to autonomous ticket execution.

**What it supersedes**, since an earlier draft claimed "supersedes nothing" and
that was not true:

- `ticket-lifecycle-and-integration-2026-08-10.md:229`, which records "scope
  review: split / blockers / stages" as shipped. Its routes were deleted
  2026-08-12; the engine survives unreferenced, and phase 3 decides its fate.
- The `touches`-enforcement precondition in `continuation-2026-07-31.md:303`
  and `latitude-model-2026-07-18.md:177`. Dropped here, with the reason stated
  at phase 5.
- Nothing else. Where this plan and a ratified decision disagree anywhere else,
  the ratified decision wins and this document is wrong.

> **This IS design doc number thirty-two, and it should have been shorter.**
> There are already ~30 across `docs/platform-redesign/`. The role model, the
> autonomy model, latitude, the `awaiting` shape and the triage decision set
> are all decided AND largely shipped. The gap is not design: it is that
> nothing runs, and that several shipped controls do nothing.
>
> An adversarial review of this document's first draft found it proposing a
> `awaiting` state split that reversed a decision recorded in code, a per-scope
> autonomy field with unnamed values, a rebuild of an inbox producer that
> already exists and works, and a first supervised sweep pointed at the one
> scope the operator ruled out as a build target. All four are corrected below,
> in place and named, because the failure mode this document is about is
> exactly that: adding to a design that was already finished.

## 0. The one paragraph that should decide the order of work

The largest measured intervention anywhere in the 2025-2026 literature on
agent systems is not a topology, a role set, a critic, or a memory
architecture. It is **a human answering a question**. Magentic-UI
([arXiv 2507.22358](https://arxiv.org/abs/2507.22358)) measured 162 GAIA
validation tasks at 30.3% autonomous and **51.9%** when a simulated user with
side information could answer, **with help requested on only 10% of tasks**.
Nothing else in the corpus comes close to +71% relative for that little.

Meanwhile, replications that hold compute constant find that most multi-agent
architectures **lose** to a single agent at the same token budget: "Do More
Agents Help?" ([arXiv 2606.05670](https://arxiv.org/abs/2606.05670), GPT-4.1,
10 benchmarks) found at most one of six tested systems beating a matched
single-agent anchor, the other five trailing by **2.6 to 11.3 points**; Tran
and Kiela ([arXiv 2604.02460](https://arxiv.org/abs/2604.02460)) found the same
holding reasoning tokens constant.

So the order is: **make asking work, make the ticket say enough, and only then
add machinery.** Everything in this plan is arranged around that, and anything
that looks like more agents should be read with suspicion.

---

## 1. What is actually true today (measured 2026-08-12)

Every number here came from the live stores and the running host.

### The loop is complete and dormant

| | |
|---|---|
| Last ticket executed | **2026-07-25 12:02** (`.runtime/ralph.log` mtime) |
| Resident queue runner | **none.** `.runtime/queue-runner.pid` absent; last runner exited 2026-08-10T06:54:42 |
| Autonomous cron | **commented out**, under the header `# ---- Autonomous work: OFF by operator decision 2026-08-10 ----` |
| Global autonomy brake | **off** (`.runtime/autonomy-override.json` `engaged: false`) |
| Runs in the log | 2753, of which **0 are non-terminal**; most recent start 2026-08-05 |

The path itself works: `POST /api/tasks/{scope}/{id}/run` opens a Job and
`Popen`s a detached `project_ralph.sh --task-id` (1168 lines), which claims the
ticket, resolves an effort tier, and fires `scripts/ralph.sh` (1022 lines) per
task. `ralph.sh` is what actually invokes `claude -p`, in a loop bounded by
`scripts/reviewer.sh`, and it is also where the one real containment control
lives: the irreversible-action deny list, built from
`agent_authority denied --unattended` and passed as `--disallowedTools`.
There is no runner agent, no queue daemon, and nothing that starts a run
except the API.

Naming those line counts matters, because phases 4 and 5 modify both files.

### The agent layer is disjoint from the system that runs

- `list_agents()` returns **3** (`masini`, `masini-commute`, `memory-keeper`).
  `build_agents.json` holds **8**, every one `state: retired`, last active
  mid-July. There are 4 more definitions under `agents/apps/`.
- **Zero registry agents have ever produced a job record.** The 7 `agent_id`
  values in the job log are Letta UUIDs belonging to per-channel Discord
  agents, which the registry explicitly excludes.
- Every agent is `autonomy: reactive`, which means never dispatched in the
  background. `dispatch_triggers.sh` (the conductor) is **not in crontab**.
- The one `triager` in the roster declares `triggers: [{kind: discord}]`, and
  the dispatcher serves `cron`/`loop`. It has never fired; `.runtime/triager/`
  does not exist.

So: the roles are real in code (`ROLES = maker, reviewer, triager, keeper,
broker`, validated on write, with a handler table), and no agent has ever been
routed through them.

### Scope review exists, is advisory, and most tickets fail it

`backend/services/ticket_readiness.py` asks six questions of a ticket: title,
objective, acceptance, entry points, boundaries, dependencies resolve. Only
`title` and `dependencies` block a run; the rest report.

Measured across all 34 open tickets in every scope right now:

| check | open tickets failing |
|---|---|
| acceptance (what done looks like) | **25 (74%)** |
| boundaries (what it may change) | **23 (68%)** |
| entry points | 6 (18%) |
| objective | 1 (3%) |
| **pass every check** | **9 (26%)** |

The checker shipped against **0 of 40** passing. 26% now looks like
improvement and may be, but the two populations are not the same tickets:
most of the original 40 are closed, so the delta is confounded by which ones
got worked. Treat it as "still not a number you can gate on", which is the only
claim this plan needs.

There WAS an LLM scope reviewer: `POST /scope-review` returned
`{verdict: ok|split|underspecified, reason, subtickets, blockers}` and `/split`
turned that into child tickets. **I deleted both routes on 2026-08-12**,
because nothing but one button called them, no ticket in the store had ever
been split, and no ticket carried a `parent`. That was the right call for a
dead route and the wrong shape to leave the capability in:
`backend/services/ticket_scope_review.py` still exists, 189 lines, reachable
only through a dangling import. **The engine survived; only its button died.**

### The linkage between tickets, runs, agents and chats is mostly unwritten

Job records carry `task_id`, `agent_id`, `chat_id`, `project_slug`,
`parent_id`. Across 2753 records:

| link | populated |
|---|---|
| `agent_id` | 1957 (71%), but every value is an unregistered UUID |
| `task_id` | 79 (2.9%) |
| `parent_id` | 74 |
| `project_slug` | 47, only two distinct values ever |
| `chat_id` | **0** |

`chat_id` was zero because `launch_task_run` never took one, while the chat's
own "file and run" path called it with the thread id sitting in scope. Fixed
2026-08-12; new runs record their origin chat, and the run page prefers the
run's own chat over the ticket's when they differ.

### Controls that exist and do nothing

Naming these is half the plan, because each one is a lie the UI currently tells.

| control | reality |
|---|---|
| `latitude` (strict/standard/open) | **prompt text.** One machine-visible consequence: reviewer rc=7 blocks instead of retrying. Zero tickets and zero projects set it. |
| `touches` | an **after-the-fact audit on the committed diff**, not a write barrier. 35 tickets set it, none of them open. |
| `depth` / `fanout` on an agent | **no executor reads either.** Parsed, echoed to the API, dropped. Already removed from the UI; only the registry parse remains. |
| Settings, "Start all" | **cannot start a runner.** `POST /queue/runner/start` Popens the watchdog with no directory; the watchdog refuses an empty dir (ADR 0014) and exits 2. It DOES clear the global brake, which is the half that works. |
| `assignee` | `"operator"` on all 93 platform tickets. `--assignee agent:<slug>` would select nothing. |
| `broker` role | in the enum, no handler, no agent declares it. |

And one honest note that the governance addendum already makes in writing:
scope, `touches`, latitude and grants are **coordination, not confinement**.
The autonomous worker runs as the same uid as the interactive session with the
docker group, so it can rewrite the very files that describe its own limits.
Nothing in this plan should be presented in the UI as security.

### The reviewer gate checks completion, not correctness

`scripts/reviewer.sh` makes **zero model calls**. It requires: a non-empty
diff, no new stub markers, python typecheck on changed `.py`, `tsc --noEmit` on
changed `.ts/.tsx`, and any executed acceptance criteria passing. A second gate
in the coordinator requires a real commit.

This is a good gate and it is worth keeping exactly as it is. It answers "did
something compile and land", not "is this the work the ticket asked for". Every
proposal below that adds judgement adds it somewhere else, never inside this
script.

---

## 2. What a ticket page is FOR, and the operations that belong on it

A ticket page is where the operator decides three things: is this the right
work, is it ready to be done by something that cannot ask them, and what
happened when it ran. Every control on the page should serve one of those.

**The operations, and where each one lives.**

| operation | surface | commit rule |
|---|---|---|
| change status | menu on the status word in the header | **on change.** Where the work has got to is not config; it is the same move as dragging a card between columns. Behind Edit mode it cost three interactions. |
| approve a draft | primary button, only while `draft` | on click. A button press is an approval. |
| run it | primary button, only while `todo` | on click, no confirm |
| answer a block | one field and one button in the Stuck section | on click, and it is written into the BODY, because a worker sees only title + body |
| edit title / body / priority / effort / depends-on | Edit mode, one Save | on Save. This is a spec a worker will execute. |
| comment | composer at the foot of Comments | on click. Deliberately NOT part of the spec (`tasks.py:1897`). |
| refresh, delete | overflow menu | delete behind `ConfirmDialog` |

**What is deliberately absent:** a Deploy button (that is `/deploys`), an
assignee picker (one value in the entire store), a latitude editor (0% set, and
the field is advisory), an attempt-cap editor (0% set), and a readiness nag
panel with checkboxes.

Note what that last one does NOT justify. An earlier draft excused the missing
readiness line by saying "approve already reports the gaps". Approve only fires
on a `draft`, and **1 ticket in 179 is a draft**: 38 are `todo`, filed
directly. The approve path reports gaps for about 3% of tickets, which is an
argument FOR the line in the next list, not against it.

**What is missing and should exist**, in the order the loop needs it:

1. **A readiness line, not a checklist.** The six checks already run on every
   ticket read. The page shows the count only at approval. One quiet line
   ("does not say what done looks like") next to the Run button turns a 74%
   failure rate into something the operator can fix in ten seconds.
   The six checks are, near enough, the rubric GitHub publishes for handing an
   issue to its coding agent: a clear problem statement, complete acceptance
   criteria, and directions about which files to change. Keep them; they are
   the right list, and they are already implemented.
2. **Re-run.** A failed run leaves the ticket `todo` with no affordance except
   Run, which reads as "start", not "try again".
3. **A scope-review action** that calls the surviving engine and proposes
   sub-tickets. See §4.
4. **A link to the chat that filed it, and to every run's chat.** The first
   exists; the second is newly recorded.

---

## 3. Autonomy, boiled down to three dials

Six autonomy-adjacent controls are listed in §1 and none of the six does what
its label implies. A control that does nothing is worse than a missing one,
because the operator reasons with it. The proposal is to keep three real dials,
delete or relabel the rest, and make each dial answer a different question.

| dial | question it answers | where it lives | today |
|---|---|---|---|
| **1. Is unattended work permitted at all?** | the brake | one global switch, `.runtime/autonomy-override.json` | **the file and its semantics are shipped and correct** (missing = clear, corrupt = engaged, fail closed). Its CONTROL is not: see below. |
| **2. What may run unattended HERE?** | per scope | `autonomy.mode` on a project / scope | exists on one project, `manual`. Needs to be a real per-scope setting with three values. |
| **3. What happens when a run finishes?** | per agent | the `autonomy` enum: `reactive` / `supervised` / `autonomous` | shipped in the registry, read by nothing, because nothing dispatches. |

The three compose as one sentence the operator can read:

> Unattended work is **on**; in **development** scope, tickets marked ready may run
> **supervised**; results land in **review** before they commit.

`development`, not `workshop`, and the difference is a ratified operator
decision rather than a preference: "`workshop` is not a build target... No
builder sweep is dispatched against it; it has no code tree"
(`dev-mode-and-development-scope-2026-08-09.md:211`). An earlier draft of this
sentence used `workshop` and would have pointed the first supervised sweep at
the one scope that cannot be built.

**The brake needs its own control, because today it does not have one.**
Settings has "Start all" and "Stop all", and those buttons are about the queue
RUNNER, but `POST /queue/runner/start` also calls `autonomy_override.clear()`
and `/stop` calls `autonomy_override.engage()`. So a per-runner button silently
moves the global brake, and the runner half of it cannot work at all. Splitting
them is part of dial 1: a switch that says what it does, and a runner control
that starts a runner.

Rules that fall out, and that the current design already decided:

- **The global brake is an overlay, never a mutation** (ADR 0008). Clearing it
  restores every agent's own setting because nothing was changed. No restore
  log, no restore API. Keep this; it is the one piece of the autonomy model
  that is both shipped and load bearing.
- **Autonomy has no cascade.** A ticket does not override its agent's autonomy.
  Latitude cascades; autonomy does not. Two different questions.
- **Manual runs are never blocked by the brake.** "Run now" is the operator
  present and asking.

**What to delete rather than fix:** `depth` and `fanout` from the agent surface
(no reader), the queue-runner Start button in its current form (it cannot
start anything), and any UI text that describes `touches`, `latitude` or grants
as a boundary. Latitude stays as a field and as prompt text, honestly labelled:
it changes what the worker DOES when the ticket is silent, and it is not a
permission.

---

## 4. The roles, and the one that is missing

The role enum is settled and correct: **maker, reviewer, triager, keeper,
broker**, closed in code, free-form in the body. The definition that makes it
work is `primitive-roles-2026-07-18.md:18`, "a role is the set of writes an
agent is permitted to make". Everything else about an agent (what it reads,
when it wakes, how it judges) is a field, not a new role. That is why planner,
watcher, researcher, scribe, critic and conductor were each rejected with a
reason, and none of those rejections should be reopened.

Two structural rules from the same design that this plan keeps:

- **No agent addresses another agent.** Coordination is through shared state:
  a ticket store and a knowledge store both sides read and write. No inbox, no
  handoff protocol, no message bus.
- **The conductor is not an agent.** The design describes it as ~20 lines of
  deterministic Python; what shipped is `dispatch_triggers.sh`, 713 lines of
  bash. The principle held (it is code, not a model); the size claim did not,
  and anyone planning against "twenty lines" should know that. What matters is
  the invariant: no LLM runs to decide whether an LLM should run.

**The stock set for this installation, and what each is for:**

| role | instance | trigger | writes | status |
|---|---|---|---|---|
| maker | the sweep worker | claimed from the queue | tree, commits, its own ticket's transitions | runs today, as a shell loop with no agent identity |
| reviewer | the completion gate | after every maker run | a verdict + rejection reasons | runs today as `reviewer.sh`, deterministic, no identity |
| triager | note and inbox router | cron, daily | tickets, and one decision record per note | shipped (`triager.py`, `triager_run.sh`), never fired |
| keeper | memory-keeper | cron, 6h | knowledge notes, learnings, skill proposals | registered, never fired |
| broker | on demand | never dispatched | nothing | enum only |

**The missing one is not a new role. It is a triager pass over the QUEUE
rather than over notes.** Scope review is a triager decision: given a ticket,
emit exactly one of `ok` / `split` / `underspecified` / `none`, where `split`
files children and `underspecified` sets `awaiting` with a specific question.
That is the same closed four-outcome set the note triager already implements,
pointed at a different input, and the LLM engine for it still exists in
`ticket_scope_review.py`.

This is the answer to "do we need a scoping agent". **No new role, no new
authority, one new trigger on an existing one.** It also respects the standing
operator rule that scoping and decomposition happen in the main chat thread
before a run is spawned: the queue triager is the *unattended* fallback for
tickets that were filed without that conversation, and its output is a question
back to the operator, not a plan it executes.

**The outside evidence says the same thing, and it is worth stating because it
is the one place a "planner agent" would have been the obvious move.**
"From Plan to Action" (2026, SWE-bench Verified + Pro, four models) measured
that removing the plan lowers success for every model, that agents already
follow an internalised plan without being told, and that **a bad or misaligned
plan is worse than no plan at all**. The value is in plan QUALITY, not in a
planner actor. Anthropic's own subagent guidance splits work by read-vs-write
and says planning, implementation and testing share too much context to
separate. The one measured win for a central planner (CAID, +25.6% on
PaperBench) credits **git worktrees and executable tests**, not the planner's
judgement.

So: the plan belongs INSIDE the maker's prompt as a required phase, re-asserted
periodically (the same paper measured that re-injecting the plan every ~5 steps
improves both compliance and success). The triager's scope pass exists to catch
tickets that cannot support a plan at all, and to ask, not to plan for them.

**And the reviewer stays deterministic and external. Do not add an LLM gate.**

This is the recommendation I changed while writing, so the reasoning is worth
keeping. The obvious next move is "add a reviewer agent that judges whether the
work is right". The evidence says do not, at least not as a gate:

- **Self-critique without an external signal makes things worse.** Blocksworld,
  GPT-4 as its own verifier: 40% alone, **55%** with self-critique, **88%** with
  a sound external verifier. And the self-verifier produced 38 false positives
  out of 45 negatives; it approves broken plans.
- **A stronger generator makes errors that are harder to verify.** Measured
  true-negative rate fell 0.68 to 0.17 as the generator got stronger. A judge
  helps most where you need it least.
- **A single LLM verdict is too noisy to gate on.** Pairwise preferences flip
  13.6% of the time on average; ~11 repeated trials are needed for a majority
  to be stable. Gating a queue on one coin-flippy verdict is worse than gating
  on nothing.
- **A judgement pass is usually the biggest line item.** Code review was 59.4%
  of ChatDev's total tokens. A pre-registered experiment where the authors
  expected their multi-agent reviewer to win found a **single frontier-model
  pass beat it, at roughly one thirtieth of the tokens**.

`reviewer.sh` is already the good shape: it IS the external verifier, because
typecheck, tests and a real diff are ground truth, not opinion. Keep it,
deterministic, and keep it unable to approve.

Where a judging model DOES earn its place, on the same evidence:

- **As a reviewer with a CLEAN context, advisory, not gating.** Cognition runs
  exactly this in production (Devin Review, no shared context with the writer,
  ~2 bugs per PR, ~58% severe) and the mechanism is that a fresh context is not
  biased by the specification the writer was working from. Anthropic's shipped
  review is the same shape: independent finders, then a separate verification
  pass to kill false positives, and it still will not approve.
- **As a critic that SHORTENS work rather than appending to it.** The one
  measured case where a critic was both more accurate and cheaper (25.2% vs
  20.8%, $0.04 vs $0.11) worked by cutting trajectories short, not by adding a
  pass at the end.

So: advisory findings on the run page, never a gate, and only above a diff-size
floor.

### Centralized or decentralized

Both, and the split is already decided: **execution is single-threaded and
supervised, coordination is stigmergic.** One writer per tree at a time (the
flock), one deterministic dispatcher, no peer negotiation. Agents "see" each
other only through the ticket store and the knowledge store. The reasoning is
recorded across several docs and it is the same in each: parallel writers make
conflicting implicit decisions, every inter-agent message is lossy compression,
and at ten agents a peer mesh has 45 edges nobody can test.

**The outside evidence is now much stronger than when that was written, and it
converges from three directions.** The read/write asymmetry was reached
independently by Cognition, LangChain and Anthropic: parallelise reads freely,
serialise writes. Two production systems then found the failure mode directly.
Cursor, running hundreds of concurrent agents on a million-line codebase, tried
a shared coordination file with locks and measured **twenty agents dropping to
the effective throughput of two or three**; what worked was workers on
independent repo copies with no real-time coordination. Anthropic's 16-agent C
compiler used a shared git repo with lock files successfully for two weeks, and
then hit the same wall on the Linux kernel, where "every agent would hit the
same bug, fix that bug, and then overwrite each other's changes."

Two consequences for this system:

1. **Concurrency, whenever it comes, arrives as worktree isolation, not as a
   smarter lock.** That work is already ticketed here (C3: worktree mode is
   built and correct, but its success path publishes a PR, so it cannot be used
   on a tree with no remote). Fixing the local-merge path is the prerequisite
   for every form of parallel execution.
2. **Until then, one writer, and that is not a limitation to apologise for.**
   Six compute-matched replications in 2026 found most multi-agent
   architectures losing to a single agent at equal token budget.

A user adding their own agent therefore adds: a slug, a persona body, a role
from the closed five, a trigger, and an autonomy mode. Nothing else, and no new
wiring, because the shell does not know what a role does.

---

## 5. Shared context: what chats and agents know about each other

This is not a side feature. In the architecture already chosen, **shared state
IS the coordination mechanism.** No agent addresses another agent; they
coordinate by reading and writing the same stores. So the quality of the shared
knowledge layer is the ceiling on how well anything coordinates, and a note
nobody can find is a note that was never written.

### Measured first, because the state here is worse than it looks

Eleven durable stores exist. The headline numbers, taken from the live tree:

| store | live volume | who can read it |
|---|---|---|
| knowledge notes | **21 notes** (18 `workshop`, 1 `development`, 1 `role:reviewer`, 1 `project:commute`) | every chat gets TITLES only; bodies need a search call |
| learnings | 25 records (14 `global`, **11 `workshop`**) | `global` only |
| chat transcripts | 69 threads, 1270 messages | own thread; others only via search, general chat only |
| tickets | 193 across 26 stores | 6 lines of 90 characters, in a digest |
| docs/ | 151 files | search only, general chat only |
| semantic index | **3417 chunks** (doc 2601, chat 762, note 54) | one tool, one caller |
| Obsidian vault | **965 files** | **nothing. Excluded from the index entirely.** |

**Three findings that make the rest of this section urgent.**

**1. The "capture a preference" path was a no-op. Fixed 2026-08-12, mid-plan.**
The description below is what the audit found; the code now reads
`if scope != learnings.GLOBAL` and the block renders, labelled OPERATOR
PREFERENCES. It is left here in full because the failure shape is the useful
part, and because §8's "if only three things" list is written against it.

When the operator states a
durable preference in a general chat, the model emits a learning-note fence,
`apply_learning_note` defaults its scope to `workshop`, the chat replies
"Captured a preference", and then `chat_context.py:1055` reads
`if scope not in (learnings.GLOBAL, "workshop")` before injecting a scope
block. A general chat's knowledge scope IS `workshop`, so the block is skipped;
only `global` is injected. The worker never asks for that scope either: it only
ever passes `project:*` or `agent:*`.

**Eleven records, written between 2026-07-18 and an hour before this audit, had
never been read by anything.** The store stamps `last_retrieved` and
`retrieval_count` on retrieval, and all eleven were unstamped.

One correction, because it matters for anyone re-checking this: **verifying it
changed it.** Calling `learnings.memory_block("workshop")` once to confirm the
block was non-empty stamped ten of the eleven with `retrieval_count: 1` and
`last_retrieved: 2026-08-13T04:08:49Z`. That timestamp is this audit, not the
system. The eleventh is unstamped because it predates the field.

This is not a gap in coverage. It is a mechanism that reports success and does
nothing, which is worse than having no mechanism, because the operator stops
repeating himself.

**2. The one rule that matters most is silently over budget.** The injected
`global` learnings block is capped at 700 tokens. Rendering it right now gives
2,483 characters ending in the literal line `- +1 more`, and the entry that
line stands for is the Design System rule, at 168 tokens, 24% of the budget.
It has been excluded for roughly 25 days, and the exclusion is
self-reinforcing: being dropped means it is never retrieved, and the retrieval
score is part of what decides whether it is dropped.

That rule is the one every UI change in this repo is supposed to obey, and it
is the one the operator has had to restate by hand most often.

**3. Only ONE surface in the system can search the workshop's own writing.**
`mcp__workshop__search` is in the general chat's allowlist and nowhere else. A
project or build chat cannot call it. A registry agent cannot. An @-mentioned
agent gets `Read/Grep/Glob` and no MCP at all. A ralph worker receives the
ticket text and a learnings block, plus whatever the CLI loads for the tree it
is `cd`'d into, which is the `CLAUDE.md` hierarchy: no knowledge notes, no
docs, no chat, no prior run, and no way to look any of them up. **The delegate
is the one that needs the context, and the delegate is the one denied the
tool.**

The `CLAUDE.md` correction matters for finding 2 below: the Design System rule
dropped from the 700-token block is separately loaded from `frontend/CLAUDE.md`
for any worker in this tree, so the worker was not blind to it. The CHAT was,
and so was any worker in a tree without that file.

Three more, briefly, because they are cheap to fix and currently mislead:

- **Nothing rebuilds the index.** It was built once, by hand, on 2026-08-12.
  75 chat files and 9 notes have been written since and are unfindable.
- **The indexer only walks `shared/knowledge`.** Notes under `projects/*`,
  `apps/*`, `.runtime/agents/*` and `roles/*` are never indexed. Measurably: 21
  of 21 indexed note refs come from one directory.
- **A 25-day-old snapshot presents as current.** `consolidated-memory.md` is in
  every prompt's knowledge block and was last written 2026-07-18, when the
  keeper's cron line was commented out.

### The rule that decides every question below

**Push a little, pull the rest.** Everything injected into a turn is paid for
on every turn, invalidates the prompt cache when it changes, and competes with
the actual task for attention. Everything retrievable is paid for only when it
is wanted. So:

- **Push**: the operator's standing preferences, the scope's own rules, and a
  short digest of what is live in this scope. Small, ranked, capped.
- **Pull**: everything else, through a tool, on demand, with the query the
  model actually has rather than the guess the injector made.

This is already a lesson learned here rather than a theory: the ambient
cross-chat digest had to be moved out of the cache-stable prefix and capped,
because it changed on every task state change and so invalidated every chat's
cache while injecting topically-related distractors.

The push side is also where the cheapest measured win in this whole area sits.
Anthropic reported that letting an agent DISCOVER tool definitions by listing a
directory, instead of loading them all up front, took a context from 150,000
tokens to 2,000: **a 98.7% reduction** ([code execution with MCP][mcp-exec]; a
demonstration rather than a benchmark). The same shape applies to knowledge.

[mcp-exec]: https://www.anthropic.com/engineering/code-execution-with-mcp
An index of titles is a directory listing; the bodies are the files.

### What has to exist for pull to work

In the order that unblocks the most, given the measurements above.

1. **Give the tool to everyone who needs it.** `mcp__workshop__search` exists,
   works, and is reachable from exactly one surface. Put it in the worker's
   context, the triager's, and every agent's. This is the highest-value item in
   this section and it is mostly allowlist plumbing.
2. **Fix the preference path so what it captures is actually read.** Either
   inject `workshop`-scope learnings, or write operator preferences to a scope
   that is injected. Eleven captured corrections currently go nowhere. Until
   this is fixed, every "Captured a preference" reply is a false receipt.
3. **Rebuild the index automatically**, on write or on a timer, and **walk
   every knowledge directory**, not just `shared/knowledge`. Both are small
   changes to code that already exists and works.
4. **One scope vocabulary.** There are three key spaces, and two of them use
   the same words for different places: for TICKETS, `workshop` means
   `shared/queue` (14 tickets) while `development` means the platform repo (93
   tickets); for NOTES, `workshop` means `shared/knowledge` and `development`
   is a subdirectory of it. Two top-level scopes that read as one thing to a
   human, and resolve to four different directories. Pick the chat-scope
   vocabulary, make the others projections of it, delete the translators.
5. **Budget the injected block by IMPORTANCE, not by recency-weighted
   retrieval count.** The current ranking silently drops the most important
   standing rule and then keeps dropping it. A rule the operator stated should
   not have to compete with mechanical worker noise for the same 700 tokens.
6. **Provenance and staleness on every hit.** Where did this come from, when,
   and is it still true? A 25-day-old consolidation currently presents as
   current fact in every prompt, and this repo has already been bitten by
   memories naming files that no longer exist.

### What is shared, and what is deliberately not

| | shared in a scope | why |
|---|---|---|
| tickets and their state | yes | it is the blackboard |
| run receipts and verdicts | yes | what was tried, and what happened |
| scoped notes | yes | the durable facts of the place |
| decisions (ADRs) | yes, workshop-wide | they bind everything |
| operator preferences | yes, everywhere | they are about the operator, not the place |
| a chat's raw transcript | **no** | it is long, it is mostly process, and its conclusions belong in a note. Chats share CONCLUSIONS, not context windows. |
| another agent's working memory | **no** | that is agent-to-agent messaging wearing a hat |

The last two rows are the load-bearing restraint. "Let every chat see every
other chat" sounds like sharing and is actually the lossy-compression failure
the research warns about: an inter-agent message is a compressed, decontextualised
summary, and piping raw transcripts around multiplies that. **The interface
between chats is the note and the ticket, both of which are written
deliberately, and both of which the operator can read.**

### The one new mechanism worth building

**A scope digest that is derived, capped and cheap**: for a given scope, the
open tickets, the runs in flight, the last N decisions, and the notes changed
since the reader last looked. Computed server-side, injected small, and
expandable through the search tool. That is what makes a second chat in the
same scope useful instead of merely allowed, and it is roughly the same
computation the inbox already needs.

Half of it already exists and is well judged: a resumed turn gets a one-line
**pointer** when another chat or agent changed the scope's notes since that
turn last ran, rather than the content itself. That is the right instinct
(point, do not paste) and the digest should extend it rather than replace it.

### What to delete while doing this

The audit found more dead knowledge machinery than live: eleven `letta_tools`
with zero callers, `memory_tree` with two routes and no client, two
`/knowledge/*` routes with no callers, `promotion_candidates` and
`mark_promoted` with no callers, `merge_duplicates` and `prune_stale` that have
never run, and a `memory_promotion` clusterer that **cannot ever fire** because
it matches notes by id and every id carries a unique millisecond suffix. Also a
persona that tells the model it has filesystem tools the allowlist does not
grant it.

None of that is a design problem. It is the cost of having designed more than
was wired, and clearing it is what makes the remaining half legible.

---

## 6. The sequence

Ordered so that each step is observable before the next one is trusted. The
rule for the whole sequence: **nothing runs unattended until the thing before
it has run attended, and been watched.**

### Phase 0: stop lying (a day of edits)

No new capability. Every item here removes a control that does nothing or a
label that is false.

- Delete `depth` / `fanout` from the registry parse. They are already gone from
  the UI; only the parse still echoes them to the API.
- Split "Start all" into two honest controls: the global brake, and a runner
  control that can actually start a runner. Today one button does both, and the
  runner half cannot work.
- Relabel `latitude` in the UI as what it is: what the worker decides when the
  ticket is silent. Remove any framing of `touches` / grants as a boundary.
- Leave `ticket_scope_review.py` alone until phase 3, which is where its fate
  is actually decided. Deleting it in phase 0 would only mean rewriting it in
  phase 3.

### Phase 1: make readiness visible where it is decided (a day of edits, a week of measuring)

- Show the readiness line on the ticket page and in the chat's file-a-ticket
  card, at the moment of filing, not only at approval.
- Make the ticket-maker fill `acceptance` and `boundaries`, the two checks 74%
  and 68% of open tickets fail. This is a prompt change in the distiller plus
  the four-field body format that already exists.
- Target: the pass rate goes from 26% toward 80% on newly filed tickets. Do not
  gate on it. Readiness reports; it does not veto, and that is a ratified
  decision, not a phase-ordering convenience: the operator is the authority and
  what the system owes them is the list.
- **Fix the preference path** (§5). Eleven captured operator corrections are
  currently written to a scope nothing reads, under a reply that says they were
  captured. This belongs in the same phase as "stop lying" and only missed it
  because it is a data-flow bug rather than a UI one.
- **Give the search tool to the worker and the triager** (§5). One allowlist
  change, and it is what lets everything downstream reason from the same
  picture as the chat.
- **One scope vocabulary** (§5). Everything after this compounds on it: the
  triager's decisions, the digest, the search tool and the inbox all key on
  scope, and three key spaces means three of everything.

### Phase 2: K watched runs, and a number (days, not a day)

- Run real tickets with `POST /run`, one at a time, watching the whole path:
  claim, worker, reviewer, commit, receipt, status.
- Confirm the run's links are written (`task_id`, `chat_id`), that the ticket
  page shows the run, and that the run page links back to the chat.
- **Record the completion rate.** One run is not evidence: this plan's own
  evidence table puts verified session success at 28-33% for experts, so a
  single success sits inside the noise, and the operator's standing rule is
  "until the automated ticket system completes several tickets end-to-end
  reliably, default to manual work". K is small (five is enough to tell 20%
  from 80%), and the number it produces is what phase 4 gates on, instead of
  "nothing was surprising".

### Phase 3: the queue triager, attended (days, and a week of reading it)

- Add the queue trigger to the triager: for each `todo` ticket not yet
  reviewed, emit one decision from a closed set.
- **This is more than "one new trigger", and an earlier draft of this plan
  undersold it.** The note triager's decisions are `task | link | escalate |
  none`; the surviving scope-review engine's verdicts are `ok | split |
  underspecified`. They share no members, so this is a new decision vocabulary
  and a new write (child tickets), not a re-pointing.
  It also weakens a ratified safety argument: the note triager "cannot write
  notes, so it cannot change its own trigger input", called out in
  `primitive-roles-2026-07-18.md:310` as "a structural guarantee, not a prompt
  instruction". A queue triager writes tickets and is triggered by tickets, so
  that guarantee does not transfer. **Termination has to come from a mark**: a
  ticket the triager has judged carries the judgement, and is not re-judged
  until it changes. The caps below are a backstop, not the argument.
- `underspecified` sets `awaiting` with ONE specific question and surfaces it in
  the inbox and in the originating chat. `split` files children as drafts,
  which the operator approves. Depth cap 2, and **a cap of 5 per pass on
  `underspecified` as well as on `split`**: 25 of the 34 open tickets fail the
  acceptance check, so an uncapped first pass produces roughly 25 newly blocked
  tickets and 25 questions in one morning, which is not a review, it is a wall.
- Decide the fate of `ticket_scope_review.py` HERE, not in phase 0. Rewiring or
  deleting it is the same decision as building this, and phase 0 deleting it
  would only mean phase 3 rewriting it.
- Run it manually first. Read every decision it makes for a week before giving
  it a cron trigger.
- **Rebuild the index automatically and index every knowledge directory**
  (§5). The triager is the first consumer that genuinely depends on retrieval:
  deciding whether a ticket duplicates another, or is already answered by a
  note, is a search question, and today it would be searching a hand-built
  index that covers one directory and is hours stale.

**Expire a stop by its `kind`. Do NOT add states.** A question and a permission
request must expire differently, and the shape to express that already exists:

| `awaiting.kind` | on no answer |
|---|---|
| `input`, `decision`, `missing-info` | may time out, let the run proceed on its own judgement, and re-ask later |
| `permission` | **never auto-resolves.** Ever. |

The asymmetry is Claude Code's, and it is the most defensible rule anyone has
shipped: questions time out, approvals do not.

**This corrects an earlier draft of this plan**, which proposed splitting
`awaiting` into `awaiting_input` and `awaiting_approval` states. That was wrong
three ways, and the review that caught it is the reason this section is short:

1. `awaiting` is **not a status**. `tasks.py` `STATUSES` has no such member; it
   is a structured field on a `blocked` ticket.
2. The one-primitive decision is recorded in code (`tasks.py:57-89`): every
   stop reason is "the SAME primitive, `awaiting`, widened in place rather than
   given a sibling status or a parallel record", with `kind` deliberately an
   OPEN slug set because "a closed set fails on the first unanticipated reason
   to stop". Splitting it reverses that, under a masthead saying the `awaiting`
   shape is already decided.
3. **`awaiting_input` is already a live Job status** (`jobs.py:52`), with three
   records carrying it, routes, a frontend filter and a status-kind map. Reusing
   the token for a ticket state collides across the exact Ticket/Run seam that
   ADR 0009 exists to keep straight.

Expiry keyed on `kind` needs no schema change, no migration and no new state,
and the auto-resolve ladder it plugs into was already ratified in
`latitude-model-2026-07-18.md:158-167`, including "hard gates never auto-resolve
at any latitude".

Two shapes worth copying, both from Linear's agent protocol, which is the
best-specified escalation contract in a shipped product:

- **The blocked state is DERIVED from what the agent emitted, never set by
  hand.** An agent emits an elicitation; the session becomes `awaitingInput`
  because of that, not because something remembered to write a status.
- **The canonical history is frozen activity records, not editable comments.**
  This repo already reached the same conclusion from the other direction: a
  comment is deliberately not part of the spec a worker executes, and an answer
  only counts when it is written into the ticket body. Same rule, and it should
  be stated once rather than twice.

### Phase 4: supervised unattended execution (a week at least, and the runner is the operator's call)

- Bring back the queue runner, one scope, `supervised`: runs land in
  `in_review` rather than committing to `done`.

  **This is the operator's call, not an edit.** The line in
  `infra/cron/workshop.crontab:72` is commented out, it is missing the `--dir`
  argument it now requires (so uncommenting it hits the same refusal as the
  Start button), and the file carries an explicit instruction above it: "THIS
  COMMENT STATE IS THE OPERATOR'S DECISION, NOT AN ACCIDENT OF EDITING... Do
  not uncomment them to 'restore' anything." Fix the missing `--dir`; leave the
  comment for him.
- **The inbox producer already exists.** An earlier draft of this plan
  scheduled building it and gave a false reason. `inbox.sweep_finished_runs`
  raises `kind="notify"` for every terminal job whose trigger is autonomous,
  it runs on every inbox read, and its watermark advanced today. The inbox
  holds 9 items, 2 of them `notify`, one titled "Run finished". It finds little
  because no autonomous-trigger run happens, which this phase changes by
  itself. Nothing to build.
- Add the stale-question escalation the docs name as unbuilt: a question that
  has sat unanswered for a day gets louder, rather than sitting silently.
- Add stuck detection, because an unattended run that loops is the failure this
  phase invents. The published rule set worth copying is OpenHands': compare
  actions and observations by **semantic** equality (strip ids, timestamps,
  metrics, or nothing ever matches), anchor the window at the last human
  message, and fire on same-action-same-observation ×4, same-action-same-error
  ×3, or an alternating A,B,A,B pattern ×6. Two additions from
  `opencode-anti-loop`: count **steps without a write**, and reset the counters
  on a real file write. Advisory first, block second.
  **Detection without a resume path is worse than none**. The documented
  failure of every implementation of this is killing an agent that was
  legitimately polling something slow. A stuck run must end `parked` with a
  reason and a resumable id, not `failed`.

### Phase 5: autonomous, per scope (when phase 4 is boring)

- Flip one scope from `supervised` to `autonomous`. The reviewer gate and the
  commit gate stay exactly as they are.
- The prerequisites the earlier design named and this plan keeps: a
  `supersedes` edge (nothing today expresses "this replaces that", so a stale
  ticket can be executed against a tree that moved on), a distinction between
  executable tickets and judgement tickets (today `role` types the agent, not
  the ticket, so a design ticket looks executable), and a dry run
  (`--plan`) so a sweep can say what it would do without doing it.

**One earlier instruction, quoted properly, because an earlier draft of this
plan misquoted it in its own favour.** `continuation-2026-07-31.md:306` says
"Do not skip to 5." Its list is five ITEMS, not phases, and item 5 is "Run one
ticket end to end, watched. Then a few. Then enable." That is **this plan's
phase 2**, not phase 5. So the original instruction means: do not run a ticket
end to end before items 2, 3 and 4, which are the `--plan` dry run, typing
tickets executable-versus-judgement, and **enforcing `touches`**.

This plan does not honour that ordering, and says so rather than quietly
reordering it:

- The dry run and ticket typing move to phase 5, three phases after the watched
  run. The reason is that phase 2 is one ticket the operator is watching, which
  is the cheapest way to learn what those two features actually need to do.
- **`touches` enforcement is dropped entirely**, which is a real reversal of a
  stated precondition. The justification is not that it stopped mattering: it is
  that `touches` never was a boundary. It runs on the committed diff after the
  run is over, and that was recorded in
  `agent-governance-addendum-2026-08-04.md:47-52` after the precondition was
  written. Confinement comes from execution identity or a sandbox, not from a
  post-hoc audit, which is why it sits under phase 5's precondition instead.
- `continuation:321` also says "Do not enable background autonomy as part of
  any of the above". Phases 0 to 3 comply. Phase 4 is exactly that step, and it
  is the operator's decision, not this plan's.

### The precondition nobody has met yet

Phases 0 to 4 are all attended or supervised, and they are safe in the sense
that a human sees every result before it counts. **Phase 5 is the first one
where the blast radius matters, and the execution boundary is currently open.**

An autonomous turn runs `claude -p` as the same Linux user as the interactive
session, in the docker group, with no sandbox and full read/write over the
tree. That is SEC-A1, open. It can rewrite `.tasks.json`, `agents/*.md` and its
own guardrail files, which is SEC-A2, also open. The industry answer to
exactly this is unanimous and it is not more prompting: **contain at the
environment layer first, then steer at the model layer**, at which point you
can relax the oversight. Sandboxing is measured to cut permission prompts by
84% ([Claude Code sandboxing][cc-sandbox])
precisely because the prompt stops being the safety mechanism.

So phase 5 has one hard prerequisite that is not in this document's scope:
either a lower-privilege execution identity for the loop, or a sandbox. Until
one exists, "autonomous" should mean **supervised with a shorter leash**, not
unattended commits.

One control that DOES exist and deserves the credit: `ralph.sh` builds
`--disallowedTools` from `agent_authority denied --unattended` and applies it
unconditionally to every worker turn. It is a real deny list on irreversible
actions, it is the only confinement in the loop today, and it is not what
`touches`, `latitude` or grants pretend to be.

[cc-sandbox]: https://www.anthropic.com/engineering/claude-code-sandboxing

---

## 7. Visibility: what the operator has to be able to see

Autonomy is only acceptable if the answer to "what is it doing, and what did it
just do" is one glance. Three surfaces, each answering one question, and all
three exist in some form today.

| question | surface | state |
|---|---|---|
| what is happening right now | the run in flight, on the ticket and in the chat that launched it | shipped; the chat link was just made real |
| what happened while I was away | the inbox | shipped, with a working `notify` producer for unattended runs; nearly empty because no unattended run happens |
| what did it cost | usage, per scope and per chat | ledger shipped; the by-chat view is an open ticket |

The rule that keeps this honest: **a run that ends without a human watching
must leave a receipt someone will see.** That machinery is built and correct;
what it lacks is unattended runs to report on.

One more, from the audit rather than the design: **status must never be
inferred in two places.** A ticket's state, a run's state and the inbox item
that points at both are one fact each, computed once server-side. The
`state_type` projection added on 2026-08-12 is the right shape: derived in the
route layer, never persisted, because the alternative is a second source of
truth in `.tasks.json`.

**The shape the field converged on for a fleet view**, and the one to build when
there is more than one run at a time: a roster row per run, carrying a status
glyph, a **one-line summary of what it is doing right now generated by a cheap
model**, its age, and a link to the artifact. Grouped by state with **needs
input first**. Peek without attaching; answer a blocked run from the peek.
Notify on "needs input", not on progress.

Two cautions that belong in the same section, because they are measured and
they cut against building more of this:

- **A trace is not a live view.** A span is not exported until it ends, so a
  45-minute turn emits nothing until it is over, and children arrive before
  parents. Whatever answers "what is it doing now" has to be a separate live
  channel, not the trace.
- **Richer progress UI has not been shown to improve oversight, and has
  coincided with less of it.** Humans intervened in 52% of agent-authored PRs
  versus 84% of human-authored ones (MSR 2026); experienced agent users move
  from per-action approval to monitor-and-pounce; and one controlled trial of
  16 experienced developers on mature repos measured them **19% slower** with
  AI while believing they were 20% faster. Build the roster because it makes a
  stuck run findable, not because it will make anyone watch more carefully.

---

## 8. Critique of this plan

Written after the plan, deliberately, and kept rather than fixed silently.

**It is bigger than it reads.** Six phases plus six items in §5 is weeks, not
the "a day" some phases claim. Those estimates are for the EDIT, and they omit
the watching, which is the part that cannot be compressed: phase 3 says "read
every decision it makes for a week", and that is the real cost.

**If only three things get done, do these**, in order. Each is small, each is
independently useful, and each is currently a mechanism that lies:

1. ~~**Fix the preference path.**~~ **Done 2026-08-12.** The `workshop` block
   is injected now, labelled `OPERATOR PREFERENCES` rather than `AGENT MEMORY`,
   because what it holds is the operator's own corrections and the label is
   what tells the model how to weigh it against mechanical worker noise. Pinned
   by a test that fails against the old condition.
2. **Un-drop the Design System rule** from the injected block, and stop
   ranking standing operator rules against mechanical worker noise for the
   same 700 tokens.
3. **Give `mcp__workshop__search` to the worker and to agents.** One allowlist
   change, and it ends the state where the only surface that can search the
   workshop's own writing is the chat, not the thing doing the work.

None of those three turn autonomy on. That is the point.

**The goal itself deserves a challenge.** This plan assumes autonomous ticket
execution is worth reaching. The evidence is more equivocal than the ambition:
a controlled trial measured experienced developers 19% SLOWER on mature repos
while believing they were faster; verified session success sits around 30% even
for experts; and the single largest measured improvement anywhere is a human
answering a question on 10% of tasks. A defensible alternative reading is:
**make the attended path excellent and keep autonomy narrow**, restricted to
tickets that are small, well specified and machine-verifiable. That is FEWER
than the 9 that pass readiness: readiness is not a quality judgement and says
so in its own docstring, and at least three of those 9 are `deep` judgement
work (an audit, a sync-semantics definition, a "revisit these deferred items"),
which is exactly the class phase 5 says there is no field to distinguish yet.
This plan is compatible with that reading, and phases 0 to 3 are worth doing
under either.

**Three things it does not answer.**

- **The 34 open tickets.** Some are from July, some are design work that is not
  executable at all, and nothing today expresses "this ticket replaced that
  one". Turning a runner loose on that backlog would work stale tickets against
  a tree that has moved. Triage before dispatch, and the queue triager's first
  pass is exactly that job.
- **Cost.** A supervised loop working several tickets a day is real money at
  frontier rates, and the reviewer plus distiller spend is the part nobody
  meters. The ledger exists; a per-scope budget should be set before phase 4,
  not after the first surprising week.
- **Isolation.** Named in phase 5 as a hard prerequisite, and out of scope
  here. It is the one item on this list that cannot be solved by better design
  in this document.

---

## 9. Evidence

Measured findings this plan leans on, so a later reader can check the ground
rather than the assertion. **[M]** measured, **[D]** a design decision whose
evidence is that a vendor shipped it, **[O]** argument only.

| claim used here | source |
|---|---|
| 41.8% of multi-agent failures are SPECIFICATION failures; "fail to ask for clarification" is a top-3 individual mode **[M]** | MAST, Cemri et al., 1,600+ traces, 7 frameworks, κ=0.88, [arXiv 2503.13657](https://arxiv.org/abs/2503.13657) |
| Removing a plan lowers success in all 4 models; a MISALIGNED plan is worse than none; re-injecting every ~5 steps helps **[M]** | "From Plan to Action", [arXiv 2604.12147](https://arxiv.org/html/2604.12147v1) |
| A central planner's win comes from git worktrees + executable tests, not from the planner **[M]** | CAID, [arXiv 2603.21489](https://arxiv.org/abs/2603.21489) |
| LLMs cannot reliably self-correct without external feedback **[M]** | Huang et al., ICLR 2024, [arXiv 2310.01798](https://arxiv.org/abs/2310.01798) |
| Self-recognition correlates linearly with self-preference in LLM judges **[M]** | Panickssery et al., [arXiv 2404.13076](https://arxiv.org/abs/2404.13076) |
| Independent reviewers + a verification pass: 16%→54% review coverage, <1% findings wrong, still will not approve **[M/D]** | [claude.com/blog/code-review](https://claude.com/blog/code-review) |
| Multi-agent beat single-agent by 90.2% on RESEARCH, at ~15x tokens, and is explicitly NOT recommended for coding or shared-context work **[M]** | [anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Parallel writers make conflicting implicit decisions; prefer single-threaded **[O]** | [cognition.com/blog/dont-build-multi-agents](https://cognition.com/blog/dont-build-multi-agents) |
| Readiness rubric: problem statement, acceptance criteria, file pointers; do not assign ambiguous or open-ended work **[D]** | [GitHub Copilot coding agent best practices](https://docs.github.com/en/copilot/tutorials/coding-agent/best-practices) |
| Mid-task scope additions make an agent worse; clear upfront scoping is the sweet spot **[M/D]** | [Devin 2025 performance review](https://cognition.com/blog/devin-annual-performance-review-2025) |
| 58% of human interventions on agent PRs are guidance, not code; agent PRs get LESS scrutiny (52% vs 84%) **[M]** | MSR 2026, "Behind Agentic Pull Requests" |
| 93% of permission prompts are approved; approval fatigue; contain at the environment layer first **[M]** | [anthropic.com/engineering/claude-code-auto-mode](https://www.anthropic.com/engineering/claude-code-auto-mode) |
| Questions may auto-continue on a timeout; permission approvals never auto-resolve **[D]** | [Claude Code tools reference](https://code.claude.com/docs/en/tools-reference) |
| Session states derived from emitted activities; frozen activities are canonical, comments are not **[D]** | [Linear agent best practices](https://linear.app/developers/agent-best-practices) |
| Stuck detection by semantic equality, windowed at the last human message, 4/3/6 thresholds **[D]** | [OpenHands StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) |
| A span is not exported until it ends, so tracing cannot answer "what is it doing now" **[D]** | OTel spec [discussion #3732](https://github.com/open-telemetry/opentelemetry-specification/discussions/3732) |
| 16 experienced devs on mature repos: 19% slower measured, 20% faster believed **[M, n=16]** | METR, [arXiv 2507.09089](https://arxiv.org/abs/2507.09089) |
| Verified session success ~28-33% for experts, 15% for novices **[M]** | [anthropic.com/research/claude-code-expertise](https://www.anthropic.com/research/claude-code-expertise) |

Two places the field genuinely disagrees, flagged rather than resolved:

1. **Multi-agent on coding work.** Measured wins exist on read-only fan-out and
   on genuinely independent modules; the case against is about parallel writes
   to a shared artifact and is argument, not measurement. This plan takes the
   conservative side: one writer per tree.
2. **Mid-run steering.** Humans want it and mostly use it for guidance; the one
   vendor with published numbers says mid-task additions make results worse.
   Probably both: steering the APPROACH helps, adding SCOPE hurts. This plan
   does not build mid-run steering yet.
