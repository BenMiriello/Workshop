# Ticket model and external trackers (2026-08-07)

- Status: proposed
- Supersedes: ADR 0001 (task text is one string), the "GitHub Issues sync stays out
  of scope" line in ADR 0001 and ADR 0003
- Depends on: entity-model-2026-07-18.md, ticket-maker-2026-07-18.md,
  blocked-and-escalate-flow-2026-07-18.md, clarifying-question-flow-2026-07-18.md

---

## 0. Audit: what the shipped system actually does

Measured against the live stores on 2026-08-07 (`**/.tasks.json`, 25 stores, 179
tickets):

| Fact | Count | Reading |
|---|---|---|
| Tickets whose first line ends in `…` | 32 (18%) | the title is a machine artifact, not a written title |
| Tickets with a body | 145 (81%) | title+description is already the real shape |
| Median first-line length | 63 chars, max exactly 80 | clamped by `TITLE_MAX`, i.e. truncation is routine |
| Tickets with `awaiting` (the block/question record) | **0** | the clarifying-question path has never fired |
| Tickets with `parent` | **0** | the sub-ticket tree has never been used |
| Tickets with `acceptance` | 5 (3%) | "what done looks like" is almost never machine-checkable |
| Tickets with `depends_on` | 5 (3%) | dependency edges are near-unused |
| Tickets with `touches` | 36 (20%) | the only execution field with real adoption |

Two conclusions:

1. **The ticket record is over-specified where nobody writes and under-specified
   where everybody does.** `text` carries a title the code has to guess at and
   mangles 18% of the time, while nine execution fields sit empty.
2. **"Scoping I can trust" is not a prompt problem.** The mechanism that would
   surface a bad scope (a worker raising `awaiting`) has zero recorded uses. It is
   either unreachable or nothing calls it. No amount of better ticket prose fixes
   that; the escalation path has to be built and proven.

ADR 0001 rejected separate `title`/`description` fields on the grounds that they
create two sources of truth and force every reconcile, export, and dedup path to
agree on a join rule. That reasoning inverted: the system today needs a *split*
rule instead of a join rule, and the split rule is lossy, has a documented
"stacked preview" bug it has been patched for twice, and mangles a fifth of all
titles. A join (`text = title + "\n\n" + body`) is one pure function and is
lossless. Every external tracker on earth also exposes title and description as
separate API fields, so the split has to exist at the boundary regardless. The
ADR is superseded.

---

## 1. The ticket

A ticket is what Linear, Jira, GitHub, Asana, Shortcut and Azure DevOps all agree
it is. Human fields first, execution metadata namespaced underneath.

```jsonc
{
  "id": "uuid4hex",
  "title": "Bind a scope to an external tracker",   // required, <= 120 chars, single line
  "body": "markdown",                                // description. free-form. may be empty
  "status": "draft|todo|doing|in_review|blocked|parked|done|cancelled",
  "assignee": null | "agent:<slug>" | "operator",
  "priority": 0,                                     // higher first
  "labels": ["infra", "sync"],                       // NEW: free strings, syncable
  "parent": null | "<task-id>",                      // depth capped at 2
  "depends_on": ["<task-id>"],
  "effort": "quick|standard|deep" | null,
  "source":  {"kind": "operator|chat|agent|import", "ref": "chat:<id>" | null},
  "scoped_by": "operator" | "agent:<slug>" | "import:<binding>",   // NEW: accountability
  "created": "iso", "updated": "iso",

  "exec": {                                          // NEW namespace, never synced out
    "touches": [], "latitude": null, "acceptance": null, "max_attempts": null,
    "attempts": 0, "claim_token": null, "lease_until": null, "last_heartbeat": null,
    "claim_pid": null, "job_ids": [], "evidence": [], "awaiting": null,
    "blocked_reason": null, "readiness": {"ok": true, "missing": []}
  },

  "sync": null | {                                   // NEW, present only when linked
    "binding": "<binding-id>", "provider": "github|jira|linear|...",
    "remote_id": "1234", "remote_key": "BM-17", "url": "https://...",
    "remote_updated": "iso", "last_synced": "iso",
    "shadow": {"title": "...", "body": "...", "status": "...", "labels": [], "assignee": null},
    "conflict": null | {"fields": ["body"], "mine": {...}, "theirs": {...}, "since": "iso"},
    "state": "linked|stale|conflict|unlinked"
  },
  "external_refs": []                                // frozen links from past bindings
}
```

