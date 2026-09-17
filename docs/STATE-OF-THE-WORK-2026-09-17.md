# State of the work, 2026-09-17

**What this is.** Ben asked for an updated report covering roughly the past
month: what he has asked for, what was specified, what was built, what is left,
and which of the remainder still needs a decision rather than an implementer.
Written 2026-09-17 against this tree, this ticket store and this chat store.
Every number below names the command that produced it, because the last document
of this kind (`platform-redesign/WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md`)
found that unsourced status claims were themselves a major failure mode.

That document also recommended a moratorium on new design documents. This is not
a design document and adds no plan. It is a status read, and it exists because it
was asked for.

**Window:** 2026-08-16 to 2026-09-17.

---

## 1. The numbers

Commits, `git log --since=2026-08-16 --oneline | wc -l`:

- **962 commits** in the window, heaviest in weeks 34 to 36 (398, 269, 274).

Tickets, swept across every scope through `routes.tasks_all.scope_roots` with an
owner pinned (a bare `SessionLocal()` reads empty under row-level security, see
the repo rules):

- **572 tickets total**, of which **249 done**, **209 todo**, **71 cancelled**,
  17 blocked, 15 in review, 9 parked, 1 doing, 1 draft.
- **491 of the 572 are in the `development` scope.**
- Open tickets in `development`: **238**. That was 196 four days ago and 195 on
  2026-09-01. The backlog is growing, not shrinking.
- **220 of the 238 open tickets have never been attempted.**

Acceptance criteria, which the 2026-09-01 document measured at **0 of 195**:

- **99 of 238 open tickets now carry executable acceptance.**
- Split by age: of tickets created in September, **27 of 28 have acceptance**. Of
  those created in July, 1 of 16.

That last split is the single clearest sign of a fix working. The gate on ticket
creation is real: I tried to file a ticket without acceptance during this session
and the store refused it.

---

## 2. A data-quality warning that changes how to read the ticket store

**171 done tickets share the identical `updated` timestamp
`2026-08-30T00:38:55`.** That is one bulk write, almost certainly the store
migration that ticket "Stop storing text beside title and body in the ticket
store" describes. It overwrote the completion date on every row it touched.

So: **the ticket store no longer knows when most work was finished.** Any report
that groups completions by `updated` will show a false spike on 2026-08-30 and
will misattribute July work to late August. This document does not use that
field for dating.

The real dates partly survive in each ticket's `evidence` entries, which carry
their own timestamps (`{"ts": "2026-07-15T13:32:54+00:00", "ref": "Done in
f031b18: ..."}`). **157 of 249 done tickets have evidence.** The other 92 have no
recoverable completion date at all.

Worth fixing, and not yet ticketed as of this writing: a `completed_at` that a
migration cannot clobber.

---

## 3. What actually shipped in the window

Grouped by theme, from ticket titles plus commit history.

**App separation, which is the oldest standing mandate.** This is the one Ben has
raised most often outside of chat defects, and in this window it moved further
than in the previous two months combined. Between 2026-09-04 and 2026-09-05, **all seven
apps were extracted from the platform repo**, one ticket each. The extractions
were not uniform: one carried a 15-module package plus a native widget, one three
payment modules and a webhook, one a standalone frontend build, one a pair of
modules whose removal broke `import main` until it was untangled, and one shipped
its own detection dataset. Alongside them: the apps' CSS left the `@workshop/ui`
kit, the operator's app documentation left the platform repo, ten app modules
hiding behind platform names were renamed, and `scripts/check_fresh_clone.sh` was
built as the executable definition of "done".

(The apps are deliberately not named here. A document under `docs/` is tracked
platform source, and naming one is the exact thing the mandate forbids.)

The honest status, per the repo rules and re-verified this session:
`check_fresh_clone.sh` exits 0, so **a stranger's clone is clean**. The
**repository is not**: git history still reaches the operator's `commute/*.json`
home and work addresses and the Plaid MCP server (ticket `43dec530`), 16
app-specific variables sit in platform env files and every `apps/*/app.env` is
inert (ticket `ed66df46`, currently blocked after 3 failed attempts), roughly 350
lines of platform prose name an app, and `frontend/src/api/schema.gen.ts`
regenerates about 124 app-route lines by design.

