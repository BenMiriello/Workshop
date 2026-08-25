# Coverage audit: the autonomous-work system, measured against the author-unavailable test

- Date: 2026-08-25
- Status: **AUDIT. No code and no ticket changed.** Read-only pass over the live
  ticket store (259 rows, 95 `todo`), the design corpus, and the tree at `51aabbb6`.
- The test applied, in the operator's words: *"If we handed this work off to an
  agent and the author of the ticket was unavailable, would it be executed
  completely and correctly, in keeping with the overall design and system?"*
- Scope: the loop by which a chat identifies work, files a ticket, an agent picks
  it up, executes it and completes it, with shared context across chats. Not apps,
  not hosting.

---

## 0. The three headline findings

1. **The readiness gate passes 91 of 95 open tickets and disagrees with a human
   read on 7 of the 12 tickets sampled here.** It is not measuring what its name
   claims. It passes a ticket whose own body says *"Do not send a worker at the
   body as written"* (`e8c53c78`), a ticket whose premise the tree has already
   overtaken (`f873ada7`), and two tickets that end with unanswered operator
   questions (`46b07a2b`, `e48268d0`) while both carry `execution: "code"` and no
   `blocked_reason`, so the unattended sweep would select them.

2. **Three ticket fields the design leans on are populated on ZERO of 95 open
   tickets:** `acceptance`, `touches`, `latitude`. Measured:
   ```
   acceptance set: 0    touches set: 0    latitude: {None: 95}
   ```
   `ticket_readiness` does not notice, because every check falls back to a prose
   label in the body. The consequences are load-bearing: `scripts/ralph.sh:252`
   `maybe_autocommit` commits only inside `touches`, and with no lane it falls
   back to *untracked files only* (`ralph.sh` ~:296), so ticket `70860d58`'s
   promise to "pause without losing the work" silently drops every modification
   to an existing file. And `project_ralph.sh:1148`'s acceptance gate reads
   `acceptance.kind`, which is never set.

3. **Four documented corrections ordered by `backlog-reconciliation-2026-08-24.md`
   were never applied**, so four contradictions it declares settled are still live
   in the tree, and one open ticket (`81198145`) implements the losing side of one
   of them. Section 3(d) quotes them with line numbers.

---

## 1. The sample: 12 central tickets against the author-unavailable test

Counts: **5 EXECUTABLE / 5 UNDERSPECIFIED / 2 STALE.**
`backend/services/ticket_readiness.check` returned `ok` for **12 of 12**, so the
automated gate disagrees with this read on **7**.

