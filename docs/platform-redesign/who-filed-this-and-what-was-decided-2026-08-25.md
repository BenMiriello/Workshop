# Who filed this, and what was decided

2026-08-25. Written in answer to four questions the operator asked after
opening a ticket that said he had filed it, for a feature he had declined.

> (a) What even IS the owner field? Owner doesn't mean the person who created
> it. What does it mean? What are the meaningful definitions we can and should
> have?
>
> (b) If tickets show as filed by the operator when they were not, that's an
> issue.
>
> (c) Why would we make fields or flags that have no useful meaning to us, or do
> they have meaning and I'm misunderstanding?
>
> (d) [about the declined feature that was re-filed anyway] How do we fix this
> and what's the proper design?

Every section below names a mechanism and proposes a fix. A finding without one
is not worth writing down.

Short answers, for a reader who wants them before the evidence:

- **(a)** Two unrelated things are spelled `owner` at two layers. The one on a
  ticket is a read-time copy of `assignee` and means nothing on its own. Delete
  it. The other one, `owner_id` in the database, is the tenancy partition key
  and is load-bearing. See §1.
- **(b)** Correct, and it is three lines of code, not a mistake anybody made.
  Three separate call paths stamp `operator` when nobody said who. See §3.
- **(c)** You are not misunderstanding. `owner`, `scoped_by` and `created_by_id`
  are fields nothing decides anything with. The bar this repo already set is
  "do not add a control without a reader" (`agents_registry.py:132-140`, on
  deleting `depth` and `fanout`: *a control that lies*). These three are the
  same thing, kept. See §2.
- **(d)** There is no way to record "we decided not to build this". The decision
  lived in a paragraph in a markdown file, which no code reads. Give it a
  status. See §4.

---

## 1. The two owners

### The collision

The word `owner` names two different things one layer apart.

**Owner, the tenancy key.** `services/ownership.py:56` `current_owner()`
returns the pinned app-user id for the current call, `services/tenancy.py:138`
`org_for_owner()` turns that into an `org_id`, and `org_id` is the Postgres
row-level-security partition key. The policy in migration `966ddcaa35ae:36,52`
reads `USING (org_id = nullif(current_setting('app.current_org'),'')::uuid)`.
This one is real, it decides which rows a query can see at all, and it is not
in question here.

**Owner, the ticket field.** `owner` on a task record is never stored. It is
computed on read, in two places in the JSON store and one in Postgres:

- `backend/services/tasks.py:969` `rec["owner"] = rec["assignee"] or DEFAULT_OWNER`
- `backend/services/tasks.py:2170` (the same projection on update)
- `backend/db/repo.py:222` `m["assignee"] or "operator"`

No RLS policy anywhere references `ticket.owner` or `ticket.assignee`. The two
owners never touch. They just share a word, and the word is doing real work at
the lower layer and no work at all at the upper one.

### What the ticket-level owner actually means today

Nothing that `assignee` does not already mean. It is `assignee`, with `None`
replaced by the string `operator`.

Measured this session: 259 of 259 tickets have `assignee == owner == "operator"`.
Two fields, one value, on every record in the system.

This was already known. Migration `db/migrations/versions/d7b3e0c5f981...py:43`
says it in the migration that created the columns:

> assignee and owner are both the literal string 'operator' on all 112 tickets
> ... Two fields, one concept, no information.

`frontend/src/pages/TicketDetail.tsx:7` says the same thing. Both were written,
both were left in place, and the field is still there. That is the pattern the
operator has now corrected twice: naming a dead field in a comment is not
removing it, and a comment saying "this is meaningless" ships the meaningless
thing with documentation.

### What reads which

`assignee` has real readers that make real decisions:

| Reader | Decision |
|---|---|
| `tasks.next_ready` (`tasks.py:1916-1919`) | may this sweep claim this ticket |
| `tasks.claim` (`tasks.py:2455`) | does this claim succeed |
| `routes/agents.py:1004-1037` | which tickets appear on one agent's list |

`owner` has readers, but none of them makes a decision `assignee` could not:

| Reader | What it does | Would `assignee` do it |
|---|---|---|
| `tasks.group_by_owner` (`tasks.py:1659`) | groups the TODO.md export by heading | yes, and the docstring already says "Group tasks by assignee (the `owner` mirror). Name kept for compat." |
| `tasks._match_assignee` (`tasks.py:1687`) | the trailing `or task.get("owner") == owner` clause | yes; that clause only fires for a record whose `assignee` is unset, which `_assignee_from_legacy` (`:397`) has already repaired on read |
| `ProjectDetail.tsx:829,846,861` | groups the project task list under a per-owner heading | yes, with one change of form, below |

