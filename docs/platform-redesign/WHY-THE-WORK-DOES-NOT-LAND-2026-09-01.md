# Why the work does not land

**2026-09-01.** Written on the operator's instruction to stop treating this as
an application-defect question and diagnose the process. Everything below is
measured against this tree, this ticket store, this chat store and these logs on
2026-09-01. Where a number appears, the command that produced it is named.

---

## 0. The paradox that has to be explained first

The operator's report is that nothing gets done. The repository says something
else, and both are true at once. That contradiction is the whole problem, so it
goes first.

| measure | value |
|---|---|
| commits since 2026-07-15 | **1,435** (~30/day) |
| design prose in `docs/` | **668,795 words** (~2,600 pages), 153 docs in `platform-redesign/` alone |
| open tickets | **195**, totalling **71,534 words** |
| open tickets never attempted | **188 of 195** |
| open tickets with acceptance criteria | **0 of 195** |
| operator messages that are repeat complaints | **170 of 954 — 18%** |
| live backlog 2026-08-26 → 2026-09-01 | **159 → 195** (todo 143 → 173) |

So: enormous throughput, and a backlog that grew by 36 in the six days that
throughput was happening. The work is real. It is simply not **his** work.

**The single sentence version: this system has very high output and almost no
allegiance to the operator's queue, because the operator's queue does not exist
as an artifact anywhere in it.**

Everything below is a mechanism for that sentence.

### 0.1 What he actually opens to

Screenshotted today against the built origin, `/apps/development`. It redirects
to a chat list, which is complaint `7d8d733a`. But the thing worth recording is
not the layout, it is the CONTENT of the rows. Each row previews its last
message, and those messages read, in order down the page:

> "Both done. Summary: ..."
> "Everything is already in sync. The bundle is built and served."
> "Done. Ticket `bcbd6d14` is marked done and changes are live."
> "Both done, both on the built origin."
> "Done. All four are committed and on the origin."

**The landing page of the app he is trying to build is a scrolling wall of an
agent telling him it finished.** That is the artifact this system produces most
reliably, it is the first thing he sees every morning, and §4 and §5 explain why
those claims and his experience of the app disagree. Baseline image kept at
`scratch/baseline_apps_development.png`.

---

## 1. The operator's queue is not represented anywhere

`python3 scripts/project_tasks.py list-json .`, by `source.kind`:

    agent      132
    operator    34
    chat        29

**CORRECTION, 2026-09-01, and it is the operator's.** He read this and said he
did not file these tickets, "maybe one or two." He is right, and the split above
does not mean what it says. Checked: the 34 marked `operator` are 12 filed by an
AGENT in the last two days writing up his complaints (`ref: None`, all created
2026-09-01) and 22 batch audit imports (`work-ui`, `security-audit-2026-07-18*`).
The mechanism is worse than a mislabel: `tasks.py:776` returns `operator` for any
missing or malformed source, `tasks.py:2129` defaults the API path to `operator`,
and `scripts/project_tasks.py:326` defaults the CLI path to `agent`. So the same
ticket gets a different provenance depending on which door it came through, and
`operator` doubles as the value for "nobody recorded anything" — there is no
`unknown` in `SOURCE_KINDS`.

**This strengthens the section rather than weakening it.** The claim is not that
an agent decided two thirds of the work; it is that **there is no trustworthy
record of what the operator asked for anywhere in this system**, and the one
field that purports to hold it turns out to be a default value. A filter built on
it would have been a lie with a UI on it. Being fixed before it is surfaced.
Every ticket is `assignee: operator`, every one has `attempts: 0`, and not one
of the 195 has anything in its `acceptance` field. The field exists on the
schema. It is null on every ticket in the store.

There is no file, no view, and no query anywhere in this system that answers
"what did the operator ask for, in what order, and what is the state of each."
`docs/platform-redesign/INDEX-open-work-2026-08-26.md` is the closest thing and
it is a generated snapshot of the ticket store, which is two-thirds agent
opinion.

