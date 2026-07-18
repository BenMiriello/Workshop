# ADR 0009: Single canonical status source

- Status: accepted
- Date: 2026-07-18
- Depends on: ADR 0007 (entity-model-as-first-class-backend-concepts)
- Implements: B2 (run-status-view design)

---

## Context

The current `/jobs/{id}` (JobDetail) page renders two status indicators for the same logical execution:

1. `job.status` — the Run's status, written by the run itself at start/finish (`running | done | failed | cancelled | awaiting_input`).
2. `job.task_status` — the Ticket's status, joined from the task store at query time by `routes/jobs._enrich`.

These two fields can agree (both "done") or disagree (run "failed", ticket now "done" because it was retried; run "done", ticket "in_review" because the reviewer hasn't checked yet). When they disagree, the operator has no way to know which one is "the truth" or what action to take.

More broadly: the entity model (ADR 0007) defines two separate entities — Run and Ticket — with distinct status machines. A Run's status is not the same as a Ticket's status; they answer different questions. Rendering both in the same view without labeling which is which conflates two orthogonal signals.

The entity model says a Ticket has zero to N Runs. A Run's status says "what did this attempt do?" A Ticket's status says "where is this work at?" These are different questions with different answers.

---

## Decision

**One entity, one status, one source. Never display two status indicators for the same entity in the same view component.**

Specifically:

1. The Run status view (JobDetail / `/jobs/{id}`) shows exactly one status indicator: `job.status`, the Run's own status. This is canonical for the Run.

2. The `task_status` Tag rendered alongside `job.status` in the current Overview card is removed from the run-status view. The Ticket's current status is not shown here; instead, a cross-navigation link to the Work page is provided so the operator can navigate to the Ticket's authoritative record.

3. Any future page that shows both a Run's status and a Ticket's status must show them in SEPARATE sections, clearly labeled by entity, never co-located in the same status row.

4. The Ticket status on Ticket list rows (Work.tsx) is canonical for the Ticket and is unaffected.

5. The Run status in the Jobs feed (JobsFeed.tsx) and on agent/home attention cards is canonical for the Run and is unaffected.

---

## Alternatives considered

### A: Keep both, add labels

Show `job.status` as "Run: done" and `job.task_status` as "Ticket: in_review". Clear up the confusion by labeling.

Rejected: labeling does not fix the staleness problem. `task_status` is a snapshot taken at query time. If the ticket state changes while the page is open (another run completes, the reviewer approves), the snapshot goes stale without a page refresh. Polling to keep both fresh adds complexity for a signal the operator can get directly from the Work page.

### B: Merge into a single enriched status

Compute a single derived status: "if ticket is done, show ok; else if run failed, show danger; else show running". Collapse both into one indicator.

Rejected: the merged signal masks important cases. A run that failed (Run: failed) while the ticket is later retried and completes (Ticket: done) should still show the run as failed when you look at that run's detail page. The Run's own status is the authoritative answer for that execution attempt; hiding it inside a derived signal destroys audit traceability.

### C: Remove job.task_status from the API response entirely

Stop enriching Job records with `task_status` at query time.

Rejected: the field may be useful for the Jobs feed or Home page future uses. The decision here is about what to render in the run-status view, not what the API returns. The field stays in the API response but is not rendered in JobDetail.

---

## Consequences

### Positive

- Single unambiguous signal on the run-status page. The operator knows exactly what they are reading.
- Eliminates the stale-snapshot problem for `task_status` in the run-status view.
- Reduces the pill count on the run-status page from 2 to 1 (for the same logical subject).
- Cross-navigation to the Work page is cleaner than an embedded stale snapshot.

### Negative

- Navigating from a run to its ticket's current status requires one extra click (run-status page → Work page). This is the right trade-off: the two pages answer different questions.

### Neutral

- The `task_status` field remains in the Jobs API response for backward compatibility and potential future uses (e.g., a dashboard that wants to show "which runs are for tickets already done").
- No backend migration required.

---

## Applicability

This decision governs any view that combines Run and Ticket entities. The rule: pick one entity per status row and clearly communicate which it is. The current JobDetail violation is the only instance identified in the 2026-07-18 audit. Apply this rule to any future page that shows both Runs and Tickets together.