| id | short title | readiness | my verdict | the specific gap |
|---|---|---|---|---|
| `e9a313c3` | Empty claim token must be refused | PASS | **EXECUTABLE** | — verified `_opt_int` at `scripts/project_tasks.py:209`, call sites `:349 :354 :358 :387 :398`, fence at `tasks.py:2110`. The one sub-decision (pid) is delegated explicitly with an instruction to record the choice. |
| `0fb57b31` | Reclaim must advance the fence | PASS | **EXECUTABLE** | — premise verified verbatim: `tasks.py:1776` and `:1813-1815` leave `claim_token` intact; `_fence_ticket_lease` and `db/lease_repo.fence` both exist. Carries a reproduction. |
| `791f334e` | Opening a change moves the ticket to in_review | PASS | **EXECUTABLE** | — verified: `work_review.py` imports only `changes, worktrees`; its "THE HONEST LIMIT ON DERIVED" paragraph is present and stale; B3 landed at `d4c82846`. |
| `2e026c69` | Merging a change completes its ticket | PASS | **EXECUTABLE** | — verified: `merge_change` returns without touching a ticket; `auto_merge.py:571` calls it with no follow-up; `routes/jobs.py:359`, `routes/tasks_all.py:1203,:1289` do the completion themselves. |
| `91a477a5` | Register the landed-ness check into tasks | PASS | **EXECUTABLE** | — the `_lease_module()` idiom and `work_review.change_for_task` both exist; the mechanism/product split is stated. |
| `e7530e75` | Intake needs a done-signal | PASS | **UNDERSPECIFIED** | Output offers two different behaviours ("excluded **or** marked as likely-done") and no acceptance to choose between them; "already exists on the same topic" has no threshold, while `services/task_dedup.py` already carries a measured one (cosine ≥ 0.90) the ticket does not reference. |
| `4c59ee01` | A displaced worker must stop working | PASS | **UNDERSPECIFIED** | Requires a new exit code ("visible in the sweep's case statement, not inferred") but never allocates one. `70860d58` independently claims *"Exit codes 0, 1, 2, 3, 5, 6 and 7 are taken, so 8 is the pause code."* Two unsequenced tickets both take the next free code; neither has a `depends_on` on the other. |
| `e48268d0` | A ticket may not read done while unmerged | PASS | **UNDERSPECIFIED** | (a) `depends_on` is **empty**, yet `91a477a5`'s body states this ticket "depends on this" — the dependency exists only as prose in a sibling. (b) Its Boundaries forbid the exact move that `791f334e` mandates: *"Do not move the sweep's in_review write into work_review… The code names that dependency (the lease refactor, B3)"* — B3 shipped at `d4c82846` on 2026-08-23. A worker reading this ticket alone refuses work another ticket requires. |
| `46b07a2b` | Teach agents the ticket state vocabulary | PASS | **UNDERSPECIFIED** | Ends: *"Two things need the operator and must not be guessed… `STATUS_MEANING["done"]` stays a placeholder until he rules."* It cannot be completed by an unavailable-author agent. It is `execution: "code"`, `blocked_reason: null`, no `blocked_request` — unattended-selectable while un-completable. |
| `70860d58` | Pause a run at the budget cap and resume | PASS | **UNDERSPECIFIED** | Line numbers verified, but the commit path is unbuildable as promised: it says "commits what exists inside the task's touches lane", and `touches` is set on 0 of 95 tickets, so `maybe_autocommit`'s no-lane fallback commits **untracked files only**. A pause on any ticket that edited an existing file loses exactly the work the ticket exists to preserve. Priority band `urgent`. |
| `f873ada7` | Give every open ticket the four labelled lines | PASS | **STALE** | Premise: *"of 38 ready todo tickets, 9 had execution 'code' and ZERO passed readiness."* Measured 2026-08-25: **91 of 95 `todo` tickets pass**; the 4 failures are all `[SEC-*]` host-admin tickets; `execution` is `code` on 75. The backlog already moved. Residual work is 4 tickets, not a `deep` ticket at the `high` band. |
| `307544a7` | Surfaces that make a triage decision visible | PASS | **STALE** | Its data source does not exist. The ticket says to read "the triager's sidecar" at `.runtime/triager/<scope>.json`; **`.runtime/triager/` is absent on this host** — the triager has never run, which `scripts/dispatch_triggers.sh:665-666` records in a comment. The page would render its empty state 100% of the time. No `depends_on` on `88a2ba81` (live trigger) or `152856ba` (the scheduler). |

Also spot-checked, not scored in the 12: `e8c53c78` is **STALE** — its own body carries a
2026-08-23 correction ending *"Do not send a worker at the body as written"*, and
`audit-what-actually-runs-2026-08-23.md` §3.1 says flatly "**Done.**" `git worktree list`
shows ten live locked worktrees. It is still `todo`. Readiness passes it.

### Why the gate and the read diverge

`ticket_readiness.check` asks six questions: title, an Objective label *or* ≥40
chars of body, an Output label *or* `acceptance`, a path-or-backtick anywhere in
the body, a Boundaries label *or* `touches`, and that named dependencies resolve
to an existing id. Every one is satisfiable by prose. `BLOCKING` is only
`("title", "dependencies")`. Nothing checks whether a cited path exists, whether
a stated premise is still true, whether the body contains an unanswered question,
or whether a named dependency is `cancelled` — `81198145` depends on `652dc696`,
which is cancelled, and passes because the id resolves. The module's own docstring
is honest about this ("It is not a quality judgement and it cannot be"); the
problem is that `scripts/project_ralph.sh:102-104` uses it as the unattended
admission predicate, where it is read as one.