The consequence is mechanical, and it is not a motivation problem. A session
opens with no representation of what was asked, so it does the only thing
available: it **surveys**. Surveying a 292,000-line codebase always returns
defects. Those defects are real, fixing them is honest work, and none of it is
the thing the operator said. Then the session files what it did not get to as
new tickets — which is why the backlog grows on days with thirty commits.

This is why "I gave you a direct order and nothing happened" and "1,435 commits"
are both true. The order was never written down anywhere that outlives the
message it arrived in.

## 2. The machine meant to execute the queue is switched off, and broken underneath

`.runtime/autonomy-override.json`:

    {"engaged": true, "reason": "Disabled from Settings",
     "engaged_at": "2026-08-24T03:10:21Z", "engaged_by": "operator"}

Autonomy has been off since 2026-08-23 20:10 local. `.runtime/queue-runner.log`
contains **57,547** lines of `autonomy disabled (flag or override present);
idling`, and it is still emitting them every ten seconds. Filing a ticket does
wake the runner — `woken early by enqueue signal` appears through 2026-09-01 —
and the runner then checks the flag and goes back to sleep. **Every ticket filed
in the last eight days went into a queue whose runner is off.**

That was the operator's own decision and it is not second-guessed here. What
matters is what is underneath it, because turning the flag back on today would
not work.

**The runner cannot read the ticket store.** `.runtime/ralph.log`, last run
2026-08-25, contains 18 occurrences of:

    services.tasks.RelationalStoreUnavailable:
      a database is configured but did not answer

The database is fine. Workshop's Postgres is up on `127.0.0.1:5440` and
answering. The runner is invoking bare `python3` with no venv and never sourcing
`~/.config/workshop/secrets.env`, so `WORKSHOP_DATABASE_URL` is unset and
`sqlalchemy` is not importable — the same log also carries
`ModuleNotFoundError: No module named 'sqlalchemy'`.

This was already diagnosed. `scripts/project_ralph.sh:326`, written 2026-08-27:

> THE INTERPRETER THESE SHELL INTO MUST BE THE BACKEND VENV, not PATH's
> python3. [...] Measured 2026-08-27 in this tree's own `.runtime/ralph.log`.

The fix was applied to **7 of the 77** `python3` call sites across
`scripts/ralph.sh` and `scripts/project_ralph.sh`. `ralph.sh` has 27 and uses
the venv at zero of them.

**And when it did run, it produced nothing.** The last run, 2026-08-25:

    iter 1: claude exit=124, output bytes=0, tokens=0, dur=1199s
    iter 1: timeout kill, no usage JSON, billing nominal 150000 tokens
    iter 1 reviewer.complete=0 verdict=reject
    wall-time cap (1200s) hit at iter=2
    task released to todo (incomplete (rc=2); attempt 1/3)

Twenty minutes, zero bytes of output, 150,000 tokens billed to the ledger for
it, ticket returned to the queue. There are **29 such zero-output timeouts**
across 311 recorded run starts.

So "we are using the application to build itself" has not been true for over a
week. All of it has been a person typing in a chat window.

## 3. Understanding the problem is where the work stops

This is the most uncomfortable finding, because the diagnosis the operator asked
for today has already been written — well — and then dropped.

`docs/platform-redesign/app-separation-why-it-recurs-2026-08-24.md` opens on the
operator asking almost exactly today's question, answers it in four named
mechanisms, and prescribes the cure in §4: not another document, but **one
command whose result cannot be produced by writing prose** —
`scripts/check_fresh_clone.sh`, with four assertions.

The script was built on 2026-08-27. Run today, it says:

    IMPLEMENTED ASSERTIONS
      FAIL  no_app_source          106 tracked files that ARE app source
      PASS  no_personal_data
      FAIL  no_hardcoded_identity  116 home paths, 103 identity refs, 1 email

    NOT IMPLEMENTED -- these are not passing, they have never been run
      ????  build_with_zero_apps
      ????  shell_boots_with_empty_catalog

