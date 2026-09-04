# Handoff, 2026-08-14: autonomy, chat storage, and the tickets in between

Status: **current.** Written at the end of a long session so a fresh context can
pick this up without re-deriving anything.

Read this first, then the two plan docs it points at. Do not read the older
dated handoffs for restart or build instructions; `docs/applying-changes.md` is
the only current source for that.

## The one-line summary

The autonomous loop **has never been able to write a file**, for a reason that
is one flag wide and one product decision deep. Everything else here is either
what led to finding that, or what was fixed on the way.

## Three threads ran in this session

1. **Chat storage migration** (stage 1 shipped earlier, stage 2 reverted here).
2. **The composer bug**, reported eight times, now has a test.
3. **Autonomy**: why the queue does nothing, traced to the bottom.

They are related only by having happened together. Take them separately.

---

## 1. Chat storage: one store, Postgres

Plan: `docs/platform-redesign/transcript-read-from-rows-2026-08-14.md`. Four
stages, stage 1 shipped in `0fc7088`.

**Stage 2 was reverted in `cac00c7`, and the code is gone.** Not stashed, not on
a branch. Only the plan survives, so stage 2 is a rewrite.

**Why it was reverted, because this is the lesson.** Stage 2 has two halves that
must move together: the backend serves rows, the renderer reads rows. Both sat
**uncommitted in the shared working tree**. `apply.sh` builds and restarts from
disk and any agent may run it at any time. One did. The new backend went live
against a bundle built before the renderer matched, the positional markers the
renderer splits replies on were gone, and the operator's mid-run message
rendered above a half-hour block of work.

The operator's reaction is the thing to remember: he had reported that same
symptom repeatedly before, and this time it genuinely was ours.

**Consequences already landed:**

- `f12e306` (earlier) made `apply.sh` refuse to publish a bundle stale only
  because of uncommitted UI work.
- This session added the **backend half of that same rule**: a stale backend now
  names the uncommitted files and skips the restart, with `--publish-wip` to
  override. It has already caught two other agents' in-flight work.
- Stage 2's ticket requires it to land on a branch, both halves in one commit.

**The tree has drifted since stage 1.** `backend/db/chat_verify.py` reported 63
of 63 clean when stage 1 shipped and **67 of 70 within hours**. Three messages:
one with no row at all, two assistant rows whose `turn_id` was never filled.
Both restarts today line up with the timestamps, so the hypothesis is a turn
whose close path does not complete. **Confirm it, do not assume it.** Run it
with `PYTHONPATH=.` from `backend/` and `WORKSHOP_DATABASE_ADMIN_URL` sourced
from `~/.config/workshop/db-admin.env`. Note `run()` has no per-thread
targeting, so `--rebuild` rebuilds every drifted thread, which is unsafe while a
turn is live in one of them.

That drift is why stage 2 is blocked on the mirror ticket: moving reads onto
something that can silently miss a row turns a lost background write into a lost
message on screen.

---

## 2. The composer bug

Eight operator reports between 2026-07-31 and 2026-08-14. Five separate fixes,
each verified by reading the source, the source correct each time, the symptom
back each time, and **never a test**.

`scripts/smoke_chat.sh` now drives the built bundle in a real browser and
asserts the invariant rather than a code path: after a send the box is empty and
stays empty, past the editing window, across a refocus, across a reload, in a
second tab, and on the first message of a new chat. Those are the five ways it
has actually come back. It intercepts and fails the send on purpose, so the box
must empty because send was pressed rather than because the server agreed. It
writes no message into any real conversation and exits 3 when it cannot run.

It passes. If the symptom returns while that script is green, **the script is
wrong and that is the thing to fix first.**

Also corrected: the knowledge store had been telling every agent since
2026-08-02 that this was "fixed via clearNow", a function deleted on 2026-08-12.
That is why each attempt looked somewhere else. The note now says what is
actually true and ends with "do not add a sixth fix on the strength of reading
the source".

---

## 3. Autonomy: the main finding

### It could never write a file

`scripts/ralph.sh` invokes `claude -p` with **no permission mode**. The CLI
takes its default, which can stop and ask, and nothing unattended can answer.
`backend/services/model_caps.py` already states the consequence: every mode
except `bypassPermissions` needs an approval channel, and without one the CLI
"silently auto-denies every gated call, which is the worst available outcome".

Measured in the first end-to-end tier-1 run: asked to create a one-line file,
the worker tried `Write` three times and a Bash redirect once, was denied every
time, wrote "All write operations require user permission approval ...
contradicts the autonomous operation model" into the project TODO, and exited
claiming completion. **So every previously green thing about this loop was
measuring the part before the writes.**

### What the same run proved DOES work

All of it previously untested, and all of it good news:

- the reviewer gate **refused** a task the worker declared complete
- the no-progress detector **parked** it instead of looping
- nothing was falsely marked done
- selection fails closed **three separate ways**: `execution`, stated
  boundaries, stated objective

### Fixed on the way

`--dir` was used as a relative path while `ralph.sh` cds into the tree it
drives, so it meant two different directories before and after the cd. Symptom:
three attempts in five seconds, every path "No such file or directory", no
worker ever started. Both entry points now resolve it absolute (`7eb84c1`).

### The plan for the rest