### Decisions

**D1. `title` and `body` are stored fields.** `text` survives as a derived
property (`title + "\n\n" + body`) for TODO.md export, dedup, and every legacy
reader; `_normalize_text`'s title clamp and `_undo_stacked_preview` are deleted.
Migration splits every existing record on the first line and *rejoins* the
ellipsis spill: if line 1 ends in `…` and line 2 exists, drop the ellipsis and
merge line 2's leading fragment back into the body. Title max becomes 120 (Jira's
limit is 255, Linear's is soft, GitHub's is 256; 120 is a house rule for
readability, enforced as a warning at 80 and a hard stop at 120).

**D2. Execution metadata moves under `exec`, in the same record.** Not a second
store: one file, one lock, one atomic write. The namespace exists to draw the
sync boundary in one obvious place. Rule: **nothing under `exec` ever crosses to
an external tracker, and nothing outside it is workshop-private.**

**D3. `draft` becomes a real status.** It is Triage/Backlog in every other tool
and it is where a ticket sits until it passes the readiness check (§6). Full
status set stays eight and maps cleanly to every provider.

**D4. `labels` become a first-class field.** They are the selection mechanism for
imports and the only field every provider has in common besides title, body,
state and assignee.

---

## 2. Provider model

**One binding per scope.** A workshop scope (the root, or a project) binds to at
most one external tracker target: a GitHub repo, a Jira project, a Linear team.
Different projects may use different providers. There is no multi-provider merge,
no "aggregate my Jira and my Linear into one queue." That path is where every
sync product goes to die, and this is a one-operator system.

**One knob, three modes**, chosen when the binding is created:

| Mode | Who owns ticket content | Who owns status | Workshop writes to remote |
|---|---|---|---|
| `off` (default) | workshop | workshop | never |
| `import` | the tracker | the tracker | only a run-summary comment, and close-on-done if permitted |
| `sync` | the tracker for title/body/labels/assignee | workshop | yes, title/body/labels/status |

There is **no per-field mapping UI**. Field ownership in `sync` mode is a fixed
table in code:

| Field | Authority in `sync` mode | Why |
|---|---|---|
| title, body, labels, assignee | remote | humans edit these where they read them |
| status | workshop | workshop runs the state machine; the tracker only learns the result |
| priority | remote if the provider has a native priority, else workshop | Linear/Jira have one, GitHub does not |
| parent, depends_on | workshop | provider models differ too much to map faithfully |
| everything under `exec` | workshop, never sent | private |

