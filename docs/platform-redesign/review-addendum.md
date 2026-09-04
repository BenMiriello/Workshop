# Review addendum: post-phase-5 state, UI completeness, and the chat fix

Status: review, 2026-07-13. Code-verified against the working tree on `main` (phases 1-5 committed: 203e51b, 388f6d9, 984ed0f, 1d13f16, 0a608f7). This document is the gap-closing build queue that follows the implementation plan. Section 5 is the punch list; Section 3 is the highest-priority item and answers the operator's chat complaint concretely.

Note on tree state: there is uncommitted voice WIP in the tree (Chat.tsx modified, VoiceMode.tsx deleted, VoiceSettings.tsx new, voice_worker.py modified). This review reviewed the working tree, which is what actually runs. The voice work should be committed on its own before the punch list starts, per the repo rule (commit your own work as you go).

## 1. State assessment: what phases 1-5 actually are in code

Verified by reading the code and on-disk runtime state, not the commit messages.

**Phase 1 (Job + triggers): COMPLETE and live.** `backend/services/jobs.py` is real (status enum exact at jobs.py:51, append-only `.runtime/jobs.jsonl`, `start()/finish()`, CLI shim at jobs.py:208-266). `triggers.py` has the four kinds and `dispatch()` opens Jobs. `routes/jobs.py` mounted at main.py:111. The migration ran: `agent-runs.jsonl.migrated` and `agent-runs-completed.jsonl.migrated` exist, `agent-runs-pending.jsonl` untouched, and `jobs.jsonl` has ~2,500 records with entries from minutes ago (trigger mix loop=1030, cron=215, event=7). Doc drift only: `cron_agent.sh` lives at the repo root, not `scripts/`, and it correctly emits Jobs.

**Phase 2 (one Agent registry): COMPLETE.** `agents_registry.py` reads markdown definitions from `agents/` (14 user-scope files exist) and project scopes (`projects/budget/agents/`, `projects/birds/agents/`, etc.). The bridge agent per-channel exclusion is enforced in `list_agents` (agents_registry.py:98-101, 191, 212). Subscriptions regenerate on create/update/retire. `GET /agents/{id}/jobs` exists (agents.py:932) with no route-shadowing issue.

**Phase 3 (chat loose coupling): COMPLETE, one benign spec deviation.** blankBuilder, `create_blank`, and the `new-project` provider are gone from product code (only scratch/ and comment mentions remain). Rescope exists (`POST /chat/threads/{id}/rescope`, chat.py:205). Deviation: `GET /chat/threads/all` was never built; instead `GET /chat/threads` with no `context` param returns all threads (chat.py:126-128, chat_store.list_threads(context_key=None)), and Chats.tsx consumes that. Functionally equivalent; update the plan doc rather than build the endpoint.

**Phase 4 (Store + Grant): backend COMPLETE, UI HALF-WIRED.** `stores.py` (declare/list/export), `permissions.py` (mint_grant :616, attenuate :647, revoke :680, check :710), `routes/grants.py` mounted (main.py:121), knowledge scopes registered as `Note` stores without touching search internals, Grants.tsx repointed to `/api/grants`. Gaps:
- `frontend/src/components/GrantPicker.tsx` is committed but imported NOWHERE. The broker/picker (the load-bearing interop gesture of the whole Store/Grant design) has no mount point. This is a committed stub in spirit, exactly what the plan's global rules forbid.
- No Stores UI exists anywhere: `GET /stores` has zero frontend consumers besides the orphaned picker. The Store noun is invisible in the product.
- The export contract has no UI trigger (no "Export" action on any store).
- Stale comments claim a `/base-apps/grants` alias that no longer exists (main.py:118, grants.py:14). Zero callers, so no breakage, but the comments lie.