### Decision: delete the ticket-level `owner`

Keep `assignee`. Delete the projection. The repo's own rule
(`agents_registry.py:132-140`) is that a field no executor reads is a control
that lies, and `owner` is a control that lies about there being two answerable
parties when there is one field with one value.

Keeping it as a documented alias is the option not taken, and the reason is
question (a) itself: an alias whose name collides with the tenancy key is how
the operator ended up asking what the field means. Renaming is not enough. The
information content is zero either way, so the cheaper fix is removal.

### What breaks, and the migration

Three readers, one line of frontend shape change, no data migration. Nothing is
stored, so there is nothing to backfill or drop.

1. **`group_by_owner`** (`tasks.py:1659`). Change the key expression to
   `t.get("assignee") or DEFAULT_OWNER`, keep the function name (the TODO.md
   export headings are unchanged, because the values are unchanged).
2. **`_match_assignee`** (`tasks.py:1687`). Drop the
   `or task.get("owner") == owner` clause. It is unreachable for any record that
   went through `_normalize`, which is every record on every read path.
3. **`ProjectDetail.tsx:829,846,855,861`**. This is the only real change. The UI
   groups on a **bare agent slug** (`agents.find(a => a.slug === owner)`), while
   `assignee` carries the `agent:<slug>` form. So the frontend needs the same
   split `_match_assignee` does: strip a leading `agent:` before the slug
   lookup, and treat `operator` as itself. One helper in
   `frontend/src/lib/`, used at the three sites.
4. **API shape.** `owner` disappears from the task JSON. Anything typed
   `owner: string` in `frontend/src/pages/ProjectDetail.tsx:85` and the other
   task types moves to `assignee: string`.

The `owner_id` / `org_id` tenancy chain is untouched. After this, `owner` in
this codebase means exactly one thing: which tenant's rows these are.

---

## 2. What a ticket should record about people and actors

This is the direct answer to (a) and (c). The question is not "what fields could
we have", it is "what distinct questions do we actually ask of a ticket, and
does a field answer each one". Five questions, and the current record answers
two and a half of them.

The bar, stated once so every row below can be checked against it: **a field
gets to exist when a named reader makes a named decision with it.** That is the
existing rule from `agents_registry.py:132-140`, and it is what (c) is asking
for.

### The five

**1. Who is accountable for the outcome.**
*One sentence:* the party who decides whether this is worth doing and whether
what came back is acceptable.
*Field:* none, and none is needed. There is one operator. Every ticket has the
same answer, forever, which is why 259 of 259 records agree. **A field whose
value is a constant is not a field.** This is the honest answer to "what does
owner mean": it was trying to be this, and this does not need storing in a
single-operator system. Do not replace `owner` with a renamed version of itself.

**2. Who or what is currently working it.**
*One sentence:* the single delegate holding the ticket right now, `operator` or
`agent:<slug>`.
*Field:* `assignee`. **Reader:** `next_ready` / `claim` / `routes/agents.py`.
**Decision:** may this sweep pick this ticket up. This one is live and correct
and is the field to keep.

**3. Who or what created it.**
*One sentence:* the actor that ran the write that brought the ticket into
existence, as distinct from whoever wanted the work.
*Field:* **missing.** `source.kind` is the closest thing and it is a different
question (see 4). `scoped_by` was meant to be "who wrote this spec"
(`tasks.py:864-867` says exactly that) but it has **one** decision reader in the
whole tree, `routes/trackers.py:200`, which only tests
`scoped_by.startswith("import:")`. Everywhere else it is written and never read
and never rendered. Postgres has `created_by_id` (`db/models.py:461`), written
once by `db/backfill.py:297` and read by no query at all. So there are two
fields aimed at this question, and between them they answer it for imported
tickets only.
**Proposed reader and decision:** §5's `created` evidence entry, carrying
`actor`. **Decision it drives:** §4's decline check ("may this actor file this")
and the operator's own "who did this" question, which is currently unanswerable
from the store.