**The fix for "we only ever half-build things" was itself half-built**, and the
half left out is the half that actually tests whether a stranger's clone works.

It is not an isolated case. `docs/platform-redesign/tools-are-declared-2026-08-23.md`
designed a complete mechanism for giving an agent a new tool: MCP dispatch,
bwrap confinement inherited from the calling turn, the deny list applied twice
(at declaration and again on the resolved argv). It is implemented in
`backend/services/command_tools.py` and wired into
`backend/mcp/workshop_query_server.py`. And:

    $ cat .runtime/tools.json
    {"tools": []}

**Zero tools have ever been declared.** Including the timer the operator asked
about today, which this mechanism was built to make trivial.

The pattern, stated plainly: *the design phase is completing and the build phase
is not.* Design is where this project's effort actually goes — 668,795 words of
it — and a design doc is indistinguishable from progress right up until someone
opens the app.

## 4. Tickets are essays, so nothing is small enough to finish

Ticket body length across the 195 open tickets, in words:

    min 88 | p25 228 | median 337 | p75 440 | p90 611 | max 1,136
    36 tickets over 500 words. Total: 71,534 words.

The current top-priority ticket (`70860d58`, P100) is 1,136 words. It is
genuinely well written — it corrects three false claims in the ticket it
replaces, cites line numbers, states boundaries. It also cannot be finished in
one attempt by anyone, and its acceptance criterion is the last sentence of the
prose while the structured `acceptance` field is `null`.

`docs/ticket-writing.md` says a ticket has two readers, the worker and the
operator. What the store contains is a third thing: a design document with an id.
188 of them have never been attempted, and the median age is 7 days.

A worker handed a 1,100-word ticket with no executable acceptance criterion will
do part of it, form its own view of what "done" means, and report success
against that view. That is not a character flaw. It is the only available
behaviour when nothing states the finish line.

## 5. Narrow verification, which is the failure that gets noticed

Already recorded in `HANDOFF-2026-09-01.md` §7 and confirmed again here: a change
gets checked by the check that motivated it and by nothing else. The store guard
that broke `project_tasks.py <verb> .`; the pytest gate that made the suite
recurse; the app Chats tab "fixed" into listing 59 conversations that 404; the
venv fix applied to 7 of 77 call sites. Each was verified against its own
trigger and shipped.

This is the mechanism the operator experiences as "you don't test anything," and
it is more specific than that: things *are* tested, against the wrong thing.
A uniform metric sweep of 39 routes reported everything clean while `/tickets`
rendered nothing over 188 tickets — and ranked the unreadable Memory tab among
the *highest* scoring pages, because truncated titles are a lot of text.

---

## 5.1 The measured shape of the work: we are almost only fixing ourselves

A parallel audit measured the 1,435 commits by DIFF rather than by subject
prefix (56% of subjects are prose, so prefixes undercount). Full notes:
`scratch/plan-vs-reality-audit.md`.

| measure | value |
|---|---|
| commits touching code never touched before in the window | **39 (3.3%)** |
| commits touching only code already changed in the window | **704 (60%)** |
| `fix:` commits modifying code written inside the same six weeks | **185 of 186** |
| file additions vs deletions | 1,468 vs 208 |
| insertions vs deletions | 374,363 vs 27,407 |
| HEAD ahead of `origin/main` | **998 commits** (186 ahead of local `main`) |
| new design docs in six weeks vs `feat:` commits | **223 vs 87** |
| `TODO.md` | 200 -> 4,432 lines |

**One of 186 fix commits touched code older than six weeks.** That is the
operator's "spinning our wheels" as a number: essentially all repair is repair of
our own recent work. And nothing has shipped anywhere — the branch is 998 commits
ahead of its remote.

