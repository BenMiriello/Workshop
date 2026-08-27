# Index of open work and the conversations it came from

Generated 2026-08-26. Source of truth for tickets:
`python3 scripts/project_tasks.py list-json .` (live set only).
Source of truth for chats: `.runtime/users/6/chat/<thread-id>/`.
A thread is reachable in the UI at `/chat/<thread-id>`.

Counts at generation: 311 tickets total, 159 live (143 todo, 9 parked,
5 in_review, 1 blocked, 1 draft), 106 done, 46 cancelled.

Ticket ids are the first 8 characters. Band is the stored priority:
P100 top, P80 high, P40 normal, P1 and P0 low. Within each theme the
order IS the build order: a ticket's dependencies appear above it.

---

## Part 1: the tickets

### Theme 1: token accounting and usage limits (18)

The meter is wrong before any limit built on it can be right. Build in
this order.

- `189b9e0e` (P80) Add `token_usage.py` so one module parses a vendor
  usage block into named quantities and dedupes stream frames by API
  message id.
- `890156ea` (P80) Settle by measurement whether the CLI result envelope
  includes subagent tokens, or whether deduplicated frames are the
  authority. One observed turn disagreed 3x.
- `ccd78488` (P80) Stop the test suite appending into the real
  `.runtime/usage.jsonl`; `LEDGER_PATH` resolves at import and only five
  test files patch it.
- `c0bac5ad` (P80, depends `189b9e0e`) Store token components on every
  usage row instead of one derived integer whose unit is unrecoverable.
- `ea6f0c1a` (P80, depends `189b9e0e` `890156ea` `c0bac5ad`) Move the
  four usage writers (text chat, voice, worker, compaction) onto one
  module and one unit.
- `b83e417a` (P40, depends `c0bac5ad` `ccd78488`) Mark surviving rows
  `v:1` with their unit and delete the test-written rows.
- `b59f6200` (P1, depends `b83e417a`) Backfill components for the recent
  ledger from the CLI session transcripts.
- `16625867` (P80, depends `c0bac5ad`) Derive dollar cost from stored
  components via a versioned price table in `model_prices.py`.
- `d0b103e3` (P80) Record provider, model and cost on every usage row,
  taken from the figure the envelope reports. See contradictions.
- `ed3d497f` (P40) Record the model on every ledger row and the tier on
  every attempt, so tier escalation can be judged. See contradictions.
- `62813fb5` (P40) Make the subscription plan a stored setting that
  starts unset, prefilled from `claude auth status --json`.
- `80c9d3b0` (P40, depends `62813fb5`) Show dollar usage on the usage
  page with one line naming the assumed plan.
- `70860d58` (P100) Pause a run at the budget cap and resume it later
  from where it stopped, so the cap is hard and no work is redone.
  Replaces cancelled `32bd76cf`.
- `a731ec9b` (P40, depends `70860d58`) Split usage into a five hour
  session window and a monthly cap. Replaces cancelled `57dac664`.
- `972c1d8a` (P1, depends `70860d58`) Mark proxy-billed rows as not
  measurements and demote the size estimate to a scheduling hint.
  Replaces cancelled `4ea2a388`.
- `42bda52a` (P80) Recognise a run that failed on a Claude subscription
  usage limit and write `.runtime/provider-block.json`.
- `b154e84b` (P80, depends `42bda52a`) Refuse autonomous admission while
  the provider block is live, and resume on its own.
- `c971b8f9` (P1, depends `1f0d6dcd`) Measure the whole turn-1 window,
  not only the prefix this platform assembles.

### Theme 2: model routing and planned runs (14)

- `bc81b563` (P80, in_review) Give the existing provider and credential
  store HTTP routes and a Settings section, so a key can be registered
  at all.
- `e9a55a3c` (P40, depends `bc81b563`) Resolve a turn's model in one
  function, in one documented order, reporting which rung decided.
- `c7bd8a4f` (P40, depends `bc81b563`) Move model credentials into the
  owner's runtime partition and require the KEK in multi-user.
- `7d295a59` (P40, depends `e9a55a3c`) Add a local HTTP model provider
  behind the provider seam.
- `372c72f7` (P80) One resolver, `work_model.resolve`, answers which
  model runs a ticket from a stored band-to-tier policy.
- `0e68c881` (P80, depends `372c72f7`) `project_ralph.sh` asks the
  resolver instead of its own case statement.
- `4f52c95c` (P40, depends `372c72f7`) Settings edits the band-to-model
  policy and a ticket states its resolved model.
- `2c7c245e` (P40, depends `0e68c881`) A reviewer-failed run retries one
  tier up, capped by the band ceiling.
- `d0ea84b4` (P80) A Job record can exist in `queued` before anything
  starts it.
- `62262e77` (P80, depends `d0ea84b4`) A queued run stores the resolved
  model and which authority chose it.
- `ca29a29b` (P80, depends `62262e77`) The sweep adopts the ticket's
  queued run instead of minting a second one.
- `60972a9c` (P40, depends `ca29a29b`) Re-resolve the model at execution
  unless a person pinned it.
- `82afaea4` (P40, depends `d0ea84b4`) Closing an imported issue
  elsewhere cancels its planned run.
- `860966e5` (P40, depends `d0ea84b4`) List a ticket's runs on its page,
  planned run first. Supersedes cancelled `48070b04`.

### Theme 3: ticket provenance, owner and declined state (13)

- `dab8a00f` (P80) `source.kind` names the actor that actually filed a
  ticket; nothing defaults to `operator`.
- `9dbb1cf2` (P80, depends `dab8a00f`) Every evidence entry carries an
  actor, and creation writes one.
- `7df0ab96` (P40, depends `dab8a00f`) An agent-filed ticket lands as
  `draft`, not in the operator's live queue.
