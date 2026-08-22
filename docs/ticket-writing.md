# How to write a ticket

The one page on ticket titles and descriptions. It exists because there wasn't
one: the four labels were enforced by code and nothing anywhere said how to
write the words, so tickets drifted into insider shorthand that the operator
could not read on his own queue.

Enforced by `services/ticket_format.py` (the labels), `services/ticket_readiness.py`
(are they present) and `services.tasks.title_warnings` (is the title usable).
Everything here that a machine cannot check is a convention, and it is not
optional for that reason.

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

The four labels serve the first reader. Serving the second is what plain
language and an honest title are for.

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

## The body

Four labelled lines, in this order. `services/ticket_format.py` owns the labels
and accepts common synonyms; write the canonical ones.

**Objective:** one sentence, stating the outcome in the present tense as though
it were already true. "The chat's Tickets panel answers a finished ticket by
accepting it." Not the history, not the diagnosis, not the story of how it was
found. If the history matters, it goes lower under its own heading, where a
reader who wants it can find it and a reader who does not can skip it.

**Output:** the observable thing that proves it is done. A command that passes, a
route that returns the new field, a screen that shows the count. Never "it
works". Without this the worker picks its own finish line and is then judged
against a different one.

**Start from:** the files, symbols or commands to read first. "The chat named no
files; grep for X" is a real answer. This is the single highest-leverage line in
the ticket.

**Boundaries:** what not to touch, and what not to build. If the work genuinely
has no boundary, write "none: may touch anything the objective requires". A
stated none is a decision; a missing line is an omission, and it is how a
one-file change becomes a refactor nobody asked for.

Anything else the spec needs goes below those four, under its own heading.

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
- **No Boundaries line.** The most expensive omission there is.
- **Insider shorthand.** See above. The operator reads these.
- **A dependency named only in prose.** "Depends on B4" is invisible to the
  scheduler. Set `depends_on` so the ordering is real.

---

## Where this is enforced

| Thing | Enforced by | Kind |
|---|---|---|
| The four labels are present | `ticket_readiness.check` | advisory report |
| Title present, not over-long, not elided | `tasks.title_warnings` | warning |
| Title and dependencies resolve | `ticket_readiness.BLOCKING` | refuses a run |
| The labels themselves | `ticket_format.FIELDS` | one table, one regex |
| Everything on this page about wording | nothing | convention |

The unattended queue additionally refuses a ticket that does not state its
boundaries, so an underspecified ticket is not silently handed to a worker.
