# Autonomy model (2026-07-18)

- Status: accepted
- Depends on: entity-model-2026-07-18.md (section 8.4), ADR 0004, ADR 0008
- Source: operator ask C1/C3/C4 (operator-asks-2026-07-16.md), deferred-autonomy task in TODO.md

## The two-part requirement

The operator stated two distinct requirements that must coexist:

1. **Per-agent**: autonomy is configurable per agent and is NOT a global setting. Each agent
   has its own autonomy mode, settable independently.
2. **Global override**: a single off-switch that supersedes all per-agent settings. When
   engaged, no background work runs regardless of individual settings. When cleared, each
   agent immediately resumes its own configured mode -- no restore step needed.

These two requirements are in tension only if the override is implemented as a mutation of
individual settings. This design resolves the tension by making the override an overlay.

---

## 1. Where the setting lives

### 1.1 Per-agent setting

The `autonomy` field lives on the Agent entity (agent definition frontmatter, parsed by
`agents_registry.py`). It is one of three values defined in the entity model section 8.4:

| Value | Background trigger behavior | Commits without review? |
| --- | --- | --- |
| `reactive` | Never fires background triggers. Runs only on explicit operator action. | n/a |
| `supervised` | Fires on background triggers (cron, loop, event). Surfaces results for review before committing. | no |
| `autonomous` | Fires on background triggers (cron, loop, event). Commits results directly. | yes |

The `triggers` field (list of cron/loop/event entries in agent frontmatter) specifies WHEN
an agent wakes. The `autonomy` field specifies HOW it behaves when it wakes.

An agent with `autonomy: reactive` may still have triggers declared; those triggers are
simply ineligible for dispatcher dispatch. The distinction matters because trigger declarations
are part of the agent's definition (what it is), whereas `autonomy` is a runtime setting
(whether it runs).

**Cascade for inheritance**: `Ticket.latitude` overrides `Agent.latitude` per the entity
model (section 8.2). Autonomy follows no cascade; it is an Agent-only setting. A Ticket
does not override its Agent's autonomy mode.

**Default**: `reactive`. An agent that does not declare `autonomy` in its frontmatter does
not run in the background. Background execution is opt-in.

### 1.2 Global override

The global override is a single JSON file at:

```
~/workshop/.runtime/autonomy-override.json
```

Schema:

```json
{
  "engaged": false,
  "reason": null,
  "engaged_at": null,
  "engaged_by": null
}
```

- `engaged`: boolean. The only field the dispatcher reads during its tick. When `true`, no
  background triggers fire. When `false` (or file absent), normal dispatch proceeds.
- `reason`: free-text string for the operator's own record. Not read by any code path.
- `engaged_at`: ISO timestamp of when the override was engaged.
- `engaged_by`: `"operator"` or `"system"` (for automated safety trips).

**File-absent means clear.** A missing file is equivalent to `{ "engaged": false }`. The
dispatcher must treat a missing file as clear, not as an error.

---

## 2. Override state machine

```
      engage                     clear
CLEAR ------> ENGAGED           ENGAGED ------> CLEAR
```

Two states, two transitions. There is no intermediate state.

### CLEAR (default)

- File is absent or `engaged: false`.
- Dispatcher fires all background triggers according to each agent's `autonomy` and
  `triggers` configuration.
- Individual agent settings are read directly from the registry on every tick.

### ENGAGED

- File exists with `engaged: true`.
- Dispatcher fires NO background triggers. The check is a single read at the top of each
  dispatch tick before any per-agent work begins.
- Manual/reactive invocations (operator pressing "Run now", explicit `POST /autonomy/run`,
  or another agent calling an agent directly) are NOT blocked by the override. The override
  stops unattended background dispatch; it does not prevent deliberate operator action.
- In-flight runs that started before the override was engaged continue to completion. The
  override affects NEW dispatches only.
- Individual agent `autonomy` fields are NOT modified. The override is purely an overlay.

### Transitions

**Engage the override:**