- `c58809c2` (P40) Add a `declined` status with a required reason,
  distinct from cancelled.
- `1ae4baa0` (P40, depends `c58809c2`) Filing a ticket matching a
  declined one is refused, naming the declined ticket.
- `4893c343` (P40) Delete the projected `owner` field; `assignee` is who
  holds it and `owner` means only the tenancy partition key.
- `3b1c5432` (P40) Delete `latitude` from tickets, agents, the scope
  cascade and every surface.
- `6a9dda58` (P40) Persist ticket `type` as a Postgres column for
  relational scopes.
- `5534f204` (P80) The tasks CLI silently discards a note and silently
  no-ops on a short id.
- `f873ada7` (P80) Give every open ticket the four labelled lines the
  readiness gate asks for.
- `3a95bdc9` (P1) `extract_refs` recognises a markdown doc path, so a
  ticket citing a design doc is reachable from that doc.
- `e7530e75` (P1) The chat-scan intake needs a done-signal; it
  re-proposes work that is already finished.
- `307544a7` (P0) Ship the two surfaces that make a triage decision
  visible where the operator already looks.

### Theme 4: prompt context and memory (17)

- `0e810bf9` (P80) Declare every prompt block, its store and its char
  cap in one `PROMPT_BLOCKS` registry.
- `4614bfee` (P40, depends `0e810bf9`) Gate the assembled prefix size
  with `scripts/check_prompt_size.py`.
- `a7e62aac` (P40) Add a cost section to the four-layer prompt contract.
- `f8fced4a` (P80) Index PROJECT.md and project-root markdown into the
  semantic corpus.
- `1f0d6dcd` (P80, depends `f8fced4a`) Inline only the PROJECT.md brief
  plus a recency window of Log entries, naming what was dropped.
- `9318323f` (P40, depends `1f0d6dcd`) Move the PROJECT.md decision log
  into its own DECISIONS.md.
- `8db95ee9` (P40) Tell an agent what memory exists at fixed per-turn
  cost, without a shopping list.
- `b8febb26` (P40) Let an agent read the whole document a search hit
  came from, and learn the corpus size before searching.
- `537437c1` (P80, in_review) Give the operator real search over notes,
  documents, chats and tickets, using the index no route reaches.
- `46b07a2b` (P80) Teach agents the ticket state vocabulary through the
  tool they already call, not through any system prompt.
- `81198145` (P0) Fold learnings into the knowledge store as a note kind
  and delete the separate store. Broken dependency, see below.
- `cb4e7ce0` (P40) Call a note a note and Memory the place, in code as
  well as UI.
- `152856ba` (P80) Run the trigger dispatcher from the backend; this
  host has no crontab.
- `ca23bf08` (P0, depends `152856ba`) Repoint `memory_keeper` to
  maintenance and schedule it, after 36 days frozen.
- `88a2ba81` (P0, depends `152856ba`) Give the note-triager a live
  trigger and adopt tended on one scope.
- `5fae4564` (P40, parked) Scope a sweep's LEARNINGS by scope key, not
  by the basename of its directory.
- `adf8dadb` (P1, depends `4a79ffc5`) One-time rewrite of raw absolute
  paths in historical transcripts into refs.

### Theme 5: the work loop, claims, leases and the reviewer gate (17)

- `0fb57b31` (P80) Reclaim must advance the fence; today a zombie's
  token stays valid in one window.
- `e9a313c3` (P80) An empty claim token is refused, never promoted to
  operator authority.
- `4c59ee01` (P80) A displaced worker stops at the heartbeat instead of
  working for another hour on a ticket it no longer owns.
- `791f334e` (P80, depends `0fb57b31`) Opening a change is what moves a
  ticket to `in_review`, and it carries the fence.
- `2e026c69` (P80, depends `791f334e`) Merging a change completes its
  ticket on every merge path.
- `91a477a5` (P80, depends `2e026c69`) Register the landed-ness check
  into the ticket store so the refusal holds for every writer.
- `e48268d0` (P80) A ticket may not read done while its work sits
  unmerged on a branch.
- `bbeacfd0` (P80) Let `acceptance` carry a normalized list of
  completion conditions.
- `f2364d6c` (P40) Store each scope's default set of completion
  conditions.
- `3df11386` (P40, depends `91a477a5` `bbeacfd0` `e48268d0` `f2364d6c`)
  Evaluate a ticket's full condition set on every done write.
- `e8c53c78` (P0) Give each unattended run its own worktree so the loop
  stops resetting the shared tree.
- `fae54a3e` (P80) Give chat-driven agents the same tree isolation the
  loop has.
- `c8cc9f3f` (P40, blocked) Show who holds the tree and let the operator
  take it back without losing work.
- `26af958e` (P40) Merge policy as a Settings screen, a deployment
  default, and two states instead of three.
- `7c095e56` (P0) Make the ratchet detect an inert mechanism, which is
  this repo's recurring defect.
- `5da7fb83` (P0) Move runs off the unpartitioned file store into
  Postgres.
- `d8ab8749` (P0, draft) End-to-end loop demo: file, arm, run, surface.

### Theme 6: app separation, extraction and the loader (23)

The extraction chain is a single line: nothing in it parallelizes.

- `2c13ef89` (P80) Get one app's CSS out of the platform kit and app
  pages off the legacy stylesheet.
- `403b3b88` (P80) Close the four blind spots in the app-name gate and
  the CSS coupling no gate sees.
- `e278fa5f` (P80) Build the fresh-clone assertion, the executable
  definition of app-separation done.
- `57d641b8` (P40) Rename every real app id in platform test fixtures to
  a fictional app.
- `9a609740` (P40) Take one app's name out of the kit, the shared bridge
  and the iOS widget.
