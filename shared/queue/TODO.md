# TODO

## operator

- [ ] Redesign how memory, notes and documents are written: structure, scope, and when
      Operator, 2026-08-12, when approving the removal of automatic chat distillation:
      "the concept of doing an automatic transcription or distillation of chats is not a bad idea and it's something that we want to implement properly later, but it's probably pretty far from being implemented well currently, so we can retain the existing things that have been written and saved and remove that functionality now, and make a ticket to re-implement it redesigned and properly. That is going to require a smarter proper design of how we structure memory and documents and notes, and when we create that, and how we scope it."

      WHAT WAS REMOVED, AND WHY (so this is re-buildable from here):
      Automatic Haiku distillation of folded transcript into the scope's `chat-distilled` note. Removed 2026-08-12 along with server-side transcript folding, which was its only trigger. Everything already written stays on disk; nothing was deleted from the knowledge store.
      It was removed rather than re-homed because:
        - `chat-distilled.md` was the LARGEST file in its scope, and auto-recall ranked by raw term frequency with no stopwords, no IDF and no length normalisation, so it won nearly every recall. It was the biggest single polluter of retrieval.
        - In `development` and `project` scopes auto-recall never ran, so every fact distilled there was write-only.
        - The raw chat logs it summarised are now in the semantic index and directly searchable, and a lossy summary of a searchable corpus is worse than the corpus.

      WHAT THIS TICKET IS FOR. Not "put distillation back". The write side of memory as a whole, designed once:
        - WHAT gets written durably, and what should stay in the transcript and be searched instead. The default should be "search the transcript", with durable notes reserved for things that are genuinely a conclusion rather than a record.
        - WHO writes: the operator, an agent mid-turn via the fenced note block, or a background pass. Today all three write to the same undifferentiated store.
        - WHEN: on demand, at a boundary, or on a schedule. The old trigger (a successful fold) was an accident of implementation, not a decision.
        - SCOPE and provenance: every durable item should carry where it came from (thread id, timestamp, author, scope) so it is reconstructable later. The operator asked for this explicitly even where nothing reads it yet. The semantic index already records kind/ref/scope/ts per chunk; the write side should match that shape.
        - STRUCTURE: `shared/knowledge/<scope>/` uses slug-plus-hash filenames and a hand-maintained MEMORY.md index that duplicates the filesystem. `.runtime/learnings/<scope>.jsonl` is a second agent-written store differing only by a `kind` tag. These two should almost certainly be one store. The operator's own Obsidian vault at `$WORKSHOP_NOTES_VAULT` is a third and stays separate (different owner, external editor).
        - RETIREMENT: nothing currently retires. `enforce_index_cap`'s archive path was removed as unreachable. Decide what supersession means before rebuilding a mechanism for it.

      READ FIRST: docs/memory-and-transcript-simplification-2026-08-12.md (the plan and its measurements) and docs/memory-stores-audit-2026-08-12.md (the nine-store map).

      Boundaries: DESIGN FIRST, as a doc under docs/, for the operator to react to. Do not restore automatic writing of any kind until that design is agreed. The stated goal is to draw complexity down and build back up from something simpler, so a proposal that re-adds a background organ needs to justify itself against not having one.
- [-] Redesign the note receipt line under a reply: it does not read as a link and its destination is unclear  (cancelled)
      Operator, 2026-08-12: "when the system message creates a note it has a little line at the bottom where it shows the document icon and then the title of the note, and it links to the note. This is honestly pretty good but it's a little bit unclear and not properly designed, it doesn't look like clickable text. It should probably be styled like a link, and I'm not sure exactly what it does or what it should do."

      CONFIRMED IN CSS. frontend/src/styles.css: `.chat-receipt` is `color: var(--muted)`, and `a.chat-receipt--link { text-decoration: none; }` with an underline applied on `:hover` only. On a touch screen there is no hover, so the affordance never appears at all: the link is permanently indistinguishable from the non-clickable receipts beside it (`.chat-receipt--ok`, `.chat-receipt--warn`), which are the same muted grey with the same icon-plus-text shape.

      The comment in the stylesheet says a permanent underline was deliberate, so this is a real design decision being revisited, not an oversight. Read it before changing it.

      The task is NOT just "add an underline". Settle the interaction first, then style to it:
        - What should opening a receipt do? Open the note in the Notes app, open a drawer over the chat, or expand inline under the reply? A note written mid-conversation is most often wanted WITHOUT leaving the conversation, which argues against a navigation.
        - Should a receipt be editable from there, or read-only with a way through to the editor?
        - Which receipts are openable at all? Today `--link` is one variant among several identically-styled ones, so "some of these are clickable and some are not, and they look the same" is the actual defect.
        - Whatever is chosen must have a non-hover affordance, since the operator is often on a phone.

      Start from: frontend/src/components/chat/receipts.ts (the typed model), the `.chat-receipt*` rules in styles.css, and services/build_streaming.build_receipts on the write side. Follow frontend/CLAUDE.md; use a kit primitive if one fits rather than another bespoke `.chat-*` rule.
