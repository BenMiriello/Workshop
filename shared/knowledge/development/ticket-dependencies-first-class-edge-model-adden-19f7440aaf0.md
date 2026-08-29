Operator directive (2026-07-18): ticket/task **dependencies** must be a first-class capability on the queue, usable manually by the operator and programmatically by agents. Priority guides *preference*; dependencies enforce *eligibility*.

## What T0's entity model must specify

- **Edges between any two tickets** (not just parent/child sub-tickets):
  - `depends-on` / `blocks` — the hard edge (inverse of each other).
  - `relates-to` — soft link, for surfacing in the UI only, no scheduling effect.
- **Sub-ticket links are a special case** of this same edge model, not a separate mechanism. B4's ticket-maker creates `depends-on` edges when it decomposes.
- **Eligibility rule for the worker's next-pick**: choose the highest-`priority` ticket whose `depends-on` set is all satisfied (done/cancelled). A ticket with an unmet dependency is NOT runnable even at higher priority than everything else. This is what enforces the 16-ticket design order without hand-tuning priority numbers.
- **Mutable manually or programmatically**: operator can add/remove edges by hand in the UI; agents (ticket-maker, orchestrator) can create them during decomposition. Include **cycle detection** so edges can't deadlock the queue.
- **Surfaced in UI**: run-status view (B2) and in-chat related-tasks dropdown (B1) show blockers/blocked-by, so a stalled ticket shows *why* (waiting on X) rather than silently sitting.

This supersedes the earlier implicit 'dependencies only inside sub-tickets' framing: dependencies are a general graph over all tickets.