- `64590936` (P40) De-hardcode app names in scripts, gates, ratchet
  baselines and env examples.
- `84028d47` (P40) Stand up `apps/` as the destination tree, with
  history, for all seven apps.
- `df91b7c4` (P40) Give the ten app modules hiding behind platform names
  their app's name.
- `1732ce4e` (P40, depends `84028d47`) Move the operator's app
  documentation out of the platform repo.
- `d65244c4` (P40, depends `84028d47` `df91b7c4`) Extract the health
  app, the rehearsal for the other six.
- `668a18f3` (P40, depends `2c13ef89` `d65244c4`) Extract the notes app;
  stop counting `notes_all.py` as app source.
- `2e958c37` (P40, depends `668a18f3`) Extract the budget app and its
  categorizer.
- `1fac9c03` (P40, depends `2e958c37`) Extract the finance app and the
  three plaid modules.
- `58136c09` (P40, depends `1fac9c03`) Extract the commute app, its
  15-module package and its native widget.
- `26dbe9b3` (P40, depends `58136c09`) Extract the location app and the
  OwnTracks pair that breaks `import main`.
- `1db9d9db` (P40, depends `26dbe9b3`) Extract the birds backend, the
  weather spine and the detection data.
- `9e554582` (P40, depends `1db9d9db`) Extract the birds frontend and
  its second Vite build.
- `38728986` (P40, depends `9e554582` `e278fa5f`) Retire the two
  enumerating registries and re-target the app-name gate.
- `86564637` (P80, depends `2c13ef89`) Stage 5 part one: build the app
  loader up to the fixture app and stop.
- `b3f900d0` (P40) Move the health app's settings panel out of the
  frontend tree and contribute it through the loader. Missing dependency
  on `86564637`.
- `0cbe76ac` (P40) Merge the two app registries into one table with a
  stable id and a renameable slug.
- `f06da673` (P40, depends `0cbe76ac`) Retire `/agents/build/{slug}` and
  `/base-apps`, serve apps from one REST surface. Premise verified live.
- `e5848c87` (P40) Correct the stage 5 and stage 4b plan docs where the
  tree has moved past them.

### Theme 7: hosting, builds, releases and containers (16)

- `726d75a9` (P40) Delete the ~1,850 line deploy subsystem that has
  never executed and logs noise every five minutes.
- `9ed7a34b` (P80) Parse a `deploy` block in `workshop.app.json`
  declaring artifact kinds.
- `fb93c4d2` (P80) Add a content-addressed artifact store under
  `.runtime/artifacts`.
- `80709722` (P80, depends `fb93c4d2`) Add Release and Deployment
  records with content-addressed digests.
- `0cae7ab2` (P80, depends `80709722`) Build an app release from a
  disposable worktree, not the source tree.
- `989ea7cc` (P40, depends `80709722`) Sweep unreferenced artifacts and
  releases on request.
- `967eaa2a` (P40, depends `0cae7ab2`) Generate compose from an
  extracted immutable release, mounted read-only.
- `4d681411` (P40, depends `0cae7ab2`) Show an app's builds, its failing
  step and the release still serving.
- `ffa91fb5` (P80) Split `app_runner._project_dir` into `source_dir` and
  `release_root`.
- `484b46cb` (P40) Record a run mode per app so a bind-mounted dev
  container is named as one.
- `97e0d73c` (P40) Compose tests write into the live runtime directory,
  and the one running container is unhardened.
- `4a9dbab2` (P80) Evaluate a caret kit range in one pure backend
  module, with the zero-major rule right.
- `fbbc55a7` (P80) Delete the dead `kit` field and `DEFAULT_KIT` from
  the app manifest store. Premise verified live in `manifests.py`.
- `d36d4fb5` (P80) Add `packages/app-build` with build-time
  compatibility refusals.
- `0a83ac01` (P40) Pack the kit into a platform-owned channel an
  out-of-repo app installs from.
- `1cb21f41` (P40) Record shared runtime versions in
  `frontend/dist/build-info.json`.

### Theme 8: the styling kit consolidation (5)

- `8f80d34f` (P40) Move the 14 generic classes app pages use from
  `frontend/src/styles.css` into the kit as `.ws-` classes.
- `f46eb6b3` (P80) Point the 299 legacy `.muted` and `.card` call sites
  at the kit classes.
- `613e1b62` (P40) Move the notes app's 92 lines of CSS out of the kit.
- `f4fcf6a7` (P40) Replace the birds app's own `.btn`, `.pill` and
  `.danger` with the kit's.
- `ef321ef0` (P1) Fix the six CSS variables the kit reads but nothing
  defines.

### Theme 9: chat, transcript and the editor surface (13)

- `97b0f04a` (P40, parked) Make the chat mirror unable to drift and
  repair the three drifted threads. Parked, and it blocks the whole
  storage chain.
- `1638d7f9` (P40, depends `97b0f04a`) Chat storage stage 2: serve
  transcript reads from rows. Attempted 2026-08-14 and reverted.
- `37617b0a` (P40, depends `1638d7f9`) Stage 3: delete the three backend
  reconstructions of message order.
- `e1f9d92a` (P1, depends `37617b0a`) Stage 4: retire the JSONL log as a
  store.
- `29d24fb1` (P0) Redesign chat branching on the standard edit/retry
  model with a branch list in the bubble.
- `2d046625` (P80) Return on a phone sends the message instead of
  inserting a newline.
- `ac4827b4` (P1) Chat auto-naming falls back to the raw first message,
  and the dev-scope default is racy.
- `06e6bf9e` (P1) Redesign the note receipt line under a reply; it does
  not read as a link.
- `b6852799` (P1, in_review) Give Memory a document view, an edit mode
  and a tree.