**4. Which conversation or run it came from.**
*One sentence:* the artifact a reader can open to see why this was asked for.
*Field:* `source = {kind, ref}`, and it is half-built. `kind` is populated
(agent 133, operator 71, chat 55) but **`ref` is null on 191 of 259 tickets**,
including 20 of the 55 chat-sourced ones, so for 36% of chat tickets the link
back to the conversation is simply gone. `routes/tasks_all.py:805` `_origin_for`
exists precisely to turn `source` into a link, and on those records it has
nothing to turn.
**Reader:** `_origin_for` -> the ticket detail page's origin link. **Decision:**
which conversation the operator opens when a ticket makes no sense. That is a
real decision and this field should stay, with `ref` made non-optional on the
paths that have one (§3).

**5. What was decided about it, and by whom.**
*One sentence:* the durable record that someone considered this and said yes,
no, or not yet.
*Field:* **missing**, and §4 is about building it. `status` carries `draft`
(proposed, not agreed) but has no negative counterpart, so "we decided not to"
is written as `cancelled` with prose in `blocked_reason`, a field named for a
different state.

### The minimal set, then

Keep `assignee`. Keep `source` and make `ref` real. Delete `owner`. Delete
`created_by_id` or give it the reader §5 defines. Fold `scoped_by` into the
actor record unless someone can name a second reader (`trackers.py:200` needs
only the import case, which `source.kind == "import"` already carries). Add
`declined`. That is it. Four live fields and one new status, each with a reader
named above.

Nothing here proposes a "created_by" string field parallel to `assignee`, and
that is deliberate: the thing that creates a ticket is an *event*, and an event
with a time and an actor belongs on a timeline, not in a scalar column that the
next writer overwrites. §5.

---

## 3. The filed-by defect

(b) is correct and it is worse than a one-line bug. Three separate paths stamp
`operator` when nobody said who.

### Correction to an earlier claim

An earlier pass in this session reported that the CLI's `--source` defaults to
`operator`. **It does not.** The line is:

```python
# scripts/project_tasks.py:262
    sp.add_argument("--source", default="agent")
```

and `git log -L262,263:scripts/project_tasks.py` shows it has read `"agent"`
since the argument was introduced (`b3ce3742`). The CLI is the one path that
gets this right. Recording the correction here rather than quietly dropping it,
because a design doc that repeats an unverified claim is how the next pass
inherits it.

### The three real defaulting sites

**1. The store's own default.**

```python
# backend/services/tasks.py:2000, inside add()
            "source": source or {"kind": "operator", "ref": None},
```

Any caller of `tasks.add()` that omits `source` files a ticket that says the
operator asked for it. `routes/agents.py:1014` is such a caller today.

**2. The read-time repair, twice.**

```python
# backend/services/tasks.py:870, inside _normalize()
    if not isinstance(src, dict):
        src = {"kind": "operator", "ref": None}
# backend/services/tasks.py:935
        "source": {"kind": str(src.get("kind") or "operator"), "ref": src.get("ref")},
```

A record with a missing or blank `kind` is *repaired into* claiming the operator
filed it. This is the worst of the three, because it manufactures an attribution
at read time on a record that never carried one, and it does so silently and
repeatedly.

**3. The API create route, hardcoded.**

```python
# backend/routes/tasks_all.py:604-610
        res = task_dedup.dedup_add(
            root,
            text=text,
            priority=int(body.priority or 0),
            labels=body.labels or [],
            source={"kind": "operator", "ref": "work-ui"},
            scoped_by="operator",
        )
```

`POST /tasks` attributes to the operator unconditionally. It was written for a
button in the work UI, where that is true. It is a public route and anything
that can reach the API can file a ticket signed with the operator's name. This
is the likeliest origin of ticket `031b626c`, whose `source.kind` is `operator`
though an agent filed it, but the store does not record enough to prove which of
the three lines produced it, and **that is itself the finding**: see §5.

A fourth, same bug, different field:

```python
# backend/services/tasks.py:839, inside _norm_comments()
            "author": str(c.get("author") or DEFAULT_OWNER),
```

An unattributed comment is rendered as the operator having said it.

By contrast, `routes/chat.py:1285` does it correctly and its comment explains
why, which is worth quoting because it is the standard the other paths should
meet:

> The operator asked for the work; the ASSISTANT wrote the spec. `source`
> records the first, `scoped_by` the second, and conflating them is what makes a
> bad spec untraceable: the field would say the operator wrote a paragraph a
> model actually wrote.

### The fix

