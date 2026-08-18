# Run-status view design + canonical status source (2026-07-18)

Status: design doc (B2), **built; §3.3 superseded 2026-08-18.** Depends on: T0
(entity-model-2026-07-18.md), B1 (chat-work-linkage-2026-07-18.md).
Single-operator system. Voice-mode files are out of scope.

**§3.3 update.** This doc's answer to its own open question ("define what evidence
means or remove it") was to keep the field opaque and render only two shapes. That
was right about the UI and wrong about the field: it left `evidence` as `Any`, and
four separate readers each grew their own coercion for it. Measured on 2026-08-18,
the freeform-string shape belonged **entirely to two deleted systems** (the Letta
overseer and an `ollama_failed:` writer with no remaining spelling). `evidence` is
now the typed `JobEvidence` model, folded to one shape at the store boundary. The
rendering rule below still stands; the "the field is opaque" premise does not. See
coherence-overhaul-2026-08-16.md §11.6.

---

## 1. Problem statement

The current `/jobs/{id}` page (JobDetail.tsx) has three defects:

1. **Dead-end navigation.** A run's detail page shows what happened but offers no path back into the work. There is no "continue from chat", no "follow up in a new chat", and the only navigation options are "Back to Jobs" and "Open project/agent". The page is a log viewer, not an action surface.

2. **Dual status creates confusion.** The Overview card shows two status indicators for the same logical execution: `job.status` (the Run's canonical status, e.g. "done") AND `job.task_status` (the Ticket's status at the time the API enriched the record, e.g. also "done" or "in_review" or something else). These can agree or disagree depending on when you look. A run that failed but whose ticket was later retried and succeeded shows `failed` + `done` — a contradiction that confuses rather than informs. A run that succeeded shows `done` + `done` — pure redundancy.

3. **"Evidence" is undefined.** The Evidence section renders a raw JSON dump when the job record's `evidence` field isn't a structured receipt or a waiting-tasks list. The field is used by shell workers for wildly heterogeneous payloads (error details, shell output, freeform notes). As a display concept it is undefined and therefore untrustworthy. Operators reading it have no idea whether to act on it.

Additionally: running jobs show only "running" with an elapsed timer. There is no live activity signal — no "currently working on X", no task-count progress.

---

## 2. Current state (audit)

### 2.1 JobDetail.tsx sections (as built)

| Section | What it shows | Verdict |
|---|---|---|
| Needs your input | Waiting tasks with approve/reject/unblock/cancel | Keep, no change |
| Overview | job.status pill, trigger tag, duration, exit code, project link, agent link, chat link, **task_status tag** | Keep all except task_status tag (see 3.2) |
| Child runs | Each child run: status pill, title, task link, view-run link | Keep |
| Error | job.error string | Keep |
| Work product (ReceiptView) | typecheck/design gates, file count, iterations, tokens, summary, changed files, commits | Keep |
| Timing | started_at, ended_at, task counts | Keep |
| Budget | loop caps | Keep |
| **Evidence** | Raw pre-formatted JSON blob OR "No evidence recorded" | **Kill** (see 3.3) |

### 2.2 Status pills across the platform (full audit)

| Location | Pill type | Field | Source | Verdict |
|---|---|---|---|---|
| JobDetail.tsx Overview | StatusPill | `job.status` | Run record (canonical) | **Keep** — the Run's own status |
| JobDetail.tsx Overview | Tag | `job.task_status` | Ticket status, read-enriched at query time | **Kill** — stale, redundant; replace with cross-link |
| JobDetail.tsx child-run list | StatusPill | `job.status` (child) | Run record (canonical) | **Keep** — one per child run, correct |
| JobsFeed.tsx | StatusPill | `job.status` | Run record (canonical) | **Keep** |
| Work.tsx Tickets tab | StatusPill | `task.status` | Ticket record (canonical) | **Keep** — Ticket status on Ticket rows |
| Home.tsx attention cards | StatusPill | `job.status` | Run record (canonical) | **Keep** |
| AgentDetail.tsx run history | StatusPill | `job.status` | Run record (canonical) | **Keep** |