`import` exists because the common real case is a tracker you do not control
(a work Jira, someone else's repo). `sync` is for a tracker the operator owns.

**Adapter surface.** A provider is 8 methods. Anything expressible in them is
supportable; anything that is not, is out of scope.

```python
class Tracker(Protocol):
    def whoami(self) -> Account: ...
    def list_since(self, cursor: str | None, filt: Filter) -> tuple[list[RemoteIssue], str]: ...
    def get(self, remote_id: str) -> RemoteIssue: ...
    def create(self, draft: RemoteDraft, idem: str) -> RemoteIssue: ...
    def update(self, remote_id: str, patch: dict, expect_version: str | None) -> RemoteIssue: ...
    def transition(self, remote_id: str, status: str) -> RemoteIssue: ...
    def comment(self, remote_id: str, markdown: str, idem: str) -> None: ...
    def labels(self) -> list[str]: ...
```

Ship GitHub first (the box is already authed as `BenMiriello` via `gh`, so it
needs no new credential and no new egress). Linear second (clean GraphQL, one
PAT). Jira third (most configuration surface: cloud id, project key, and a
transition map that must be read from the workflow, not assumed). Anything else
is a new adapter file and a row in the status map, no core changes.

**Transport is polling, not webhooks.** This box has no public inbound. Poll every
60s while a Work/Project view is open, every 10 min in the background, and on an
explicit Sync now. Egress note: `api.linear.app` and `*.atlassian.net` are
currently blocked by the environment's egress policy. GitHub works today through
`gh`. Linear and Jira are blocked until the operator opens those hosts, and this
plan does not pretend otherwise.

**Credentials** live in `secrets_store` under `tracker:<binding-id>`, never in the
binding record. Registration is in Settings (§3).

---

## 3. Registration and the settings surface

`Settings > Trackers` lists bindings. Creating one is a four-step form, and it is
the only place a provider is configured:

1. **Provider** — GitHub / Linear / Jira (select).
2. **Credential** — GitHub uses the existing `gh` auth and shows the resolved
   account with a Test button; Linear and Jira take a token that is written
   straight to `secrets_store` and never read back to the UI. Test button calls
   `whoami()` and shows the account, or the failure verbatim.
3. **Target** — repo / team / project key, fetched from the provider after the
   credential passes. Never free-text.
4. **Mode and selection** — `import` or `sync`; selection rule (below); and the
   one-time reconciliation choice if the scope already has tickets (§5).

**Selection rule** (what counts as workshop work in the tracker), one of:
- `label` (default) — a configurable label, default `workshop`.
- `query` — a raw provider query (GitHub search / JQL / Linear filter).
- `all` — everything in the target. Allowed, warned about, not the default.

Bindings show a health row: last poll, last error, count linked / pending triage /
conflicted / stale. A binding that has failed its last three polls shows `danger`
and appears on the Work page health strip.

---

## 4. Import is a triage pipeline, not a firehose

A poll never creates a runnable ticket. It creates **candidates**.

```
remote issue matches selection rule
  -> candidate row in the scope's Triage inbox   (state: pending)
  -> operator or triager agent decides
       accept  -> local ticket created, status=draft, sync.state=linked, scoped_by=import:<binding>
       ignore  -> tombstone by remote_id; never proposed again (reversible from a filter)
       snooze  -> re-propose after N days
  -> accepted ticket must still pass the readiness check (§6) to leave draft
```

Three properties this buys:

- Nothing external ever enters the run queue without a local decision.
- A poorly written remote issue is visibly a *draft with missing scope*, not a
  worker failure at 3am.
- Ignoring is durable. Re-polling cannot resurrect a rejected issue, which is the
  failure mode that makes people turn sync off.

A triager agent may auto-accept candidates that already pass the readiness check
*and* carry the selection label, if the operator enables that per binding. Default
off. Auto-accept still lands the ticket in `draft`, never in `todo`.

---

## 5. Source of truth, conflicts, and switching platforms

### Shadow-based three-way merge

Every linked ticket stores `sync.shadow`: the field values as of the last
successful sync. Each poll compares three versions (local, remote, shadow):

| local changed | remote changed | action |
|---|---|---|
| no | no | nothing |
| yes | no | push if workshop owns the field, else revert local to remote and log |
| no | yes | pull |
| yes | yes | authoritative side per the field table wins the write; the other side's value is stored in `sync.conflict` and the ticket shows a conflict banner with **Keep mine / Take theirs** |

No silent clobber, ever. Conflicts are rare by construction because the field
table gives each field exactly one writer; they happen mostly around a rename
race or an offline window.

### Idempotency

Every create carries an idempotency key (`workshop:<task-id>`), written into the
remote body footer as an HTML comment and, where the provider supports it, a
label. Before creating, the adapter searches for the key. A crashed create can
therefore never double-file, which is the single most damaging sync bug.

### Binding a scope that already has tickets

Asked once, at bind time, three options, no default:

1. **Adopt remote** — local tickets stay local and unlinked; only remote issues
   flow in. Use when the tracker is the team's system of record.
2. **Publish local** — every *open* local ticket is created in the tracker and
   linked. Closed and cancelled tickets stay local only (history does not
   migrate; it clutters the tracker and nobody reads it).
3. **Start clean** — bind, import nothing historical, link only tickets created
   from now on.

### Switching providers

Unbind, then bind. Unbinding does not delete or rewrite anything: each ticket's
`sync` record collapses into an entry in `external_refs` (provider, url, key,
unlinked-at) and renders as a read-only "was BM-17 in Linear" chip. The new
binding re-asks the same three questions. **Dual binding is not supported** and
the UI refuses it. If the operator wants their Jira tickets in Linear, that is
Linear's importer's job, not workshop's.

### The rule, stated once

> A ticket has exactly one authority for each field at any moment, determined by
> its binding mode and the fixed field table. Workshop is always the sole
> authority for execution state and run history. The tracker is never asked to
> store anything workshop-specific beyond an idempotency key.

---

## 6. Readiness: how a bad scope gets stopped

A ticket cannot leave `draft` for `todo` (and therefore cannot be claimed by a
worker) until it passes a **readiness check** computed on write and stored at
`exec.readiness`:

| Check | Rule |
|---|---|
| title | non-empty, single line, <= 120 chars, not ending in `…` |
| objective | body states a concrete outcome (an `Objective:` line, or a first paragraph the ticket-maker attests to) |
| acceptance | body has a "done looks like" statement, or `acceptance` is set |
| entry points | body names at least one file, command, or symbol, unless `effort=quick` |
| boundaries | `touches` is set, or `latitude` is explicitly `open` |
| dependencies | every `depends_on` id resolves and is not itself cancelled |

Failing checks render as a checklist on the ticket with a Fix in chat button. The
run endpoint rejects a non-ready ticket with the list. This is the concrete answer
to "pushed through under bad scope": there is one gate, it is machine-checkable,
and it is the same gate for a chat-filed ticket, an agent-filed ticket, and an
imported issue.

`scoped_by` records who wrote the scope. When a run fails, the ticket's history
shows the scope, its readiness result, and who produced it, so a systematic bad
scoper is visible instead of anecdotal.

---

## 7. Questions: the path that has never fired

Zero tickets have ever carried an `awaiting` record. The schema is fine; the
plumbing is missing. Required to make scoping trustworthy:

**Before filing (chat).** The ticket-maker's Phase 1 (already designed in
`ticket-maker-2026-07-18.md`) asks clarifying questions in the chat thread before
any card is emitted. Non-negotiable rule: if a request has more than one plausible
decomposition, the agent asks rather than picking.