---

## 2. Coverage map: the stages, end to end

Built-ness was checked in code, not taken from a doc.

| # | Stage | Specified by | Doc state | Tickets | Verdict |
|---|---|---|---|---|---|
| 1 | **identified** (chat → candidate work) | `deciding-on-work-from-a-chat-2026-08-22.md`; `work-loop-core-design-2026-08-21.md` §2 "intake" | current; **unratified proposal** | `e7530e75` | **PARTLY BUILT** — `services/ticket_drafts.py` + `routes/chat.py:1141,:1156`, operator-triggered only; no automatic intake, no done-signal |
| 2 | **filed** (a ticket exists) | `docs/ticket-writing.md`; `services/ticket_format.py` | current | `f873ada7` (stale), `5534f204` | **PARTLY BUILT** — store, format and readiness all exist; the structured fields (`acceptance`/`touches`/`latitude`) are populated on 0/95 |
| 3 | **scoped / triaged** | `primitive-roles-2026-07-18.md` R4; `work-loop-core-design` §2 | R4 current; §2 unratified | `307544a7`, `88a2ba81`, `152856ba` | **BUILT BUT HAS NEVER RUN** — `services/triager.py` is ~650 lines; `.runtime/triager/` does not exist; `.runtime/trigger-state.json` frozen since 2026-07-18 |
| 4 | **ready / admitted** | `autonomy-implementation-2026-08-13.md` §3; `limiting-usage-2026-08-24.md` | spec, binding; **proposal** | `70860d58`, `a731ec9b`, `d37c3314` | **PARTLY BUILT** — `project_ralph.sh:102` gates on `execution=="code"` + readiness; `usage_ledger._admission:697` enforces three paths; no session window, no monthly cap, no pause/resume |
| 5 | **claimed** | `workspace-ownership-and-leases-2026-08-22.md` §13; `lease-and-status-authority-2026-08-24.md` | current; plan | `0fb57b31`, `e9a313c3`, `4c59ee01` | **PARTLY BUILT** — lease table, atomic claim and fencing all exist (`d4c82846`); reclaim does not advance the generation, an empty token is silently promoted, a displaced worker continues |
| 6 | **executed** | `branches-worktrees-and-environments-2026-08-20.md` §4, §8.2; `work-loop-core-design` §5 | current; **§5 is stale** | `fae54a3e`, `e8c53c78` (stale) | **BUILT, CURRENTLY DISARMED** — `ralph.sh:538` sets `--permission-mode bypassPermissions`; ten locked worktrees; `queue_runner.sh` is resident (pid 1060001, since Aug 23). `.runtime/autonomy-override.json` is `engaged: true` since 2026-08-24. Chat-spawned subagents still share `/workspace` |
| 7 | **reviewed** | `changes-and-review-2026-08-22.md` (C1–C7 **BUILT**) | current | — | **BUILT, HALF INERT** — `scripts/reviewer.sh` + the `work-reviewer` subagent run; the acceptance-criteria half (`project_ralph.sh:1148` reads `acceptance.kind`) never fires because no ticket sets it |
| 8 | **landed** | `changes-and-review` §4 (C2 table); `work-status-contract-2026-08-24.md`; `lease-and-status-authority-2026-08-24.md` | current; proposal; plan | `791f334e` → `2e026c69` → `91a477a5`; `e48268d0`; `26af958e` | **PARTLY BUILT** — merging works; *nothing anywhere reads a change's status to decide a ticket's*. `auto_merge.py:571` lands code and leaves the ticket `in_review` forever |
| 9 | **reported** (back to the originating chat) | **one bullet** in `work-loop-core-design` §3: *"Track-back delivery — currently poll-only; add push (SSE/notification)"* | unratified proposal | **NONE** | **UNDESIGNED** — verified poll-only: `chat_work.thread_work:304` matches on `source.ref`; no notify/push path from `tasks.py` or `project_ralph.sh` |
| 10 | **memory / context across chats** | `what-a-memory-is-2026-08-23.md`; `finding-and-surfacing-memory-2026-08-23.md`; `agent-context-plan-2026-08-24.md`; `agent-context-and-toolkit-2026-08-20.md` | audit; current design-only; plan; contract | `8db95ee9`, `46b07a2b`, `ca23bf08`, `81198145`, `557ec533` | **PARTLY BUILT** — `semantic_index` search works and `orientation.py` exists (3.2 KB, `manifest_text` + `where_is`); `memory_keeper` watermark unmoved for 38 days; no corpus map, no relevance hint |
| 11 | **worker self-assessment** ("can I do this as written?") | `work-loop-core-design` §8, which defers itself: *"should be specified when S3/S4 land, not invented ad hoc per run"* | unratified proposal, self-deferred | **NONE** | **UNDESIGNED** |

