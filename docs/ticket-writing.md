# How to write a ticket

The one page on ticket titles and descriptions. It exists because there wasn't
one: the four labels were enforced by code and nothing anywhere said how to
write the words, so tickets drifted into insider shorthand that the operator
could not read on his own queue.

Enforced by `services/ticket_format.py` (the labels, and the acceptance rule
that REFUSES a filing), `services/ticket_readiness.py` (are they present) and
`services.tasks.title_warnings` (is the title usable). Everything here that a
machine cannot check is a convention, and it is not optional for that reason.

---

## A ticket has two readers

This is the thing that was missed, and it explains most of what follows.

**The worker** never sees the conversation that produced the ticket. It gets the
title and the body and nothing else. Anything the ticket does not say is guessed
at or asked about, and both cost a run.

**The operator** opens the ticket to decide something, often on a phone, often
weeks later, often having never seen the code. He needs to know what this is,
where it stands, and what he is being asked to do, before any of the worker's
instructions make sense.

The labels (of which Problem is type-conditional) serve the first reader.
Serving the second is what plain language and an honest title are for.

---

## The title

One line. Under 80 characters, and better under about ten words. Imperative.

It says **what changes and where**, in words someone who was not in the
conversation would use. It has to be skimmable in a list: if a reader needs the
body to know what the ticket is about, the title has failed, because the list is
where every ticket is met first.

- Yes: `Accepting a finished ticket from the chat marks it done`
- Yes: `Loopback-bind Ollama so it is not reachable from the LAN`
- No: `Pin the backend-to-renderer seam with a contract test`
- No: `Fix the thing in tasks_all`
- No: a sentence that trails off in an ellipsis. It is elided, not written.

**Do not name code structure with a metaphor** unless the word is the actual name
of something in the tree. "Seam", "spine", "surface", "layer", "primitive" and
"substrate" all read as meaning something specific and do not. A filename, a
function name or a status word is not jargon: it is the name of the thing.

Say what, not how, unless the how is the point. "Add a contract test" is a how;
"a backend format change cannot silently break the transcript" is the what.

---

## Ticket types

Every ticket is one of three types. The type is set on the proposal, not written
into the body, but it decides which labels the body needs.

- **Bug** — something is broken; behavior differs from intent.
- **Feature** — a new capability that does not exist yet.
- **Task** — any other defined work: a refactor, wiring, a migration, an
  investigation, or routine maintenance (deps, cleanup, config). Deliberately
  broad; a "chore" is just a low-ceremony task, not a separate type.

The labels a body carries are type-conditional. Lead with **Problem** when the
type has a motivation to state (a bug or a feature); lead with **Objective**
otherwise:

| Type | Labels, in order |
|---|---|
| Bug | Problem, Output, Start from, Boundaries |
| Feature | Problem, Objective, Output, Start from, Boundaries |
| Task | Objective, Output, Start from, Boundaries |

---

## The body

Labelled lines, in the order the type requires (see the table above).
`services/ticket_format.py` owns the labels and accepts common synonyms; write
the canonical ones.

