# 0028. Tickets is the name everywhere the operator can see

- Status: accepted
- Date: 2026-08-20
- Supersedes the "rename stops at the label" clause of
  [0016](0016-tickets-is-the-surface-name.md). 0016's *choice* of the word
  Tickets stands and is not reopened.

## Context

ADR 0016 chose "Tickets" as the surface noun and then deliberately stopped:
the route stayed `/work`, the code nouns stayed `task` and `job`, and it argued
"a rename that reaches further buys nothing and breaks links."

We now have a measurement of what it bought and what it cost.

In August 2026 an outside design agent was given the running UI and asked to
design the navigation shell for it. It came back with a dock whose ticket
destination was named **Work**. It was not being careless. Reading only the
screen, that is the better-supported answer:

- `pages/Work.tsx` rendered the eyebrow **Work** directly above the `<h1>` that
  said **Tickets**. One header, two nouns, the wrong one on top.
- The route said `/work`.
- The chat toolbar button said Tickets and opened a panel headed **Work**.
- The agent page listed the same records under a third heading, **Tasks**.

The operator's verdict on the returned design: "the proper name is tickets and
if the design says work then that was an error, but you should also consider why
it got the idea that work was the term."

The answer to "why" is that the product was telling it three different things
and 0016 is why. So the cost of the half-rename is not aesthetic. A partial
rename is worse than either whole one, because it makes the surface
unreadable as evidence: nobody looking at the app can tell which noun is the
product's, and everyone who reads it downstream (a design agent, a new
contributor, the operator six months later) has to guess.

0016's own supporting argument had also expired. It reasoned that `/work` must
stay so links keep resolving, but by then a ticket RECORD already lived at
`/tickets/:scope/:id`. The surface was already split; `/work` was not holding
a consistent world together, it was one half of an inconsistency.

## Decision

**Every name the operator can read says Ticket. Code identifiers are not
covered by this ADR.**

Concretely:

- The list route is `/tickets`. `/work` redirects to it **preserving the query
  string**, because `?scope=` and `?status=` are what the in-app links carry
  and a bare `<Navigate>` would silently drop the filter.
- Every user-facing string that said Work-as-a-ticket now says Ticket: the page
  eyebrow (which now names the scope, the thing it was actually for), the chat
  panel heading and its dialog and group labels, "Open in Tickets", "Open
  blocked tickets", "View in Tickets", Ticket settings.
- `TaskPanel`'s user-facing copy says ticket, including its default heading and
  its empty and error states. The agent page's heading goes Tasks -> Tickets.
  This was the third noun and the least defensible: it was neither the ratified
  surface word nor a code identifier, just an old label.

## What this ADR does NOT do

`task` remains the code identifier: the HTTP path `/api/tasks`, `services/tasks.py`,
`.tasks.json`, `lib/tasks.ts`, and the component and directory names
(`WorkPanel`, `components/chat/work/`, `lib/workParams.ts`) are untouched here.
ADR 0001 stands.

That is a scoping decision, not a re-endorsement of the split. The gap between
`task` in code and Ticket on screen is now the ONLY gap, and it is invisible to
the operator, which is the property that matters. Closing it as well is a real
option and a live one: `docs/decisions/0019-work-data-lives-in-postgres.md`
already named the Postgres tables `ticket`, so the storage layer has moved and
the identifier rename is half-done from the other end. It is sequenced with the
app/project model work rather than done here, because it touches the mobile
client, `scripts/project_tasks.py`, and a generated OpenAPI type file, and
should land as one migration rather than as a copy edit's tail.

## Consequences

- One noun on screen. A reader of the UI can no longer derive a wrong answer.
- A ticket record now keeps the Tickets row lit in the page column, because the
  list and the record finally share a path prefix. That behaviour was
  impossible before and it was a genuine defect: reading a ticket darkened the
  whole column. `places.test.ts` pins it.
- `/work` is a redirect, so nothing that exists breaks.
- Prose in historical `docs/platform-redesign/*` that says "the Work page" is
  now off by one word. As 0016 said of the same class of text: those are
  records of decisions, not live copy, and are not rewritten.