`check_no_app_names.py` reports 23 sites. Per the repo rules **all 23 are false
positives**: each is a platform field or route whose ordinary English name
happens to collide with an app's name (a token allowance, a readiness-probe
field, an iOS background mode, a redirect). So 23 is that gate's floor, not
remaining work, and driving it to zero would mean renaming platform fields.

An illustration of how easily this regresses, from writing this very document:
the first draft listed the seven extracted apps by name in the paragraph above,
and `check_no_app_names.py` went from 23 to 26 on the spot. A document in
`docs/` is tracked platform source. The count below is back at its floor because
that sentence was rewritten, not because the gate was re-pinned.

**Security audit.** The whole SEC-* batch closed: docker-socket root equivalence
(C1), unauthenticated redis on all interfaces (C2), the public-edge blocklist
typo exposing `/auto-pr` (H1), binding the backend to loopback (H2), the
unauthenticated :9000 control panel (H5), the mobile WebView bridge (H6),
fail-closed registration (M4), the egress guard extended to WebFetch (A3),
recursive claude spawn clamping (A4), and an execution boundary for the autonomy
loop (A1). Six SEC items remain **parked as "host administration; not
agent-executable"**: they need root and therefore need Ben.

**Memory.** The MEM-1 to MEM-19 batch is done. Memory became one page instead of
a four-way tab switch, notes and files share one header and editor, the file tree
gained a caret and active-path mark, `.runtime/learnings` folded into the note
store, wikilinks survive a retitle, and the note collector stopped globbing one
level deep (which had made 195 of 196 notes unsearchable).

**Chat and turn machinery.** Nine turn-state booleans collapsed into one turn
lifecycle; compaction state is published on the thread so clients can see it;
chat turns are metered into the usage ledger; who sent a message is recorded on
the message and on the wire; per-thread chat mode (orchestrated vs direct)
landed; empty turns (26% of prompts returning no assistant text) were reduced.

**Platform hygiene.** `/runs`, `/changes` and `/deploys` deleted with their 17
links retargeted; the deploy subsystem deleted, having never run once; the
dashboard rebuilt as the four-card grid; one file API replacing four that
disagreed; the `dev_only` migration finally applied and six alembic heads
collapsed to one.

**This session (2026-09-13 to 17).** The apps-404 outage (all eight apps serving
404 for two days, caused by an editable install baking an absolute path into the
venv) was diagnosed and fixed; `/healthz` now publishes what the process actually
mounted. A model-resolution consolidation and a local HTTP model provider landed.
`wsplatform/paths.py` was made a genuine leaf, taking the largest import cycle
from 107 modules to 82 and closing two smaller cycles.

---

## 4. Ben's actual queue, measured

From `mcp__workshop__search` with `repeats: true`, which counts what he has said
more than once. This disagrees sharply with ticket priority and is the better
signal. Fifty-one repeated statements exist; the top clusters:

1. **Chat message ordering. Said 24 times, on 17 occasions, across 15 chats,
   2026-07-25 to 2026-09-04.** His new message renders above the "working"
   system message and still shows unread. This is the single most-repeated thing
   he has ever said in this system. Ticket: "Messages sent during a running turn
   render out of order in the transcript" (urgent, **still todo**, filed
   2026-08-28).
2. **"Be the orchestrator, work in parallel in worktrees, finish things." Said 17
   times, 7 occasions.** Includes the app-generalization mandate and the
   complaint that agents repeatedly claim it is done.
3. **Chat card message preview truncated unnecessarily. Said 8 times, 7
   occasions**, 2026-07-25 to 2026-08-27.
4. **Compaction and input-box behaviour. Said 5 times, 5 occasions.** Says
   "working" instead of "compacting"; the expanding input covers the bottom of
   the transcript and the scroll-to-bottom logic hides the last message.
5. **The in-chat options/decision card is broken, showing raw JSON. Said 6 times,
   4 occasions.**
6. **"The development app takes me to the wrong chats page, an old one that
   should not exist." Said 5 times, 4 occasions**, 2026-08-26 to 2026-09-02.
7. **Chat auto-naming is inconsistent and sometimes does not run. Said 5 times, 4
   occasions.** Two tickets exist for it; both open.
8. **Sent text remains in the input after submission, sometimes for days. Said 5
   times, 3 occasions.**
