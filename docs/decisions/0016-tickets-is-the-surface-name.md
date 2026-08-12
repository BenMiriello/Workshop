# 0016. The work surface is named Tickets, and the rename stops at the label

- Status: accepted
- Date: 2026-08-12

## Context

Every entity around this page was carefully named and ratified. The page itself
was not.

`entity-model-2026-07-18.md` §1 fixes the vocabulary: a durable work item is a
**Ticket** (code identifier `task`), a single execution attempt is a **Run**
(code identifier `job`), an attended conversation is a **Chat**, a typed
dispatchable worker is an **Agent**. That table is marked accepted, and renaming
the code identifiers is explicitly closed twice: entity-model §9.5 preserves
ADR 0001, and `coherence-migration-plan-2026-07-18.md` §12 says "Do not
litigate."

"Work" appears in none of it. It is not in the vocabulary table, no doc proposes
renaming it, and no doc ratifies it either. It survived as a bare surface label
from before the vocabulary existed.

The operator raised it directly on 2026-08-11: the surface needs a name for the
individual item, "like how github has issues", and asked whether to use tickets
or adopt something else.

## Decision

**The nav item and page header say "Tickets". The route stays `/work`. The code
nouns stay `task` and `job`.**

The three tabs become **Open / Runs / Review** (was Tickets / Runs / Review), so
the page and its own first tab do not collide.

## Why this and not the alternatives

**"Work" cannot stay**, because it is a mass noun. It can name a page and it can
never name one item, and naming one item is exactly what the operator needed.
You cannot say "assign me a work."

**Not "Issues."** It is GitHub's word and carries a defect connotation. Most of
what is in this store is a feature, a design pass, or a chore. Adopting it would
also invite the assumption that the model mirrors GitHub Issues specifically,
when `ticket-model-and-external-trackers-2026-08-07.md` §1 deliberately targets
the common denominator of Linear, Jira, GitHub, Asana, Shortcut and Azure DevOps.

**Not "Tasks."** It collides with the code identifier, which is the one thing the
vocabulary work was trying to keep separable, and it collides again with the
chat toolbar's existing Tasks drawer.

**Not "Items."** It says nothing.

Ticket was already the ratified noun for the thing the page lists. The page
should be named after its contents.

## The rename stops at the label

This is a surface-only change, on purpose:

- `/work` keeps resolving. Every bookmark, every `?tab=` deep link, every
  `nextWorkParams` caller and every in-app link keeps working with no redirect.
- `task` and `job` stay in the store, the CLI, the API paths and every
  identifier. ADR 0001 stands.
- The internal tab value stays `"tickets"`, so `parseWorkTab` and existing URLs
  are unaffected; only its label reads "Open".

A rename that reaches further buys nothing and breaks links.

## Consequences

- One more place where the UI word and the code word differ. That gap is already
  the ratified design (ticket/task, run/job), and it is documented in
  `routes/tasks_all.py`'s module docstring and in `lib/tasks.ts`.
- `docs/platform-redesign/*` prose that says "the Work page" is now off by one
  word. Those docs are historical records of decisions, not live copy, and are
  not being rewritten for this.
- The Runs tab still renders `JobsFeed`, whose component name keeps the old
  noun. Consistent with the rule: code identifiers do not move.