**Net change:** remove exactly one pill type — `task_status` in JobDetail Overview — replacing it with a navigation link. All other pills are canonical for their entity and correct.

---

## 3. Design decisions

### 3.1 Live activity band (running jobs)

When `job.status === "running"`, the current page shows only the elapsed timer. The redesigned running state adds a live activity band at the top of the page, above the other sections:

```
┌─────────────────────────────────────────────────────────────────┐
│  ● running  ·  3m 24s elapsed                                   │
│                                                                 │
│  Currently: Add OAuth middleware to auth service                │
│  3 tasks done · 1 running                                       │
│                                                                 │
│  [Stop run]                                                     │
└─────────────────────────────────────────────────────────────────┘
```

- Status pill: `running` kind (animated per design system).
- "Currently" line: the title of the task currently in `doing` status within this run's scope. Fetched from `GET /api/tasks?scope={scope}&status=doing&task_id={job.task_id}` (or, for sweeps, from the most recently started child run's `task_text`). Falls back to "working..." when not determinable.
- Progress line: `tasks_done` + `tasks_failed` counts from the job record (already stored; already shown in Timing; shown here while live).
- Stop: `POST /api/jobs/{id}/cancel`. Shows a ConfirmDialog ("Stop this run? Work done so far is not undone.") before firing.
- When `job.status` is not `running`, the band is absent. No placeholder.

Polling: the page polls `GET /api/jobs/{id}` every 5 seconds while status is `running`, then drops to manual refresh once terminal.

### 3.2 Links band + "edit in" actions

A compact band just below the header (and above the live-activity band when running) shows the contextual cross-links in one row:

```
[FolderGit2 project:auth]   [Bot ralph]   [MessageSquare Chat "auth refactor" →]   [Edit in this chat]   [Edit in new chat]
```

Fields:
- **Project** (if `job.project_slug`): link to `/projects/{slug}`.
- **Agent** (if `job.agent_id`): link to `/agents/{agent_id}`.
- **Original chat** (if `job.chat_id` + `job.chat_context_key`): link to `/chat/{context_key}?thread={chat_id}`. Label is `job.chat_title || "Chat"`.
- **Edit in original chat** (ghost Button, only if `job.chat_context_key` and `job.task_id`): navigates to `/chat/{context_key}?thread={chat_id}&ref_ticket={task_id}`. This is a signal to the chat to surface the ticket in the compose area for follow-up. The chat route reads `ref_ticket` from the query string and prefills a context note ("Continuing: {ticket title}") in the compose area.
- **Edit in new chat**: a ghost Button always shown (so the operator can always open a follow-up, even for unbound runs). Navigates to `/chat/new?scope={scope}&ref_ticket={task_id}` where `scope` = `scopeForJob(job)` (project slug → `project:<slug>`, else `workshop`). If no `task_id`, navigates to `/chat/new?scope={scope}`.

The `ref_ticket` query param is handled by `Chat.tsx` at load time: if present, the compose area shows a context block "Continuing: {ticket title}" that the operator can clear. This is a UI-layer concern; the chat does not auto-file a ticket. The operator types their follow-up naturally.

### 3.3 "Evidence" section: removed

The raw-dump Evidence section (`<pre>` of `evidenceText(job.evidence)`) is removed from the UI. Reasons:

1. The structured ReceiptView already shows all meaningful proof-of-work (files, commits, typecheck results, summary). When a receipt is present, the Evidence section shows nothing (already suppressed in the current code).
2. When there is no receipt, the raw dump is `null`, a generic shell error string, or an unstructured blob. None of these are actionable in the UI.
3. The "No evidence recorded" StateBlock (shown when `ev === ""`) implies the operator should expect proof-of-work here, which is false. Proof-of-work is the receipt. If there is no receipt, the run did not produce a structured artifact — this is normal for cron/event runs that produce no file output.

