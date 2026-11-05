# Index: knowledge notes, decision records, learnings

Written 2026-08-26 against HEAD `8f56db45`. First inventory of these three
stores. Read-only audit; nothing was deleted or edited.

Three durable stores, three different failure modes:

- **Notes** (`shared/knowledge/`, `services/knowledge.py`): 136 notes, injected
  into chat prompts by scope. Mostly true, ~25 actively wrong.
- **ADRs** (`docs/decisions/`): 29 numbered records, no gaps. Under-used since
  2026-08-20; a dozen durable rulings from 08-24 and 08-25 live only in prose.
- **Learnings** (`.runtime/learnings/`): 58 entries, injected into every agent
  prompt. 12 are false and are crowding the token budget.

---

## Part 1: the knowledge notes

### Distribution

- `workshop`: **95 notes** (`shared/knowledge/*.md`)
- `development`: **34 notes** (`shared/knowledge/development/`)
- `agent:<bridge-agent>`: 2 notes (`.runtime/agents/<bridge-agent>/knowledge/`)
- `agent:<bridge-agent>-commute`: 2 notes
- `project:commute`: 1 note
- `project:development`: 1 note
- `role:reviewer`: 1 note (`shared/knowledge/roles/reviewer/`)

Empty knowledge directories, i.e. scopes that exist and have learned nothing:
several `apps/<slug>` and `projects/<slug>` directories,
`.runtime/agents/development-maker`, and another maker agent's directory.

Two-thirds of everything is in one scope. `workshop` is the layer
`read_layers` puts into every prompt, so it is also the scope where a wrong
note costs the most.

Two `development` notes are missing from `MEMORY.md` and are therefore
invisible to the index reader:
`importing-backend-main-writes-live-instance-state-e6a6c2a3677.md` and
`tasks-update-needs-a-path-not-a-str-05033161d3b.md`. Both are load-bearing.

### Load-bearing: read these

Roughly in order of how much time each one saves.

1. **A chat's system prompt is frozen at session spawn** (`development`,
   `a-chat-s-system-prompt-is-frozen-at-session-spaw-1a0088f0481`). Prompt or
   contract changes never reach an already-open chat. Explains most "I fixed
   it and it still does the old thing".
2. **Chat protocols self-refresh** (`development`,
   `chat-protocols-self-refresh-how-a-contract-chang-1a00958798e`). The fix for
   the above, and which blocks are wire formats rather than character.
3. **Chat state bugs come from the reducer owning only part of the truth**
   (`development`, `chat-state-bugs-come-from-the-reducer-owning-onl-1a03c5268d5`).
   37 useState + 32 useRef beside a pure store. Names why five chat fixes kept
   regressing.
4. **The chat sync endpoint is a single point of failure with four faces**
   (`workshop`, `the-chat-sync-endpoint-is-a-single-point-of-fail-1a0131b4024`).
   One 500 on `GET /chat/threads/{id}/events` presents as four unrelated bugs.
5. **Chat load flicker root cause** (`development`,
   `chat-load-flicker-root-cause-eager-full-history-1a03a6e2c8f`). Measured with
   a real browser probe after 5+ agents guessed wrong.
6. **New-chat first-message jump was the Loading placeholder** (`development`,
   `new-chat-first-message-jump-was-the-loading-plac-1a03be9db05`). Same bug
   family, same lesson: it was never the placement logic.
7. **Headless `claude -p` has no native compaction** (`development`,
   `headless-claude-p-has-no-native-compaction-manua-1a03a6fa76c`). Measured.
   Kills the assumption behind ADR 0011 and one older note.
8. **The prompt prefix had a size problem because the layer contract never
   mentioned cost** (`development`, `the-prompt-prefix-had-a-size-problem-because-the-1a03a537caa`).
   114K chars measured; PROJECT.md's Log was 63% of it.
9. **The usage ledger counts four different questions with one number**
   (`development`, `the-usage-ledger-counts-four-different-questions-1a03a7a1a82`).
   83.5% of ledger rows are test writes into the production ledger.
10. **A monthly usage cap derives from the plan's price** (`development`,
    `a-monthly-usage-cap-derives-from-the-plan-s-pric-1a03b5efca9`). Records two
    wrong attempts so they are not repeated. Anthropic publishes no token figure.
11. **Chat subagents must be foreground** (`workshop`,
    `chat-subagents-must-be-foreground-the-guard-fail-1a02048d4d5`). The guard is
    live and fired during this audit. Explains "agent says it will report back
    and never does".
12. **Claude Code permission modes need `--permission-prompt-tool stdio`**
    (`workshop`, `claude-code-permission-modes-need-permission-pro-19fe9598408`).
    Undocumented upstream, silently auto-denies without it.
13. **AskUserQuestion is denied in chat turns** (`development`,
    `askuserquestion-is-denied-in-chat-turns-1a0136e8b13`). Deliberate, still
    enforced, says what to do instead.
14. **Importing `backend/main.py` writes live instance state** (`development`,
    `importing-backend-main-writes-live-instance-state-e6a6c2a3677`). A read-only
    probe reorders the operator's launcher cards. Unindexed.
15. **Never leave work uncommitted in the shared tree** (`development`,
    `never-leave-work-uncommitted-in-the-shared-tree-1a02802a08d`). A sweeping
    worker resets the tree and eats tracked modifications.
16. **DB tests error rather than skip without the admin env**
    (`project:development`, `db-tests-error-rather-than-skip-without-the-admi-1a011f4947d`).
    85 fake regressions in a fresh shell. One `set -a && .` fixes it.
