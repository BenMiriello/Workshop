<!-- author: self -->
Start at `docs/tickets-and-autonomy-handoff-2026-08-12.md`. It names the 4 docs worth reading out of 51, what shipped with commits, and the traps.

The plan and authority for this work is `docs/platform-redesign/autonomy-turn-on-2026-08-12.md`.

Headlines: the automation loop is complete and DORMANT (last ticket executed 2026-07-25, no resident runner, autonomous cron commented out by operator decision). Zero registry agents have ever produced a job record. The roles enum (maker/reviewer/triager/keeper/broker) is real in code and routes nobody.

Fixed 2026-08-12 and live: preferences captured from chat were written to a scope nothing injected (11 records, 3 weeks, zero retrievals); the Design System rule had been dropped from every prompt for ~25 days by the 700-token budget.

Highest-value remaining item: `mcp__workshop__search` is in exactly one allowlist (the general chat). Workers and agents cannot search the workshop's own writing.

Scoped `workshop` deliberately: the semantic index only walks `shared/knowledge`, so a `development`-scoped note would be unfindable. That is itself one of the findings.