**Definition of "evidence" going forward:** The `evidence` field on the Job record is retained in the data model (shell workers use it; the append-only log does not migrate). The UI renders `evidence` ONLY through structured views:
- If `evidence.kind === "receipt"`: render ReceiptView.
- If the job is `awaiting_input` and `evidence` is an array of task-shaped objects: render the Needs-your-input section.
- Otherwise: evidence is not rendered. The field is opaque to the operator.

New workers writing to `evidence` SHOULD use the receipt shape. The receipt shape is the only one that produces user-visible output on this page.

### 3.4 Removing `task_status` from the Overview card

The `task_status` Tag in JobDetail's Overview card is removed. Replaced by a cross-link in the links band (section 3.2). The operator can navigate to the Work page for the Ticket's current status; they do not need a stale embedded snapshot.

This change applies only to the JobDetail / run-status page. `task_status` as a backend field is retained in the Jobs API response for consumers that need it (e.g. the Jobs feed uses it nowhere currently; it was joined for the detail view only).

---

## 4. Redesigned page layout

Full section order in the redesigned JobDetail:

```
[← Jobs]                                        [Refresh]
<PageHeader title={job.title || "Run"} />

Links band: project · agent · chat · edit-in-chat · edit-in-new-chat

━━━ Live activity band (only when running) ━━━━━━━━━━━━━━━━━━━━━━━━━
  ● running · 3m 24s
  Currently: <current task title>
  <tasks_done> done  ·  [Stop run]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Needs your input]  ← only when awaiting_input

[Error]             ← only when job.error

[Work product]      ← only when receipt present

[Child runs]        ← only when children.length > 0

[Timing]
[Budget]            ← only when budgetEntries.length > 0
```

What was removed:
- Evidence section (raw dump + "No evidence recorded" StateBlock)
- `task_status` Tag from the Overview card
- The Overview card itself is dissolved into the links band + the live-activity band; its remaining contents (trigger tag, duration, exit code) move into the Timing section

The Overview Card was a catch-all. Its only unique content was `job.status` (now the live-activity band), `job.trigger` + label (move to Timing), and duration/exit code (move to Timing). Removing the card simplifies the hierarchy without losing information.

---

## 5. Implementation touchpoints

Design only; no code shipped this ticket. The following files change in implementation:

| File | Change |
|---|---|
| `frontend/src/pages/JobDetail.tsx` | Add links band (section 3.2); add live-activity band (section 3.1); remove evidence section; remove task_status Tag; dissolve Overview card; add 5s polling while running |
| `frontend/src/lib/jobs.ts` | No change needed to types; `task_status` field retained for API compatibility but not rendered |
| `backend/routes/jobs.py` | `_enrich` keeps `task_status` for backward compat; no change |
| `frontend/src/pages/Chat.tsx` | Handle `ref_ticket` query param on load: fetch ticket title, inject into compose area as context note |
| `backend/routes/tasks_all.py` | Expose `GET /api/tasks?scope=…&status=doing&limit=1` (verify this query is already supported; likely is) |

---

## 6. Sequencing

This design has no hard blocker — the dead-end page is not a prerequisite for anything else. However:

- The "edit in original chat" action (section 3.2) requires `chat_context_key` to be populated on Job records. Currently it is joined at read time in `routes/jobs.py` (`_enrich`). Verify coverage before shipping that action.
- The live current-task lookup requires the task scope to be set on the Job record (`project_slug`). Unbound runs (no project, no agent) cannot show a current task; fall back to "working..." gracefully.
- The `ref_ticket` Chat integration is an additive change to Chat.tsx routing and can ship independently of the rest of this redesign.

---

## 7. Out of scope

- WebSocket or SSE for real-time status (polling at 5s is sufficient for a single-operator system)
- Stop-run enforcement in the backend worker (the cancel action sets a flag; whether the worker respects it is an implementation detail of the queue runner, not this design)
- The chat toolbar redesign (B3) — separate ticket
- Voice-mode handling of the run-status view (B8)
- Multi-run views (one page per run; parallel runs visible in the related-work dropdown, section 4 of B1)