17. **Gzip middleware must be registered FIRST** (`workshop`,
    `gzip-middleware-must-be-registered-first-or-it-s-1a002e9891e`). Verified
    still true; a 4.83MB response went out raw when it was not.
18. **`response_model` on a route is not enough, its fields must be typed too**
    (`workshop`, `response-model-on-a-route-is-not-enough-its-fiel-1a013175a03`).
    The ADR 0023 gate can pass while the drift survives one level down.
19. **`read_thread` reports `active_run` non-atomically** (`workshop`,
    `read-thread-reports-active-run-non-atomically-wi-19ff6e56d74`). The
    server-side window is confirmed **still open** at `chat_log.py:515-527`.
20. **Where notes go: three tiers** (`workshop`, `notes-and-docs-policy.md`).
    The policy this index is an inventory of.

Honourable mentions worth keeping in reach: **transcript splitting is atomic**
(`transcript-splitting-a-delegation-is-atomic-and-19ff4398f60`), **the turn
stream's wire vocabulary has one owner** (`the-turn-stream-s-wire-vocabulary-has-one-owner-1a003231bba`,
lesson survives its fixed bug), **a flex item with default min-width defeats
ws-truncate**, **a background shorthand resets the caret it paints**, and
**your apps are NOT committed** (`your-apps-are-not-committed-the-tracked-violatio-1a03493734e`).

### Stale or wrong: the highest-value section

A stale note is worse than no note. Each entry below states what the note
claims and what is actually true at HEAD. Nothing here was deleted.

**Entity and app model**

- `apps-and-projects-have-no-bridge-finance-can-t-b-19fd02d03f8` (`workshop`).
  Says the registries are unlinked and tells you to check `backend/apps.json`.
  That file no longer exists (it is `.runtime/apps.json` now),
  `services/app_links.py::linked_project_slug` **is** the bridge, and
  `POST /api/apps` takes `project_slug` (`routes/apps.py:79-87`). Also says
  `apps/` holds only `budget/knowledge`; it holds birds, budget, commute,
  location.
- `the-empty-app-page-column-and-agents-keep-naming-1a0208f976b` (`workshop`).
  Its literal greps still hold, but its conclusion, "the shared slug is a
  coincidence", is now wrong: slug equality is the deliberate link rule
  (`app_links.py:46`).
- `scope-is-a-location-workshop-and-projects-only-1a0028fb2d1` (`workshop`).
  Wrong. Scope kinds are development, workshop, project, app, agent, role
  (`scope_tree.py:103-105`). Apps and agents are first-class scopes with their
  own trees, not "participants".
- `an-app-resolves-to-two-different-roots-depending-1a02fe8d022` (`development`).
  Half fixed. `app_files.py:154-194` now prefers an app-declared manifest root;
  `paths.scope_root` still returns `apps/<slug>`. The divergence is arbitrated,
  not eliminated.
- `tickets-need-name-description-comments-and-scope-1a0028fb2c7` (`development`).
  Contradicts ADR 0001, which pressure-tested and **rejected** separate title
  and description fields. Following this note re-opens a settled call.

**Autonomy and the work loop**

- `handoff-tickets-surface-automation-loop-2026-08-19ff9747432` (`workshop`).
  Says the loop is complete and DORMANT with no resident runner. Wrong:
  `scripts/queue_runner.sh` + `queue_runner_watchdog.sh` exist and
  `services/work_driver.py` is a lifespan reconciler that starts runners for
  armed scopes. Only the cron line is still commented out
  (`infra/cron/workshop.crontab:70`).
- `autonomy-toggle-was-not-accessible-on-the-usage-1a0028fb2a4` (`workshop`).
  Fixed. `AutonomyControls` renders on Settings (`Settings.tsx:32,109`), plus a
  per-project `AutonomyPanel`.
- `autonomy-worker-loop-wiring-was-broken-with-no-u-1a0028fb2a9` (`workshop`).
  Same. Wired.
- `autonomy-disabled-until-supersession-dry-run-bla-1a0028fb2ca` (`workshop`).
  Autonomy is not disabled.
- `the-autonomous-loop-has-never-been-able-to-write-1a002e04bad` (`workshop`).
  Half stale. `scripts/ralph.sh:536-541` now sets `--permission-mode`, gated on
  `WORKSHOP_UNATTENDED_WRITES`. `scripts/project_ralph.sh` still has none, so
  the failure is real there and only there.
- `autonomy-containment-is-coordination-based-pendi-1a0028fb2d8` (`workshop`).
  The uid layer exists (`scoped_exec.py:491-545`), opt-in via
  `WORKSHOP_TURN_USER` and carving out the platform repo. Not "pending".
- `tickets-two-stores-and-which-one-a-writer-reache-1a00881d657` (`workshop`).
  Fixed and inverted. `tasks.py:1432-1460`: a relational scope writes Postgres
  with `required=True` and does **not** write `.tasks.json`; reads raise rather
  than fall back. The asymmetry this note warns about is the defect that was
  removed.
- `run-job-chat-linkage-is-a-separate-workstream-1a0028fb2b0` (`workshop`).
  Built 2026-08-20, see `fire-and-continue-how-a-chat-acts-on-a-finished-1a020796c2f`.
- `manual-work-until-the-ticket-system-proves-relia-1a0028fb2a6` (`workshop`).
  Posture predates the working loop. Read alongside the runner evidence above.
- `ticket-implementation-had-leftover-junk-needing-1a0028fb2cc` and
  `work-pages-renamed-to-tickets-1a0028fb2b7` (`development`). Both overtaken by
  ADR 0028.

**Chat and UI**