- `da0994f9` (P1, in_review) Rebuild the app Files tab on the editor and
  tree primitives.
- `c686ad35` (P1, depends `da0994f9`) Verify the file editor on a real
  phone and fix what the WebView breaks.
- `30777f91` (P1, parked) `useEditMode.save()` has no catch, so a failed
  save becomes an unhandled rejection.
- `100ba5d2` (P0) Two hand-rolled file trees survive beside the kit one,
  plus a dead file API. Finish after the two in_review tickets land.

### Theme 10: navigation shell and UI (5)

- `d4876c7c` (P0) Audit the toolbar and the chat-adjacent pages (runs,
  agents) against the design system. Its named `Jobs.tsx` target was
  already deleted in commit `505f5907`.
- `557ec533` (P0) Execute P5 (Run to Session rename) and P6 (memory
  layers).
- `6f119cf4` (P0) Memory drops the `?scope=` deep link on a phone, and
  app scopes have no label.
- `f5dd78cf` (P0) Show elapsed run time on the Agents page beside each
  agent's last run.
- `2acaaa4e` (P0) Keyboard shortcut that moves focus to the chat scope
  picker.

### Theme 11: security, SEC-prefixed (14)

See the dedicated section below. These need root or operator action.

### Theme 12: everything else (4)

- `7c3b804d` (P40) Explain or reduce the backend dependency cycle that
  grew to 110 modules, or record an honest baseline entry.
- `ec420a11` (P0) Testing tickets in chat UI. Diagnostic filed from a
  probe chat; render a proposal card.
- `2caa002b` (P0) Testing tickets in chat UI (2). Card retains its filed
  state across refresh.
- `6fff3fd9` (P0) Testing tickets in chat UI card-state repro.

The last three are throwaway diagnostics from 2026-08-15 probe threads
that were never closed. They are noise in the live queue.

---

## The critical path

Ranked by how many live tickets each one transitively unblocks.

1. `84028d47` Stand up `apps/`. Unblocks 10: the whole seven-app
   extraction chain and the doc move.
2. `2c13ef89` App CSS out of the kit. Unblocks 9: the extraction chain
   from notes onward, plus the stage 5 app loader `86564637`.
3. `df91b7c4` Name the ten disguised app modules. Unblocks 9: health
   extraction and everything downstream of it.
4. `d65244c4` Extract health. Unblocks 8: it is the rehearsal, and the
   other six extractions queue behind it.
5. `d0ea84b4` Queued run state. Unblocks 5: planned-run routing, the
   sweep adopting a run, run cancellation and the ticket runs list.
6. `189b9e0e` `token_usage.py`. Unblocks 5: components, one unit, the
   price table, the row migration and the backfill.
7. `fb93c4d2` Artifact store. Unblocks 5: releases, worktree builds,
   compose from a release, build history and GC.
8. `c0bac5ad` Token components on the row. Unblocks 4: pricing, the
   v1 migration, the writer move and the backfill.
9. `0fb57b31` Reclaim advances the fence. Unblocks 4: the entire
   change-to-ticket status chain up to condition evaluation.
10. `152856ba` Backend trigger dispatcher. Unblocks 2 directly and is
    the reason nothing scheduled has fired: the memory keeper and the
    note-triager both wait on it. A third dependant, `652dc696`, was
    cancelled.

Runner-up worth naming: `372c72f7` (one model resolver) unblocks 3 and
is the precondition for the sweep no longer routing by case statement.
`bc81b563` unblocks 3 and is the only way a credential can be registered
at all today.

## Broken or stale dependencies

Live tickets whose stated dependency cannot be satisfied.

- `81198145` (fold learnings into the knowledge store) has
  `depends_on: 652dc696`, which is **cancelled**. The ticket body knows
  this and says the condition is void, but the field was never cleared,
  so any dependency-aware runner will treat it as blocked forever. Clear
  the field.
- `1638d7f9` (chat storage stage 2) depends on `97b0f04a`, which is
  **parked**. Not broken, but it stalls three tickets: `1638d7f9`,
  `37617b0a` and `e1f9d92a`. Nothing in the chat storage migration can
  move until `97b0f04a` is unparked.
- `4a79ffc5` (SEC-A1) depends on `b66ea4c1`, already **done**. Satisfied,
  no action. Same for `e8c53c78` (`32c8e47b`), `5da7fb83` (`9dc3b4f5`),
  `1638d7f9` (`86f58aea`), `b6852799` and `da0994f9` (`24dc27a3`,
  `713d2fd9`, `e7ad4853`). Seven tickets carry a satisfied dependency
  that reads as an open blocker in the list view.
- `29d24fb1` names ticket `dc186a37` in its Boundaries. That id does not
  exist. The body carries its own correction naming `1638d7f9` instead.
  A worker reading only the first paragraph will chase a dead id.
- `b3f900d0` (health settings panel through the app loader) has **no
  dependency on `86564637`**, the ticket that builds the loader. It
  cannot be done first. Missing edge, not a broken one.
- `d4876c7c` names `frontend/src/pages/Jobs.tsx`, deleted in commit
  `505f5907`. The ticket body records the correction; the title does not.

Body references that look broken and are not: `48070b04` and `031b626c`
and `652dc696` are cancelled tickets cited as history (supersession or
"this is why"), not as work. `37732fe1`, `d4c82846`, `b734e4b7`,
`754facd9`, `7f9275a7`, `505f5907`, `f95ebb5c` are commit shas.
`57e6c4b8`, `4102a2fe`, `65341b40`, `f635a5e8` are chat thread ids or
sample output.

## Contradictions