**Problem:** what is wrong or missing today and why it matters, stated *before*
the outcome. For a bug it is the defect: what is observed versus what is
expected. For a feature it is the need being met; a user story ("As a X I want Y
so that Z") is one acceptable way to phrase it, not a separate field. This is the
line that was missing for months: the template only ever stated the fixed-state
outcome, so the actual problem never got written down. A plain task
usually has no Problem line and leads with the Objective.

**Objective:** one sentence, stating the outcome in the present tense as though
it were already true. "The chat's Tickets panel answers a finished ticket by
accepting it." Not the history, not the diagnosis, not the story of how it was
found. If the history matters, it goes lower under its own heading, where a
reader who wants it can find it and a reader who does not can skip it.

**Output:** the observable thing that proves it is done. A command that passes, a
route that returns the new field, a screen that shows the count. Never "it
works". Without this the worker picks its own finish line and is then judged
against a different one.

This line is PROSE, and prose is not enough on its own. The `acceptance` field
below carries the same statement in a form a machine can run, and it is what the
filing gate checks.

**Start from:** the files, symbols or commands to read first. "The chat named no
files; grep for X" is a real answer. This is the single highest-leverage line in
the ticket.

**Boundaries:** what not to touch, and what not to build. If the work genuinely
has no boundary, write "none: may touch anything the objective requires". A
stated none is a decision; a missing line is an omission, and it is how a
one-file change becomes a refactor nobody asked for.

Anything else the spec needs goes below those four, under its own heading.

---

## Acceptance: the finish line, as a command

**A ticket that cannot be checked without judgement is not a ticket, and filing
one is refused.** This is the newest rule on this page and the one with the most
evidence behind it.

What was measured on 2026-09-01: 208 open tickets, and the `acceptance` field
null on every one of them. The field had existed since the store did. So a
worker's only statement of "done" was a sentence somewhere in a body whose median
length was 337 words, it formed its own view of what that meant, and it reported
success against that view. That is not carelessness; it is the only available
behaviour when nothing states the finish line. It is also the specific mechanism
behind "you have no concept of completeness".

Two forms count, and both are executed by machinery that already exists:

| form | write it as | who runs it |
|---|---|---|
| one command | `{"kind": "command", "spec": "npm run typecheck"}` | a reviewer, or you, before saying it is done |
| assertions | `{"kind": "reviewer", "criteria": [{"desc": "the suite passes", "cmd": "pytest -q", "expect": "exit0"}]}` | the sweep, reported as the `acceptance_criteria` gate |

`expect` is `exit0` (the command exits 0) or a substring that must appear in its
stdout. Both forms may be given at once.

**A screenshot comparison is a command**, naming the screenshot tool and the
page. There is deliberately no "compare these two images" kind: a form nothing
can execute would put the judgement call straight back.

**What does not count**, and why the gate says so in those words:

- `{"kind": "reviewer"}` or `{"kind": "operator"}` with nothing else. Both are
  legal values of the field and neither is a check. They mean "a person will
  decide", which is the thing being replaced.
- A `spec` that is a sentence. `"The endpoint returns X and a test covers it"` is
  restating the Objective in the acceptance slot. The gate reads the first token
  of the spec: a known runner or a path is a command, an English word is not.

Where to write it:

| filing through | how |
|---|---|
| `scripts/project_tasks.py add` | `--acceptance-cmd 'npm run typecheck'`, or repeat `--criterion 'the suite passes::pytest -q::exit0'` |
| `POST /api/tasks` (the New ticket dialog) | the `acceptance` field |
| a chat's `task-patch` block | `"acceptance": {...}` on the `add` op |
| `PATCH /api/tasks/{scope}/{id}` | the `acceptance` field, and it may not be cleared |

**The existing 208 are not being backfilled.** Writing acceptance onto a ticket
whose body never stated one is guessing at what someone meant weeks ago and
storing the guess as fact, which is the failure that produced this. They are
COUNTED instead: `scripts/check_ticket_acceptance.py` pins the number per scope
and `scripts/reviewer.sh` fails a run that makes it go up. The debt is visible
and shrinks; today's work does not add to it.

---

## Plain language

Prefer the ordinary word. The audience includes someone who has not read the
conversation, and that someone is often the operator.

- Jargon is fine when it is the real name of a thing: a path, a function, a
  status, a flag. Write it as code so it reads as a name.
- Jargon is not fine as a description. If a term is doing explanatory work,
  either gloss it on first use or use the ordinary word instead.
- No em-dashes. Repo-wide rule.
- Write it so it survives being read out of context, because it will be: in a
  list, in a notification, in a chat message, on a phone.

A useful test: read the title and the Objective aloud. If someone who does not
work on this repo could not say what would change, rewrite them.

---

## Say where it stands

A ticket waiting on a person says so at the top of its body, in one line, in
plain words: what happened, what is being asked, and what each answer does.

That line is **generated, not typed** (`scripts/project_ralph.sh` for the sweep,
`services/run_inbox.py` for the row, `services/question_report.py` for the chat
message), so it cannot go stale the way a hand-written status note does. The
rule is here because the wording is a writing decision even when a program does
the writing:

- Name the state in words, not only as a status pill. "The work on this ticket
  is finished and waiting on you" beats "in_review".
- Name what each answer does. "Accept it to mark it done, or send it back with
  a note saying what to change."
- Never write one generic sentence for every ticket in a state. Four rows that
  say the same thing under four titles tell the reader nothing about which one
  to open, which is exactly the complaint that produced this page.

---

## Common failures

- **A title that is a summary.** The spec goes in the body. A title carrying the
  whole spec is split by the store and reads badly in every list.
- **An Objective that opens with a date and a story.** Put the outcome first.
- **"Output: it works."** Not an output.
- **Acceptance that restates the objective.** "The page loads correctly" is a
  sentence. `bash scripts/check_design.sh` is a check.
- **No Boundaries line.** The most expensive omission there is.
- **Insider shorthand.** See above. The operator reads these.
- **A dependency named only in prose.** "Depends on B4" is invisible to the
  scheduler. Set `depends_on` so the ordering is real.

---

## Where this is enforced

| Thing | Enforced by | Kind |
|---|---|---|
| The labels are present (Problem is type-conditional) | `ticket_readiness.check` | advisory report |
| Title present, not over-long, not elided | `tasks.title_warnings` | warning |
| Title and dependencies resolve | `ticket_readiness.BLOCKING` | refuses a run |
| The labels themselves | `ticket_format.FIELDS` | one table, one regex |
| Acceptance is runnable | `ticket_format.acceptance_refusal` | **refuses the filing**, at every door |
| Open tickets with no runnable check | `scripts/check_ticket_acceptance.py` | pinned ratchet, may only go down |
| Everything on this page about wording | nothing | convention |

The unattended queue additionally refuses a ticket that does not state its
boundaries, so an underspecified ticket is not silently handed to a worker.