- `five-fixed-color-scheme-pairs-decided-1a0028fb2e5` (`workshop`). The most
  misleading UI note in the store. Names Violet, Cobalt, Teal, Plum, Sandstone.
  `packages/ui/src/styles/palettes.gen.ts:27-34` has eight: lagoon, iris,
  cobalt, fern, clay, slate, linen, graphite. Only Cobalt survives.
- `chat-modes-were-named-direct-and-orchestrated-1a0028fb2d4` (`workshop`).
  Superseded by the Tickets rename, and never deleted.
- `chat-modes-are-named-direct-and-tickets-1a0028fb2de` (`workshop`). Also now
  incomplete: a third mode `auto` ("Auto", Play icon) shipped 2026-08-15
  (`chatModes.ts:38-60`). Stored ids are still `direct`/`orchestrated`/`auto`.
- `mid-turn-chat-bubbles-should-render-dimmed-still-1a0028fb2e9` (`workshop`).
  Says "remains broken after attempted fix". It works:
  `.chat-bubble--inflight { opacity: 0.55 }` (`styles.css:2291`), applied at
  `MessageList.tsx:292`, with a test.
- `defect-confirmed-ui-card-prompt-submit-is-broken-1a025e170f1` (`workshop`).
  Fixed 2026-08-23 in `466cab93`. The note tells an agent not to use ui-card
  prompts at all, which is now a real capability loss.
- `defect-prompt-clarifying-question-card-mishandle-1a025cf4832` (`workshop`).
  Its second problem (typed answer rejected) is fixed. Its first (a card
  answered late or out of band) is still undefined behaviour.
- `a-lazy-route-blanks-permanently-on-a-stale-chunk-19ff45161c0` (`workshop`).
  Fixed. `RouteBoundary` + chunk auto-reload at `lib/lazyPage.tsx:66,117,134`;
  `registry.tsx:325-330` no longer uses `fallback={null}`.
- `nul-bytes-in-a-source-file-make-it-invisible-to-1a007155a4e` (`workshop`).
  `ProjectDetail.tsx` has zero NUL bytes now. The general lesson (confirm a
  grep actually read the file before concluding a symbol is unused) still holds.
- `chat-bugs-were-front-end-state-machine-defects-1a0028fb2ae` (`workshop`).
  A symptom list, superseded by the 2026-08-25 note that names the root cause.
- `chat-composer-input-retention-fixed-via-clearnow-1a0028fb2c8` (`workshop`).
  Already self-corrected in place. Kept as the worked example of what a stale
  note costs: it said "fixed", named a `clearNow` that no longer exists, and
  sent every later agent looking elsewhere.
- `chat-drafts-are-keyed-per-thread-not-per-scope-1a0028fb2ce` (`development`).
  Imprecise. The key is `${contextKey}::${activeId}` (`Chat.tsx:454`), so the
  scope is still half the key and a scope has its own `::new` slot.
- `workshop-ui-is-a-real-package-at-packages-ui-2-0-19feea2363f` (`workshop`).
  Version is **0.6.2**, not 0.2.0. Note that `.runtime/apps.json` manifests
  still pin `@workshop/ui@^0.2`, which is its own problem.

**Context and memory**

- `operator-removed-automatic-compaction-and-foldin-1a0028fb2d4` (`development`).
  Says "use native `/compact` only". Directly contradicted by the 2026-08-25
  measurement: `/compact` is dead in `-p` and automatic compaction never fires.
  An agent reading only this note plans around a fallback that does not exist.

**Gates and tooling**

- `audit-personal-project-data-hardcoded-in-platfor-19fecee4f75` (`workshop`).
  Its subject, `scripts/check_pluggable_apps.sh`, has been replaced as the gate
  by `scripts/check_no_app_names.py` (see root `CLAUDE.md`). The inventory is
  historical; the current one is §6 of `HANDOFF-app-platform-and-github-2026-08-20.md`.
- `skill-proposal-cc2e61fcd30f` (`workshop`). A proposal awaiting operator
  confirmation since before 2026-08-14. Never actioned either way.

**Agent scopes**

- `.runtime/agents/<bridge-agent>/knowledge/letta-human.md` and
  `.runtime/agents/<bridge-agent>-commute/knowledge/letta-human.md`. Both are
  byte-identical copies of the `<service-user>` user-scope `CLAUDE.md`, frozen at the
  2026-08-16 Letta export. That file reloads from disk on every compaction, so
  these are a snapshot of something that moves.