**Phase 5 (surfaces): MOSTLY COMPLETE, three real gaps.** Home is `/`, Activity deleted with redirects, Events/Services render under Settings > System (Settings.tsx:583, 617, 620), Location/Commute/Finance sections moved to `components/appSettings/`, AutonomyControls moved to the Agents page, `/agents/build/:slug` redirects. Gaps:
- **The Jobs page is orphaned.** Nothing in the product links to `/jobs`: not the nav (SectionNav has the six destinations, correctly), not the Agents page, and Home's "Recent jobs" View-all goes to `/agents` (Home.tsx:269). The only inbound path is the legacy `/runs` redirect. A first-class surface for the redesign's central new noun is unreachable.
- **No Job detail exists at all.** `GET /jobs/{id}` has zero frontend consumers. The proposal names Run/Job detail as a first-class surface ("one Run's transcript, evidence, binding, and outcome. Linked from everywhere a Run is mentioned"). Today Jobs cards, Home attention cards, and AgentDetail job history are all dead ends (AttentionStrip links to `/agents`, Home.tsx:159).
- **HealthSection did not move** out of Settings.tsx (:79, :439). Caveat: unlike Finance/Location/Commute, there is no `/apps/<slug>` page to host it, so "move it" was never actually executable. Needs a decision, not just a move (see Section 4).

## 2. UI completeness audit, screen by screen

Judged against frontend/CLAUDE.md (primitives, tokens, five status kinds, edit-mode, four list states, one header) and the proposal's Section 7.

**Home (pages/Home.tsx).** Good bones: attention strip, four sections, all four states per section, StatusPill mapping correct. Gaps:
- "Recent jobs" View-all links `/agents` instead of `/jobs` (Home.tsx:269). AttentionStrip cards link `/agents` instead of the Job (Home.tsx:159); once Job detail exists they should link `/jobs/{id}`, until then `/jobs`.
- Em-dashes in rendered hint strings (e.g. Home.tsx:213, 271: `<code>/api/jobs</code> — {err}`). Violates the operator's no-em-dash rule in UI copy. This pattern repeats across Jobs.tsx, Agents.tsx, and others; needs a sweep of rendered strings (comments can be a separate, lower-value sweep).

**Chats (pages/Chats.tsx).** Search + loading/error/empty/no-match states are right. Gaps:
- **No way to start a chat.** The PageHeader has no action, and the empty state says "Start a conversation with the general assistant" with no affordance. The page needs a primary "New chat" action (navigate `/chat/general`, and ideally a small context picker for project/app scopes).
- No overflow state: every thread renders unbounded (rule 9). Cap the list (e.g. 100) with a "showing N of M" note or incremental reveal.
- Filter is free-text only; the plan promised filter by context/agent. A Segmented filter over context kind (All / General / Projects / Builders / Apps) is cheap and matches the mental model.
- `contextLabel` handles an `agent:` kind that the backend cannot actually serve (see the agent-binding hole in Section 3/4).

**FocusedChat (pages/FocusedChat.tsx).** The viewport-pinning shell is genuinely good (visualViewport handling, single scroller). The problem with this surface is the Chat component's mode split, which is Section 3. No independent gaps.

**Agents (pages/Agents.tsx).** Unified roster with runtime badges, trigger summaries, last-Job line, status strip, autonomy toggle, AutonomyControls at the bottom. This matches the proposal. Gaps:
- **Two creation paths for one concept:** the "+ New agent" route (AgentDetail create form, which handles both runtimes) AND a separate "+ New builder" modal (Agents.tsx:62-180) that posts to `/api/agents/build` directly. The proposal's whole point is one Agent concept; collapse the modal into `/agents/new` (pre-select runtime=builder) and delete NewBuilderModal.
- No link to `/jobs` from the page that conceptually owns it (the plan re-homed Runs "under Agents").
- The status strip's Builders/Assistants split is fine as telemetry, but ensure it never grows back into segmentation.