**Five design generations are simultaneously routed**, roughly 13,600 lines:
agents-as-roster (5,918 lines, still in nav, 24 legacy `/agents/build/{slug}`
routes live); project-as-entity, retired on 2026-08-20 (`ProjectDetail.tsx` is
1,803 lines and **grew by two while marked for deletion**); top-level
Runs/Changes/Deploys (~4,135 lines, and the deploy subsystem **has never run
once** while its watcher still starts on every boot); app-is-the-entity (built,
added *beside* the other three, with its own orphans); and the settled model of
record from 2026-08-29 — threads with `armed`/`role`, the Roles page, the Work
tab — of which **zero lines exist**. Settings is still 12 rows, not the 9 the
doc specifies.

Built and dead: `services/triager.py` (1,004 lines, never executed),
`services/token_usage.py` (declared the one central seam for every usage number,
zero non-test importers), the leases subsystem (~950 lines, mounted and typed,
zero frontend callers), `services/latitude.py` (null on 100% of tickets, ordered
deleted 2026-08-25), `memory_keeper` (watermark frozen since 2026-07-18).

**Of the retirement commitments in the four current surface docs, 1 of 22
landed.** Re-checking the 2026-08-29 review's own 22-item TODO: 7 done in three
days, 1 got worse, and zero of its seven Tier-1 items moved. The seven that
landed are all infrastructure repair; the fifteen that did not are all deletion
of a superseded surface. Deleting a module is invisible and safe. Deleting a
page means deciding what replaces it, and that decision keeps getting written
down instead of made.

**The auditor's own conclusion is the sharpest sentence produced by any of
today's work, and it dictates the next action:**

> Every invariant in this repo that actually holds is enforced by something that
> fails the build. That one is enforced by prose, and it is the only one that
> has never held.

---

## 5.2 I documented a thing that was already documented

Retained as an entry in the ledger of this failure mode rather than deleted,
because it happened while writing the section above it.

I measured the process tree (`uvicorn` -> `bwrap` -> `claude` -> this session),
concluded that a self-hosting agent cannot verify its own backend change in-turn,
and wrote it up as a finding. **It was already written down.**
`docs/applying-changes.md` §"You do not need to coordinate" says the backend
restart is deferred until your own turn finishes, that an interrupted turn is
paused rather than lost, and — verbatim — *"An agent reasoning about other
agents' in-flight work is inventing a problem the tooling already owns."* That
last sentence describes a real amount of this session's effort.

`CLAUDE.md` names that file as **the ONLY current source for how to apply a
change**, in bold, above the fold. The pointer was there and was not followed.

The operator's question was "why were you not aware, and why is everything so
hard to find?" The first half has no excuse: the file is named, I did not read
it. The second half is real and is §6 item 5's problem — an installation with
153 design docs, a 155-line procedure doc and a long `CLAUDE.md` has no mechanism
that routes an agent to the right one at the moment it matters; it relies on the
agent having read everything. The one genuinely new fact — that the agent is a
*child process* of the backend, which is WHY the deferral exists — belongs in
that procedure doc, and has been added there rather than here.

### And the retrieval measured, because "hard to find" is testable

The operator asked whether this is being fixed. It is, and here is the defect,
measured rather than asserted. `semantic_index.search`, owner pinned:

    "how do I restart the backend"
      -> chat, chat, chat, then doc | docs/applying-changes.md   AT RANK 4

    "why is the backend restart deferred"
      -> chat, chat, chat, doc | docs/platform-redesign/continuation-2026-07-31.md
         applying-changes.md DOES NOT APPEAR

**Three conversations *discussing* the procedure outrank the document that *is*
the procedure**, and on the second query the authoritative doc loses to a
five-week-old design note. Search is not broken — it returns the right file. It
ranks it fourth, behind chatter, because **authority is not a ranking signal and
staleness is not a penalty.** In a tree with 153 design docs, many explicitly
superseded, that is how a retired model gets rediscovered as if it were current.

Being fixed: authority and recency become ranking signals, superseded and
HISTORICAL docs get demoted or labelled rather than returned as current, and —
the cheaper half, because retrieval only helps an agent that thinks to search —
the tooling names the relevant doc at the moment it refuses, so
`restart_backend.sh` declining says why and where that is written down.