- **`d0b103e3` against `16625867`.** Both write cost onto a usage row.
  `d0b103e3` records the dollar figure the result envelope reports;
  `16625867` computes it from stored components with a versioned price
  table. Neither depends on the other, both are live, both are P80.
  Whichever lands second either overwrites or duplicates the other's
  column. Decide which is the cost of record before starting either.
- **`d0b103e3` against `ed3d497f`.** Both add `model` to
  `usage_ledger.record()`. `ed3d497f` says outright that section 7.2
  "already asked for provider and model on the ledger row; this is
  that", which is also `d0b103e3`'s stated objective. These are one
  piece of work filed twice from two design docs.
- **`d0b103e3` against `ea6f0c1a`.** `d0b103e3` extends the ledger with
  no dependency on the one-unit work, so it can land while the `tokens`
  column still mixes a weighted unit (ralph, reviewer) and a raw unit
  (chat, voice). Any money computed over that column before `ea6f0c1a`
  is wrong by up to the cache-read discount. Add the edge.
- **`3b1c5432` against `77b22987`.** `3b1c5432` deletes `latitude`
  entirely. `77b22987` (SEC-A2) requires documentation stating that
  latitude is not a security boundary. If latitude is deleted first,
  half of SEC-A2's deliverable has no subject.
- **`100ba5d2` against `da0994f9` and `b6852799`.** `100ba5d2` deletes
  the hand-rolled trees; the other two are in_review and are rebuilding
  those surfaces on the kit primitives. Same files. Land the two
  in_review tickets first or the delete collides.
- **`726d75a9` against `80709722`.** Not a true conflict, verified: the
  delete set excludes `app_runner`, `compose_gen` and `container_runtime`
  and explicitly says not to delete the `Deploy` table if a keep-shaped
  item references it. But both introduce a noun called "deployment"
  meaning different things. Do `726d75a9` first so the word is free.

## Stale premises

Spot-checked 14 suspicious tickets against the live tree, chosen by
title keywords (`latitude`, `runtime`, `appetite`, Letta, project vs
app, deploy, kit).

Confirmed still true, no action:

- `3b1c5432` latitude. Still present in `db/models.py`, `db/repo.py`,
  `db/ticket_sync.py` and ten tests. Live field, valid ticket.
- `f06da673` legacy routes. `/agents/build/{slug}` has ten live route
  decorators in `backend/routes/agents.py`, and `/base-apps` is included
  in `main.py:382`. Valid.
- `fbbc55a7` dead kit field. `DEFAULT_KIT` is live at
  `backend/services/manifests.py:27`. Valid.
- `726d75a9` deploy subsystem. `deploy_runner.py`, `deploy_watcher.py`,
  `deploys.py`, `deploys_override.py` and `deploy_providers/` all still
  exist. Valid.
- `1e8c2fc7` Letta `/v1`. Both servers are still listening, now on
  127.0.0.1:8283 and :8284 rather than all interfaces. The loopback is
  still shared between host users, so the cross-user reach the ticket
  describes still holds. Narrow the ticket to that, drop the exposure
  wording.
- `ca23bf08` memory keeper. `backend/services/memory_keeper.py` exists
  and is referenced nowhere in `triggers.py` or `main.py`. The "36 days
  frozen" premise holds.

Stale or drifted:

- **`4f52c95c` and `2c7c245e` say "appetite band".** The word `appetite`
  appears nowhere in `backend/`, `frontend/src`, `packages`, `scripts`
  or `docs/`. What they mean is the priority band (`tasks.py:1223`, ADR
  0019, floors 0/1/40/80/100). Rewrite the wording or a worker will
  search for a field that has never existed.
- **`d4876c7c`** names a deleted file, see above.
- **`29d24fb1`** names a nonexistent ticket, see above.
- **`ec420a11`, `2caa002b`, `6fff3fd9`** are 2026-08-15 chat-UI card
  diagnostics whose probe threads have four to five messages each. The
  card work they were testing has since shipped. Close them.
- **`e5848c87`** exists specifically because two plan docs are stale.
  Its own premise is the staleness, so it is valid, but it should run
  before `86564637` and `2c13ef89`, which follow those docs. It has no
  dependency edge saying so.

No ticket was found asserting the retired `runtime` axis. The
project-versus-app split survives only as the two registries `0cbe76ac`
and `38728986` exist to merge, which is the current model, not a stale
one.

## SEC tickets, operator-only

**These cannot be executed by an agent.** Each needs root, another host
user's account, or a decision only the operator can make. An autonomous
run should skip them. Nine are traceable to
`docs/masini/security-audit-2026-07-18.md`; `source.ref` on these is an audit
slug, not a chat id.

- `4a79ffc5` (P80) [SEC-A1] Run every chat and autonomy turn as a
  dedicated non-`clawd-a` sub-user without the docker group, inside a
  bwrap view of one scope root. Needs root and systemd.
- `5c893b55` (P100, parked) [SEC-C3] Verify inbound IPv6 default-deny
  for 6379/8188/11434/11435/445/139/22/111. Root.
- `f128c5dc` (P80, parked) [SEC-H3] Replace or drop the NOPASSWD sudo
  git as `clawd-house`. Root.
- `dad50a93` (P80, parked) [SEC-H4] Loopback-bind ComfyUI :8188. Owned
  by another host user plus root.
- `cc2e83df` (P80, parked) [SEC-H7] Loopback-bind Ollama :11434 and
  :11435. Owned by another host user.
- `30d2d99e` (P40, parked) [SEC-M3] Disable sshd password
  authentication. Root.
- `911be1b2` (P40, parked) [SEC-L5] Samba anonymous enumeration and
  rpcbind :111. Root.
- `1e8c2fc7` (P40) [SEC-M1] Enforce the Letta server password on `/v1`
  for both instances. Crosses into another host user's stack.