```bash
cat > ~/workshop/.runtime/autonomy-override.json <<EOF
{
  "engaged": true,
  "reason": "<operator note>",
  "engaged_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "engaged_by": "operator"
}
EOF
```

Or via the UI action described in section 5. The transition takes effect on the next
dispatcher tick (within 20 minutes). In-flight runs are not interrupted.

**Clear the override:**

```bash
echo '{"engaged":false,"reason":null,"engaged_at":null,"engaged_by":null}' \
  > ~/workshop/.runtime/autonomy-override.json
```

Or delete the file. Either is equivalent to CLEAR. The transition takes effect on the
next dispatcher tick.

---

## 3. Restore-on-clear: why it is implicit

The operator requirement is: "each individual restores to its configured value iff the
override clears."

This is satisfied by design without any restore operation. Because the override NEVER
modifies individual agent `autonomy` fields, there is nothing to restore. When the override
clears, the dispatcher reads each agent's `autonomy` field as it was before, during, and
after the override. The field's value never changed.

**This is the load-bearing invariant**: the override is an overlay, not a mutation. Any
implementation that modifies per-agent fields when engaging the override breaks this invariant
and requires a restore log. The file-flag design avoids that entirely.

Concrete example:

```
Agent A: autonomy = autonomous   (runs every cron tick, no review)
Agent B: autonomy = supervised   (runs on event trigger, waits for review)
Agent C: autonomy = reactive     (never runs in background)

Override ENGAGED:
  - A's cron tick fires: dispatcher skips A (engaged = true)
  - B's event fires: dispatcher skips B (engaged = true)
  - Operator manually invokes A: A runs (manual, not blocked)
  - A.autonomy = autonomous  (unchanged)
  - B.autonomy = supervised  (unchanged)

Override CLEARED:
  - Next tick: dispatcher reads A.autonomy = autonomous -> fires
  - Next event for B: dispatcher reads B.autonomy = supervised -> fires
  - No restore step ran. There was nothing to restore.
```

---

## 4. How a run reads effective autonomy

Every dispatch decision reduces to this two-step read:

```python
def effective_autonomy(agent_slug: str) -> str:
    override = read_override()          # .runtime/autonomy-override.json
    if override.engaged:
        return "reactive"               # override forces reactive for dispatch purposes
    agent = registry.get(agent_slug)
    return agent.autonomy               # individual setting
```

The dispatcher calls this at the start of each per-agent evaluation. If the result is
`reactive`, the agent is skipped for background dispatch (though it may still be manually
invoked). If the result is `supervised` or `autonomous`, the agent is eligible for dispatch
according to its triggers.

The `supervised` vs `autonomous` distinction is enforced at run completion, not at dispatch
time:

- `supervised`: the run ends in `in_review`; the operator must approve before the result
  is committed.
- `autonomous`: the run ends in `done` (subject to the completion gate); results commit
  without operator review.

This matches the entity model's Ticket state machine: `doing -> in_review -> done` for
supervised, `doing -> done` (via reviewer gate only) for autonomous.

---

## 5. Operator surface

### Via UI (Phase O6, per orchestration-and-autonomy.md)

The AutonomyPanel (one per scope, plus a workshop-level panel) exposes:

- Per-agent: a three-value select for `reactive / supervised / autonomous`. Edit-mode +
  Save (config write, never commit-on-change per the design system).
- Global override: a single prominent toggle labeled "Suspend all background work", with
  an optional reason field. Toggle state reads from `.runtime/autonomy-override.json`.
  State is `StatusPill` rendered as `warn` when engaged, `ok` when clear.

The override toggle appears at the workshop scope level only (not per-project, not
per-agent). It is a system-wide control and must be visually distinct from per-agent
settings to prevent confusion.

### Via CLI / scripts

The `AUTONOMY_DISABLED` environment variable used in maintenance scripts
(`orchestration-and-autonomy.md` section 11 conventions) is a BUILD-TIME guard for
humans editing the dispatch scripts, not the runtime override. Do not conflate them.