- [-] Reduce empty turns: 26% of prompts return no assistant text  (cancelled)
      Objective: a quarter of turns commit with no top-level assistant text. Most are
      explainable, but the operator experiences them as the chat not answering.
      Output: each cause either eliminated or given copy that says what happened.
      Measured 2026-08-12 over the last 80 sessions (275 prompts, 73 with no text):
        33  slash-command turns. The CLI runs /context and /compact locally and never
            calls the model. FIXED for the injected case in commit 9c49696; verify the
            non-injected path also reads well.
        16  operator stop. Deliberate: _commit_turn commits empty text and the tag
            carries it. Probably correct, confirm the UI reads clearly.
         5  synthetic placeholder was the whole output (subset of the slash-command
            case). The placeholder itself is CLI resume repair emitted at turn START,
            not a turn-ending filler; dropping it is correct.
         1  model emitted tool_use with no closing text. Model behaviour.
      Start from: backend/services/build_streaming.py _commit_turn (the rc==0 branch),
      _clean_exit_text, and the is_command derivation.
      Boundaries: do not suppress the empty reply entirely; a turn that ran must still
      leave a record.
- [-] Make the working-for-N-minutes note say what is happening  (cancelled)
      Objective: the elapsed note is one flat timer, but a turn is a series of different activities.
      Output: the in-flight indicator reflects the current activity rather than only elapsed time.
      Start from: frontend/src/components/chat/turnStatus.ts (turnLabel, elapsedSeconds) and the activity region work in commit e23a2e1.
      Context: operator 2026-08-11: the elapsed note is "not very nuanced... really it's a series of different things".
      Boundaries: do not add a second status surface; this rides the existing indicator.
- [ ] Audit and redesign notes, memory and task storage
      Objective: the operator asked to record this as a discussion point, not to start building.
      Output: a written review of how notes, memory, tasks and their updates are stored and related, with a proposed redesign, as a doc under docs/ for the operator to react to.
      Start from: shared/knowledge/, backend/services/knowledge.py, backend/services/tasks.py, the notes-and-docs skill.
      Context: operator 2026-08-10: "mark as a discussion point that we need to review and audit and redesign how we take notes and store memory and update them and tasks and everything else around that. But not yet."
      Boundaries: DISCUSSION ONLY. Do not migrate or restructure any store until the operator agrees.
- [ ] Define external tracker (Jira) sync semantics
      Objective: connect/disconnect/reconnect divergence, per-item merge, and source-of-truth marking for an external tracker were specified in chat and never captured.
      Output: a design doc covering: what happens on connect, on disconnect, and on reconnect after divergence; how a diverged item merges; how source of truth is marked per item.
      Start from: the tickets model in backend/services/tasks.py (note the `sync` field on add()) and the current tickets redesign docs.
      Context: long operator brief on 2026-08-10. The active tickets redesign covers schema and views only.
      Boundaries: design first, no integration code until the operator approves.
- [-] Chat auto-naming falls back to the raw first message, and the dev-scope default is racy  (cancelled)
      Operator, 2026-08-12: "the system does not seem to be creating its own chat names when I would expect... And I'm not sure if it's really defaulting to the development scope when I am in the development environment."

      TWO PARTS, both observed.

      1. NAMING FALLS BACK SILENTLY. Most threads do get a generated title ("Chat File Attachments", "Workshop UI Kit Versioning Issue"), but the newest development thread (2026-08-12 10:17) is titled "Hey, so how is the audio going?", which is the raw first message, not a name. So the namer is failing intermittently and the seed title is standing in for it with no signal that naming ever ran.
         Start from: services/chat_naming.py. `compute_title` counts CONTENT rejections against `_MAX_NAME_ATTEMPTS` (5) and then stops asking, keeping the seed. Nothing records that it gave up: no `name_attempts` key is persisted in meta.json (checked across 60 threads, the key does not exist), so the attempt counter's home and its durability need establishing before anything else. Find out whether the counter survives a restart at all; if it does not, a chat could be re-asking forever or giving up instantly and both look identical from outside.
         Wanted: naming either succeeds or is visibly unnamed. A raw first message masquerading as a title is the failure mode to remove.

      2. DEV DEFAULT IS IMPLEMENTED BUT GATED ON A RACE. frontend/src/lib/chatSelection.ts:98 reads `devMode && known(DEVELOPMENT_SCOPE) ? DEVELOPMENT_SCOPE : DEFAULT_SCOPE`, where DEFAULT_SCOPE is "general". `known()` tests the loaded provider list, so if a new chat is opened before providers have loaded, the condition is false and it silently lands in `general`. That matches "sometimes".
         Wanted: in developer mode a new chat lands in `development` deterministically, not depending on whether a fetch finished first.

      Boundaries: no new scope kinds, no change to the scope picker's model.