- `5b2737b8` (P40) [SEC-M2] Get open-wearables vendor OAuth, AWS and
  admin secrets out of container env. Another project's deployment.
- `54f4da13` (P40) [SEC-G1] Narrow the shared GitHub classic PAT
  (`repo`, `read:org`). An operator decision, not a code change.
- `61ac9564` (P40) [SEC-L2] Stop serving the Vite dev server :5173 to
  the tailnet. Edge configuration.
- `77b22987` (P80) [SEC-A2] Stop describing scope, touches, latitude and
  grants as a security boundary in docs and prompts. This one an agent
  could do; it is documentation.
- `7f8f22d7` (P40) [SEC-A5] Write down that the personal-data partition
  is app-layer only (a contextvar) and what would close it. An agent
  could do this one too.
- `98ad02be` (P40, in_review) [SEC-A6] Harden generated app containers
  (`security_opt`, `cap_drop`, non-root). Sequenced after SEC-A1.

---

## Part 2: the conversations

133 thread directories under `.runtime/users/6/chat/`. 71 are
substantial (8 or more messages and a log over 15 KB). The rest are
probes, single-turn tests, and `New chat` stubs from 2026-06-04 and
2026-08-15, safe to ignore.

Open any of these at `/chat/<thread-id>`.

### Substantial threads, newest first

- `2c44eaf71ef14a3f83c96d119787ba9f` Platform Redesign Execution.
  2026-08-25 to 08-26, 31 msgs. Parallel subagents scoping and filing
  the current ticket wave. Most of today's tickets came from here.
- `95f7fd7fe8f6413dbcea1b7e80d47979` Chat Flickering On Mobile.
  2026-08-25 to 08-26, 33 msgs. Root cause and fix for chat flicker.
- `703d91609b684f0fad28797d1d0667bc` Chat Thinking Display Regression.
  2026-08-26, 19 msgs. Why the thinking-message display plan was never
  applied.
- `c28d9ebfd161439e9ad2e517405c028b` Chat Scrollbar And Dropdown Issues.
  2026-08-26, 14 msgs. Composer scrollbar, chat selector height.
- `65838049410f4a55b5534892ab554895` Ticket Proposal Design In Chat.
  2026-08-25 to 08-26, 23 msgs. How a ticket proposal card should look
  and behave in a conversation.
- `1ebc303f366e46eb81719f4dc57e745e` Upgrade Compaction Logic.
  2026-08-25 to 08-26, 26 msgs. Compaction does not actually compact.
- `4102a2fec0384067887d76c5af007201` Chat message structure redesign.
  2026-08-14 to 08-23, 67 msgs. The JSONL-plus-Postgres mirror and why
  it exists.
- `203c8692fac44e508b83835f2f453b86` Collapsed Step Text Display.
  2026-08-18 to 08-23, 35 msgs.
- `dbd875271cbe40f888e589ae4b59abac` Memory File Editor Implementation
  Scope. 2026-08-23, 21 msgs.
- `93e3534a389a4940836a083e415a24d5` System Message Display Design.
  2026-08-23, 9 msgs.
- `cffd768d3c0845d1b7d1f63ff3b741f7` Chat Go Back Button Design.
  2026-08-23, 11 msgs.
- `1a16f117a3424db2af97bd19b17d4d10` Chat Dropdown Switch Loading Bug.
  2026-08-23, 13 msgs.
- `5b651cb0b7944032b81686e9a4e6f537` Settings Code Audit. 2026-08-20 to
  08-21, 18 msgs.
- `07182c3c851e419ebbec8b790f13a6da` Branches Work Trees GitHub
  Integration. 2026-08-20 to 08-21, 9 msgs.
- `1a6cf9dda32f467295f46c97594f4e3c` Agent Stops Responding. 2026-08-20
  to 08-21, 41 msgs.
- `1bb046be8ead4180af0399b8176513a5` CLAUDE.md conventions and where
  uploads go. 2026-08-20, 21 msgs.
- `17b37d32ae9a410a83076e49572c0fa3` Chat Compaction Completing Too
  Quickly. 2026-08-20, 25 msgs.
- `68295acba8134a1ea9f3d8f59b05db1a` Chat Auto Rename Implementation
  Review. 2026-08-20, 13 msgs.
- `61151b7b4a6241ce80b14b6be99e3734` Chat Messages Jump After Reading.
  2026-08-20 to 08-21, 15 msgs.
- `b9eec3baf144458f810a178133fec42b` Clarify Settings Memory Copy.
  2026-08-20, 9 msgs.
- `aa52bae46f7545cf9d03b601dc51b3af` Chat Loading And Switching
  Performance. 2026-08-21, 8 msgs.
- `89bb85aeb3c04d3bb4e0e67dd8d09bea` Chat Input Centering And Error
  Handling. 2026-08-21, 5 msgs.
- `6901cbd426fc4dfe93d30a7353ea1fef` Chat Reload Loads Wrong Chat.
  2026-08-18 to 08-19, 25 msgs.
- `3bed1473303e4b83bcad0a85653e8dd4` Chat Absolute Paths And
  Containerization. 2026-08-18 to 08-19, 70 msgs.
- `8903f54d6d834fbcbbf7ca21f5fe86d0` App Architecture And Scope
  Redesign. 2026-08-19, 27 msgs.
- `c88633dce3ee47759f24e07b90a06110` Tickets Error Message Template
  Design. 2026-08-18 to 08-19, 25 msgs.
- `89223a472a8e40918b4955610755d836` Chat File Attachment Read Failure.
  2026-08-19, 17 msgs.
- `d0100608395f40a3b67828748e55c9e0` Background Agent Response
  Disappearance. 2026-08-19, 9 msgs.