---

## 3. The holes

### (a) Stages with no design doc at all

1. **reported / track-back push** (stage 9). One bullet in an unratified proposal
   is the entire corpus. Every other stage has at least one dedicated document.
   Without it, "an agent completes the work without the operator managing it" ends
   with the operator polling.
2. **worker self-assessment at claim time** (stage 11). `work-loop-core-design` §8
   states the requirement in the operator's own words and then explicitly defers
   its specification. Nothing has picked it up in four days.
3. **who populates `acceptance`, `touches` and `latitude`.** Three fields the
   schema defines, the sweep reads, and no document assigns to a producer.
   `docs/ticket-writing.md` teaches the four prose labels, not the fields.
   Measured result: 0/95.

### (b) Stages with a doc but no ticket

- `work-loop-core-design-2026-08-21.md` §7 lists four decisions and ends: *"None
  of these are filed as tickets yet, per the operator's instruction to plan
  first."* Four days on, still none. Decisions 1 and 4 gate stages 6 and 8.
- `the-plan-2026-08-23.md` §7.1, the nine/ten stranded branches: *"Merge them, or
  discard and re-run? Five tickets are marked done against them."* No ticket.
  `e8c53c78`'s own correction says "it is not this ticket".
- `the-plan` §7.4 / `lease-and-status-authority` §8.2, auto-merge policy:
  `merge_policy` *"is still set nowhere"*. `26af958e` covers the Settings screen
  but not the default.
- The four in-place doc corrections ordered by `backlog-reconciliation-2026-08-24.md`
  §1.1, §1.2, §1.3 and §1.8. `e5848c87` covers "two plan docs", not these.

### (c) Tickets depending on something that does not exist, not marked blocked