- [!] Point LETTA_URL at the right Letta instance  (blocked)
      Objective: the Letta service probe defaults to a Letta that may not be Workshop's.
      Output: the default (or an explicit instance.env setting) points at Workshop's own Letta, and Settings > System reports its health.
      Start from: backend/routes/services.py:50, scripts/restart_backend.sh:187, backend/services/port_registry.py:58.
      Context measured 2026-08-12: two Letta servers listen here. :8283 is root-owned from /app/.venv and HAS agents (oldest created 2026-06-04). :8284 answers /v1/health/ but returns zero agents. The current default is :8283.
      Boundaries: CONFIRM WITH THE OPERATOR which instance is Workshop's before changing anything. Do not migrate or delete agent data.
- [-] A sent message should not look already-read  (cancelled)
      Objective: a just-sent chat message renders in full colour immediately, so it is indistinguishable from one the server has accepted.
      Output: an in-flight message is visibly distinct until the server confirms it, verified against a real send (not just in source).
      Start from: frontend/src/styles.css:1048 (.chat-bubble--inflight) and the send path in frontend/src/components/Chat.tsx / useSendPipeline.ts.
      Context: operator raised this three times on 2026-08-09 ("i see it did not fix the issue as my 'testing - ignore' message showed"). A .chat-bubble--inflight class was added and the symptom persisted; it was written off as environmental and never confirmed fixed. Re-verify whether the class is applied at all, and whether the optimistic message is committed too early.
      Boundaries: do not change send/queue semantics; this is a visual state bug.
- [x] Set a memory ceiling for agent subprocesses
      Objective: cap resident memory for heavy agent/CLI subprocesses so one run cannot exhaust the box.
      Output: a real enforced ceiling (systemd MemoryMax on the unit, or an rlimit applied at spawn), documented, with the chosen limit justified against observed usage.
      Start from: the spawn path in backend/services/build_streaming.py (start_turn) and the systemd units under scripts/; `grep -rn MemoryMax scripts/ backend/` currently returns nothing.
      Context: concurrency was capped (commit b137615) but memory never was. Operator asked twice: "should we set a memory ceiling? research into it and suggest" then "make a plan then implement it".
      Boundaries: do not cap so low that a normal long turn is OOM-killed; measure first.
- [ ] Revisit deferred chat-plan items 12-14, 16 and 19
      Objective: five chat-plan items were deferred with "discuss later" on 2026-08-10/11 and never revisited.
      Output: each item restated in current terms with a recommendation (do / drop / defer), for the operator to decide.
      Start from: the chat plan doc under docs/ (grep docs/ for the chat plan containing numbered items 12-19).
      Boundaries: do not implement any of them off the back of this; bring the list back first.
- [x] Make page chrome consistent across pages
      Objective: the toolbar and page rhythm still differ between pages (chat vs settings vs others).
      Output: one page-shell pattern applied consistently, so header/toolbar spacing and structure match across routes.
      Start from: the variance already found: some pages wrap content in <Stack>, others in a bare <div>. Compare frontend/src/pages/Settings.tsx, Chats.tsx, Work.tsx against the shell in frontend/src/App.tsx and packages/ui components.css (.ws-app, .ws-topnav).
      Context: operator on 2026-08-09: "why are you not responding or fixing the toolbar difference or page styling difference between pages? chat and settings and perhaps others". It was investigated, a clarifying question was asked, and it was dropped. Do not ask again; pick the dominant correct pattern and make the others match.
      Boundaries: page WIDTH is already settled (see frontend/CLAUDE.md 'Page width'); this is spacing/structure, not width.
- [!] Commit a root package-lock.json  (blocked)
      Objective: the monorepo root has package.json but no lockfile, so installs are unpinned.
      Output: package-lock.json committed at the repo root, matching the current workspace tree.
      Start from: package.json at the repo root; `npm install --package-lock-only`.
      Boundaries: BLOCKED IN-APP. Generating it needs registry.npmjs.org and this environment has no external egress, so an agent cannot produce it. The operator runs the command in their own terminal, or approves registry access for a run.
      Reported by the operator 2026-08-12: "Don't you usually want to commit package lock? Why not do it?"
- [d] Should not be filed  (draft)
