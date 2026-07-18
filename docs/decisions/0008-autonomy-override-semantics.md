# 0008. Autonomy override is a file-flag overlay, not a per-agent mutation

- Status: accepted
- Date: 2026-07-18
- Depends on: ADR 0004 (agent identity), ADR 0007 (entity model), entity-model-2026-07-18.md section 8.4

## Context

The operator's requirement has two parts that pull in opposite directions:

1. Per-agent autonomy is a configurable individual setting (not global). Each agent
   declares `reactive | supervised | autonomous` in its definition frontmatter and the
   dispatcher honors it independently.
2. There must be a single global override off-switch that supersedes all individuals.
   When engaged, no background work runs. When cleared, each agent immediately returns
   to its configured mode.

The "restore" clause in requirement 2 creates a design fork:

**Option A -- Mutation**: when the override is engaged, set every agent's `autonomy` to
`reactive`. When cleared, restore each agent's prior value from a saved log. This
satisfies the requirement but introduces a restore log that must be maintained, a
write path that modifies agent definitions, and a failure mode where the restore log
diverges from reality (e.g., an agent is deleted while override is engaged, or an
agent's setting is deliberately changed while override is engaged).

**Option B -- Overlay**: the override is stored as a single file flag. The dispatcher
reads the flag first; if engaged, it skips all background dispatch. Per-agent `autonomy`
fields are never read during an engaged override for dispatch purposes. Per-agent fields
are never modified. When the override clears, the dispatcher reads per-agent fields as
usual -- they were never changed, so no restore is needed.

Option B satisfies the requirement without a restore log because it makes the restore
trivially correct by construction: there is nothing to restore.

The entity model (section 8.4) already defined the three autonomy modes. This ADR
settles where the override lives, how the dispatcher reads it, and why the restore
property holds.

## Decision

The global autonomy override is implemented as a file flag at
`~/workshop/.runtime/autonomy-override.json` with schema:

```json
{
  "engaged": true | false,
  "reason": string | null,
  "engaged_at": ISO-timestamp | null,
  "engaged_by": "operator" | "system" | null
}
```

The dispatcher (`scripts/dispatch_triggers.sh`) reads this file as its first action on
every tick. If `engaged` is `true`, the dispatcher logs a one-line notice and exits
without evaluating any agent's triggers. If the file is absent or `engaged` is `false`,
normal dispatch proceeds.

Per-agent `autonomy` fields in agent definitions are NEVER modified by engaging or
clearing the override. The override is an overlay, not a mutation.

Restore-on-clear is implicit: because per-agent fields are never changed, the
dispatcher's normal per-agent evaluation after the override clears reads the same values
that were set before the override was engaged. No restore step, no restore log, no
restore API.

**Fail-closed rule**: a missing or unreadable override file is treated as CLEAR
(engaged = false). A corrupt or permission-error file is treated as ENGAGED (engaged = true).
The asymmetry is intentional: a filesystem error should not allow unattended background
dispatch to proceed.

**Manual invocations are not blocked**: the override stops the dispatcher's unattended
background sweep. Explicit operator actions (UI "Run now", `POST /projects/{slug}/autonomy/run`,
direct agent invocation) are not gated by the override.

**In-flight runs continue**: runs that started before the override was engaged are not
interrupted. The override applies to NEW dispatcher decisions only.

## Consequences

What is now settled:

- The override file at `.runtime/autonomy-override.json` is the single source of truth
  for the global override state. No other mechanism (env var, database entry, in-memory
  flag) governs this. The `AUTONOMY_DISABLED` env var used in maintenance scripts is a
  separate, build-time convention for humans editing dispatch scripts; the two must not
  be conflated.
- Any UI control for the global override writes this file. Any script or API that checks
  the override reads this file.
- The dispatcher must be written so that the override check is the first operation,
  before any agent iteration, with no per-agent side effects on engagement or clearance.
- The Agent entity's `autonomy` field (per entity-model-2026-07-18.md section 6.2) must
  be treated as read-only by the override mechanism. Any code path that writes agent
  definitions must not write the `autonomy` field as part of engaging or clearing the
  override.

What requires follow-on work:

- The `autonomy` field must be added to `agents_registry.py` parsing (alongside
  `_coerce_role`, fixing defect D2 from entity model section 8.5). Default: `reactive`.
- `dispatch_triggers.sh` must add the override file read at tick start.
- The AutonomyPanel must expose the global override toggle at the workshop scope level.
- Per-agent `autonomy` select (reactive/supervised/autonomous) must be added to the
  agent detail UI.

What this ADR does not change:

- The `supervised` vs `autonomous` distinction at run completion (in_review vs done) is
  governed by the Ticket state machine in the entity model, not by this ADR.
- The `triggers` field on agents (cron/loop/event schedule) is unchanged. The override
  suppresses dispatcher evaluation of those triggers; it does not remove them.
- Voice-mode files are not touched.
- Any agent with `autonomy: reactive` before the override was engaged stays `reactive`
  after the override clears. The override did not change anything; the cascade is correct
  by construction.