**The default must be "unknown", never "operator".** This repo already settled
that argument in the neighbouring field. `--execution` unset is *unclassified*
and unclassified is ineligible for unattended dispatch, "fail-closed on purpose"
(`scripts/project_tasks.py:255-261`). `autonomy_override` treats a corrupt file
as engaged (`autonomy_override.py:10-11,56`). The same rule applies here, and
the safe direction is obvious: **claiming a human authorised something he did
not is the failure to avoid.**

Concretely:

1. `tasks.add()`: `source` becomes **required**. No default. A caller that does
   not say who filed the ticket does not get to file one.
2. `_normalize()`: a record with no parseable `source` normalizes to
   `{"kind": "unknown", "ref": None}`, and `unknown` joins the `source.kind`
   enum. It renders in the UI as "filed by: not recorded", which is true, rather
   than as the operator, which is not.
3. `POST /tasks`: `source` comes from the authenticated caller, not from a
   literal. Only a request carrying the operator's own session gets
   `kind: "operator"`. Everything else is `kind: "agent"` with `ref` set to the
   calling agent's slug or run id.
4. `_norm_comments`: unattributed comments get `author: "unknown"`.
5. The CLI keeps `--source agent` as its default, because an agent is what runs
   it, and additionally **`--source operator` from the CLI requires `--source-ref`**
   naming what the operator said, so an agent cannot sign the operator's name by
   typing four characters.

### How an agent identifies itself

It does not get to assert an identity; the process it runs in already has one.
`ownership.pin_operator()` is described in its own docstring as "THE ONE
SET-POINT FOR AUTOMATION" for the tenancy owner, and the actor identity should
follow the same shape: one process-scoped context variable, set by the
entrypoint that knows what it is (the sweep sets `agent:<slug>` plus the job id,
a chat turn sets `agent:chat` plus the thread id, the operator's own terminal
sets nothing and therefore reads as `unknown` rather than as `operator`),
consumed by `tasks.add()`. An identity a caller can pass as a string is an
identity a caller can lie about, and the point of this section is that the
system has been lying about it by accident already.

---

## 4. Declined as a state

### What happened

The operator declined the "list of limit rules" feature. The decline exists
nowhere in any store. It exists as English prose in two markdown files:

- `docs/platform-redesign/limiting-usage-2026-08-24.md:341`: "The operator has
  twice declined to request this, so it is last"
- `docs/platform-redesign/backlog-reconciliation-2026-08-24.md:134-136`

Ticket `031b626c` was filed for it anyway on 2026-08-24T03:59:13Z at priority
band `medium`. On 2026-08-25T01:15:45Z an audit re-filed it as `55c395b9` with a
warning banner in the body and `--priority 1` (the `low` band), and again at
01:17:20Z as `d37c3314`. Three tickets for one declined feature.

Nothing malfunctioned. No code anywhere can read an English sentence in a
markdown file, so no code could have stopped it. **A decision recorded only in
prose is not a decision the system has.**

### Why `cancelled` is not it

`STATUSES` (`tasks.py:147`) is `draft, todo, doing, in_review, blocked, parked,
done, cancelled`. There is no `declined`, `wontfix` or `rejected`.
`review_decision` (`tasks.py:2766`) handles the `cancel` verb by writing
`status="cancelled"` with the reason stuffed into `blocked_reason`, a field
named for the `blocked` state. 43 tickets are `cancelled` and 16 carry no reason
text at all, so on the current record "the operator decided against this",
"duplicate of another ticket" and "obsolete, the code moved" are the same value.