Runtime override: write the file. Maintenance override: set `AUTONOMY_DISABLED=1` in the
shell before running scripts. These are independent mechanisms.

---

## 6. Edge cases

**Agent added while override is engaged.** The new agent is registered normally with its
declared `autonomy`. It does not run in the background until the override clears. This is
the correct behavior: new agents should not surprise the operator by starting work
immediately during an override.

**Individual `autonomy` changed while override is engaged.** The new value is stored in
the agent definition. It takes effect on the next tick after the override clears.

**Override file is corrupt (invalid JSON, permission error).** The dispatcher must treat
a corrupt or unreadable file as `engaged: true` (fail-closed). A corrupt override file
should not allow background dispatch to proceed; it signals a filesystem problem the
operator needs to investigate. The dispatcher logs the error and skips all background
dispatch for that tick.

**Multiple simultaneous manual runs while override is engaged.** Manual runs are not
blocked; they proceed normally. The override is specifically a stop on background
(unattended) dispatch.

**The cron tick fires while override is engaged.** The dispatcher reads the override,
finds it engaged, logs "autonomy override engaged -- skipping background dispatch", and
exits. No per-agent evaluation happens.

---

## 7. Relationship to other axes

The `autonomy` axis is one of six orthogonal axes defined in the entity model (via
concept-schema section 2, now folded into the entity model). It is independent of:

- **`role`**: a triager can be `autonomous` (it will self-dispatch); a maker can be
  `reactive` (it only runs when the operator presses Run). Role is output authority;
  autonomy is dispatch behavior.
- **`latitude`**: a `strict` latitude maker can be `autonomous` (dispatches itself, but
  blocks on every ambiguity when running). A `open` latitude maker can be `reactive`
  (never dispatches itself, but acts freely when manually invoked). They are orthogonal.
- **`depth`**: a `quick` depth agent can be `autonomous`; a `deep` depth agent can be
  `reactive`. These are independent knobs.
- **`triggers`**: the declared triggers define WHAT events are eligible to wake the agent.
  The `autonomy` field determines whether the dispatcher acts on those events. A trigger
  with `autonomy: reactive` is declared but never dispatched automatically.

---

## 8. Implementation pointers

This is a design doc. No code is shipped in this ticket. The implementation work lives in
phase O6 of `orchestration-and-autonomy.md`. Files that will need to change:

| File | Change needed |
| --- | --- |
| `backend/services/agents_registry.py` | Add `autonomy` field parsing (alongside `_coerce_role`). Default: `reactive`. |
| `scripts/dispatch_triggers.sh` | Read `.runtime/autonomy-override.json` at tick start; skip all background dispatch if engaged. Wrap per-agent dispatch in `effective_autonomy()` check. |
| `backend/routes/agents.py` | Expose `autonomy` field on agent read/write endpoints. |
| `frontend/src/components/AutonomyPanel.tsx` | Per-agent autonomy select. Global override toggle at workshop scope. |
| `scripts/cron_agent.sh` | Pass override state through to dispatcher. |

The override file path `.runtime/autonomy-override.json` must be created by the first write
(no mkdir required beyond the `.runtime/` dir which already exists). The dispatcher must
handle the missing-file case as CLEAR.

---

## 9. What is not in scope

- **Per-project override**: there is one global override, not one per project. A project-
  level suspend would require the operator to know which projects to suspend; the global
  override is the emergency brake. Individual agents can be set to `reactive` per-agent if
  a project-level effective suspension is needed.
- **Scheduled override** (e.g., "suspend background work between 2am and 6am"): not in
  scope. The override is operator-engaged and operator-cleared.
- **Override log or audit trail**: not in scope for a single-operator system. The file's
  `engaged_at` and `reason` fields serve as a lightweight record; no separate journal.
- **In-flight run interruption**: the override does not interrupt running jobs. Interruption
  is a separate concern (budget caps, wall-clock limits) handled in `ralph.sh`.