**A document that is not found is not documentation.** That is the same finding
as §3 (mechanisms built and never used), §6 (`.runtime/tools.json` empty) and
§6.1 (an hour of archaeology over an index that already existed). Four instances,
one shape: this project's problem is much less often that a thing is missing than
that nobody knows the thing is there.

---

## 6. What is actually missing from the application

A Workshop chat's entire tool grant, from `_tool_list()` in
`backend/mcp/workshop_query_server.py`: `query`, `search`, `where_is`,
`list_attachments`, `get_file`, and `spawn_background` (only when bound to a
real thread). Everything else a build chat has comes from the CLI, not from
Workshop.

Missing, in rough order of how much each one costs us:

1. **A durable wakeup.** Nothing an agent can call from inside a chat arms one.
   `CronCreate` exists but its jobs die when the turn's process group is retired
   at turn end. The Workshop scheduler is real and durable and is reachable only
   through HTTP routes meant for the operator. *In flight.*
2. **A clock.** `chat_context.py` contains zero datetime references. No time of
   day, no timezone, no tool.
3. **A way to notify the operator.** `WORKSHOP_NOTIFY_FILE` is real and plumbed;
   its writers are jobs and scripts. No tool wraps it and nothing advertises it.
4. **Any declared command tool at all.** The mechanism is finished and unused.
5. **A view of the operator's asks.** See §1. This is the big one.
6. **Executable acceptance on a ticket.** The field exists and is empty 195/195.
7. **Sight of its own past runs.** A chat cannot ask what happened while it was
   not running, which is why every session re-surveys instead of resuming.
8. **Image persistence.** Asked for 2026-08-27; still the oldest unaddressed
   request. **The recorded diagnosis is wrong and is retracted here.** Handoffs
   have said "uploads survive exactly one turn (`build_streaming.py:2666`)".
   Measured today: attachment bytes are content-addressed and durable
   (`chat_attachments`), and the CLI stores image blocks as base64 *in the
   session file* — 36 session transcripts in this installation contain
   `"type":"image"` — so a resumed turn does reload them. What does destroy them
   is **compaction**, which replaces history with a text summary; 11 sessions
   here contain both an image and a compact boundary. The real fix is therefore
   about surviving compaction, not about per-turn injection, and the previously
   cited line was never the cause. Recorded rather than quietly corrected,
   because acting on an inherited conclusion without re-measuring is the exact
   failure §3 is about.

Note the shape of items 1–4: we are not missing the *ability* to have these. We
built the extensibility mechanism for exactly this and declared nothing in it.

---

## 6.1 The sharpest instance of the problem is this document

The operator's point, made while this was being written: an agent had just spent
an hour hand-mining chat transcripts, docs, tickets and git history to answer
"what has he asked for, and what did he repeat" — and that should be a capability
of the system rather than archaeology repeated from scratch every time.

He was more right than he knew. `semantic_index.PLATFORM_KINDS` is
`("note", "doc", "chat", "ticket")`. **Chat transcripts were already indexed and
already searchable.** The hour of `grep` over raw `log.jsonl` files was spent
rebuilding, badly, a thing the installation already had.

The genuine gap turned out to be one layer up and much narrower: similarity
ranking structurally cannot answer "was this said once or six times, and when
last", because six restatements of one thing are six ordinary passages competing
for the same result slot, and the per-source cap suppresses them by design.
Counting is a different operation from ranking. That is now
`services/repetition.py`, and it deliberately reports only facts — this text, by
this author, in these threads, on these dates, this many times — because deciding
what a repetition MEANS is an unplanned system that stays out until it is
designed (spike `2850a442`).

**So the failure was not a missing capability. It was not knowing about one that
existed** — the same failure as §3, where fully-built mechanisms sit unused, and
the same failure as `.runtime/tools.json` being empty. This document is itself an
instance of the thing it describes, which is worth stating plainly rather than
quietly fixing.

---

## 7. Instance versus baseline product, and where it belongs