**During a run.** A worker that hits ambiguity writes `exec.awaiting` and the
ticket goes `blocked`. That record must land in exactly two places at once:
1. the **Questions queue** on the Work page, with a reply box, and
2. a message in the **originating chat** (`source.ref = chat:<id>`), so the
   question reaches the operator where the request came from.

Answering in either place unblocks the ticket, writes the answer into the body as
a `## Clarification` section (so the next attempt sees it), and posts the resolution
to the other surface.

**Unanswered questions decay loudly.** A question unanswered for 24h appears in
the daily digest and the binding health strip. A blocked ticket is never silent.

**Proof it works, not a promise.** Before this is called done: a test that drives a
worker into `awaiting`, asserts the question appears in both surfaces, answers it
from the chat, and asserts the ticket returns to `todo` with the clarification in
the body. Until that test exists and passes, the escalation path is unbuilt, which
is exactly the state it is in today.

---

## 8. Nothing gets lost: invariants and the conformance checker

The audit the operator asked for is not a document, it is a program that fails.
`scripts/ticket_audit.py --scope all` checks these and exits non-zero on any
violation. It runs in the reviewer gate, in a daily sweep, and renders as a health
strip on the Work page.

| # | Invariant |
|---|---|
| I1 | Every ticket's status is in the eight-state set, and every state has at least one defined exit transition |
| I2 | Every non-terminal ticket has an owner: an assignee, or a seat in the triage inbox, or a seat in the questions queue. No unowned open work |
| I3 | `doing` implies a live lease and a live pid, or the reclaim sweep must have already fired |
| I4 | `blocked` implies `exec.awaiting` with a question and a `raised_by` |
| I5 | Every open question appears in the questions queue **and** its originating chat |
| I6 | Every chat-created ticket carries `source.ref=chat:<id>` |
| I7 | Every linked ticket has a shadow and a `last_synced` within two poll intervals, else `sync.state=stale` |
| I8 | No ticket is ever deleted. Cancel is a status; unlink is a link state; ignore is a tombstone |
| I9 | Dwell-time bounds: `draft` > 14d, `todo` > 30d, `in_review` > 3d, `blocked` unanswered > 2d, `doing` > lease TTL all raise a staleness item |
| I10 | Every `depends_on` and `parent` id resolves; no cycles; depth <= 2 |