`docs/platform-redesign/autonomy-execution-boundary-2026-08-14.md`, written this
session, **uncommitted at handoff time**. It covers four permission options
(`bypassPermissions`, `acceptEdits`, `dontAsk`, a programmatic approver), the OS
sandbox, and the Claude API paths, with a three-phase recommendation.

Two things in it that a fresh context should not re-derive:

- **We already have a working programmatic approver.** `build_streaming.py`
  drives the chat's own `claude -p` with `--permission-mode auto
  --permission-prompt-tool stdio` and answers the control requests. Its comment
  records verification against CLI 2.1.143, which is the version installed. The
  chat has had an approval channel all along; the autonomy loop never got one.
- **Every sandbox dependency is already installed**: `bwrap`, `socat`, `rg`.

**Measured, not cited:** `claude -p --permission-mode acceptEdits` was asked to
create a file in the smoke project and did, cleanly. That is the same request
that produced four straight denials with no mode set.

**NOT verified, and phase 1 must verify it:** that `acceptEdits` refuses writes
*outside* the working directory. The probe for that was blocked by the auto-mode
classifier before it ran, which is reasonable. If that containment does not
hold, option B collapses into option A and the recommendation changes.

---

## Tickets filed from this chat

Six, all `execution=code`, all linked to thread
`4102a2fec0384067887d76c5af007201`:

- `5f0cf756` p70 — rebuild chat branching to the standard model (operator's own
  spec: rewind here rather than before, no toolbar surface, count pill, modal)
- `97b0f04a` p60 — make the chat mirror unable to drift, repair the three
- `86f58aea` p60 — pin the backend-to-renderer seam with a contract test
- `1638d7f9` p50 — stage 2, blocked on the two above
- `37617b0a` p40 — stage 3, blocked on stage 2
- `e1f9d92a` p30 — stage 4, blocked on stage 3

### Two CLI bugs that made ticket filing useless, both fixed in `531abfb`

- `project_tasks.py` hardcoded `"ref": None`, so `--source chat` recorded the
  *kind* and threw away *which* chat. That one line is load-bearing:
  `routes/tasks_all._origin_for` resolves it into the ticket page's link back to
  the conversation, and `routes/jobs._task_context` uses it to give a
  queue-started run its chat. Without it a chat-filed ticket was a dead end and
  so was every run from it. There is now a `--source-ref` flag.
- `tasks.add` has always accepted `execution`; the CLI never passed it. Since
  unattended selection fails closed on it, **every ticket an agent filed through
  this CLI was permanently ineligible for the queue.** Measured: six tickets at
  p70/60/50 invisible while the queue offered a p3 one. There is now an
  `--execution` flag.

### One thing to watch

Something scanned this chat and filed five *draft* tickets duplicating the six
above. Dedup did not catch them: the paraphrases score 0.76 cosine, below even
the 0.80 "related" threshold, so it behaved correctly by its own rule. The
embedder is fine (`ollama:bge-m3`). They were cancelled with the reason
recorded. The real fix is that a scan should skip work already filed **from that
same thread**, rather than relying on similarity.

---

## Open decisions that are the operator's, not a worker's

1. **The permission mode for the autonomous loop.** The one-line fix is
   `--permission-mode bypassPermissions`. It is documented at the call site in
   `ralph.sh` and deliberately **not applied**, because SEC-A1 records that an
   autonomous turn already runs as <service-user>, in the docker group, unsandboxed,
   with full tree write and the OAuth token, and that flag is what makes it
   reachable. The auto-mode classifier also blocked the shell the moment the
   file contained it, which is the same objection.
2. **Whether the Claude API path is open at all.** Managed Agents and
   self-hosted sandboxes both solve the execution boundary properly, and both
   require `ANTHROPIC_API_KEY` and per-token billing. The user-scope rules say
   subscription only, never that key. So it is a change of auth posture and a
   new cost line, not a technical detail.
3. **Turning the runner on.** `queue_runner_watchdog.sh` is commented out in
   crontab and the commented line lacks its tree argument. Both trivial. Do not
   enable until tier 1 passes: a resident loop turns one bad run into a
   repeating one.

## State at handoff

- **Backend**: healthy, restarted 18:49:52 PDT.
- **Uncommitted and mine**: only
  `docs/platform-redesign/autonomy-execution-boundary-2026-08-14.md` and this
  file. Everything else dirty in the tree belongs to other agents (knowledge and
  notes routes, `useTurnStream`/`streamWire`).
- **`apply.sh --check`**: reports drift, correctly skipping the bundle because
  of another agent's uncommitted UI work. Nothing of mine needs applying.
- **Smoke project**: `projects/autonomy-smoke/` still exists, gitignored, its
  own git repo. One ticket parked (the permission denial), two todo. Recovery is
  `rm -rf projects/autonomy-smoke`. Keep it; phase 1 re-runs tier 1 there.

## Where to pick up

1. Get a decision on open question 1. Nothing else in autonomy moves without it.
2. Then phase 1 of the execution-boundary plan, whose first job is verifying the
   `acceptEdits` containment claim that could not be tested here.
3. Independently of autonomy, ticket `97b0f04a` (mirror drift) and `86f58aea`
   (seam test) are both unblocked and are what make stage 2 safe rather than
   hopeful.