9. **Pencil/rename icon sizing and title padding in the chat toolbar. Said 3
   times, 3 occasions.**

Items 1, 3, 4, 5, 6, 7, 8 and 9 are all chat-surface defects, all open, and
several are more than six weeks old. **The most-repeated complaints in the system
are almost entirely in one surface, and that surface is the one he uses to talk
to it.**

---

## 5. Where work is stuck, and the four different reasons

Reading `blocked_reason` on every non-todo open ticket separates four distinct
failure modes that all currently look the same in a list.

**(a) Finished but never certified or merged. Nine tickets.** Their blocked
reason says the work landed. Examples: "reviewer approved; the work is committed
at 8c9a64c0 and not yet merged to main" (queue planner); "Landed in 7562a719;
app_id written at creation" (project chats not appearing on the project's Chats
tab); "Both halves fixed and committed in c31f3f0f with failure-first tests" (one
failed database probe hiding every app). This is completed work that never
reached him. It is the cheapest category to clear and the most misleading to
leave.

**(b) The worker tried three times and gave up. Ten tickets**, all reading
`incomplete (rc=2) after 3 attempt(s) (cap 3)`. These include real and important
items: a project chat setting its own repo, the completion gate certifying a
ticket whose criteria were already green, a killed worker's committed work being
graded as a failed attempt, one model-spawning path skipping the sandbox, a
confined turn being able to write hooks that run outside the sandbox, and the 16
inert `app.env` files. These need a person to look, not another attempt.

**(c) The ticket's premise is false and it should be cancelled, not blocked. Four
tickets.** "Remove the inert Thought fold" is annotated **"KEEP IT. The ticket's
premise is false and deleting would destroy a working feature."** "Move the
PROJECT.md decision log into its own DECISIONS.md" conflicts with the settled
"decisions get one home, in notes" directive. "Inline only the PROJECT.md brief"
is void because `live_context()` no longer inlines PROJECT.md. These are clutter
that makes the backlog look worse than it is.

**(d) Needs Ben specifically. Seven tickets.** Six SEC-* items marked "host
administration; not agent-executable" (they need root: sshd password auth,
loopback-binding ComfyUI and Ollama, Samba anonymous restriction, the NOPASSWD
sudo git, IPv6 firewall verification). Plus "Show who holds the tree, and let the
operator take it back without losing work", explicitly **"Blocked on an operator
decision, not on engineering."**

One more worth calling out on its own: **"Finish generalizing the platform: zero
app names in platform source" is blocked with reason `criteria_were_already_green`.**
Its three acceptance criteria all pass, so the completion gate refused to certify
it. The mandate Ben has chased longest is stuck because it succeeded in a way the
gate cannot express.

---

## 6. The execution machine is off

- `systemctl --user list-timers`: **0 timers.**
- Last autonomous sweep iteration: `.runtime/ralph-iter-785537-1.out`, **2026-08-25**, and it is zero bytes.
- `provision_worktree` is called from `scripts/project_ralph.sh` and nowhere else.
- The interactive worktree trigger has never fired in production, because
  `_auto_create_worktree_for_chat` is gated behind `WORKSHOP_TREE_LEASE_MOUNT`,
  which is not set in this instance's config.

So the worktree machinery is **built and proven** (six linked worktrees under
`branches/development/` from 2026-08-22 and 2026-08-23, all merged to main, the
full loop having run six times) and **nothing has used one since 2026-08-23**.
The gap is routing, not plumbing. Arming the flag requires writing to
`~/.config/workshop/instance.env`, which is on a read-only mount from an agent's
view, so it is Ben's to set.

Two tickets in review would restore scheduling: "Run the trigger dispatcher from
the backend, because this host has no crontab" (`152856ba`) and "Run the bare
ratchet checks on a schedule so an upward drift is caught the day it lands"
(`0f2f3fa6`).

---

## 7. Specified and buildable now, no discussion needed

These have a design of record and an implementer could start today.

- **Chat message ordering** (the 24x complaint). Ticket exists, urgent, untouched.
- **The chat-surface cluster**: preview truncation, "compacting" vs "working",
  input-box scroll, stale input text, rename-pencil sizing. Small, individually
  specified, repeatedly asked for.
- **The broken in-chat decision card** showing raw JSON.
- **The development app landing on an old chats page that should not exist.**
- **Chat auto-naming** consistency and the racy dev-scope default.
- **Clear category (a) above**: nine tickets whose work is already committed.
  Merge, verify, close.
