# Ticket lifecycle, approval, and external integration (2026-08-10)

- Status: proposed, partially shipped
- Builds on: `ticket-model-and-external-trackers-2026-08-07.md` (the ticket shape,
  the provider adapter surface, the credential model)
- Scope note: this document records the WHOLE arc, including phases that are
  deliberately not being built yet. Section 7 says what is in scope now.

---

## 1. The one-sentence model

> The system drafts, suggests, scopes and asks. The operator approves. Only then
> does anything run, and only then does a ticket reach an external tracker.

Every decision below falls out of that sentence. It is a *managed* system by
default: more autonomy is a later configuration, not a redesign (§8).

---

## 2. Approval is the spine (shipped)

`draft` is the mechanism. A draft is a suggestion; a `todo` is agreed work.

| Who files | Starts as | Why |
|---|---|---|
| Assistant, from a chat | `draft` | it wrote the spec; the operator has not agreed |
| Assistant, via a task-patch block | `draft` | same |
| Operator, "file and run" | `todo` | asking for the work outright IS the approval |
| Operator, Work UI / backlog | `todo` | they typed it |
| Import from a tracker | `draft` | nobody here has looked at it yet |

Two doors had to be closed, not one. `next_ready` only ever picks `todo`, so the
sweep already ignored drafts — but `launch_task_run` did not check status, so the
"Run now" button would have executed a suggestion. A gate that holds for
autonomous work and quietly does not hold for a button is not a gate. Both now
refuse.

**Approving does not require a well-specified ticket.** The readiness check
(`services/ticket_readiness`) reports what the ticket fails to answer, and the
approval records it, but the operator is the authority. Refusing someone's own
approval turns a checklist into an obstacle, and obstacles get routed around.
What the system owes them is the list, not a veto.

**Approval is also the publish gate** (§5). A ticket becomes real work and reaches
the external tracker at the same moment, for the same reason. This is why no
"should this go to Jira?" option is needed.

---

## 3. Jira and ADF: the earlier answer was wrong

The previous document concluded Jira should be import-only because markdown↔ADF
is lossy. Pushed on this, and the pushback was right. The honest position:

**Lossiness is a property of the converter, not of the formats.** Converters that
drop unknown nodes are lossy — `adf-to-md` discards mentions, panels and similar
outright, and Atlassian's own MCP server has an open bug where editing through a
conversion silently destroys all rich content. Converters that PRESERVE unknown
nodes round-trip: `marklas` keeps ADF-only constructs (panels, mentions, colored
text) as HTML elements carrying `adf` attributes, so the structure survives a
round trip.

But the stronger point is that **our write surface barely touches the description
at all**:

| Operation | Direction | ADF risk |
|---|---|---|
| Create a remote issue from a local ticket | md → ADF | **none.** There was no ADF to lose; we are the origin |
| Read a remote issue | ADF → md | **none**, if the raw ADF is kept in `sync.shadow` alongside the rendered markdown |
| Status, labels, assignee, comments | either | **none.** Not rich text |
| Rewrite an existing remote description | md → ADF | the only lossy operation |

So Jira gets full two-way sync, with one rule: **never regenerate a description
we did not write.** Concretely:

1. Keep the raw ADF in `sync.shadow.body_raw`. Nothing is ever lost locally.
2. Render ADF → markdown for display and for the worker's context.
3. On a local body edit of a remote-origin ticket, splice: convert the edited
   markdown back to ADF and re-insert the preserved opaque nodes. If a node
   cannot be placed with confidence, refuse the write and offer "edit in Jira"
   rather than silently dropping a panel.
4. A ticket we created is ours; its description is plain converted markdown and
   rewriting it is unconditionally safe.

Rule 3's refusal case is expected to be rare and is honest when it happens, which
is the whole difference from the failure mode in Atlassian's own tooling.

---

## 4. What "integrated" means for a ticket

A ticket is either **local** (no `sync` record) or **linked** (has one). Linked
tickets render the provider's icon; local ones do not. That is the whole
user-facing distinction, and it is the one the operator described.

While linked:

- The **tracker owns the content** — title, body, labels, assignee.
- **Workshop owns execution** — status transitions driven by runs, and everything
  under `exec` (touches, latitude, attempts, lease, awaiting). None of it is ever
  sent anywhere.

**Writes are write-through, not write-later.** A local edit to a tracker-owned
field goes to the remote first and the local copy is updated from the response.
If the remote write fails, the edit is rejected with the error. This is the
operator's instinct ("don't update ourselves till we update that system") made
precise, and it means there is never a local state the remote has not seen. Local
divergence can then only come from three places: changes made remotely between
polls (normal, handled by the shadow merge), a period when the integration was
off (§6), and nothing else.

**Not every ticket is in the tracker.** A draft never is. A local ticket the
operator never approved never is. That is by design, not a gap.

---

## 5. One integration at a time

**One provider connection for the whole system.** Not one per project. A scope may
point at its own target *within* that provider (project:birds → one repo,
workshop root → another), but there is one provider, one credential, one set of
semantics to hold in your head. Multi-provider aggregation is the feature that
sinks every sync product and it is not coming.

The only mode knob:

| Mode | Meaning | Use when |
|---|---|---|
| `import` | remote → local only; we never write anything but a run-summary comment | the tracker belongs to someone else |
| `sync` | two-way, per the ownership split in §4 | the tracker is yours |