- `5d4567bb1a0c44c6b75bc2b32ccbfc27` Chat Message Sequencing Issue.
  2026-08-19, 6 msgs.
- `83a36d191022451f85f17029da9690c7` Duplicate Permission Request Bug.
  2026-08-18, 63 msgs.
- `70a3bfc156e341b6949bf6f944ac6888` Platform Ontology Review.
  2026-08-18, 47 msgs.
- `277da9337e634b6a8060f658b01ec4e6` Diagnosing Subagent Lifecycle
  Failures. 2026-08-18, 33 msgs.
- `2ed0ad5695594ed19f16c3763987157a` Real-time Chat Status Updates.
  2026-08-18, 23 msgs.
- `61766023dfdb4e449a4f0fd8838e7aca` Chat Message Ordering During
  Compact. 2026-08-18, 21 msgs.
- `67e5b629ff8844febe27d6b94f91f4d5` Chat System Multiple Failures.
  2026-08-18, 18 msgs.
- `b5aa4757bbba4b0ca775a8afc7e7f559` Chat File Attachments. 2026-08-12
  to 08-18, 70 msgs.
- `57e6c4b8e34a4c96acab4b5d1f184b65` Chat fixes 2. 2026-08-14 to 08-16,
  43 msgs.
- `65341b404db1430583cc72aabbdb3e2d` tickets redesign. 2026-08-16, 41
  msgs.
- `8f6dd2c241484fb0840bfa59d171ace3` Shared Chat Memory System Design.
  2026-08-14 to 08-16, 22 msgs.
- `8c83e1fda9a34124989b7abf773da44d` Chat Loading Performance.
  2026-08-14 to 08-16, 38 msgs.
- `bc708dc854784dc0b3a8b0cb92e68fdd` Fix Old System Fields Showing.
  2026-08-15 to 08-16, 27 msgs.
- `cc0ab90c53294ea5a05f19a11800dbde` Chat Settings Application Timing.
  2026-08-15 to 08-16, 25 msgs.
- `4392c29c188c401c826b4e6cd8c4bdd6` Tickets Redesign Message Unread.
  2026-08-16, 9 msgs.
- `78154ad72c1b44df98f04bb92b1e35a2` Tickets Page Redesign. 2026-08-15,
  39 msgs.
- `fc88a1b8d14e4879bd8835e45a5554b5` Multi-LLM In-App Authentication.
  2026-08-14, 8 msgs.
- `e3f5fab50879498e96ea036b205010df` Reducing Restart Anxiety.
  2026-08-13 to 08-14, 21 msgs.
- `596c8d5c311c47a9aef247ac52aa53f6` Misc fixes. 2026-08-12 to 08-14,
  58 msgs.
- `47b637e74ab74a2f916cd0c1037d97e8` Workshop Voice Chat Broken.
  2026-07-31 to 08-13, 29 msgs.
- `dbc089afae9244d1a03cffa23b99e0ca` Audio chat mode. 2026-08-12, 41
  msgs.
- `8afccb02aaad4fed9be62a8e5f13b4ef` Refine Chat UI Copy. 2026-08-12,
  19 msgs.
- `e3754e6e1ddd4defb5d7eb16f4b1aeee` Audit And Fix Hardcoded Personal
  Data. 2026-08-10 to 08-12, 30 msgs.
- `ee3d6e3db8c640ac8204a7c7071c4d34` Workshop UI Kit Versioning Issue.
  2026-08-10 to 08-12, 43 msgs.
- `2ff72ec0a9c54694a3bfd583b7e97df0` Filter Agent Changes For Review.
  2026-08-11 to 08-12, 27 msgs.
- `9642255601d94019a56833731d91f2bf` Mobile Typing Performance Audit.
  2026-08-11, 26 msgs.
- `d899901fe4fe405a840dd2c5b57c904c` Chat Baseline Audit And Redesign.
  2026-08-10 to 08-11, 44 msgs.
- `4525c979264840e98692a36e8d5fd450` Chat Send Failures And Message
  Order. 2026-08-11, 5 msgs.
- `4c6bb3fdc0ad4d0abe3fd38b9ae9f554` Chat Effort and Mode Support.
  2026-08-10, 29 msgs.
- `919bc9495830417eada41988dffaa2f0` Auditing Ticket Definition And
  Design. 2026-08-07 to 08-10, 21 msgs.
- `69a1627a601744e2b59b42bd0f3b378c` Standardize All Workshop Page
  Widths. 2026-08-09, 20 msgs.
- `403992d188364fbdbf22180cdc363a31` Workshop Color Themes and
  Personalization. 2026-08-07 to 08-09, 25 msgs.
- `9b2ab600ea6542a2a6b523f55ed519d4` Chat Subagents and Ticket Policy.
  2026-08-07 to 08-08, 25 msgs.
- `49cd7b22e4234db9aeea2188d23a8795` Workshop Chat. 2026-08-03 to
  08-08, 72 msgs.
- `b3a9f09f0fd6489da1c719e6f082095f` Add Opus 5 To Chat Models.
  2026-07-25 to 07-31, 82 msgs.
- `d18e3b6d08a847a19b6cf4b27f15b96c` Platform Redesign Research And
  Synthesis. 2026-07-18, 44 msgs.
- `45f78ce019d7449b876d2987895fed8f` Refining The Workshop Notes App
  Plan. 2026-07-14, 8 msgs.
- `c5ba6c1d243746e3abc4fe671402619a` Verify Workshop Chat Context
  Measurement. 2026-07-14, 10 msgs.
- `436947ee6bad4393b12b09e403562c71` (untitled). 2026-06-22 to 07-14,
  116 msgs. The longest thread in the store: agents tab, autonomy
  toggle, and the first pass at the agent model.