- `.runtime/agents/<bridge-agent>-commute/knowledge/letta-persona.md`. Mislabelled: the
  "persona" block for the **commute** agent is the whole <bridge-agent> **bridge**
  CLAUDE.md. It also asserts a live-state table as authoritative ("Gmail
  connected, Calendar needs OAuth") and a model ("Opus 4.7, 1M context") from a
  system outside this tree. Treat as an archive artifact, not as current state.

**Suspect but not verified**

- `chat-header-row-layout-1a0028fb2b8` (`workshop`). A 2026-08-14 enumeration of
  header controls, written before several toolbar changes. Not checked here.
  Verify against the source before relying on it.

### Duplicates

- **Ticket dependencies**, twice: `ticket-task-dependency-tracking-is-required-1a0028fb297`
  is a one-line restatement of `ticket-dependencies-first-class-edge-model-adden-19f7440aaf0`.
- **Scoping happens in the main thread**, three times:
  `scoping-and-decomposition-happen-in-the-main-thr-1a0028fb296`, §1 of
  `orchestration-surface-requirements-addendum-re-s-19f74360f21`, and
  `workshop.jsonl` learning #1.
- **Research before doing**, four times: `deep-research-before-autonomous-execution`,
  `deep-exploration-before-splitting-work-into-tick`,
  `research-first-before-design-or-styling-decision`,
  `comprehensive-system-analysis-required-before-fe`. Plus `workshop.jsonl` #18.
- **Autonomy without prompting**, twice: `autonomous-work-execution-once-directed-no-magic`
  and `no-step-turn-limits-no-repeated-continue-prompts`.
- **Nothing hardcoded**, three notes plus the root `CLAUDE.md` plus a learning:
  `workshop-must-be-deployable-with-nothing-hardcod`,
  `paths-and-machine-identity-must-derive-from-conf`,
  `apps-must-be-pluggable-not-enumerated-in-platfor`, and `workshop.jsonl` #13.
- **The Tickets rename**, four times: two notes, [ADR 0016](decisions/0016-tickets-is-the-surface-name.md),
  [ADR 0028](decisions/0028-tickets-is-the-name-everywhere-the-operator-can-see.md).
- **Chat mode names**, twice, contradicting each other (see stale list).
- **Filing a ticket is not progress**: note plus `workshop.jsonl` #5, verbatim.
- **Verbose note capture**: note plus `workshop.jsonl` #3 and #4.
- **Color decisions**: `five-fixed-color-scheme-pairs` +
  `status-colors-stay-byte-identical` restate [ADR 0012](decisions/0012-color-schemes-are-generated-status-is-fixed.md),
  and the ADR is the one that stayed correct.
- **App/project split investigation**, two notes from the same 2026-08-04 sweep:
  `apps-and-projects-have-no-bridge` and `prior-art-survey-build-run-use-in-one-system-and`.
- **`notes-and-docs-policy.md`** is mirrored as the `notes-and-docs` skill and
  restated in the root `CLAUDE.md`. Deliberate, per the note itself.
- **`letta-human.md`** across two agent scopes (see stale list).

### Full enumeration by subject

Every note, one line. `[W]` = workshop scope, `[D]` = development, others named.

**Entity and app model**

- `[W] canonical-entity-model-chat-ticket-run-agent` Chat/Ticket/Run/Agent, sub-tickets capped at 2 levels.
- `[W] workshop-terminology-needs-clear-distinct-roles` jobs/runs/agents/tickets were confused and need distinct roles.
- `[W] agent-system-needs-one-coherent-definition-metho` seven competing agent definition paths, want one.
- `[W] agents-must-come-from-explicit-declarations` no runtime synthesis of agents.
- `[W] agents-are-opt-in-mentionable-delegatable-config` most agents should be neither mentionable nor delegatable.
- `[W] apps-are-data-tool-grants-and-personas` an app is grants plus a persona, scoped to itself.
- `[W] apps-must-be-pluggable-not-enumerated-in-platfor` routes from manifest, not static imports.
- `[W] apps-and-projects-have-no-bridge-finance-can-t-b` STALE, see above.
- `[W] the-empty-app-page-column-and-agents-keep-naming` STALE conclusion, see above.
- `[W] prior-art-survey-build-run-use-in-one-system-and` five prior-art families for build/run/use in one system.
- `[W] chats-have-user-changeable-scope` a chat's scope is global, project or app, and the user changes it.
- `[W] scope-is-a-location-workshop-and-projects-only` STALE.
- `[D] an-app-resolves-to-two-different-roots-depending` PARTLY STALE, two roots for `app:<slug>`.
- `[D] proposed-record-vs-file-test-and-tree-as-homes-p` unratified nav proposal: record vs file test, tree as homes.
- `[D] your-apps-are-not-committed-the-tracked-violatio` `git ls-files apps/` is 0; 102 tracked files are app source.

**Work loop and autonomy**

- `[W] tickets-are-the-approval-unit-for-autonomous-wor` approve the plan, the agent executes inside it.
- `[W] autonomy-gates-per-action-reversibility-not-per` gate on action reversibility, not a per-agent switch.
- `[W] reversibility-gate-denies-actions-when-unwatched` irreversible actions denied when nobody is watching.
- `[W] autonomy-override-*` see `autonomy-disabled-until-supersession-dry-run-bla` (STALE) and `autonomy-containment-is-coordination-based-pendi` (STALE).
- `[W] autonomy-toggle-was-not-accessible-on-the-usage` STALE. `[W] autonomy-worker-loop-wiring-was-broken-with-no-u` STALE.
- `[W] the-autonomous-loop-has-never-been-able-to-write` HALF STALE, still true for `project_ralph.sh`.
- `[W] handoff-tickets-surface-automation-loop-2026-08-` STALE, the loop is not dormant.
- `[W] manual-work-until-the-ticket-system-proves-relia` and `[W] filing-a-ticket-is-not-progress` posture from before the loop worked.
- `[W] chat-system-needed-a-complete-overhaul-first` fix chat in-thread, not via the queue.
- `[W] fire-and-continue-how-a-chat-acts-on-a-finished` a finished run seeds the chat's own next turn, full wiring.
- `[W] in-chat-background-agent-spawn-background-how-it` the `spawn_background` MCP tool, end to end.
- `[W] run-job-chat-linkage-is-a-separate-workstream` STALE, it was built.
- `[W] chat-generated-tickets-propose-0-n-distinct-draf` propose 0..N drafts with confidence, do not squash to one.
- `[W] ticket-drafts-persist-on-threads` drafts survive on the thread, no re-generation.
- `[W] ticket-dependencies-first-class-edge-model-adden` depends-on/blocks/relates-to as first-class edges.
- `[W] ticket-task-dependency-tracking-is-required` duplicate one-liner of the above.
- `[W] tickets-two-stores-and-which-one-a-writer-reache` STALE, the split was closed.
- `[W] orchestration-surface-requirements-addendum-re-s-19f74360f21` scoping in-thread, re-scope warning wording.
- `[W] orchestration-surface-requirements-addendum-re-s-19f743cb2c2` cross-chat editing within a scope, resilience, card links.
- `[W] concurrent-agents-editing-chat-ui-race-on-files` parallel agents race on the same files.
- `[W] work-reviewer-had-invalid-yaml-for-months` lenient vs strict YAML parser mismatch hid it.
- `[D] never-leave-work-uncommitted-in-the-shared-tree` a sweeping worker resets the tree; commit per edit.
- `[D] a-card-that-files-a-duplicate-ticket-looks-like` dedup merges at cosine >= 0.90, so the card reports success and nothing appears.
- `[D] ticket-implementation-had-leftover-junk-needing` and `[D] work-pages-renamed-to-tickets` overtaken by ADR 0028.
- `[D] tickets-need-name-description-comments-and-scope` CONTRADICTS ADR 0001.
- `[D] kanban-style-status-columns-wanted-for-ticket-vi` and `[D] mobile-support-needed-for-ticket-views` open UI wants.

**Prompts, context and memory**

- `[D] a-chat-s-system-prompt-is-frozen-at-session-spaw` prompt assembled once at spawn, frozen for the chat's life.
- `[D] chat-protocols-self-refresh-how-a-contract-chang` `instruction_contract` re-states wire formats each turn.
- `[D] headless-claude-p-has-no-native-compaction-manua` measured: no manual and no automatic compaction in `-p`.
- `[D] operator-removed-automatic-compaction-and-foldin` STALE, contradicted by the above.
- `[D] the-prompt-prefix-had-a-size-problem-because-the` L0-L3 contract specifies authority and is silent on cost.
- `[W] note-capture-defaults-to-verbose-with-importance` capture verbosely, classify rather than trim.
- `[W] notes-and-docs-policy` scratch vs scoped Notes vs docs, and commit-or-not.
- `[W] skill-proposal-cc2e61fcd30f` unactioned skill proposal awaiting confirmation.
- `[D] working-state.md` pinned, auto-written at compaction, current subject is the chat flicker work.
- `[role:reviewer] review-evidence` a verdict without a command that was run is an opinion.

**Chat and transcripts**

- `[W] the-chat-sync-endpoint-is-a-single-point-of-fail` one endpoint, four faces.
- `[W] read-thread-reports-active-run-non-atomically-wi` server-side window CONFIRMED STILL OPEN.
- `[W] the-turn-stream-s-wire-vocabulary-has-one-owner` the `cut` frame had no listener; now wired, lesson stands.
- `[W] transcript-splitting-a-delegation-is-atomic-and` never divide a subagent chip across a mid-turn message.
- `[W] chat-message-ordering-follows-what-the-agent-cou` render in the order the agent could have seen them.
- `[W] chat-scope-one-race-caused-every-opens-blank-ope` one async scope flip caused every open-blank bug.
- `[W] chat-attachments-reach-the-model-as-content-bloc` native content blocks over stream-json, not file paths.
- `[W] chat-subagents-must-be-foreground-the-guard-fail` the guard fails closed on the CLI's default-background.
- `[W] chat-send-failures-the-edge-s-connection-pool-no` httpx keepalive vs uvicorn keep-alive, both reap at 5s.
- `[W] chat-default-mode-changed-to-direct` still true, `DEFAULT_CHAT_MODE = "direct"`.
- `[W] chat-modes-*` two contradicting notes, both now incomplete. STALE.
- `[W] chat-bugs-were-front-end-state-machine-defects` symptom list, superseded.
- `[W] chat-composer-input-retention-fixed-via-clearnow` self-corrected; the canonical worked example of a stale note.
- `[W] defect-confirmed-ui-card-prompt-submit-is-broken` STALE, fixed 2026-08-23.
- `[W] defect-prompt-clarifying-question-card-mishandle` half stale, late-answer handling still undefined.
- `[W] chat-should-show-related-tasks-runs-with-live-pr` want live run progress in the originating chat.
- `[W] turns-must-pick-up-new-messages-without-dropping` new messages join the next cycle, only Stop interrupts.
- `[D] chat-state-bugs-come-from-the-reducer-owning-onl` the render is a tuple that disagrees with itself.
- `[D] chat-load-flicker-root-cause-eager-full-history` eager paging prepended 18k to 74k px above the viewport.
- `[D] new-chat-first-message-jump-was-the-loading-plac` the Loading placeholder inserted above the message.
- `[D] the-chat-thought-fold-26ae0a2-is-inert-productio` no thinking_delta arrives, so the fold never fires.
- `[D] chat-restores-scroll-position-per-thread-via-ses` sessionStorage per thread, still true.
- `[D] chat-drafts-are-keyed-per-thread-not-per-scope` imprecise, the key includes contextKey.
- `[D] askuserquestion-is-denied-in-chat-turns` baseline-disallowed, use a ui-card or prose.
- `[D] silence-threshold-tuning-cannot-fix-voice-fragme` needs turn merging, not parameter tuning.
- `[D] voice-worker-needs-full-history-and-persistent-s` still true, `--resume` sessions.

**Usage and cost**

- `[D] the-usage-ledger-counts-four-different-questions` 83.5% test rows, three metering defects in `build_streaming.py`.
- `[D] a-monthly-usage-cap-derives-from-the-plan-s-pric` derive from the plan price, never from this installation's telemetry.
- `[D] context-meter-shows-rounded-whole-percentages-on` whole percents, no tilde. Still true.

**Tooling and gates**

- `[W] claude-code-permission-modes-need-permission-pro` `--permission-prompt-tool stdio` or silent auto-deny.
- `[W] response-model-on-a-route-is-not-enough-its-fiel` the same drift survives one level down, in the fields.
- `[W] gzip-middleware-must-be-registered-first-or-it-s` register first so it is innermost. Verified true.
- `[W] deep-search-dies-with-the-gpu-no-lexical-fallbac` self-corrected: `search()` is hybrid, BM25 on embed failure.
- `[W] nul-bytes-in-a-source-file-make-it-invisible-to` file is clean now, lesson stands.
- `[W] a-lazy-route-blanks-permanently-on-a-stale-chunk` STALE, boundary and chunk reload exist.
- `[D] importing-backend-main-writes-live-instance-state` import is not read-only. UNINDEXED.
- `[D] tasks-update-needs-a-path-not-a-str` `str` root crashes four frames down in `_locked`. UNINDEXED.
- `[D] empirical-testing-required-to-verify-fixes` code reading is not verification.
- `[D] derived-state-fields-must-never-be-directly-patc` patch the source, let derivation compute dependents.
- `[project:development] db-tests-error-rather-than-skip-without-the-admi` 85 fake errors without the admin env.

**UI and the design system**

- `[W] a-background-shorthand-resets-the-caret-it-paint` use `background-color` in every state rule, name the longhand.
- `[W] a-flex-item-with-default-min-width-silently-defe` `min-width: 0` or `ws-truncate` never ellipsises.
- `[W] focus-ring-inherits-border-radius-not-forced-on` inherit the radius, do not force it on descendants.
- `[W] modal-is-the-default-dialog-pattern` drawer only when functionally necessary.
- `[W] no-borders-for-visual-hierarchy` position/scale/weight/color instead. Corrected repeatedly.
- `[W] ui-info-on-hover-via-title-attribute-not-inline` no inline explainer text.
- `[W] status-colors-stay-byte-identical-across-all-pal` ok/warn/danger fixed across palettes. Still true.
- `[W] five-fixed-color-scheme-pairs-decided` STALE, eight palettes now.
- `[W] model-picker-belongs-in-the-composer-fable-5-fir` still true.
- `[W] mid-turn-chat-bubbles-should-render-dimmed-still` STALE, it works.
- `[W] mobile-chat-mode-selector-icon-closed-text-in-dr` icon closed, text in the dropdown.
- `[W] chat-header-row-layout` SUSPECT, not verified.
- `[W] workshop-ui-is-a-real-package-at-packages-ui-2-0` version is 0.6.2, not 0.2.0.
- `[D] chat-width-1200px-intermediate-full-width-toolba` `--page-max: 1200px`. Still true.
- `[D] device-list-pages-10-items-newest-first-deduped` still true, both halves.

**Security, boundaries and portability**

- `[W] work-org-is-a-hard-boundary-kept-out-of-this-ins` never reference or depend on the operator's work org; the blocked-owner list comes from instance config (`WORKSHOP_BLOCKED_REPO_OWNERS`), never from source.
- `[W] workshop-must-be-deployable-with-nothing-hardcod` no personal or machine data in platform source.
- `[W] paths-and-machine-identity-must-derive-from-conf` paths, hostnames, snowflakes, map center from config.
- `[W] personal-data-files-should-be-gitignored` relay corpus and store policies untracked.
- `[W] audit-personal-project-data-hardcoded-in-platfor` historical inventory; its gate has been replaced.

**Operator working preferences** (all `[W]`, all migrated 2026-08-14 one-liners)

`autonomous-work-execution-once-directed-no-magic`, `no-step-turn-limits-no-repeated-continue-prompts`,
`deep-research-before-autonomous-execution`, `deep-exploration-before-splitting-work-into-tick`,
`research-first-before-design-or-styling-decision`, `comprehensive-system-analysis-required-before-fe`,
`comprehensive-handoff-documentation-is-wanted`, `prefer-rework-and-integration-over-feature-layer`,
`proactive-cleanup-and-ownership-over-feature-sta`, `system-design-must-prioritize-usability-over-tec`,
`queue-state-must-be-visible-with-obvious-afforda`, `task-filing-should-learn-from-operator-patterns`,
`fable-review-and-refine-improves-design-plans`, `scoping-and-decomposition-happen-in-the-main-thr`.

**Per-agent and per-project**

- `[agent:<bridge-agent>] letta-persona` supervisor persona, Letta export 2026-08-16.
- `[agent:<bridge-agent>] letta-human` copy of the <service-user> user-scope CLAUDE.md.
- `[agent:<bridge-agent>-commute] letta-persona` MISLABELLED, it is the bridge CLAUDE.md.
- `[agent:<bridge-agent>-commute] letta-human` second copy of the same CLAUDE.md.
- `[project:commute] user-note-july-23-mryacgeo` operator wants a near-home train alert, partially buildable now.

---

## Part 2: the decision records

`docs/decisions/` holds `0000-template` plus **0001 through 0029, contiguous,
no gaps**. One stray non-ADR file:
[`loop-demo-2026-08-21.md`](decisions/loop-demo-2026-08-21.md), whose entire
content is "loop verified through the chat". Test residue in the ADR directory.

### In force

- [0002](decisions/0002-notes-substrate-is-knowledge.md) accepted. The scoped knowledge store is the one notes substrate. 0022 sits on top of it.
- [0003](decisions/0003-github-is-per-project-binding.md) accepted. GitHub is a per-project repo binding on the operator's personal account. Under pressure from `app-is-the-entity-2026-08-20.md`, which retires Project as an entity.
- [0008](decisions/0008-autonomy-override-semantics.md) accepted. Autonomy override is a file-flag overlay, not a per-agent mutation.
- [0009](decisions/0009-single-canonical-status-source.md) accepted. One canonical status source per run. Extended in prose by `lease-and-status-authority-2026-08-24.md` and `work-status-contract-2026-08-24.md`.
- [0010](decisions/0010-notes-vault-is-read-only-to-agents.md) accepted. The `$WORKSHOP_NOTES_VAULT` vault is read-only to agents.
- [0012](decisions/0012-color-schemes-are-generated-status-is-fixed.md) accepted. Schemes are generated from one model, status color is fixed across them. The mechanism held while the note listing the five names did not.
- [0013](decisions/0013-public-autodeploy-is-pre-launch.md) accepted. Committing to main publishes; treat that as pre-launch, not production.
- [0014](decisions/0014-developer-mode-gates-the-resolved-directory.md) accepted. Developer mode gates the resolved directory, not the scope name.
- [0015](decisions/0015-transcript-structure-and-content-provenance.md) accepted. Transcript is Turn/Group/Step and injected content carries an origin.
- [0017](decisions/0017-spoken-text-is-recorded-at-capture.md) accepted. Spoken text is persisted at capture; unsent speech is a draft.
- [0018](decisions/0018-transcript-order-is-recorded-not-reconstructed.md) accepted. Order is recorded, not reconstructed. Both halves of the `cut` frame are now wired.
- [0020](decisions/0020-a-transcript-is-a-tree.md) accepted. A transcript is a tree and it lives in PostgreSQL.
- [0021](decisions/0021-a-thread-admits-one-operation-at-a-time.md) accepted. One operation at a time per thread, and operations land on a quiet thread.
- [0022](decisions/0022-memory-is-three-tiers-and-reconciles-on-write.md) accepted. Memory is three tiers and reconciles on write. This index is its overdue inventory.
- [0027](decisions/0027-agent-triggered-input-in-chat.md) accepted. Agent-triggered input goes through a ui-card; `AskUserQuestion` stays banned.
- [0028](decisions/0028-tickets-is-the-name-everywhere-the-operator-can-see.md) accepted. Tickets is the name everywhere the operator can see.
- [0029](decisions/0029-app-used-styling-consolidates-into-the-kit.md) accepted. App-used styling consolidates into `@workshop/ui`; app-specific styling stays with the app.

### Overtaken

- [0001](decisions/0001-task-store-is-canonical.md) accepted, 2026-07-15. The Task store is the canonical ticket substrate. Its **storage** claim is superseded by [0019](decisions/0019-work-data-lives-in-postgres.md). Its other two holdings stand and are still load-bearing: one work concept, and no separate title/description fields.
- [0006](decisions/0006-auto-run-learning-mechanism.md) proposed. Auto-run learning. Superseded for the chat-policy case by `docs/platform-redesign/tickets-in-chat-2026-08-15.md` §7, which shipped the `auto` chat mode. The per-kind preference it actually proposes was never built.
- [0007](decisions/0007-entity-model-as-first-class-backend-concepts.md) accepted. The entity model doc is the single authoritative reference. Its App-vs-Project split is superseded by `docs/platform-redesign/app-is-the-entity-2026-08-20.md`.
- [0011](decisions/0011-context-memory-is-managed-by-the-model.md) accepted, 2026-08-04. The model manages the conversation. Undercut by the 2026-08-25 measurement that `-p` has no compaction at all, and by `chat-context-and-compaction-2026-08-25.md`.
- [0016](decisions/0016-tickets-is-the-surface-name.md) accepted. The rename stops at the label. That clause is superseded by 0028; the choice of the word stands.
- [0025](decisions/0025-one-owner-per-server-entity.md) proposed. Partly overtaken by `client-state-management-2026-08-25.md`, which sequences it and adds a turn-lifecycle union it does not cover.

### Still proposed, waiting on their plan docs

- [0004](decisions/0004-agent-is-a-third-identity-kind.md) Agent identity is a third kind beside operator and guest.
- [0005](decisions/0005-resource-policy-rules-engine.md) Resource policy is a homegrown JSON plus prose engine, not OPA/Rego.
- [0019](decisions/0019-work-data-lives-in-postgres.md) Work data lives in PostgreSQL. **Status line lags the code**: `tasks.py` already writes Postgres as required for relational scopes.
- [0023](decisions/0023-the-http-contract.md) The HTTP surface has one contract. Gated on `target-architecture-2026-08-17.md` leaving PROPOSAL.
- [0024](decisions/0024-a-domain-is-a-package.md) A domain is a package and the import graph is a DAG. Same gate.
- [0026](decisions/0026-one-identity-root-and-tenancy-is-a-column.md) One identity root, one id scheme, tenancy is a column.

### Decisions that should be ADRs and are not

The convention is that a durable choice with a rejected alternative goes in
`docs/decisions/`. These were settled in prose instead, mostly on 2026-08-24
and 2026-08-25. ADR 0029 was committed in the same session that left several of
them unfiled.

1. **The unit is dollars at published API prices.** `counting-tokens-2026-08-25.md` §5 and `limiting-usage-2026-08-24.md`. Store four raw token components, derive dollars from a versioned price table, no Workshop token unit. The "effective tokens" composite was explicitly withdrawn.
2. **The monthly cap governs autonomous work only.** `limiting-usage-2026-08-24.md`, operator ruling 2026-08-25. Chat is never capped, warned or drawn down; `plan` starts unset and is prefilled, never inferred; admission refuses for two independent reasons.
3. **`done` is refused at write time when the change is not merged.** `work-status-contract-2026-08-24.md` §6 and §7. Configurable only as a closed set of conditions on `acceptance`, `merged` the sole default.
4. **Work routes on `effort`, through a settings-owned policy table.** `routing-work-to-a-model-2026-08-25.md` §0 and §8. No rename, no per-ticket `model` field. The doc itself says "nothing here is open".
5. **A planned run is an existing run in `queued`.** `planning-a-run-before-it-runs-2026-08-25.md` §0. No new table, object or status word; holds no claim, lease or worktree.
6. **The chat prefix has a hard budget, enforced at three levels.** `context-assembly-budget-2026-08-25.md` §4 and §5. `PREFIX_CEILING_CHARS = 46_000`, a ratcheted gate, and assembly that logs rather than failing a turn.
7. **Hosting is two tiers plus bring-your-own-host, and the platform never names a provider.** `hosting-apps-design-2026-08-24.md` §2.4, §4.1, §4.2. Carries an in-line retraction of a three-tier sketch.
8. **`auto_when_green` is deleted; merge policy is two states.** `merge-policy-levels-2026-08-24.md` §4.4 and §5, which even has a "Rejected, with reasons" section.
9. **A ticket's status has exactly one authority, proven by a fencing generation.** `lease-and-status-authority-2026-08-24.md` §2. Adjacent to ADR 0009, not covered by it.
10. **An agent-filed ticket enters as `draft`, enforced in `tasks.add()` not in prompt text.** `who-filed-this-and-what-was-decided-2026-08-25.md`. Cites a prior failure of the prompt approach.
11. **Delete the ticket-level `owner`; keep `assignee`.** Same doc. `owner` now means tenancy and nothing else.
12. **State is never polled into a conversation.** `chat-context-and-compaction-2026-08-25.md`. Real events append once; everything else is pulled on demand.

Weaker but real: `app-loader-distribution-and-versioning-2026-08-24.md` §4.2
(the platform is the distribution channel, options A to E weighed) and the
"decisions get ONE home" rule in `project-context-files-and-flows-2026-08-25.md`,
which is still framed as a recommendation.

---

## Part 3: the learnings store

`.runtime/learnings/*.jsonl`, injected into prompts by
`services/learnings.memory_block`. Chat path:
`chat_context.learnings_context` via `routes/chat.py:1588`. Sweep path:
`scripts/ralph.sh:425-428`. Budget per rendered block:
`_MAX_BLOCK_ENTRIES = 60` and `_BLOCK_TOKEN_BUDGET = 900`
(`services/learnings.py:87,98`), ranked by recency, reinforcement, severity and
retrieval.

### What is in it

- `workshop.jsonl`: **28** entries, 2026-07-18 to 2026-08-26. The good one.
  Operator doctrine: no explanatory copy, design for production not this box,
  no app names in platform source, a backend capability is not delivered until
  it is visible in the UI, screenshot at 390px and 1440px, use a
  MutationObserver not a rAF sampler for flicker.
- `global.jsonl`: **12** entries, all 2026-08-17. Completion-gate rules: no
  stub bodies, no `[x]` with caveats, wire the router, wire the route, do not
  claim a restart you did not check.
- `development.jsonl`: **8** entries. 6 are good (escalate only a genuine
  operator choice, do pipeline surgery in-chat, name a concept after what it
  does, do not ask through a broken UI surface, only `check_fresh_clone.sh`
  proves app separation, fold narration not thinking). **2 are false.**
- `project-development.jsonl`: **9** entries. **All 9 are false.**
- `project-workshop.jsonl`: **1** entry. **False.**
- `archive/`: 21 previously retired entries, with reasons.

`project-development.jsonl` and `project-workshop.jsonl` are pre-fix residue:
their newest rows are 2026-08-23 and 2026-08-22, while `development.jsonl` and
`workshop.jsonl` are still being written. The scope-key fix landed in
`3eaf0e04` (`scripts/project_ralph.sh:970-987`).

### The false entries

Same defect the prior session archived three of. The shape is:

> Ralph keeps hitting "<gate>" on this task; the direct approach is not
> converging, try a different tactic: <ticket title>

Each was true while its ticket was stuck. **All twelve landed.** Verified at
HEAD:

- Loop demo doc: `docs/decisions/loop-demo-2026-08-21.md` exists.
- Scope a sweep's learnings by scope key: fixed in `3eaf0e04`.
- Usage page by-chat view and grouping toggle: `pages/Usage.tsx:55-59,260-307`.
- `useEditMode.save()` unhandled rejection: `packages/ui/src/useEditMode.ts:11-15,48`.
- Fold MemoryDrill onto kit FileDrill: `MemoryDrill` is gone, `Memory.tsx:487` uses `FileDrill`.
- Toolbar chat-switch picker race: `useChatScope.ts:118-135`, commits `5bf6a2a4` and `b0a72c64`.
- Resolve tool names against the registry: `services/tool_registry.py:388-407`, pinned by a test.
- Make a user-defined command tool run: `services/command_tools.py`, dispatched at `workshop_query_server.py:523-527`.
- Reclaim a locked worktree: `worktrees.py:527` unlocks before removing.
- Touches gate blaming other agents' commits: `project_ralph.sh:257-265` uses the receipt's own commit list.
- Four gates rejecting inherited debt: `e355f5b4` added `--since`; `reviewer.sh:109-155` documents `_delta_check`.

Two of them were recorded *after* the fix commit landed, within minutes.

Cost: these are ~200 chars each and carry high `render_count`, so they rank
well and consume a large share of the 900-token budget in the scope that
renders most often, displacing true guidance. The remedy is the same archive
path the prior session used. Not applied here.

One adjacent finding, unasked: `scripts/reviewer.sh:682-683` invokes
`check_routes_answer.py` without `--since` although the script supports it. It
is the one delta-capable checker not wired through `_delta_check`.