Everything else that looked like an option collapsed into an existing decision:

- *"Which tickets get published?"* → the ones that get approved. Approval is the
  publish gate. No option.
- *"Should drafts publish?"* → no. A draft is not work yet. No option.
- *"Should we delete remote issues on disconnect?"* → **never.** Deleting someone's
  issues because you turned off an integration is indefensible. Not an option.

---

## 6. Connect, disconnect, reconnect

### Connecting

Remote issues **always** arrive as triage candidates, never as auto-adopted work
(they land as `draft` even once accepted). The only question asked at connect
time, and only when it applies:

> You have N open local tickets. Publish them to `<target>`, or leave them local?

One question, two answers, asked once. "Leave them local" is not a dead end:
publishing an individual ticket later is a per-ticket action.

### Disconnecting

Remote issues are untouched, always. The single question:

> Keep the M tickets that came from `<target>` as local tickets, or drop them?

Default is keep — never lose data as a side effect of a settings change. "Drop"
sets them `cancelled` with a tombstone; it does not delete. Invariant: **no ticket
is ever deleted, by any path.**

Either way, every ticket's `sync` record collapses into an `external_refs` entry
(provider, url, key, unlinked-at) and renders as a read-only "was BM-17 in Jira"
chip. Provenance survives disconnection permanently.

### Reconnecting after both sides moved

This is the hard one, and the answer is that **most of it is not hard**, because
a disconnected period is just a very long poll interval.

**Tier 1 — deterministic, no LLM.** Anything that was ever linked matches exactly:
every ticket we published carries `workshop:<task-id>` in the remote body footer,
and every ticket we imported kept `external_refs[].remote_id`. Re-linking is a
key lookup.

**Tier 2 — three-way merge, no LLM.** For a re-linked pair, both sides may have
changed. That is the same shadow-based three-way compare the steady-state sync
already does: the field table decides who wins, and the loser's value surfaces as
a conflict banner with Keep mine / Take theirs. The disconnected window needs no
special rule.

**Tier 3 — the genuine residue.** A ticket written locally while disconnected and
an issue written remotely while disconnected that describe the same work. There is
no shared key and no deterministic answer. **Reuse `task_dedup`** — it already
does canonical hash, then embedding cosine, then a cheap LLM same-work
confirmation above 0.90, and it is the same question ("are these two the same
work?") the system already answers when filing from a chat. Cost is bounded:
candidates × open local tickets, with the model firing only on near-misses.

**Nothing is auto-merged.** A suspected diverged duplicate lands in triage as
"this looks like <ticket>" with **Link** or **Keep separate**. One extra verb on a
surface that already exists.

So the answer to "does it require per-item LLM review?" is: no for everything that
was ever linked, and for the residue it reuses the deduper the system already has,
with a human confirming.

---

## 7. In scope now

Building: **tickets, integration, and user-triggered chat scanning → ticket
making → ticket execution.** Questions surface in the inbox.

| # | Work | State |
|---|---|---|
| 1 | Ticket shape: title/body, draft, labels, scoped_by | shipped `010a9a4` |
| 2 | Questions reach the next attempt and the originating chat | shipped `a8e576f` |
| 3 | Readiness check; broken tickets refused | shipped `ca515bc`, `2e04482` |
| 4 | Approval gate: draft → approve → run | shipped `9c33b1c` |
| 5 | Questions in the inbox as one list | **next** |
| 6 | User-triggered chat scan → suggested tickets, deduped, as drafts | **next** |
| 7 | Scope help: split suggestions, blockers, stages, on the draft | **next** |
| 8 | Tracker binding: settings, GitHub App + device flow, one connection | then |
| 9 | Triage inbox; connect/disconnect/reconnect per §6 | then |
| 10 | Two-way sync: write-through, shadow merge, conflicts, ADF handling | then |

## 8. Explicitly later, recorded so it is not re-litigated

- **Autonomy levels.** The managed model is level one. Later, a per-scope setting
  moves the approval line (auto-approve readiness-passing drafts, auto-approve in
  a named scope, full autonomy). The line moves; the machinery does not change,
  which is the point of putting approval in one place now.
- **Background scanning and autogen.** An agent that reads chats and notes on its
  own schedule to propose tickets. Needs: what it scans, what it pulls into
  context, how it avoids re-proposing what it already proposed, how it detects
  that a design discussion has a newer version, and how it stays current without
  re-reading everything. Currently user-triggered only (#6), which sidesteps all
  of it.
- **Topic and design tracking.** Detecting that two chats discuss the same design,
  tracking the latest version of a decision, reconciling superseded documents.
  Related to Tier 3 above and probably shares the deduper.
- **Distributed memory.** How scoped knowledge, notes and ticket context stay
  coherent across agents.
- **Background review agents** and their state/autonomy management.

---

## 9. Open, with a recommendation

1. **Does an imported ticket need operator approval before running?** Recommend
   **yes** — it arrives as `draft` like any other suggestion. Someone else's Jira
   issue is exactly the kind of thing that should not silently start a worker.
2. **Comment mirroring.** Recommend a single run-summary comment per run, and the
   question text when a ticket blocks in `import` mode (that is where the person
   who can answer lives). Not full comment sync.
3. **`scoped_by` for an imported ticket.** Recommend `import:<binding>` — the
   spec's author is the tracker, and a run that fails on a vague Jira description
   should point there.
