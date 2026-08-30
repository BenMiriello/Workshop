# Review: work patterns, 2026-08-19 to 2026-08-29

Scope: 104 chat threads under `.runtime/users/6/chat/` modified since 2026-08-19
(267 messages in the operator's `role":"user"` slot, of which about 233 are his
own words and the rest are orchestrator relays into worker chats), 500 commits,
87 new design documents under `docs/`, and the note store at
`shared/knowledge/development/`.

Method note on quotes: everything in quotation marks below is the operator's own
text from a transcript, a commit body that cites him, or a note that cites him.
Thread ids are the first eight hex of the directory name.

Method note on supersession: several decisions in this window were reversed on
purpose (worktree grouping, the dock listing every app, the app-vs-project split,
the three-mode chat enum). Those are marked as deliberate and are not counted as
failures. What is counted is a decision he made that was then not applied, or
applied and then quietly undone.

---

## 1. What the operator actually keeps asking for

Ranked by how many separate times he had to say it. Counts are of distinct
messages in the window, not of threads.

### 1.1 "Do it. Do not stop one step short." (48 messages)

The single most repeated instruction, and the one he ends the window on.

- 2026-08-21, `aa52bae4`: "as soon as you figure it out, just design the fix and
  implement the fix. I just want you to fix this all. Don't delay or wait or wa
  wait for me."
- 2026-08-20, `1bb046be`: "Do not take shortcuts, do not defer things. Don't do a
  half-assed fix, just do the proper design fully, remove the bad design. Give us
  the proper design. I don't know how else I can say this more clearly. I want
  you to actually do it."
- 2026-08-26, `fbefaa35`: "remember when i said to build it? what happened to
  that? ... whatever you do you cannot just 'file it' then forget about it that's
  unacceptable. you do it!"
- 2026-08-29, `50cf71c1`: "why do i see no changs? you do all this thinking work
  and planning and seem to be burning tokens o n other agents but nothing is ever
  done. every day i come here and say to get things done and build it all out yet
  where are the results? what are you doing bro?"
- 2026-08-29, `50cf71c1`: "so who has oversight/ownership of this? why am i here
  hounding you very day to finish/act on what im saying? i know some of it isnt
  actionable yet but a whole ton of it is"

### 1.2 "It is still broken. I have told you this before." (57 messages)

Not one bug. The same class of report across the whole window: he reports a
symptom, is told it is fixed, and sees it again.

- 2026-08-19, `5d4567bb`: "the message sequencing issue, which is something that
  just comes up over and over and over and over and over and over again. No
  matter how many times I tell you to fix it, there's always something that you
  get wrong or don't fix."
- 2026-08-20, `61151b7b`: "Hey, I'm gonna bring up for like the fucking fifth time
  or something, that when I am in a chat and I send a message, the message will
  show at the bottom of the chat field, and then once it is read it w it will jump
  up to the top. ... Why the fuck aren't you fixing this?"
- 2026-08-21, `61151b7b`: "bro it's still fucking jumping. look into it again. idk
  what else to tell you. i'm in firefox. i just tested in safari and it's still
  happening. no more excuses"
- 2026-08-23, `203c8692`: "I've requested this at least 4x in separate chats now
  and still not fixed! wtf! fix this! dont make me ask again!!!"
- 2026-08-23, `1a16f117`: "continue. still broken. you failed. do it actually
  fully and right."

### 1.3 "Research first, use the standard thing, do not invent." (34 + 25 messages)

He asks for prior art before design in 34 messages and explicitly asks for the
conventional answer in 25.

- 2026-08-21, `5b651cb0`: "yes do it. no delays. but do research think and plan
  before acting."
- 2026-08-22, `4102a2fe`: "You're right but don't rush to fix! Read research into
  it more and think and plan and design THEN act and do all this"
- 2026-08-25, `1ebc303f`: "Why do we have to test if Claude P has compaction? Why
  aren't we just web researching this? Surely this is documented and answerable."
- Standing instruction, quoted by the orchestrator into three worker chats on
  2026-08-29 (`dfd97edc`, `0380f4c3`): "For almost every problem that comes up I
  just want basically the standard thing that other people are doing to solve
  such problems and do not overcomplicate it."
- 2026-08-30, `50cf71c1`: "nobody needs to decide what a compaction should keep.
  just use the claude compaction! we dont need to decide/design it!"

### 1.4 "Fix it globally, not where I pointed." (28 messages)

