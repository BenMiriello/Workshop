# What may run unattended, measured

**2026-09-04.** Written because the dispatcher now ticks from the backend
(`services/backend_schedule`), which makes the question "what would it pick up?"
answerable instead of hypothetical. Everything below is a measurement of this
checkout on this date, not a design. Nothing here was changed by the work that
produced it; autonomy is still off and turning it on is the operator's call.

## The fear, restated as a number

The stated reason autonomy stays off is that an armed worker would "blindly
execute all of the past tickets, most of which agents filed for themselves and
many of which are already built with a stale status."

Measured against the live ticket store, with the data owner pinned:

| | count |
|---|---|
| tickets in the store | 440 |
| status `todo` | 184 |
| `todo` and `execution == "code"` | 140 |
| `todo`, `code`, and `ticket_readiness.check(...)["ok"]` | **130** |
| of those 130, filed by an agent (`source.kind == "agent"`) | **105** |
| of those 130, filed by the operator | 12 |
| scopes armed today | one: `development`, at `supervised` |

`tasks.next_ready(<repo root>, unattended=True)` returns a ticket right now. The
scope is armed, so the only thing standing between that ticket and a running
sweep is the global override at `.runtime/autonomy-override.json`, engaged since
2026-08-24. **The fear is accurate.** Clearing the override today would feed the
resident runner a queue of 130 tickets, 81% of which agents wrote for
themselves, one at a time until the usage ceiling refused.

## What the gates actually do

`unattended=True` is a real conjunction, not a formality. In order:

1. `scope_execution.is_armed_root(root)` — fail-closed at `reactive`. A scope
   nobody armed yields nothing.
2. `status == todo` and every `depends_on` is `done` or `cancelled`.
3. `execution == "code"` — 44 of 184 `todo` tickets are excluded here.
4. `ticket_readiness.check(t)["ok"]` — all six of title, objective, acceptance,
   entry points, boundaries and resolvable dependencies. 10 more excluded.
5. Above all of it, `autonomy_override.is_engaged()`, a union of the JSON
   overlay and the legacy flag file that fails closed.
6. Beside it, `scope_execution.stage_ceiling`: `development` is armed
   `supervised`, so a passing sweep lands `in_review`, never `done`.

Two more sit on the dispatch path rather than the selection path:
`usage_ledger.can_admit` (a cost bound), and the ADR 0014 developer-mode gate in
`task_scope.root_for_context` (the platform's own tree is refused unless
`WORKSHOP_DEV_MODE=1`).

That is a lot of gates, and they are all real. **They still do not answer the
fear**, because none of them is a decision about a particular ticket. 184 → 130
is a filter over everything ever filed. Arming one scope arms all 130 at once.

## The one field that would make it impossible by construction

`assignee`. It already exists, `next_ready` already reads it, and today every
one of the 184 `todo` tickets carries the same value: `operator`.

That produces a measurable asymmetry:

    next_ready(root, unattended=True)                   -> a ticket (1 of 130)
    next_ready(root, assignee="agent:...", unattended=True) -> None

An assignee-scoped sweep selects **nothing**, because nothing is assigned to a
worker. The unscoped sweep selects everything, because `in_scope` returns True
for every ticket when no assignee is named — and the unscoped sweep is what a
project or scope sweep runs.

So the rule is one sentence: **the unattended path is always assignee-scoped,
and `operator` is not a worker.** A sweep with no named assignee selects nothing.

What that buys, in this checkout, today:

- Eligibility goes from 130 to **0**, and every increment is a per-ticket act.
- Arming a scope grants nothing on its own, which is the property the current
  arrangement lacks — arming is a switch with a blast radius of the whole
  backlog.
- The 105 agent-filed tickets stay inert until somebody moves one off the
  operator's own queue, which is exactly the review the operator asked for.
- It is reversible per ticket, by reassignment, with no new field, no new store,
  no approval step and no allowlist.
- It composes with everything above rather than replacing it: the readiness and
  execution gates still apply to the assigned set.

The cost is that "arm the scope and let it work the queue" stops being a thing
you can do in one action. That is the point.

## What this document is not

It is not a plan and it does not authorise anything. The change it describes is
three lines in `tasks.next_ready`'s `in_scope`, and it was deliberately **not
made** here: it changes what may run, which is the operator's decision and is
outside the boundaries of the tickets this work was done under (`152856ba`
scheduling only, `2f1b6a66` no second predicate).

The measurements are reproducible:

    backend/.venv/bin/python -c "
    import sys; sys.path.insert(0, 'backend')
    from pathlib import Path
    from services import tasks, ticket_readiness, ownership, scope_execution
    ownership.pin_operator()
    ts = tasks._load(Path('.').resolve())
    todo = [t for t in ts if t.get('status') == 'todo']
    elig = [t for t in todo if t.get('execution') == 'code'
            and ticket_readiness.check(t).get('ok')]
    print(len(ts), len(todo), len(elig))
    print(scope_execution.armed_scopes())
    print(tasks.next_ready(Path('.').resolve(), unattended=True) is not None)
    print(tasks.next_ready(Path('.').resolve(), assignee='agent:x',
                           unattended=True) is not None)"