**AgentDetail (pages/AgentDetail.tsx).** The best-conceived new screen: definition/triggers/limits with real edit-mode + ActionBar, TriggerEditor covering all four kinds, Job history, runtime badge. Gaps:
- **Grants section is a placeholder that is now false** (AgentDetail.tsx:561: "Scoped capability grants land here in Phase 4" while Phase 4 is landed). Wire it: list resource grants where `to == agent:<slug>` from `/api/grants`, and mount GrantPicker here as the "Grant access to a store" action. This is the natural first home for the orphaned broker.
- Job history rows are not links (no Job detail to link to; fix together).
- **Assistant agents have no conversation entry point anywhere.** Builders get "Open work pane"; an assistant runtime agent is config-only. You can create a persona and never talk to it. See Section 3 fix 3.
- Retire is builder-only; assistants cannot be retired from the UI even though the registry supports it.

**AgentBuild (pages/AgentBuild.tsx).** The 3-pane IDE with region focus, status pills, branches, preview, diff, and the embedded build Chat is in good shape and matches the locked layout decisions (memory: do not add a segmented switcher or hideExpand). Config correctly moved to AgentDetail. No action needed beyond the shared-Chat improvements landing here for free.

**Jobs (pages/Jobs.tsx).** Cards, status pills, trigger tags, duration, four states: fine as a v1 list. Gaps:
- Orphaned (see Phase 5 above).
- Cards are not clickable and there is no Job detail page (the proposal's Run-detail surface). Evidence, transcript, binding, and outcome are all invisible.
- The status filter omits `awaiting_input` (the one status that wants the operator), `queued`, and `cancelled`. Add at least Awaiting.
- No agent/project filter despite backend support (`/api/jobs?agent_id=&project_slug=`), and no pagination beyond the fixed limit=100 even though `count` is displayed.

**Projects / ProjectDetail (pages/ProjectDetail.tsx).** Rich: Chat, Build agents, Brief, Tasks, Notes, Branches, Share, AutonomyPanel (trigger view, :913). Against the plan's "project detail owns project config":
- Project-scoped agent DEFINITIONS (`projects/<slug>/agents/*.md`, which Phase 2 created and the registry reads) are not surfaced; the "Build agents" section lists runtime build agents only. Add a section listing definition-file agents scoped to this project, linking to AgentDetail.
- Grants held by the project's agents are not shown (plan item). Cheap once the grants API is queried by scope.
- Sensitivity renders as a Tag but is not editable here.
- The deploy seam (Share/Deployments) is present; adequate for the reserved seam.

**Settings (pages/Settings.tsx) and children.** Split is done except Health (see above). Two small things:
- `/settings/base-apps` and `/settings/grants` do not get the roomy width class (App.tsx:74-83 matches `pathname === "/settings"` exactly), so the Grants tables live under the 980 cap while the Settings hub is roomy. Use `pathname.startsWith("/settings")`.
- Grants.tsx renders legacy app-level grants (editable, ActionBar, correct commit model) plus read-only store grants with revoke. Missing: any view of the Stores themselves (URI, type, owner app, export action). A "Stores" SectionLabel on this page listing `/api/stores` with a per-store Export button would make Phase 4 visible and testable.

**Grants broker (components/GrantPicker.tsx).** Well-built, never mounted. Mount points, in order of value: AgentDetail grants section; Grants settings page ("Mint a grant"); later, app manage surfaces.

## 3. The chat fix (highest priority)

### What the operator is seeing, precisely

Every chat surface renders the same `components/Chat.tsx`, but there are two send paths inside it, and the split is decided by the mount URL alone:

- `deriveMode` (Chat.tsx:62-64) marks a chat "build" only when the context key starts with `build:`. Everything else, the general chat, app chats, and even `project:` chats, is "data".
- "data" sends via `sendData` (Chat.tsx:779-804): a synchronous `POST /threads/{id}/messages` that blocks until the whole turn finishes. While it runs the UI shows only the "Working" dots. The reply arrives as one text blob. No streaming, no tool visibility, no work steps, and the stored message has no `meta.segments`, so even after reload there is nothing to expand. This is the imported notes-app-era chat: a bare request/response document list.
- "build" sends via `sendBuild` + `attachStream` (Chat.tsx:806-830, 513-621): SSE with interleaved text/tool events, ToolChip and WorkSteps rendering, live elapsed timer, reconnect-on-drop, type-ahead queueing, Stop, and persisted segments. This is the builder chat the operator wants everywhere.

The asymmetry is not cosmetic. Data-mode chats DO run tools server-side (WebSearch/WebFetch plus MCP per provider, chat.py:652-663), and `project:` chats run the FULL Claude Code toolset over the project tree with a 900-second timeout (chat.py:620-651), all completely invisible while blocking. The server enforces the split: `POST /threads/{id}/turns` 400s for anything but `BuildAgentContextProvider` (chat.py:763-765, "streaming turns are Build-only"), so even the build-provider project chats are barred from streaming.

Two more pieces of the "same old chat" feel:

- The data-mode toolbar leads with the **provider `<select>`** (Chat.tsx:1005-1017), the pre-redesign context-provider ontology (general + app personas + "Build · project/agent · X" entries). The new model says a chat is agent-optional with an attachable Agent; the provider dropdown is the old model still on screen. It is also a live footgun: in a general full chat, picking a "Build · agent · x" provider keeps `effectiveMode === "data"` (mode derives from the mount contextKey, never from `activeProvider`), so a full build turn runs down the blocking synchronous path.
- All chat styling lives in the legacy sheet (`styles.css:543-900`: `.chat-panel`, `.chat-toolbar`, `.chat-log`, `.chat-bubble` with a raw `#fff` at :610 and raw rgba at :695-697), not in `components.css` as `.ws-*` rules. `check_design.sh` only scans `.tsx`, so it passes while the chat remains the one surface never migrated to the design system.

Also confirmed: `agent:<slug>` context keys silently resolve to the general provider (`chat_context.get_provider`, :835-843). The unified registry's assistant agents are unreachable from every chat surface; the "attach an agent to a chat" half of the proposal's Chat noun was never built.

### The decision: (a), not (b)

Do NOT route general chats through the AgentBuild surface (option b). AgentBuild is a project IDE (files/editor/preview/diff) and a general chat has no cwd or worktree; mounting general chats there would be chrome without a subject. The right fix is (a): make the shared Chat builder-grade for every context by unifying on the streaming turn path, and replace the old provider dropdown with the new agent-binding control. One chat component, one send path, mode differences reduced to genuine capability differences (task pane and workbench link stay build-only).

### The buildable spec

**Fix 1: streaming turns for every provider (backend).**
- `backend/services/build_streaming.py`: extend `start_turn` (:243) with optional `allowed_tools: list[str] | None`, `mcp_servers: list | None`, `tools_disabled: bool`, and `timeout_s` kwargs that map to the same CLI flags `llm_router.call_cloud` uses; when `session_id is None`, spawn stateless (no `--session-id/--resume`). Everything downstream (event log, SSE replay, segment persistence, finalize) already works on generic events.
- `backend/routes/chat.py` `start_streaming_turn` (:754): drop the BuildAgent-only guard. Branch per provider class: BuildAgent providers keep the session path exactly as today; Project providers stream with full tools over `provider.dir` (no persistent session, as now); data providers assemble the prompt exactly as `send_message` does today (preamble, auto-recall, `_maybe_compact`, skills block) and stream with `effective_allowed_tools`/`mcp_servers`/`tools_disabled` per the same ENABLE_CHAT_TOOLS ladder. Reuse the prompt-assembly block by extracting it from `send_message` into a helper both endpoints call; do not fork the logic.
- Keep `POST /threads/{id}/messages` working unchanged: the voice worker and any scripted callers use it. It becomes the non-UI path.
- Persist data-turn segments in `meta.segments` identically to build turns so reloads render work steps.
- use_skill expansion for data turns: run it in the turn driver before finalize (same single bounded hop), so the streamed answer includes the expansion.

**Fix 2: one send path (frontend, Chat.tsx).**
- Delete the `sendData`/`busy` fork: `submitText` always uses `sendBuild`/`attachStream` (rename to `sendTurn`). The working indicator with elapsed time, ToolChip/WorkSteps, reconnect, queueing, and Stop then apply to every chat, including general.
- Keep `effectiveMode` only for capability chrome: task pane (build), workbench link (build), slash passthrough list (build). The transport is no longer mode-dependent.
- Voice: `useVoiceSession` drives the same `/messages` path today; leave it as is in this pass (it has its own worker), but confirm the settled-turn refetch still folds correctly when normal turns are SSE.

**Fix 3: agent-optional binding instead of the provider dropdown.**
- `backend/services/chat_context.py`: add an assistant-agent provider so `agent:<slug>` resolves to the registry agent (persona from the definition file/Letta block, data-tier tools, `knowledge_scope()` = the agent scope) instead of silently falling back to general. Reject unknown `agent:` slugs with 404 rather than lying.
- Chat toolbar: replace the provider `<select>` with (1) a context Tag naming the binding (General / Project x / Builder x / App x), and (2) an Agent select listing registry agents ("No agent" default, assistants and builders both). Picking an agent rescopes or creates the thread against `agent:<slug>` (assistant) or `build:<slug>` (builder), and the mode/transport follows the THREAD's context key, not the mount URL, which also kills the pick-a-build-provider-in-data-mode footgun. App-scoped docks (`scoped`) keep their single fixed provider and hide the control, as now.
- AgentDetail: give assistant agents a "Chat" action navigating `/chat/agent:<slug>` (builders keep "Open work pane").

**Fix 4: chat visuals onto the design system.**
- Move the `.chat-*` rules from `styles.css` into `styles/components.css` as `.ws-chat*` (or keep the class names but re-home and tokenize them); replace `#fff` (styles.css:610) and the rgba literals (:695-697) with tokens (`var(--accent-contrast)` or equivalent; add the token if missing). Delete the dead rules left in styles.css per the migration rule. While in there: give the assistant turn column slightly stronger structure (the toolbar, log, composer are already right; this is a re-home and tokenization, not a redesign).

**Verification (run the app, not just the checks).**
1. `/chat/general`: send a message that triggers a web search; watch tool chips and interim text stream in with the elapsed timer; reload mid-turn and confirm it reconnects and finishes; confirm the stored message shows work steps after reload.
2. `/chat/project:<slug>`: a file-touching request streams Read/Edit tool chips and the turn checkpoint still fires.
3. `/agents/:slug/chat` (builder): unchanged behavior, sessions still resume, slash passthrough intact.
4. Attach an agent from a general chat; confirm the thread rescopes and the reply persona changes; confirm `agent:bogus` 404s.
5. Voice round trip on a general chat still folds the spoken turn into the log.
6. Widths 1440/1024/768/390 on FocusedChat and the AgentBuild chat column. `npx tsc --noEmit`, `bash scripts/check_design.sh`, the bridge agent smoke check (chat.py touched; per-channel endpoints untouched but restart happens).

## 4. Coherence gaps between the two redesigns and the model

1. **Which doc wins:** `platform-redesign/proposal.md` + `implementation-plan.md` supersede `agent-chat-redesign/plan.md` wherever they conflict. Specifically superseded: plan.md's chat-to-project conversion as a promoted gesture (locked decision 2 killed promotion; only the neutral rescope endpoint remains), the build-with-chat blank-project mode (deleted in Phase 3), and the autonomy_mode enum vocabulary (replaced by triggers). Still live and correct from plan.md: the context engine (compaction/distillation), knowledge scopes and recall, skills, ui-cards, voice modality.
2. **A promotion remnant survives and needs an explicit call.** `components/chat/uiCardActions.ts` still ships a project-create card flow with a "Move chat into project" follow-up (:120, :135, :151, driving `rescopeThread`). Locked decision 2 says no conversion pipeline, no promote gesture. Reading it narrowly (rescope is a binding change, and the card is model-emitted, not platform chrome) it can stay; reading it strictly it is the promotion gesture with a different entry point. Recommend: keep the generic ui-card primitive and the rescope endpoint, drop the canned "Move chat into project" follow-up action from the card vocabulary, or get the operator's one-line ruling before touching it.
3. **The Agent noun is unified in the registry but not in conversation.** Assistants exist, are configurable, and cannot be talked to (Section 3 fix 3). Until that lands, "one Agent concept" is only half true in the product.
4. **Letta deferral is holding, but AgentDetail double-writes persona** (definition file, then best-effort PATCH to the Letta persona block, AgentDetail.tsx:397-403). Acceptable during the deferral; when the Letta-removal phase starts, this is the first seam to collapse. Do not add more double-write paths.
5. **Task management: two live systems, both known-wrong.** Per-agent tasks (chat task drawer, `/agents/build/{slug}/tasks`) and per-project tasks (ProjectDetail, `project_tasks.py`). The task exploration doc already brands the scatter as the thing to replace and reserves Task as project-scoped. Rule for the interim: build NOTHING new on the per-agent store; new task-adjacent features wait for the task effort.
6. **Store/Grant is a backend without a product.** No picker mounted, no store browser, no export trigger, no "needs resolved through the broker" flow anywhere in the UI. Until an agent or app actually acquires a grant through the picker, Phase 4 is unfalsifiable from the operator's chair. The punch list makes the smallest real loop: see stores, mint a grant from AgentDetail, revoke from Settings, watch a call fail.
7. **Job detail is a named surface in the proposal and absent in the product.** Also the awaiting_input loop is incomplete: the status exists, Home surfaces it, but nothing shows WHAT input is awaited or offers a way to provide it. Job detail should render the job record (trigger, binding, timing, exit, tasks done/failed, evidence when present) and link its chat/project/agent; providing input can be a later iteration once something actually emits awaiting_input.
8. **Small doc/comment drift to fix while nearby:** implementation-plan says `scripts/cron_agent.sh` (it is repo-root); `/chat/threads/all` deviation (Section 1); stale `/base-apps/grants` alias comments (main.py:118, grants.py:14); stale `agent_runs` reference (jobs.py:7).

## 5. Prioritized punch list

Ordered by operator impact. Each item is independently shippable and lists its files. Chat items first: they are the stated complaint.

- [ ] **P1. Stream every chat turn (backend).** Extend `start_turn` with tools/timeout kwargs and stateless spawn; drop the Build-only guard in `POST /threads/{id}/turns`; extract shared prompt assembly from `send_message`; persist `meta.segments` for all turns. Files: `backend/services/build_streaming.py`, `backend/routes/chat.py`. Verify: general-chat web search streams tool chips; project chat streams full toolset; builder unchanged; the bridge agent smoke check.
- [ ] **P2. One send path in the shared Chat (frontend).** Remove the `sendData` fork; all turns go through `attachStream`; keep build-only chrome (tasks, workbench, slash passthrough) gated on mode but not transport. Files: `frontend/src/components/Chat.tsx`. Verify: reload-mid-turn reconnect on a general chat; Stop and queueing work on general chats.
- [ ] **P3. Agent binding replaces the provider dropdown.** `agent:<slug>` provider in `chat_context.py` (404 on unknown); toolbar shows a context Tag + Agent select that rescopes the thread; mode follows the thread's context key; AgentDetail gains "Chat" for assistants. Files: `backend/services/chat_context.py`, `frontend/src/components/Chat.tsx`, `frontend/src/pages/AgentDetail.tsx`. Verify: attach agent, persona changes, Chats page shows the agent binding.
- [ ] **P4. Chat CSS onto the design system.** Re-home `.chat-*` rules from `styles.css:543-900` into `styles/components.css`; tokenize `#fff` (:610) and rgba (:695-697); delete the vacated legacy rules. Files: `frontend/src/styles.css`, `frontend/src/styles/components.css`. Verify: visual parity across dock/panel/full at 1440/1024/768/390, both themes.
- [ ] **P5. Job detail page + linkify every Job mention.** New `pages/JobDetail.tsx` reading `GET /jobs/{id}` (status, trigger, binding links, timing, exit code, tasks done/failed, evidence); route `/jobs/:id`; make Jobs cards, Home attention/recent cards, and AgentDetail job rows link to it. Files: `frontend/src/pages/JobDetail.tsx` (new), `App.tsx`, `components/appNav.tsx` (crumb), `pages/Jobs.tsx`, `pages/Home.tsx`, `pages/AgentDetail.tsx`.
- [ ] **P6. De-orphan the Jobs page.** Home "Recent jobs" View-all -> `/jobs`; AttentionStrip cards -> `/jobs?status=awaiting_input` (or the job once P5 lands); add a "Jobs" link on the Agents page header actions; add `awaiting_input` (label "Awaiting") to the Jobs filter; support `?status=` initial filter. Files: `pages/Home.tsx`, `pages/Agents.tsx`, `pages/Jobs.tsx`.
- [ ] **P7. Chats page: New chat + filter + overflow.** PageHeader action "New chat" -> `/chat/general`; Segmented context-kind filter (All/General/Projects/Builders/Apps); cap the rendered list with a showing-N-of-M note. Files: `frontend/src/pages/Chats.tsx`.
- [ ] **P8. Mount the grant broker and make Stores visible.** AgentDetail Grants section: list `/api/grants` rows for this agent, "Grant access" opens GrantPicker, remove the false Phase-4 placeholder copy; Settings > Grants: add a Stores section listing `/api/stores` (URI, type, owner) with a per-store Export action, plus "Mint a grant" via GrantPicker. Files: `pages/AgentDetail.tsx`, `pages/settings/Grants.tsx`, `components/GrantPicker.tsx` (first real mounts). Verify: mint, call, revoke, call fails; export produces a file.
- [ ] **P9. Collapse builder creation into /agents/new.** Delete `NewBuilderModal` from `pages/Agents.tsx`; "+ New builder" navigates `/agents/new?runtime=builder` (CreateAgent reads the param). One creation flow for one Agent concept. Files: `pages/Agents.tsx`, `pages/AgentDetail.tsx`.
- [ ] **P10. ProjectDetail: surface project-scoped agent definitions + grants.** Section listing `projects/<slug>/agents/*.md` registry entries (link to AgentDetail) alongside the runtime build-agents list; section listing grants held by this project's agents. Files: `pages/ProjectDetail.tsx`, possibly a `GET /agents?project=<slug>` filter param in `routes/agents.py`.
- [ ] **P11. Assistant retire + Health decision.** Enable Retire for assistant runtime (registry.retire exists); decide Health: either ship an `/apps/<slug>` page with the app-settings slot and move HealthSection, or explicitly document Health as platform-level in Settings and close the plan item. Files: `pages/AgentDetail.tsx`, `pages/Settings.tsx` (+ decision).
- [ ] **P12. Copy and width sweep.** Remove em-dashes from rendered UI strings (Home.tsx, Jobs.tsx, Agents.tsx, and the other pages flagged by `grep "—"` on string literals); `startsWith("/settings")` for the roomy width class (App.tsx:74-83). Files: several pages, `App.tsx`.
- [ ] **P13. Doc/comment hygiene.** Fix implementation-plan's `scripts/cron_agent.sh` path; note the `/chat/threads/all` deviation as accepted; delete the stale `/base-apps/grants` alias comments (main.py:118, grants.py:14) and the `agent_runs` reference (jobs.py:7); commit the voice WIP as its own commit before starting. Files: docs + the three comment sites.

Ruling needed from the operator (one line each): the ui-card "Move chat into project" follow-up (keep or drop, Section 4.2); the Health placement (P11).