- `032fd114be51495787a7ac2386daffea` Note-Taking App Integration
  Research. 2026-07-12 to 07-13, 20 msgs.
- `1e18aae7cc8e402791f71670b74ec390` Location Persistence And Path
  Lines. 2026-06-18 to 06-30, 10 msgs.
- `04da1578ca644646a143e155cb59ffd7` Commute App Not Implemented Yet.
  2026-06-16 to 06-29, 20 msgs.
- `5d5ef34c6a0a46bcb8ba0aa74db07520` Configurable Agent Chat For All
  Apps. 2026-06-18 to 06-29, 8 msgs.
- `64a1abd319aa428d875bfe3cd75d59fe` Bird Presence Window Logic.
  2026-06-18 to 08-20, 19 msgs.
- `104cb27172d74a798f767c6d5b6530ce` "hey what do we have here?"
  2026-06-14 to 06-15, 28 msgs. The earliest orientation thread.

### The threads that carry design context

Read these before touching the systems they cover.

1. `d18e3b6d08a847a19b6cf4b27f15b96c` **Platform Redesign Research And
   Synthesis** (2026-07-18, 44). The origin of the entity model. Every
   `docs/platform-redesign/*-2026-07-18.md` document traces here.
2. `70a3bfc156e341b6949bf6f944ac6888` **Platform Ontology Review**
   (2026-08-18, 47). The ontology and schema overhaul, checked for
   completeness. This is where role-not-runtime was settled.
3. `8903f54d6d834fbcbbf7ca21f5fe86d0` **App Architecture And Scope
   Redesign** (2026-08-19, 27). The zip-file redesign that became
   "app is the entity". Read with
   `app-is-the-entity-2026-08-20.md`.
4. `2c44eaf71ef14a3f83c96d119787ba9f` **Platform Redesign Execution**
   (2026-08-25 to 08-26, 31). Where roughly 40 of the live tickets were
   scoped and filed, including the usage, routing and provenance waves.
5. `4102a2fec0384067887d76c5af007201` **Chat message structure
   redesign** (2026-08-14 to 08-23, 67). Why chat is stored twice, what
   the mirror is for, and the reverted stage 2. Cited by four tickets.
6. `919bc9495830417eada41988dffaa2f0` **Auditing Ticket Definition And
   Design** (2026-08-07 to 08-10, 21). Why a ticket is title plus
   description like Linear or Jira, and where the four labels came from.
7. `8f6dd2c241484fb0840bfa59d171ace3` **Shared Chat Memory System
   Design** (2026-08-14 to 08-16, 22). How tickets, chat and agents
   share memory. Background for themes 3 and 4.
8. `9b2ab600ea6542a2a6b523f55ed519d4` **Chat Subagents and Ticket
   Policy** (2026-08-07 to 08-08, 25). When work deserves a ticket and
   when a subagent is enough. Still the only statement of that rule.
9. `07182c3c851e419ebbec8b790f13a6da` **Branches Work Trees GitHub
   Integration** (2026-08-20 to 08-21, 9). The design behind the
   worktree and lease tickets, `e8c53c78`, `fae54a3e` and `c8cc9f3f`.
10. `277da9337e634b6a8060f658b01ec4e6` **Diagnosing Subagent Lifecycle
    Failures** (2026-08-18, 33). Why a parent announces a subagent and
    then both die. Unresolved and recurring.
11. `ee3d6e3db8c640ac8204a7c7071c4d34` **Workshop UI Kit Versioning
    Issue** (2026-08-10 to 08-12, 43). `@workshop/ui` consumed as a path
    alias by out-of-repo apps. The reason `d36d4fb5`, `4a9dbab2`,
    `0a83ac01` and `1cb21f41` exist.
12. `e3754e6e1ddd4defb5d7eb16f4b1aeee` **Audit And Fix Hardcoded
    Personal Data** (2026-08-10 to 08-12, 30). The origin of "no
    platform source file may name a specific app" and the whole of
    theme 6.
13. `3bed1473303e4b83bcad0a85653e8dd4` **Chat Absolute Paths And
    Containerization** (2026-08-18 to 08-19, 70). Why transcripts leak
    host paths. Directly behind `adf8dadb` and SEC-A1.
14. `fc88a1b8d14e4879bd8835e45a5554b5` **Multi-LLM In-App
    Authentication** (2026-08-14, 8). In-app login and token install for
    Claude, designed to generalize to other vendors. Behind `bc81b563`
    and `c7bd8a4f`.
15. `1ebc303f366e46eb81719f4dc57e745e` **Upgrade Compaction Logic**
    (2026-08-25 to 08-26, 26). Compaction does not compact. Related to
    the prompt-size work in theme 4.

Honourable mention: `436947ee6bad4393b12b09e403562c71` (116 messages,
2026-06-22 to 07-14) is the longest record in the store and the only
account of the original agents and autonomy design, but it is untitled
and predates most current vocabulary.

### Ticket to chat link coverage

- 159 live tickets. **28 carry a `source.ref`; 131 do not.**
- Of the 28, **15 resolve to a real chat thread directory**. The other
  13 point at audit slugs (`security-audit-2026-07-18`,
  `-agents`, `-connectors`), which are documents rather than chats.
- Across all 311 tickets including done and cancelled: 69 have a ref,
  36 resolve to a thread, 242 have none.

So **9 percent of live tickets are traceable to the conversation that
produced them.** The prior measurement of 191 nulls out of 259 has moved
to 131 out of 159, a worse ratio, because the ~40 tickets filed on
2026-08-25 and 08-26 were written by subagents that did not set
`source.ref`. Fixing that is `dab8a00f` plus `9dbb1cf2`: the actor and
the origin should be written at `tasks.add()`, not reconstructed later.
Until then this index is the link.