- **Cancel category (c)**: four tickets with false premises.
- **Chat storage stages 2, 3 and 4**: serve transcript reads from rows, delete the
  three backend reconstructions of message order, retire the JSONL log. Stage 1
  is done and the plan is written.
- **Retire the legacy `/agents/build/{slug}` surface**: 20 live routes, 18 called
  by `AgentBuild.tsx`. Target named in `app-is-the-entity-2026-08-20.md` section 5.
- **Merge the two app registries** into the one table that already exists (Stage A
  is built; the registries are reconciled but not retired).
- **A `completed_at` field** that a store migration cannot overwrite (section 2).

---

## 8. Still needs a decision from Ben

These are blocked on judgment, not effort. Each is quoted or summarised from his
own words so the question is his, not a re-derivation.

**8.1 Worktrees, branches and leases: the interaction model.** This is the
largest open design question and he has described it at length (2026-08-28 and
2026-08-29, four occasions). His position, compressed: a worktree and a branch
should present as one thing by default, not three separate concepts to manage;
branches named as hex strings are wrong; "switch to any branch" presented as a
casual dropdown action is wrong; a lease should govern who can work, but **a
direct user message in a user-operated chat should override or take over the
lease**; and possibly the Chats page becomes worktree rows containing chats. He
explicitly did not want this decided unilaterally. Design doc:
`platform-redesign/worktree-as-the-place-2026-08-28.md`.

**8.2 Autonomy: what it is allowed to do before it is switched on.** Said 4 times
across 3 chats. His concern is that turning it on would "blindly execute all of
the past tickets". With 209 todo tickets, 220 never attempted and 71 already
cancelled, that concern is current. Needs: a policy for what an autonomous worker
may pick up, and what happens to finished work (merge automatically, or queue for
review, and at which level that policy is set: global, per project, per ticket,
per agent).

**8.3 Environment identity and prompt layering for non-development scopes.** His
words: "if all non-development-scope agents/chats have no awareness of
environment, that seems right, right? ... development chats/agents can know about
the workshop code and development/production builds and other branches, but
regular users NEVER have this." He asked for web research plus existing docs
before a proposal, and specifically objected to an agent deciding this alone.

**8.4 The app-agent access boundary.** The repo rules state the requirement as
making the mistake *impossible rather than detected*: an app's agent has no
access to platform source and a platform agent has no app source. Option not yet
chosen; section 10 of `app-is-the-entity-2026-08-20.md` holds the candidates. The
end state deletes `check_no_app_names.py` rather than extending it.

**8.5 Merging chats, agents and subagents into one concept.** Filed as an urgent
operator ticket 2026-09-04, still todo, no design doc. He has referred to it
repeatedly as a direction ("which again we are going to be merging into a single
concept of threads and also sub agents or whatever you choose to do") without it
ever being specified.

**8.6 Operator-intent.** Ticket "Research and design the operator-intent concept
before anything implements one" (urgent, todo, 2026-09-01). Explicitly research
first.

**8.7 Six SEC items needing root**, listed in section 5(d), and
**`WORKSHOP_TREE_LEASE_MOUNT`**, which unblocks the interactive worktree path.

---

## 9. The pattern worth naming

The 2026-09-01 document's central sentence was: "this system has very high output
and almost no allegiance to the operator's queue, because the operator's queue
does not exist as an artifact anywhere in it."

Sixteen days on, that is partly fixed and partly not.

**Fixed:** acceptance criteria are now enforced at creation (27 of 28 September
tickets carry them, against 0 of 195 before). The repeat-counter exists and
surfaces his real queue. App separation moved from perpetually-claimed to
executably-verified.

**Not fixed:** the open backlog grew from 195 to 238 in the same period, 220 of
those have never been attempted, and the top nine most-repeated complaints are
all still open. Nine tickets contain finished work that never reached him. The
scheduler is off and no worktree has been cut in three weeks.

The throughput is real and the direction is right. What is missing is the last
mile: finishing, certifying, merging, and closing the loop back to the person who
asked. Categories 5(a) and 5(c), plus section 7, are that last mile, and together
they are perhaps two days of work that would change what the backlog looks like
more than another month of building would.