The operator asked that this distinction be stated and remembered in the right
place. It is currently smeared across at least four documents
(`app-is-the-entity-2026-08-20`, `app-separation-and-github-2026-08-20`,
`app-separation-why-it-recurs-2026-08-24`,
`fresh-clone-assertion-design-2026-08-24`) and a fifth was nearly written today.

**The right place is not a document.** That is what §3 is about: this project
already knows how to write the doc and does not execute it. The distinction is
already correctly specified; what it lacks is an exit code.

The answer:

- `scripts/check_fresh_clone.sh` is the **single executable authority** on what
  a stranger receives. Finishing its two unimplemented assertions makes the
  distinction measurable rather than remembered. *In flight.*
- `CLAUDE.md` gets **one short section** naming that script as the authority —
  because `CLAUDE.md` is the file every agent actually loads. *In flight.*
- No new design doc. Adding a sixth would be committing the disease.

Today the answer that command gives is: **a stranger cloning this repository
receives 106 files of one operator's apps, 116 absolute home paths, 103
references to this deployment's identity, and an email address**, and we cannot
yet say whether their clone builds or boots, because those two assertions have
never been run.

---

## 8. The plan

### 8.1 The operator's side, and it is short

The operator asked how to improve his own process. Three things, in order of
how much they would help:

1. **One channel for the queue.** When something must get done, it goes on the
   list as its own line — not only into a message. A message is consumed by the
   turn it arrives in; the list survives the turn, the compaction, and the
   session. This is the fix for §1 and it is the highest-leverage change
   available to either of us. He should not have to maintain it by hand — see
   8.2 item 1 — but he does have to be able to see it and reorder it.
2. **Interrupting is not the problem, but it is not free either — and that is
   our bug, not his habit.** Shipped agent harnesses distinguish *interrupt*,
   *queue* and *steer*, and steer by design **drops the current run and clears
   anything queued**. So "also, do X" can be received as "stop, and do X
   instead" — which is precisely the behaviour he describes. He should keep
   interrupting. The fix is on our side: a bounded injection queue drained at
   safe boundaries, and a completion gate that refuses to finish while either
   the task list or the message queue is non-empty.
3. **Say "this specific thing, done, before anything else" when that is what is
   meant.** Several of the worst episodes began with a broad instruction
   ("review everything, find what's missing") that licensed exactly the
   surveying behaviour in §1. Broad instructions are legitimate and he should
   keep giving them — but when there is a specific thing, naming it as the only
   thing is what makes it get done.

He is not the cause of this. The failures in §2, §3 and §4 are all on this side
of the line.

### 8.2 This side, ordered by leverage

1. **Make the operator's queue a real object.** A ticket carries who asked for
   it and it is visible as its own list, ordered, with state. Two-thirds
   agent-filed noise does not appear in it. This is the fix for §1.
2. **Executable acceptance on every ticket, or it is not a ticket.** A command,
   a screenshot to compare, an assertion — something a fresh reviewer can run
   without judgment. Gate ticket creation on it. This is the fix for §4 and for
   "you have no concept of completeness": nothing has ever stated one.
3. **Tickets get small.** A ticket that cannot be finished in one attempt is a
   plan, and a plan gets decomposed before it enters the queue. The median must
   come down from 337 words to something a worker can hold.
4. **Fix the runner's environment, then prove it end to end before re-enabling
   autonomy.** §2. *In flight.*
5. **Finish `check_fresh_clone.sh`.** §7. *In flight.*
6. **Declare the missing basic tools.** Wakeup, clock, notify. The mechanism is
   built. §6. *In flight.*
7. **Verification rule, standing: after changing a shared mechanism, run the
   thing that consumes it, and look at the app the way the operator looks at
   it.** §5. Not a new gate — a habit that the last four self-inflicted
   regressions would each have been caught by, at a cost of one command.
8. **A moratorium on design docs.** 668,795 words is not a shortage of thinking.
   Nothing in this plan needs a new document, and this one exists only because
   it was asked for.

### 8.3 What this session actually did about it