| ticket | depends on what | state |
|---|---|---|
| `81198145` | `depends_on: 652dc696`, which is **cancelled** | readiness passes: the id resolves |
| `1638d7f9` | `depends_on: 97b0f04a`, which is **parked** | same |
| `307544a7` | `.runtime/triager/<scope>.json`, which has never been written | no `depends_on` at all |
| `70860d58` | a `touches` lane on the ticket being paused | set on 0/95 tickets |
| `46b07a2b` | an operator ruling on what `done` claims | `execution: code`, not blocked |
| `e48268d0` | `91a477a5` (stated in `91a477a5`'s prose) | `depends_on` empty |
| `4c59ee01` | an unallocated exit code that `70860d58` also claims | neither references the other |

### (d) Contradictions between current documents

**C1 — Learnings: a `kind` or a `reach`. An open ticket implements the losing side.**

`backlog-reconciliation-2026-08-24.md:45` (a document whose own banner reads
*"Where this disagrees with an earlier doc, this wins"*):

> **`reach` wins.** A `kind` preserves the unanswerable write-time question of
> whether a sentence is a note or a learning … Correct reassessment section 6.2 in place.

`agents-and-memory-reassessment-2026-08-23.md:340`, uncorrected:

> **Decision: fold `learnings` into the knowledge store as a note kind, and delete
> the separate store**

And open ticket `81198145` is titled *"Fold learnings into the knowledge store as
a note kind, and delete the separate store"*. A worker handed it builds the
rejected design.

**C2 — Tokens or money for a limit. Two same-day documents, opposite answers.**

`backlog-reconciliation-2026-08-24.md:70`:

> **Money wins for LIMITS. Tokens stay for ACCOUNTING.** … a limit is expressed in money.

`limits-plan-audit-2026-08-24.md` §4:

> **Which unit for the monthly limit on day one: tokens.**

Both are dated 2026-08-24 and both claim decision authority. Ticket `a731ec9b`
follows the second (*"Tokens and not dollars on day one"*). Nothing in the corpus
orders them.

**C3 — "Nothing queues" versus pause-and-resume. The reconciliation was never applied.**

`providers-credentials-and-limits-2026-08-23.md:736`, still uncorrected:

> **Nothing queues.** A queued turn is a refusal that lies about when it will run.

`backlog-reconciliation` §1.2 ruled *"a refusal never queues; a pause is resumable
work and is not a queue entry"* and instructed: *"Add that to providers 7.3."* It
was not added. `70860d58` (priority band `urgent`) builds pause-and-resume against
a document that still reads as forbidding it.

**C4 — `micro_usd = 0` on a subscription row.**

`providers-credentials-and-limits-2026-08-23.md:676`:

> `micro_usd` is an integer so it never accumulates float error, and it is **0,
> for local and for subscription**

`limits-plan-audit-2026-08-24.md` §4:

> As the value to record, no, and here it is actively harmful. It would zero every
> row in this installation … and make a dollar limit non-binding.

`backlog-reconciliation` §1.3 said "Correct providers 7.2." Not corrected.

**C5 — `work_review.py`'s docstring names a blocker that shipped, and `e48268d0` quotes it forward.**

`backend/services/work_review.py`, docstring:

> Moving that write in here would take the fence with it … Doing it properly means
> the lease refactor (B3), so it is left alone and named rather than half-done.

B3 landed at `d4c82846` (2026-08-23). Ticket `791f334e` is scoped to make exactly
that move; ticket `e48268d0`'s Boundaries forbid it, citing the same dead blocker.
Two open tickets in the same chain give a worker opposite instructions.

**C6 — the built state of the execution boundary.**

`work-loop-core-design-2026-08-21.md` §5:

> `ralph.sh` invokes `claude -p` with **no `--permission-mode`**, and the loop
> has … **never successfully written a file unattended**

and §3, under ADD:

> **A resident driver + scheduler.** … nothing consumes it … no runner is resident.

Both are false on disk: `scripts/ralph.sh:538` sets
`PERM_FLAGS=(--permission-mode bypassPermissions)`, and `queue_runner.sh` has been
resident since Aug 23. The document carries no supersession banner, so a fresh
agent reading the corpus's own "core work loop" doc starts from a false picture of
the two most important facts in it.

---

## 4. What genuinely needs the operator

Ranked by downstream work unblocked. Every item is a choice between defensible
options, not a lookup.

**D1. What does `done` claim?**
Options: (a) `done` = accepted **and** landed, which makes today's unmerged-branch
state a contradiction the store refuses; (b) `done` = the worker finished, with
landed-ness read separately from a derived field, leaving "done but not on main" a
legal, visible state.
Implies: (a) is stricter and closes the seven-duplicate-implementations defect at
the write; (b) is cheaper and keeps the file-backed scopes simple.
Unblocks: `46b07a2b` (which stops on this by name), `e48268d0`, the contract text
in `91a477a5`, and `agent-context-plan` §8 D1.
**Recommendation: (a).** It is what `work-status-contract` §3.2 already implies,
and `791f334e`+`2e026c69`+`91a477a5` build the machinery that makes it enforceable
rather than aspirational.

**D2. Turn the loop back on, and under what boundary?**
`.runtime/autonomy-override.json` has been `engaged` since 2026-08-24 ("Disabled
from Settings"). `work-loop-core-design` §7.1 asks: green-light unattended writes
under bwrap + worktree with one attended verification, or hold for the full
uid-layer (`4a79ffc5`, SEC-A1)?
Implies: holding means the whole system stays theory; bwrap + worktree is already
default-on and proven, and ten worktrees have already been produced under it.
Unblocks: stages 6–9 stop being untestable. Nothing else in this audit can be
validated in practice while the loop is off.
**Recommendation: bwrap + worktree now, after `0fb57b31` and `4c59ee01` land**
(the two claim-integrity defects), with the uid-layer as the durable follow-up.

**D3. The nine/ten stranded branches: merge or discard?**
`the-plan` §7.1. Five tickets are marked `done` against them, so discarding means
reopening five.
Implies: merging makes `main` honest and validates the landing chain against real
work; discarding costs the re-implementation the system already paid for once.
Unblocks: the credibility of every `done` in the store, and it is the real-world
test case for `e48268d0`.
**Recommendation: merge them one at a time under the reviewer gate**, then file
the reopen only for those that conflict.

**D4. Money or tokens for the monthly limit?** (contradiction C2)
Options: (a) money, per `backlog-reconciliation` §1.3, which needs `d0b103e3`
(record provider/model/cost) first; (b) tokens on day one at 60M/month with the
dollar figure displayed, per `limits-plan-audit` §4, switching later.
Implies: (a) sequences `d0b103e3` ahead of `a731ec9b` and delays the cap; (b)
ships a binding cap this week and makes the unit switch a one-line change.
Unblocks: `a731ec9b`, `d0b103e3`, `972c1d8a`, `d37c3314`.
**Recommendation: (b), and mark `backlog-reconciliation` §1.3 as superseded on
the day-one question only.** The reason is measured: `micro_usd` as specified
is 0 for every row in this installation, so a money limit would never bind.

**D5. Who fills in `acceptance`, `touches` and `latitude` — or do they go?**
Measured: 0 of 95. Options: (a) the scoping chat sets them when it files, which
makes `ticket-writing.md` produce fields not just prose; (b) the worker derives
`touches` at claim time from its own plan; (c) delete the fields and let the prose
labels be the whole contract.
Implies: (a) makes the reviewer's acceptance gate and `70860d58`'s commit lane
real; (c) means admitting the reviewer gate checks only deterministic repo checks,
never the ticket's own criteria, and that a paused run cannot commit modified files.
Unblocks: `70860d58`'s core promise, stage 7's inert half, and the honesty of
`ticket_readiness`.
**Recommendation: (a) for `acceptance` and `touches` at filing time, and delete
`latitude` from the ticket** — it is null on 100% of tickets and duplicates the
`Boundaries` line.

**D6. Does an auto-merge complete a ticket, or leave it for a human to close?**
`lease-and-status-authority` §8.2 and `the-plan` §7.4. `merge_policy` is set
nowhere in the tree.
Implies: completing it closes G3 and stops `auto_merge.py:571` orphaning tickets
in `in_review`; leaving it for a human keeps a person in every loop and makes the
autonomous mode not actually autonomous.
Unblocks: `2e026c69`, `26af958e`.
**Recommendation: a merge completes it.** C2's table already says so, and
reversing it later is one line.

**D7. Does the sweep worker get the orientation manifest?**
`agent-context-plan` §8 D4: ~322 tokens added to a 612-token prompt, on the path
that runs unattended. Today the sweep worker is the thinnest-context agent in the
system and is the one making irreversible commits.
Implies: injecting it costs ~50% more prompt on every iteration; not injecting it
means the worker never learns the vocabulary `46b07a2b` is building.
Unblocks: the last part of `46b07a2b`.
**Recommendation: inject it.** The whole point of `46b07a2b` is that the agent
executing work knows what a status claims; excluding the one agent that writes
statuses defeats it.

**D8. Learnings: `kind` or `reach`?** (contradiction C1)
`backlog-reconciliation` §1.1 settled on `reach`; the reassessment doc and open
ticket `81198145` still say `kind`.
Implies: whichever loses, one document and one ticket need correcting before
anyone builds.
Unblocks: `81198145`, `ca23bf08`, and the memory stage generally.
**Recommendation: confirm `reach`, cancel `81198145`, and refile it.** Its stated
dependency `652dc696` is already cancelled, so it is broken either way.

---

## 5. Sequencing

`depends_on` is the only real sequencing mechanism; `priority` is a five-value
band, not a scale.

### The order the tickets should be built in

**Wave 0 — decisions and the one scheduler.** D1, D2, D4, D6 answered. Then
`152856ba` (run the trigger dispatcher from the backend). It is the single
scheduler and it already correctly blocks `ca23bf08` and `88a2ba81`.

**Wave 1 — claim integrity, before any status work.** `0fb57b31` (reclaim
advances the fence) → `e9a313c3` (empty token refused) → `4c59ee01` (displaced
worker stops). Nothing downstream is sound while a zombie can write `done`.

**Wave 2 — the status chain.** Already correctly chained:
`791f334e` → `2e026c69` → `91a477a5`, then `e48268d0`.

**Wave 3 — limits.** `70860d58` (pause/resume) → `a731ec9b` (two limits), with
`d0b103e3` first only if D4 goes to money. `70860d58` cannot ship as written
until D5 answers the `touches` question.

**Wave 4 — intake, triage, memory.** `88a2ba81` → `307544a7`; `ca23bf08`;
`46b07a2b`; `8db95ee9`; `e7530e75`.

**Wave 5 — the two undesigned stages.** Track-back push and worker
self-assessment need a design pass before a ticket exists.

### Tickets that should carry a `depends_on` and do not

| ticket | should depend on | why |
|---|---|---|
| `e48268d0` | `91a477a5` | `91a477a5`'s body states it outright: *"Those belong to ticket e48268d0 … which depends on this."* The link exists only as prose. |
| `4c59ee01` | `70860d58`, or vice versa | Both allocate the next free worker exit code. `70860d58` asserts 8 is free; `4c59ee01` needs one and names none. Whichever lands second is silently wrong. |
| `70860d58` | a `touches`-population ticket (does not exist; see D5) | Its commit path is a no-op for modified files with `touches` empty on 100% of tickets. |
| `307544a7` | `88a2ba81` (and transitively `152856ba`) | It renders a sidecar that has never been written because the triager has never run. |
| `fae54a3e` | `4c59ee01` | Both change what happens when two agents contend for one tree; landing chat-subagent worktrees while a displaced worker keeps writing widens the window they each narrow. |
| `81198145` | — | Its only dependency, `652dc696`, is **cancelled**. Re-point or cancel. |
| `1638d7f9` | — | Depends on `97b0f04a`, which is **parked**. Readiness passes it because the id resolves. |
| `46b07a2b` | should be `blocked` with a `blocked_request`, not `todo` | It ends on two questions for the operator and is `execution: "code"` with no block, so the unattended sweep can select a ticket that cannot be finished. |

### The one gate change this audit would recommend

`ticket_readiness` should add two checks that cost nothing and would have caught
five of the seven divergences above: **a named `depends_on` must resolve to a
ticket that is not `cancelled` or `parked`**, and **a body containing an explicit
operator question (or a dated CORRECTION block that says not to start) is not
unattended-ready**. Both are membership tests, not judgement. Neither belongs in
`BLOCKING` — they belong in the `--unattended` predicate at
`scripts/project_ralph.sh:102`, which is where readiness is actually used as a
gate rather than as advice.