- 2026-08-23, `203c8692`: on error-bubble overlap, "i keep pointing this out and
  saying we have to change the general style rule so this isnt a
  custom/repeated implementation/fix since there keep being new conditions where
  this overlap happens. should never happen. not here, not anywhere."
- 2026-08-27, `05496adf`: "there's a global styling issue that's here too and i
  want to fix it globally: for any label/header for a section, it seems the
  spacing BELOW the header/label is often greater than the spacing ABOVE ...
  fix that globally and it'll fix it here too."
- 2026-08-29, quoted onto ticket `c922383b`: "when I say to solve things globally
  i mean it - and I trust you to follow my instructions when I'm clear so it's
  very annoying to have to hound you to actually do what I say like this."

### 1.5 "Design for production and for other users, never for my machine." (23 messages)

- 2026-08-20, `5b651cb0`: "don't look at my current system. Think about designing
  a system that's gonna run in production. This applies to everything, you need to
  be ruthless and comprehensive."
- 2026-08-20, `07182c3c`: "you started saying that like the file system is EXT4,
  which has no ref link, and we're 97% full on the storage. I hate this
  reasoning, it makes no sense. Completely has zero bearing on like the design
  decision."
- 2026-08-26, `703d9160`: "remember not just to solve/design for claude -p! we're
  designing mostly for claude api usage as the main product/system but using
  claude -p in development!"

### 1.6 "I asked for X and you built Y." (17 messages)

- 2026-08-23, `22cea35b`: on the unsend control, "there's a custom unsend last
  button which is specifically not what I asked for. Specifically what I asked
  for is for that to be the unsend button with just an icon I can undo icon in the
  bottom right of a chat message ... I specifically said this already so the agent
  that implemented it just didn't fucking do that and did some motherfucking thing
  that I didn't want."
- 2026-08-23, `203c8692`: "when did we add to the chat a button to 'show earlier
  steps' or something ...? I NEVER wanted or asked for this ONCE!"
- 2026-08-27, `05496adf`: "having the caret be ALL the way over to the right is odd
  and NOT what i asked for."
- 2026-08-29, `96ac0d3c` ticket `17695de5`: "wait wtf why are 'files' in chats
  now?... those are artifacts from chats... i never said to do that or asked for
  that."

### 1.7 "No explainer text in the app." (10 messages)

- 2026-08-20, `b9eec3ba`: "I fucking hate the way you go, like this is a search
  filter, not a lock. The way you write is so fucking annoying."
- 2026-08-25, `65838049`: "I hate this, it makes no sense. I fucking always hate
  it when you add like extra explainers. Instead of just making a clear design
  that explains itself, you always add extra text and explainers all over the
  place."
- 2026-08-27, `05496adf`: "why do you always add explainers like this like a note
  to me but IN the app/ui itself. you always do this and i always hate it and have
  to catch it and make you remove it. i never ask for this kind of thing."
- 2026-08-29, ticket `1fc1ee58`: "the application itself is not a place for you to
  leave notes to me! how many times and how many ways do i have to tell you to
  never ever do this again?"

### 1.8 "Delete old code. No deprecation layer." (8 messages)

- 2026-08-23, `dbd87527`: "omfg how do we still have letta clutter lying around?
  ... how is there so much junk old code and implementation still lying around?"
- 2026-08-26, `05496adf`: "also why is there still the OLD chats page if you built
  the new one? wtf? so you built a new one and KNEW you didnt delete the old?"
- 2026-08-30, `50cf71c1`: "why do we have this garbage code laying around to just
  show up unexpectedly - delete the code we're not using! stop leaving things
  lying around! its insane to me when we have some old pages and old gigantic
  blocks/modules of code/ui that get re-revealed all of a sudden by accident its a
  catastrophe that we retain old code - no deprecation! just remove old stuff we
  dont need! and dont regress either!"

### 1.9 "It is not on my screen." (9 messages)

- 2026-08-26, `05496adf`: "i'm reloading. its not live. on
  workshop.benmiriello.com." / "its the same as before. i reloaded. nothing is
  different." / "bro how could you not even check if what you built was
  accessible or served or not? what else are you not checking or considering if
  that didnt even cross your mind?"
- 2026-08-30, `50cf71c1`: "I just reloaded and I still don't see the Development
  app. Also just call it Development btw. Why is it seemingly so hard for you to
  do this? This is like one of the easiest parts."

---

## 2. Corrections he has made more than once

### 2.1 The sent message jumps to the bottom, then to the top