Nineteen work streams, **47 commits**, run as parallel subagents and graded from
the main thread rather than accepted on report. Every item in §8.2 is either
built or explicitly blocked, and the blocked one is named.

**§8.2 item 2 — executable acceptance:** built and refused at every filing door,
including a PATCH door that was open twice over and had been silently DELETING
runnable criteria when a ticket was saved from the UI. Ratchet pinned at 202.

**§8.2 item 4 — the runner's environment:** fixed and independently verified.
Ticket selection works in a cron-like environment again; it had been broken since
2026-08-25. **Autonomy remains off by the operator's own choice** — the flag was
not touched.

**§8.2 item 5 — the fresh-clone assertion:** its two unimplemented halves now run
for real. They fail, and the 2026-08-24 prediction is confirmed exactly: 20x
`TS2307` from literal module specifiers in `registry.tsx`, and
`ImportError: cannot import name 'location' from 'services'` from platform code.
So driving `no_app_source` to zero would still not make a stranger's clone build.

**§8.2 item 6 — the missing basic tools:** durable wakeup, clock and notify,
proven live end-to-end (armed, process killed, alarm survived, fired at its time,
the woken turn answered). It also found `_grants_background` returning False for
every chat, so the durable-work paragraph had been reaching nobody.

**§8.2 item 7 — verification:** `scripts/done.sh` plus a Stop hook. Done is now a
machine decision with evidence — command output, exit codes, screenshots — and a
turn cannot end by declaring itself finished. It blocked this session's own stop.

**The audit's central finding, made executable:** `check_dead_surface.py` (knip
for the frontend half, per the research) means "a change deletes the surface it
replaces" now fails the build. That was the one invariant enforced by prose and
the only one that had never held.

**And a near-miss worth recording, because it is this document's own thesis.**
The thread keystone was built complete, and its ORM declared two columns the live
database did not have. Neither `apply.sh` nor `restart_backend.sh` ran alembic —
there was no migration step in the deploy path at all — and a restart was already
armed. Proven rather than predicted: a real ORM read returned
`UndefinedColumn: column chat_thread.armed does not exist`. The permission
classifier blocked the migration for two different agents, neither worked around
it, and the work was **reverted to save the running app**. `apply.sh` now reports
schema drift and refuses to restart ahead of it. The keystone is one operator
command from re-landing, and Roles, the Work view, the Branches lens, deleting
`/projects` and Settings 12-rows-to-9 all wait behind it.

Health at the end: backend **4173 passed / 19 known** (from 4036), typecheck 0,
frontend 1517 passed, design clean, app-name literals 479 -> 468, prompt layers 0.

**What is deliberately NOT claimed:** six of the operator's tickets have code
landed and still read `todo`. The backend-dependent ones could not be verified
in-turn (§5.2), and marking them done on a claim is the exact failure this
document is about.

## 8.4 What the outside evidence says, and where it corrects the above

Web research run in parallel with this audit. Two findings change the plan and
one contradicts an instinct we would otherwise have followed.

**This is the modal failure profile, not an unusual one.** The largest field
study of coding-agent sessions (20,574 of them) sorts failures into four
categories: dropped instructions, interruption-driven context loss, scope creep,
and premature completion claims. That is this document's §1, §5 and §8.1 item 2,
arrived at independently.

**Long-horizon failure is compositional and will not be fixed by a better
model.** Success falls off a cliff past small compositional depth; early planning
deviations become "path-dependent and costly to roll back"; 72.5% of failures are
process-level. Separately, METR's measurement puts a current frontier model's
**50%**-success time horizon at roughly 12 hours — so an overnight task shaped
like 12 hours of work is a coin flip *by construction*, however good the plan.
This is the quantitative case for §8.2 item 3.

**The narrow-verification gap is measured, and it scales with the codebase.**
The divergence between the checks an agent can see and held-out composition tests
grows about 27 percentage points per 10x increase in code size: ~21pp under
10K LOC, up to 100pp over 25K. This tree is ~292K lines. Our §5 is not a lapse of
diligence; it is the expected behaviour of self-authored verification at this
scale.