Note the asymmetry this leaves. `draft` already means *proposed, the operator
has not agreed yet* (`task_dedup.dedup_add` docstring: "A ticket the system
wrote for itself starts as `draft`: a suggestion the operator has not agreed to
yet"). The system can record a pending yes and has no way to record a no.

### The design

**Status.** Add `declined` to `STATUSES` and to `TERMINAL`. It means: *this was
considered and the answer was no.* It is a peer of `done`, not a variant of
`cancelled`. `cancelled` keeps its own meaning, which is "this ticket is void",
covering duplicates and obsolescence.

**The reason is required.** A `declined` ticket without a stated reason is
refused at the write. Not in `blocked_reason`: a new `decision` block on the
record, `{"verb": "declined", "reason": str, "actor": str, "ts": iso}`, written
through the same `update()` path. One field, four keys, and it is the durable
form of the paragraph that failed to be durable.

**Who may set it.** The operator, through `review_decision` with a new `decline`
verb, or through the ticket detail page. An **agent may propose** a decline the
way it proposes anything else: it may set `status="draft"` and attach a
`decision` block with `verb: "decline-proposed"`, which surfaces on the
operator's review queue and does nothing until he confirms. An agent may not
write `declined` itself, for the same reason it may not accept its own work: the
completion gate rule in `CLAUDE.md` is that the worker does not judge its own
completion, and declining is a judgement about what the operator wants.

**What `add` does when a new ticket matches a declined one.** It **refuses**,
with a message naming the declined ticket, its reason and its id. Not a warning,
because the actual sequence of events was an agent reading a warning banner (it
wrote one itself, into `55c395b9`'s body) and filing anyway. Not a draft,
because a draft is a queue the operator then has to clear, which turns "I said
no" into recurring work for him. Refusal is the only outcome that costs him
nothing.

The refusal is overridable, once, by the operator: `--reopen-declined <id>`
flips the declined ticket back to `todo` and records who reopened it. The
operator changing his mind is a normal event and must be one action, not a
workaround.

**Where the check lives.** In `tasks.add()`, under the store lock, immediately
beside the existing provenance check:

```python
# backend/services/tasks.py:1972-1976
        if provenance:  # idempotent re-add (work_generator / dedup key)
            for t in tasks:
                if t.get("provenance") == provenance and t.get("status") != "parked":
                    return t
```

That loop is already the "have we seen this before" gate. The declined check is
the same loop with a different verdict: same key, status `declined`, raise
instead of return.

**How matching works.** Two tiers, both already built.

- *Exact.* `provenance` defaults to `task_dedup.canonical_hash(text)`
  (`task_dedup.py:430`), a sha256 over lowercased, stopword-stripped, sorted
  tokens. Identical or near-identical wording collapses to the same key. This
  catches an agent re-filing the same ticket text, which is what `d37c3314` was
  relative to `55c395b9`.
- *Near.* `task_dedup.decide()` already embeds the new text against open tickets
  and returns `exact | merge | related | distinct`. Its candidate set is
  `_open_tasks(root)`, which excludes terminal statuses. Include `declined`
  tickets in the candidate set for this check only, and treat a `merge`-grade
  match against a declined ticket as a refusal.

### Where this fails, honestly

**The hard case is two differently-worded tickets for the same declined idea,
and this mechanism handles it poorly.**

The canonical hash is a bag of words. "Show a list of the limit rules that are
configured" and "Add a rules table to the usage settings page" share almost no
tokens after stopword removal, hash differently, and would score low on the
term-frequency vector too. The semantic tier is better and still not good: it
was measured at 17.5s cold and is explicitly disabled on the interactive path
for that reason (`routes/chat.py:1275-1283`), so the tier that would have the
best chance is the tier that does not run where it matters.

So state the guarantee accurately: **this stops the same ticket being filed
twice, and it does not stop the same idea being filed twice in different
words.** `031b626c` and `55c395b9` would both have been caught, because the
second was a deliberate re-file of the first's text. A fresh ticket written from
scratch three weeks later by an agent that has never read the decline would not
be caught.

What closes that gap is not better matching. It is that the decline lives on the
ticket rather than in a doc, so an agent asked to "audit the limits plans" and
reading the ticket list sees `declined: the operator has twice declined to
request this` on a ticket it can actually read, instead of a paragraph on line
341 of a markdown file it may never open. Matching is the backstop. Visibility
is the fix.

**Second known gap:** the decline is per scope, because the ticket store is per
scope. A feature declined in one scope can be filed in another. Not worth
solving now; noted so it is not discovered as a surprise.

### In the UI

- `declined` is a fifth terminal state with its own status kind, distinct from
  `cancelled`, in `frontend/src/lib/jobs.ts`'s map and the ticket status pill.
- The ticket detail page renders the `decision` block as a sentence at the top:
  "Declined by the operator on 24 Aug: the operator has twice declined to
  request this." Per `docs/ticket-writing.md`, name the state in words, not only
  as a pill.
- The default ticket list filter hides `declined` alongside `done`. A "declined"
  filter chip shows them. The list is a queue and a decline is not queue work.
- When `add` refuses, the caller gets the declined ticket's id and reason in the
  error, so a chat can tell the operator "I did not file this, you declined it
  on 24 Aug" rather than silently doing nothing.

---

## 5. The actor record

### The question the store cannot answer

Who filed `031b626c`? The answer was reconstructed this session from a chat
transcript: a `general-purpose` subagent, spawned at 2026-08-25T01:01:06Z by
interactive session `308ba3fd-0030-4096-b850-f4469debafe0`, model opus,
description "Audit and finalize the limits plans". `.runtime/jobs.jsonl` has
zero records in that window, and `.runtime/triager/` does not exist because the
triager has never run. There is no `task_event`, `TaskHistory` or `task_audit`
table anywhere in the tree.

So: who changed what, when, and as what actor is **unrecoverable from the
record**. It was recovered from a chat log that happened to still exist. Three
weeks from now it would not have been recoverable at all.

### The tension, stated and resolved

`CLAUDE.md` forbids audit journals: "This system has one user (the operator). Do
not add allowlists, audit journals, mention-required gating, multi-user
permissions..." That rule is not negotiable and this section must not violate
it.

And the operator is asking who filed a ticket.

**The resolution.** The forbidden thing is an audit journal for *security*: a
record kept to establish, after the fact, whether a party was permitted to do
something, in a system with parties who might not be. That has no meaning here.
There is one human and everything else is his own automation, so there is no
principal to hold accountable and nothing to prove to anyone.

What is being asked for is *provenance for debugging a self-modifying system*.
This system writes its own tickets, and when a ticket appears that the operator
did not want, "which of my own processes wrote this" is a debugging question
about a program's behaviour, exactly like a stack trace. The distinction is not
the data, it is the purpose, and the purpose determines the size: a security
journal must be complete, tamper-evident, retained and reviewable, which is the
cruft that is forbidden. A provenance trail needs only to answer "what wrote
this" and may be lossy, local, and as small as the question.

The test to apply to anything proposed here: **would this exist if there were
two users?** If yes, it is provenance. If it only makes sense because someone
might be untrusted, it is an audit journal and it does not get built.

### The mechanism, kept as small as it can be

**Do not build an event table.** The ticket already has an append-only timeline:
`evidence`, `[{kind, ref, ts}]` (`tasks.py:58`), appended by every `update()`
call that passes one (`tasks.py:2264-2267`), rendered on the ticket detail page
today. `review_decision` already writes to it. It is the right structure, in the
right place, already built and already displayed.

It has exactly one thing missing. From the store's own comment
(`tasks.py:918-923`):

> Evidence is what the SYSTEM recorded about a run (a receipt, a gate verdict, a
> dedup merge) and **carries no author**; a comment is something a person or an
> agent SAID about the ticket, and is attributed.

So the whole change is:

1. **Add `actor` to the evidence entry shape.** `{kind, ref, ts, actor}`, where
   `actor` is the process identity from §3 (`operator`, `agent:<slug>`,
   `agent:chat`, or `unknown`). Set in the same `ev.setdefault` block at
   `tasks.py:2264`. Existing entries have no `actor` and read as `unknown`,
   which is true. No migration.
2. **Write one evidence entry when a ticket is created.** `tasks.add()` appends
   `{kind: "created", ref: <source.kind>:<source.ref>, actor: <actor>}`. This
   is the record that does not currently exist and whose absence is the entire
   §3 investigation.
3. **Write one on a status change.** `update()` already receives the status;
   when it changes, append `{kind: "status", ref: "todo->declined", actor}`.

Three verbs: `created`, `status`, and the existing `review`. Nothing else. No
new table, no new file, no retention policy, no viewer, no query interface. The
ticket detail page's existing evidence list gets an actor name on each line.

That is the smallest thing that would have answered the operator's question
directly from the ticket, and it is small because the question is small.

**Explicitly not proposed:** a `task_event` table, a system-wide activity feed,
a per-actor action log, anything in `.runtime/*.jsonl`, and any retention or
export mechanism. Each of those fails the two-user test above.

`created_by_id` (`db/models.py:461`) is written once by `db/backfill.py:297` and
read by no query. Either it becomes the Postgres column backing the `created`
evidence entry, or it is dropped. It does not stay as it is.

---

## 6. The policy gap

### What actually happened

A chat spawned a `general-purpose` subagent with a prompt that said "Audit, then
produce a document AND fix or file tickets as needed". The subagent audited,
found a ticket for a declined feature, and filed two more tickets about it,
typing `--priority 1` by hand.

The subagent did what it was told. The instruction to file came from a chat
prompt, not from any policy, and there was no policy. Nothing in the system had
an opinion about whether a subagent should be able to file tickets, so nothing
stopped it.

### The rule that would have stopped it

**A ticket filed by an agent enters as `draft`, not `todo`.**

Not "an agent may not file tickets", which is wrong, because an agent noticing
work is the main way work gets noticed here. The problem is not that the
subagent filed something. It is that what it filed landed in the operator's live
queue at a real priority, indistinguishable from work he asked for.

`draft` already means "a suggestion the operator has not agreed to yet" and
`task_dedup.dedup_add` already takes a `status` parameter for exactly this. The
machinery exists. It is simply not applied to the CLI path, which is the path
agents use.

Note the deliberate exception, and keep it: `routes/chat.py:1285` files as
`todo`, and its comment explains why. The operator clicked a button having read
the draft, and that click is the approval. An approval that already happened
must not be asked for twice.

### Where the rule must live

**In `tasks.add()`. Not in a prompt.**

This repo has already run this experiment. On 2026-08-19 a guard against
backgrounded chat subagents was written partly as prompt text
(`chat_context.DELEGATION_RULE`) and partly as a check that only caught the
explicit case. It failed: the default-background call sailed through, and chat
turns kept dying. On 2026-08-20 it was fixed by making
`services/subagent_guard.py` a `PreToolUse` hook wired through
`build_streaming._build_cmd`, allowing the tool **only** when the safe parameter
is explicitly present. From `shared/knowledge/chat-subagents-must-be-foreground-the-guard-fail-1a02048d4d5.md`:

> The first guard (2026-08-19) denied only EXPLICIT `run_in_background: true`,
> so the default-background (omitted) call sailed through. Fixed 2026-08-20:
> `services/subagent_guard.py` now allows Agent/Task ONLY when
> `run_in_background` is explicitly false (fail closed).

The lesson is the one that applies here exactly: a rule a model is asked to
follow is followed most of the time, and a rule the code enforces is followed
every time. A prompt rule also does not reach a subagent, which is what this
incident was: the subagent inherited a task prompt, not the chat's system
prompt, so a rule written into `chat_context` would not have been in its context
at all.

`tasks.add()` is the correct enforcement point because it is the single
chokepoint. The CLI, `POST /tasks`, `dedup_add`, the triager, `triage_inbox` and
`routes/agents.py` all funnel through it. A guard there cannot be bypassed by
picking a different door, and the actor identity from §3 is already in scope at
that point.

**Recommendation:** in `tasks.add()`, when the resolved actor is an agent and
the caller did not pass an explicit operator-approval marker, force
`status="draft"`. Combined with §4's declined check in the same function, the
two rules that would have prevented this incident live four lines apart in the
one function every filing path calls.

A hook is not needed here and should not be added. The `subagent_guard`
precedent is about *where enforcement has to live relative to the model*, not
about hooks specifically; `tasks.add()` is already below the model in the same
way a `PreToolUse` hook is.

---

## 7. Tickets filed from this doc

All filed `--source agent --execution code`. Dependencies are set on the
records, not only named here.

| Ticket | Title | Band | Section | Depends on |
|---|---|---|---|---|
| `dab8a00fe984404bbcbbfbecd9344caf` | A ticket filed by an agent no longer says the operator filed it | high | 3 | |
| `9dbb1cf248cb40a1bb9bad6ccfb5f19a` | Record the actor on every ticket evidence entry, and write one when a ticket is created | high | 5 | `dab8a00f` |
| `c58809c27b5c43389966813e9fc3259d` | Add a declined status so a decision not to build something outlives the doc that recorded it | medium | 4 | |
| `1ae4baa0574a40649d490e751e925638` | Filing a ticket that matches a declined one is refused, naming the declined ticket | medium | 4 | `c58809c2` |
| `7df0ab9640214f1c817ef1fc1d213220` | An agent-filed ticket lands as draft, not in the operator's live queue | medium | 6 | `dab8a00f` |
| `4893c343374847439672c30834820a63` | Delete the projected owner field from a ticket and group by assignee instead | medium | 1 | |
| `3a95bdc959014435be01517b85b08f1f` | A ticket that cites a doc path links to it | low | 2 | |

Two of these, `dab8a00f` and `7df0ab96`, land four lines apart in
`tasks.add()`, together with `1ae4baa0`. That is on purpose: the one function
every filing path calls is where the rules that would have prevented this
incident belong.