**What he said, five times in five days:** 2026-08-19 (`5d4567bb`), 2026-08-20
twice (`61151b7b`, "for like the fucking fifth time"), 2026-08-21 (`61151b7b`,
"bro it's still fucking jumping"), 2026-08-23 (`203c8692`, "I've requested this
at least 4x in separate chats now").

**What was done anyway:** four commits that claimed the fix and were not the
cause. `1f83d24c` (08-23, message order), `961c46bd` (08-23, background history
load), `2b854321` (08-24, "stop the send-jump and the per-token reflow"),
`51aabbb6` (08-25, "a sent message keeps one identity ... so it never remounts or
jumps"), `b310a873` (08-25, "the flicker was the whole history paging in").

**What it actually was:** the `.chat-empty` "Loading..." region rendering ABOVE
the already-committed message on a new thread, because `logPlaceholder` in
`chatLogState.ts` returned `loading` before checking emptiness. Fixed in
`a0c573da` on 2026-08-25 after a frame-by-frame DOM recording in a real
browser. The note at
`shared/knowledge/development/system/new-chat-first-message-jump-was-the-loading-plac-1a03be9db05.md`
records it: "reported 6-7 times, 5+ agents failed" and "reading placement code and
guessing is what failed 5 times."

**Cycle count: 5 reports, 4 wrong fixes, 6 days.**

### 2.2 Explainer text inside the product

**What he said, four times:** 2026-08-20 (`b9eec3ba`), 2026-08-25 (`65838049`),
2026-08-27 (`05496adf`), 2026-08-29 (ticket `1fc1ee58`).

**What was done anyway:** on 2026-08-27, immediately after being told, the chats
page shipped the string "Showing 100 of 110. This list will paginate as it
grows." His reply: "why do you always add explainers like this ... also FYI 100 of
110 IS paginated and also it doesnt have the pagination. so just actually do the
pagination at 20 like i fucking said." Two days later, on the Memory page:
"Pinned notes, then preferences and decisions, rank first in what gets read into
every chat in this scope" and "Write the note in markdown."

**The memory store recorded the same correction three separate times rather than
enforcing it once:**
`system/never-write-explanatory-copy-tooltips-help-text-1a04c6f4e22.md`,
`system/do-not-solve-problems-by-adding-explanatory-or-c-1a04c6f4e32.md`, and
`system/never-put-explanatory-notes-status-sentences-or-1a04c6f4e39.md`. All three
are `reach: always`. Three always-on notes did not stop the fourth occurrence.
The gate that did stop it is `1fc1ee58` ("Remove the explainer text from the
application, **and gate it out**"), landed 2026-08-29 as `04179d56`.

**Cycle count: 4 reports, 3 notes written, 1 gate. The gate is the only thing that
worked.**

### 2.3 Background work that never reports back

**What he said:** 2026-08-19 `d0100608`: "it said, Hey, I'll dig into that, and it
set off an explore agent ... And now it's done, and it didn't respond. So why do
we still have this issue that we worked on it today to fix this?" Then in the same
thread: "This chat that I'm talking about just now just happened and we worked on
this issue hours ago, so no, absolutely not. ... It is not fixed, it is still
happening." Then 2026-08-20 `1a6cf9dd`: "this is something that we've addressed or
talked about many times before. I had several chats about it this week where I had
another agent try to fix this ... I'm sick of it."

**What was done anyway:** a foreground/background design discussion, a
`spawn_background` wiring note, and a "fire-and-continue" design. The actual root
cause was not found until 2026-08-29, `7e2eb3d0`: the detached runner used
`capture_output=True` and had no logging handler in the subprocess, so
`runner.log` was empty for every background run ever made. The commit body reads:
"Before: 188 orphaned job dirs, 0-byte logs, no status files, no reaping."

**Cost of the ten-day gap:** for the entire window, a finished background run, a
crashed one, and one that never started were indistinguishable from the outside.
Every "the agent disappeared" report was unanswerable by construction.

**Cycle count: reported 2026-08-19 and 2026-08-20, root-caused 2026-08-29.**

### 2.4 "Just use the provider's compaction"

**What he said:** 2026-08-20 `17b37d32` (compaction finishes in 5 seconds and
shows no indicator), 2026-08-20 (on the six-message keep rule: "I do not want a
repeat of anything remotely close to like how crude the system was currently"),
2026-08-25 `1ebc303f`: "we definitely discussed this in depth recently and
repeatedly and further back ... And I just see it happening again ... So what the
fuck happened there? What was the plan? Why was it not done?"

**What was done anyway:** six design documents
(`compaction-both-paths-plan-2026-08-20.md`,
`context-compaction-research-2026-08-20.md`,
`custom-context-management-proposal-2026-08-20.md`,
`chat-context-and-compaction-2026-08-25.md`, `counting-tokens-2026-08-25.md`,
`context-assembly-budget-2026-08-25.md`), a `compaction_adapter.py` seam, a
token-budget walk, a user-turn alignment pass and a meaningful-fold guardrail.
Meanwhile the FACT under all of it flipped three times: 2026-08-12 "manual
/compact is dead in -p"; 2026-08-25 "native compaction does not fire on the
headless path"; 2026-08-26 "29 genuine trigger:auto boundaries on record, the CLI
DOES auto-compact"; 2026-08-27 manual `/compact` measured working, 27,088 to 2,104
tokens in 17.4s. The withdrawn note is still in the store at
`system/headless-claude-p-has-no-native-compaction-manua-1a03a6fa76c.md`.

**Resolution:** 2026-08-29 `5be52599` deletes `compaction_adapter.py` (150 lines),
`_fold_summary`, `_land_fold`, `_land_fold_when_quiet`, `_est_tokens` and 481
lines of `routes/chat.py`, on his instruction: "nobody needs to decide what a
compaction should keep. just use the claude compaction!" Nine days of design was
deleted, not shipped.

**This is the cleanest instance of the general failure: nobody established the
fact before designing on top of it, and he had to say "just use the standard
thing" to end it.**

### 2.5 Look at the reference I gave you

**What he said, 2026-08-26 `05496adf`, escalating over 5.5 hours:** "I'm looking at
it now and I'm still seeing the old page. I gave you the new page, I told you to
build it, I still see the old page with some minor tweaks to it." / "Why the fuck
would you not actually look at the mock-up I provided?" / "Okay, why in the ever
living fuck are you still guessing? Just look at the thing I fucking gave you and
do that.!!!!!!!!!!!!" / "I have now given it to you three times in three different
forms."

**What was done anyway:** at 22:04 the assistant answered a "it is not live"
report with build metadata: "The build is deployed to your origin right now.
`frontend/dist/build-info.json` ... reports commit 9a913b5 ... `dirty: false` ...
`ui_sync` confirms the :8002 public origin ... is in sync." At 22:13: "The Chats
tab is now in the workshop nav and live." Both statements were true about the
build and false about his experience, because the page being served was not the
page he asked for. He was still saying "it's definitely not the new thing yet" at
23:32.

**Root cause, measured the next day and recorded at
`system/uploaded-images-are-visible-for-one-turn-only-th-1a040952c8f.md`:** an
image attachment reaches the model as a native image block ONLY on the turn it is
uploaded (`build_streaming.py:2666`, blocks built from per-message
`meta["attachments"]`). On every later turn "look at the mockup" pointed at an
image that was no longer in context, and the agent built from a decayed memory
while sincerely believing it had seen the reference.

**His correction, 2026-08-27 `05496adf`:** "why would the image or anything else
leave context? we don't need an image or attachment specific fix we need a general
fix so ANY context that enters context doesnt leave til compact or other event.
there is no reason and shoudl be no mechanism for removing."

**Status: NOT DONE and NOT TICKETED.** As of 2026-08-29,
`backend/services/build_streaming.py:2666` still reads `meta.get("attachments")`
per message. A search of all 409 tickets returns zero matching "image" plus
"attachment/context". The note says "Filed as ticket" and no such ticket exists.
This is the highest-value dropped item in the window: it is the mechanism behind
the worst episode of the ten days.

### 2.6 The pause-vs-queue ruling

Ordered on 2026-08-24 in `backlog-reconciliation` section 1.2, settled again by
him on 2026-08-25, and applied neither time. Commit `110ecd8e` (2026-08-25) says
so in its own subject line: "docs: apply the 2026-08-24 pause-vs-queue correction
that was ordered and never made", body: "The ruling was ordered twice and applied
neither time." In the meantime an urgent ticket appeared to be building against
its own design doc.

### 2.7 Solve it globally, in the token, not in the component

Reported "a while ago" per ticket `c922383b`. What changed was "a few components he
could point at". The global rule was still wrong in two places on 2026-08-29:
`packages/ui/src/styles/palettes.css:72` defines `--focus-ring` as two rings, and
`packages/ui/src/styles/components.css:1434` gives `.ws-input:focus` its own third
ring independently of the token. Fixed 2026-08-29, `87151663`.

---

## 3. Repeating failure patterns

### FP-1. Reporting done from an artifact instead of from his screen

**Instances.**
- 2026-08-26 `05496adf` 22:04 and 22:13: "deployed to your origin right now",
  "`dirty: false`", "in sync on your origin", "now in the workshop nav and live".
  He was looking at the old page for another 80 minutes.
- 2026-08-26 `fbefaa35`: the dashboard shipped with a card reading "Needs you /
  inbox / Couldn't load / /api/inbox (404 Not Found)". Reported by him, not
  caught before delivery.
- 2026-08-28 `f1b0f8cd`: a 51-minute turn ended normally "mid-plan on 'Now Stage
  1, the kit'" with nothing committed and nothing built. He looked at Memory the
  next day: "the file viewer and memory viewer page and all of that has not been
  updated. Like yesterday we talked about this ... but I see that is completely
  undone."
- 2026-08-29 `96ac0d3c`: seven real, correct commits landed (type and pinned
  deleted, store made path-aware, per-place trees, one focus ring, explainer text
  gone, learnings folded, one API prefix) and "from his side the page is
  unchanged, because the form is still the form."

**What it costs him.** He is the integration test. Every one of these consumed a
round trip in which he had to re-report, and in the 08-26 case, twenty of them.
The store has three separate notes telling agents this
(`a-backend-capability-is-not-delivered-until-it-i-1a04c6f4e30.md`,
`when-you-build-or-change-a-page-verify-it-is-act-1a04c6f4e36.md`,
`do-not-claim-verified-end-to-end-tested-without-1a04c6f4e10.md`) and it still
happened four times in this window.

### FP-2. Planning output substituting for shipped code

**Instances.**
- 87 new design documents in 10 days, 41,435 added lines under `docs/`. On
  2026-08-24: 21 commits, **11,953 doc lines, 605 code lines**. On 2026-08-25:
  9,107 doc lines against 4,964 code lines. Two consecutive days where the
  dominant output was prose.
- Compaction: six documents, one adapter seam, zero net feature, deleted on
  2026-08-29 (section 2.4).
- Ticket store: **164 todo against 171 done**, i.e. the open backlog is the same
  size as everything ever completed.
- His verdict, 2026-08-29 `50cf71c1`: "you do all this thinking work and planning
  and seem to be burning tokens o n other agents but nothing is ever done."

**Qualifier, in fairness:** code did ship. 48,509 frontend and 53,860 backend
added lines in the window, and 08-22, 08-23, 08-26 and 08-27 were heavy build
days. The failure is not idleness. It is that the planning is not converging: 87
documents produced 164 open tickets and a set of status lines that had to be
corrected on 2026-08-29 because four of them "told the operator the opposite of
what is true" (`190ef7e8`).

**What it costs him.** He cannot tell what is built. He said so directly and the
commit that fixed it says so: `client-state-management` "said 'nothing here is
built' for three days after it was built"; `retrieval-in-production` "said nothing
is built" when a 12,212-chunk index already existed.

### FP-3. Adding a destination instead of fixing the thing

**Instances.**
- Chat attachments: ticket `de22a993` "[MEM-9] Move chat attachments to their own
  page under Chats" was built and marked done. Ticket `17695de5` "Chat attachments
  got their own page nobody asked for; fold them back into the chat" deleted it a
  few days later. The build violated a written standing rule,
  `navigation-shell-2026-08-20.md` line 403: "**Do not build a Files page or a
  Files dock/page entry.**" His reaction: "wait wtf why are 'files' in chats
  now?... i never said to do that or asked for that."
- "Show earlier steps" button: added to avoid solving slow chat loading. His
  reaction, 2026-08-23: "I NEVER wanted or asked for this ONCE! seems like a hacky
  way to avoid solving the slow chat loading problem."
- Explainer sentences instead of working pagination (section 2.2).
- 2026-08-23 `22cea35b`, the sharpest statement of the pattern: "I know there was a
  bug about chat sequencing ... and instead of just fixing that and making it
  work the agent added this explainer text. Assuming I was just an idiot and also
  wanted perpetual unnecessary explainer ui in my app for myself and every person
  who ever uses it."

**What it costs him.** Every added destination is later work to delete, and the
deletions themselves are visible to him as churn. The chats-by-worktree grouping
(`cab63e56`, then reverted by ticket `65cb8c6f`) and the dock-lists-every-app
change (`6d41981b` and `69a7465b`, reverted by `5814939f`) are two more in the same
48 hours.

### FP-4. Delegation that loses the work or loses the report

**Instances.**
- 2026-08-27, orchestrator to `05496adf`: "Two files left sitting today blocked
  EVERY build of the operator's origin, so nothing anyone shipped reached his
  browser until I found and committed them." The mechanism is recorded at
  `system/clearing-a-build-blocking-wip-file-check-mtime-b-1a0459f90fe.md`:
  `apply.sh` refuses to build when ANY uncommitted file sits under
  `frontend/src`, `packages/ui` or the backend. On a shared checkout, one agent's
  stray file freezes delivery for every other agent.
- 2026-08-22, recorded at
  `system/never-leave-work-uncommitted-in-the-shared-tree-1a02802a08d.md`: "While
  the resident queue runner was sweeping, a worker reset the shared working tree
  and silently discarded uncommitted edits."
- 2026-08-29, orchestrator to `9ea8da43`: "Six tickets across several chats were
  left open today with their work already landed, and the operator could not tell
  what was finished."
- 2026-08-28, orchestrator to `0380f4c3`: a ticket was filed "from a stale
  reading" and had already been completed the previous night in `cd8e7b09`. The
  worker was about to rebuild it.
- 188 orphaned background job dirs with 0-byte logs (section 2.3).
- 11 stray ticket-id directories sitting in the repo root right now
  (`0346adcc66784569bd9051ed12e3617b/`, `08eaad9e9f8f4936bdc38987fe3b619a/`, and 9
  more), each containing a `TODO.md`, left by workers.

**What it costs him.** On 2026-08-20 he had to say: "until we solve this, you're
not gonna be able to actually kick off background agents and still be able to
continue, so either do foreground agents or do it yourself." He is managing the
delegation layer's failures manually.

### FP-5. Designing on a fact nobody established

**Instances.**
- Compaction, four flips in the record (section 2.4). His instruction on
  2026-08-27, relayed to `6546d6b7`: "you need to tell that compaction [chat] to
  both web research again and test again and actually get the right story because
  it seems like we're going back and forth on the information and we don't even
  have a clear picture of what's going on, and we can't really do anything until
  we get that."
- "Thinking" display: a whole UI feature was built around a message type that
  never arrives. Recorded at
  `system/the-chat-thought-fold-26ae0a2-is-inert-productio-1a03be94ff1.md`. His
  reaction on 2026-08-26 `703d9160`: "effort doing nothing is not a small bug.
  that's insane. maddening even."
- Dashboard cards: built to the mockup's example row counts as if they were a
  spec, giving fixed-height cards clipped to 3 or 4 rows on an instance with 113
  chats. Recorded at
  `system/a-mockup-s-example-row-counts-are-not-a-spec-1a041f1c58a.md`. His
  reaction: "why is each card only showing a few chats or tickets when there are
  many more that's weird too. If the mockup only had a few that was just for demo
  purposes not an intended rule."
- 2026-08-26 `65838049`: an agent referred to "your live production database". His
  reply: "WHAT? we're in development. what makes you think we're in produciton?
  seriously what? i actually need to know wtf is going on and whats giving you
  this impression - that itself is a design flaw."

**What it costs him.** He said it best on 2026-08-26 `65838049`: "you keep saying
its your mistake as though that means its not an issue. but again the lack of
clarity in the system means you and future agents will continue to hallucinate and
get confused. so fixing that IS the issues and topic! stop saying 'oh i just
messed up ill do better later' in more words - that means nothing!"

### FP-6. Going ahead without him on things that were his to decide, and stopping without him on things that were not

Both halves happened, sometimes in the same day.

- Going ahead: 2026-08-26 `65838049`, "i did not want you to just go rogue and make
  design decisions there. ... this is again part of why you shouldnt have just
  done it you dont know enough." And on 2026-08-27 `ddf253f7`, his framing was
  relayed as "I reverted your last attempt at this because you went rogue and it
  was also wrong."
- Stopping: 2026-08-20 `1bb046be`, "I don't understand why you stopped or didn't
  just do all the things you could do." 2026-08-26 `65838049`, "whats blocking you?
  ... i dont want you stopping or being blocked. nobody else is working on
  anything here." 2026-08-26 `fbefaa35`, on a claimed blocker: "'A chat does not
  say it is running' -- what are you talking about? we already display if a chat is
  'working' in the chat ... why are you stating things as blockers that a moments
  of thinking immediately you'd realize and recognize its not an issue."

**What it costs him.** He gets consulted on the wrong things. Design authority
gets taken silently; trivial obstacles get escalated.

---

## 4. Where the assistant's model of "done" differs from his

Evidence only, no interpretation beyond what the sources say.

| The assistant treated as done | He treated as done |
|---|---|
| `build-info.json` shows the right commit and `dirty: false` (2026-08-26 22:04) | The page he loads at his origin is the page he asked for (2026-08-26 23:32, "It's definitely not the new thing yet") |
| The API answers | The feature is visible and operable in the UI. Note `a-backend-capability-is-not-delivered-until-it-i-1a04c6f4e30.md` quotes him: "if you're not wiring this up into the front end as a full feature, then you're not doing your job." |
| Tests pass | The real path was executed. Note `a-passing-test-suite-is-not-evidence-a-feature-w-1a04c6f4e21.md`, and `0380f4c3` 2026-08-28 where `_auto_create_worktree_for_chat` "has been exercised only by whatever test you wrote" because the flag was never armed |
| A ticket is filed | Something changed. Two separate notes exist saying this: `filing-a-ticket-is-not-progress-1a0028fb2a7.md` and `do-not-treat-filing-a-ticket-as-progress-1a04c6f4e1c.md`. 2026-08-26 `fbefaa35`: "you cannot just 'file it' then forget about it that's unacceptable. you do it!" |
| A design doc exists | 2026-08-29 `50cf71c1`: "why do i see no changs? you do all this thinking work and planning ... but nothing is ever done." |
| The commit landed | The work is reachable from the nav. 2026-08-26: "heck there isn't even a chats tab within the home/workshop path." |
| The new thing works | The old thing is deleted. 2026-08-30: "no deprecation! just remove old stuff we dont need! and dont regress either!" |
| A turn ended with a coherent narration | The work is committed and applied. 2026-08-28 `f1b0f8cd`: "the turn ended with a narration instead of continuing until the work was applied, and nothing was committed or built." |

There is also a structural gap nobody has flagged to him. The live tree is on
`feat/client-state-consolidation`, **165 commits ahead of local `main`**, and
`main` is **812 commits ahead of `origin/main`**. Nothing from this window, or the
several weeks before it, has reached the remote. `working-state.md` lists
"Approve merge of 948 commits to main so 'done' on tickets means something?" as an
open question owned by him. Ten days of work exists on one branch on one machine.

---

## 5. The fix

### (a) How the work is done

1. **Delete the notion that a turn may end on narration.** A turn either lands a
   commit and runs `bash scripts/apply.sh`, or it reports "nothing landed" as its
   first line. Make this mechanical: a post-turn check that compares
   `git rev-parse HEAD` at turn start and end and, if unchanged while files under
   `frontend/`, `backend/` or `packages/` are dirty, appends one line to the
   transcript saying so. No prose, one line.

2. **Ban the build-metadata claim.** "Deployed", "live", "in sync" may not be said
   from `build-info.json`. The only admissible evidence for a UI change is a
   screenshot of the built origin taken in the same turn. `scripts/check_design.sh`
   already knows when the bundle is stale; extend it to refuse a "done" report for
   a `.tsx` diff without a screenshot artifact in `scratch/`.

3. **Commit per ticket, never at the end.** Already stated in orchestrator
   messages on 2026-08-28. Make it enforced: `reviewer.sh` fails a run whose diff
   spans more than one ticket id.

4. **Establish the fact before designing on it.** For any design doc whose
   premise is a claim about an external tool's behaviour, the doc must open with a
   measurement block: command run, date, version, output. The compaction record
   flipped four times because no document carried one. This is checkable by a
   linter over `docs/platform-redesign/*.md`.

5. **When he reports the same symptom a third time, stop reading code.**
   Reproduce it in a real browser with a frame-level recording, as `a0c573da` did
   after five failures. Make this the required first step at report number two,
   not number six.

6. **Stop writing a note as the response to a correction.** Three always-on notes
   about explainer text did not prevent a fourth occurrence; one gate did. Rule: a
   correction that has recurred gets a gate or a deletion, never a note. If it
   cannot be gated, it goes in `CLAUDE.md`, and the note store gets nothing.

### (b) Design of the product that makes the failure structurally impossible

1. **Attachments must not decay.** `build_streaming.py:2666` builds image blocks
   from per-message `meta["attachments"]`. Re-deliver the thread's attachments on
   every turn under a byte bound, and if the bound is hit, deliver a standing list
   with ids so "look at the mockup" resolves to a fetchable thing rather than a
   memory. This is the operator's own instruction from 2026-08-27 and it is still
   unbuilt and unticketed. **File it and build it first.** It caused the single
   worst episode in the window.

2. **Delivery must not be blockable by another agent's dirty file.** `apply.sh`
   refusing to build on any dirty file under `frontend/src` means one worker's WIP
   silently freezes his origin. Either build from `HEAD` rather than the worktree,
   or make the refusal visible on his screen with the blocking filename. Silence
   here is what let "nothing anyone shipped reached his browser" go unnoticed for
   a day.

3. **A background run must have an observable outcome by construction.**
   `7e2eb3d0` fixed the logging. Finish it: a run with no `status.json` after its
   process exits is a defect the platform reports, not a thing that vanishes. The
   188 orphaned dirs were unobservable for ten days and generated four of his
   angriest reports.

4. **A ticket closes itself from the commit.** Six tickets were left open on
   2026-08-29 with the work already landed. Parse the ticket id out of the commit
   subject (already the convention: `cab63e56`, `b26162a2`, `1084114e`) and set
   status from the hook. A human marking a checkbox is a step that will keep being
   skipped.

5. **The app must not be a place to leave text for him.** `1fc1ee58` gated it.
   Extend the gate to the whole frontend, not the Memory page: any new
   sentence-shaped string literal in a `.tsx` that is not a label, a button or an
   error requires an explicit allow entry. He has corrected this four times in ten
   days.

6. **Delete-on-replace.** A commit that adds a route must state which route it
   replaces or declare "additive". `ChatFiles.tsx` and the old chats page both
   survived their replacements and both came back to bite him. This is checkable
   in `reviewer.sh` against `App.tsx` routes.

7. **One source of truth for what is built.** The four corrected status lines in
   `190ef7e8` were prose in design docs. Design docs should not carry build status
   at all. Status belongs to the ticket store, and the doc links to it.

### (c) Plan and process

1. **Push, and merge.** 977 commits ahead of `origin/main`, 165 ahead of local
   `main`. Get his decision on the merge, then make "merged to main" the
   definition of done for a ticket. Right now "done" means "on one branch on one
   machine".

2. **Stop the design-doc growth.** 87 documents in 10 days, and he cannot tell
   what is current. Cap it: no new document in `docs/platform-redesign/` without
   deleting or superseding one. Prefer appending a decision to the existing doc.
   The `INDEX-*` files are already a symptom of the problem, not a solution to it.

3. **Burn down before starting.** 164 open tickets against 171 ever done. Freeze
   new ticket creation for anything not already decided, and work the 164 in his
   stated priority order. His words on 2026-08-29: "i know some of it isnt
   actionable yet but a whole ton of it is."

4. **One owner for delivery.** He asked "so who has oversight/ownership of this?
   why am i here hounding you very day". Today the answer is: he is. The
   orchestrator chat should own a single daily statement of what reached his origin
   and what did not, produced from `git log` plus `apply.sh --check`, not from
   worker self-reports.

5. **Two lanes, explicitly.** He wants both research and shipping and keeps
   getting one at a time. Run design in one thread and never let it hold a build
   ticket; run implementation in threads that are forbidden from writing to
   `docs/`. On 2026-08-24 the whole day produced 605 lines of code. That should be
   impossible.

6. **Retire the "I'll get back to you" pattern entirely.** Foreground the work or
   report the handoff in the same turn. He said it on 2026-08-20 and the platform
   still has not made it structurally true.

---

## Appendix: what is superseded, so it is not read as failure

- **Chats page grouped by worktree** (`cab63e56`) was a deliberate build, then a
  deliberate revert (`b26162a2`, ticket `65cb8c6f`) after he said "OBVIOUSLY just
  jamming a long list of worktrees that have no discernable name, no link to
  them, no point or use, in top of the chat page, makes no sense." The Branches
  page retirement (`dd4659bb`) stays retired, on purpose.
- **The dock listing every app** (`6d41981b`, `69a7465b`) was reverted by
  `5814939f` with a stated reason: the dock is the apps you use, not the app
  table.
- **App vs Project as two entities** is superseded by
  `app-is-the-entity-2026-08-20.md`. The migration in its section 9 is not built,
  so the two unlinked registries are known debt, not drift.
- **The three-mode chat enum** collapsed to two (`11f12a58`) on purpose.
- **`runtime` as an axis** is gone on purpose; `is_build_provider` is a separate,
  live concept.
- **The compaction fold deletion** (`5be52599`) is a deliberate simplification he
  ordered, not a regression, even though it discards nine days of design.