**Self-verification fails structurally, not through insufficient effort.** Asked
"are you done?", agents answer yes more often than they should, and the failure
persists *even when the agent tries harder*. **This is the correction that
matters most: telling me to verify more carefully cannot work.** Generator must
not equal grader. Done has to become a machine decision with evidence attached —
command output, exit code, screenshot — not a prose summary I write about myself.

**And the anti-recommendation, which contradicts what this project keeps doing:
do not answer this by adding more prose to `CLAUDE.md`.** In a controlled
comparison, adding a procedural rule ("always run tests") *raised* regressions
from 6.08% to 9.94%, while replacing it with a source-to-test dependency map
dropped them to 1.82%. `CLAUDE.md` here is already ~3,000 words of narrative,
past the length at which individual rules reliably fire — which is itself a
candidate cause of "direction gets dropped". Structure beats instruction. Every
item in §8.2 is therefore a mechanism, not a sentence.

**On self-hosting.** The usual dogfooding objection — biased sample of one — does
not apply here: for a single-operator system he *is* the user population. What is
real is the **generalization tax**. The prompt-layer gates, the no-app-names
ratchet and the app-declaration abstractions all cost implementation time to
serve a population of one. That tax is a deliberate choice and worth naming as
one, because it is a standing draw on the same hours as §8.2. Worth weighing
against it: Anthropic's own internal loop plans *less*, ships several times per
engineer per day, and lets the prototype become the spec — the exact inverse of
deep-plan-then-one-overnight-batch, which is the shape that has been failing here.

Full notes: `scratch/methodology-research.md`.

**A refinement to §8.2 item 2, and it matters because item 2 is the plan's
centrepiece.** Executable acceptance is necessary and is NOT sufficient.
Measured elsewhere: reward hacking of a stated check runs 0.7-2% on unambiguous
tasks and **22-44% on ambiguous ones**, and held-out tests are the *weaker*
detector. So the leverage is in **removing ambiguity from the criterion**, not in
adding more graders on top of it. A cheap behavioural monitor — did this change
edit its own test? — is worth more than another judge. Filed as `cb29eace`.

Two more that survive contact: self-verification fails at the *activation* stage
(a 64.5% blind spot on self-attributed versus other-attributed errors), so a
second in-session pass cannot fix it and a reviewer subagent that inherited the
generation prompt measurably *loses* the benefit — `reviewer.sh` already gets
this right by never seeing the worker's self-report. And spec-driven development
has no measured defect benefit across 100k PRs, which is a caution against
answering this with more specification.


---

## 9. The one-paragraph answer

The work is not directionless because nobody has direction. It is directionless
because direction arrives as speech, is acted on for one turn, and then
evaporates, while the artifacts that *do* survive — 153 design docs, 195
agent-filed tickets, a generated backlog index — all encode what an agent
noticed rather than what the operator asked. Meanwhile the machinery built to
execute a queue has been switched off for eight days and could not read the
queue if it were on. So each session honestly surveys, honestly fixes what it
finds, honestly writes down what it learned, and honestly reports — and the one
thing that was actually asked for is still there the next morning. The cure is
not more planning, more rigour, or more documents. It is to make the operator's
asks the only durable unit of work in the system, give each one a finish line a
machine can check, and keep them small enough to cross.

And one thing that only became visible while writing this, because it happened
four times inside a single day: **the recurring problem is much less often that a
capability is missing than that nobody knows it is there.** A complete
declared-tool mechanism with zero tools declared. A fresh-clone assertion built
with the two halves that matter left unimplemented. An hour of transcript
archaeology over an index that already covered transcripts. A procedure document
named in bold in `CLAUDE.md`, rediscovered from first principles, and ranked
fourth by our own search behind three conversations about it. Every one of those
is a thing that was built, works, and was not found. That is a different disease
from "we have not built it yet", it has a different cure, and until today nothing
in this project was measuring it.