Plus a transition-table test: the state machine is declared as data, and a property
test asserts every reachable state has a path to a terminal state and that no
transition is performed by more than one component. **One writer per transition**
is the design rule that makes responsibility legible:

| Transition | Sole owner |
|---|---|
| `draft -> todo` | readiness check (on operator or agent edit) |
| `todo -> doing` | the claim/lease code in `services/tasks.py` |
| `doing -> in_review` | the worker, at end of run |
| `in_review -> done` | the reviewer gate only |
| `* -> blocked` | any worker, by writing `awaiting` |
| `blocked -> todo` | an answered question |
| `doing -> parked` | attempt-cap reclaim |
| `* -> cancelled` | the operator only |
| any remote-driven change | the sync engine, never a worker |

---

## 9. Deliberately not built

Per-field mapping UI. Multi-provider merge. Webhook receiver. Custom-field
mapping. Sprints, epics, story points, boards. Full comment mirroring (one
run-summary comment per run, that is all). Attachment sync. Migrating closed
history between providers. Conflict CRDTs. GitHub Projects v2. Anything that
requires the operator to reason about a sync matrix.

---

## 10. Code layout and phasing

```
backend/services/tasks.py            title/body split, exec namespace, draft status, readiness
backend/services/ticket_readiness.py the six checks, one pure function
backend/services/trackers/__init__.py  Tracker protocol, registry, field table, status maps
backend/services/trackers/github.py    adapter (uses existing gh auth)
backend/services/trackers/linear.py    adapter
backend/services/trackers/jira.py      adapter
backend/services/tracker_bindings.py   binding records + credential handles
backend/services/tracker_sync.py       poll loop, shadow merge, idempotent create, conflicts
backend/services/triage_inbox.py       candidates, accept/ignore/snooze, tombstones
backend/routes/trackers.py             CRUD + test + sync-now + triage actions (wire in main.py)
frontend/src/pages/settings/Trackers.tsx   binding list + 4-step form + health
frontend/src/pages/Work.tsx                triage inbox, questions queue, health strip
frontend/src/pages/TicketDetail.tsx        title/body fields, readiness checklist, sync banner
scripts/ticket_audit.py                    the invariant checker
```

Phases, each independently shippable and each with its own proof:

1. **Ticket shape.** title/body fields, `exec` namespace, `labels`, `draft`,
   migration of all 25 stores, `text` as derived. Proof: migration is idempotent,
   all 32 mangled titles are repaired, TODO.md export is byte-identical modulo the
   repairs, existing tests pass.
2. **Readiness + accountability.** the checks, `scoped_by`, run endpoint refuses a
   non-ready ticket. Proof: a non-ready ticket cannot be claimed, in a test.
3. **Questions.** wire `awaiting` end to end, both surfaces, decay into the digest.
   Proof: the round-trip test in §7. **This is the highest-value phase and should
   arguably go first** — it is the one the audit says has never worked.
4. **Invariant checker.** `ticket_audit.py`, reviewer-gate integration, health strip.
5. **GitHub binding.** adapter, bindings, triage inbox, `import` mode only.
6. **Two-way sync.** shadow merge, conflicts, `sync` mode, publish-local at bind.
7. **Linear, then Jira** — blocked on egress being opened.

Phases 1 to 4 are entirely local, need no network, and deliver most of the trust
the operator asked for. Phases 5 to 7 are the integration.

---

## 11. Open questions for the operator

1. **Order.** Recommendation: 3, 1, 2, 4, then 5. Questions-plumbing first because
   an unasked question is the failure mode that actually burns runs; ticket shape
   second because it is the migration and is best done while the store is small.
2. **GitHub target granularity.** One binding per project repo is the obvious
   read, but the workshop root scope has no repo of its own. Does root-scope work
   go to a dedicated `benmiriello/workshop-tickets` repo, or does the root scope
   simply stay `off`?
3. **Linear vs Jira priority.** Linear is a much cleaner adapter and a better fit
   for one person; Jira is more work and mostly matters if there is a work Jira in
   play, which the Veritus separation rule would exclude anyway. Recommendation:
   Linear second, Jira only on demand.
4. **Egress.** `api.linear.app` and `*.atlassian.net` are blocked from this box
   today. GitHub via `gh` is not. Confirm before phases 6 and 7 are scheduled.
