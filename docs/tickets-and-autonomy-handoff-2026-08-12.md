# Handoff: the tickets surface and the automation loop (2026-08-12)

**Read this, then read [`platform-redesign/autonomy-turn-on-2026-08-12.md`](platform-redesign/autonomy-turn-on-2026-08-12.md), which is the plan and the authority. This file is only the state of play and the traps.**

The work has two halves that arrived together and are easy to confuse:

1. **The tickets SURFACE** (list page, ticket page, run page) was rebuilt over
   several turns of direct operator review. That is largely done.
2. **The automation LOOP** behind it (what Run does, roles, autonomy, shared
   knowledge) was audited and planned. Almost none of that is built, and the
   plan says so phase by phase.

---

## 0. What to read, in order, and what to skip

There are **51 docs in `docs/platform-redesign/` and 19 ADRs**. Do not read
them all; most are superseded or were folded into the four below. This ordering
is the shortest path to being able to act.

**Read these four, in this order.** Together they are about 90 minutes and they
are enough.

| # | doc | what it gives you |
|---|---|---|
| 1 | [`platform-redesign/autonomy-turn-on-2026-08-12.md`](platform-redesign/autonomy-turn-on-2026-08-12.md) | **The plan, and the authority for this work.** What is real, what is decoration, the phase order, the evidence table with sources. Written against the live tree, and carries its own self-critique in §8. |
| 1b | [`platform-redesign/autonomy-implementation-2026-08-13.md`](platform-redesign/autonomy-implementation-2026-08-13.md) | **The HOW for phases 0 to 4**, and what you actually build from: the two schema fields, file-by-file changes, endpoint shapes, UI primitives per surface, and the test plan. Read it after the plan's §1 and §3.1, which are what make its phase 1 make sense. |
| 2 | [`platform-redesign/entity-model-2026-07-18.md`](platform-redesign/entity-model-2026-07-18.md) | **The spine.** Chat / Ticket / Run / Agent: one word per concept, states, fields, cross-links. Ratified by ADR 0007. Its §9.2 tells you which 2026-07-16 docs are superseded, folded in, or preserved. When it and an older report disagree, it wins. |
| 3 | [`platform-redesign/ticket-system-consolidation-2026-08-12.md`](platform-redesign/ticket-system-consolidation-2026-08-12.md) | The same-day ticket-surface consolidation this session's UI work sits on top of. Read it before changing the ticket list or page again. |
| 4 | [`platform-redesign/system-audit-2026-07-18.md`](platform-redesign/system-audit-2026-07-18.md) | The blunt version of "the execution half is real, the orchestration half routes zero agents". Still true, and it is where the four-serializers finding comes from. |

**Read when you touch that specific thing, and not before:**

| topic | doc |
|---|---|
| roles, the blackboard/stigmergy argument, why planner/watcher/critic were rejected | `platform-redesign/primitive-roles-2026-07-18.md` |
| autonomy enum, the global override, restore-on-clear | `platform-redesign/autonomy-model-2026-07-18.md` + ADR 0008 |
| latitude vs scopedness vs depth, and the auto-resolve ladder | `platform-redesign/latitude-model-2026-07-18.md` (note: `services/latitude.py:20-41` RETRACTS its "enforced floor" section) |
| how a ticket is captured, approved, published | `platform-redesign/ticket-lifecycle-and-integration-2026-08-10.md` |
| the block/escalate and clarifying-question flows | `platform-redesign/blocked-and-escalate-flow-2026-07-18.md`, `clarifying-question-flow-2026-07-18.md` |
| where a note vs a doc vs scratch goes | `shared/knowledge/notes-and-docs-policy.md` and the `notes-and-docs` skill |
| the knowledge stores as they actually are | `memory-stores-audit-2026-08-12.md` (predates the auto-recall removal, so its "PULL, top 3" column is now wrong) |
| execution isolation, and why nothing here is security | `masini/security-audit-2026-07-18-agents.md` (A1-A6) + `platform-redesign/agent-governance-addendum-2026-08-04.md:47-65` |
| what dev mode gates, and why `workshop` is not a build target | `platform-redesign/dev-mode-and-development-scope-2026-08-09.md` + ADR 0014 |
| the standing "do not enable autonomy yet" sequence | `platform-redesign/continuation-2026-07-31.md:211-321` |

**Skip unless you are doing archaeology:** everything dated 2026-07-15 or
earlier, the three `session-handoff-*` files, `proposal.md`,
`implementation-plan.md`, `coherent-rebuild-2026-07-14.md`, and the
2026-07-16 set that `entity-model` §9.2 marks SUPERSEDED (`_brief-agent-protocols`,
`concept-schema`, `synthesis-agent-protocols`). `docs/workshop-v1/` and
`docs/agent-chat-redesign/` are historical and carry banners saying so.

**The research this session commissioned is summarised, with sources, in the
plan's §9 evidence table.** It was gathered by four subagents (a live-system
audit, a design-doc digest, web research on multi-agent architectures, and a
shared-knowledge audit) whose full reports exist only in this session's
transcript. The load-bearing findings and every citation are in the plan; the
transcript is searchable with `mcp__workshop__search kinds=chat` if you need
the raw detail.

**UI work has its own law:** [`frontend/CLAUDE.md`](../frontend/CLAUDE.md), and
it is binding. The sections added this session are "A record page is not a list
page" and the chat-ordering rules.

---

## 1. What is true right now

### Shipped and live

| | commit |
|---|---|
| The shell can no longer scroll its own toolbar away (`.ws-root` box-sizing + `100dvh` clip) | `2d2f72a` |
| Ticket list is rows, not a table; no sideways scroll at any width | `2d2f72a` |
| Status is a plain word in the list meta line, not a chip | `0750a3f` |
| Detail pages get `PageHeader` `back`/`meta`/`variant="entity"`, `DetailLayout`, `Props`/`Prop`, `Clamp` (kit 0.5.2) | `557a0f2` |
| Section spacing: `<section>` + `SectionLabel` + `Card compact`, everywhere | `958d488` |
| Status commits on change from a header menu; removed from the edit form | `4290c0b` |
| A run records the chat that launched it (`chat_id`), and the run's own chat outranks its ticket's | `4290c0b`, checkpoint `0427b6e` |
| **A captured preference now reaches a prompt** (see §3.1) | `b65ed58` |
| **A rule the operator wrote outranks a worker's incident report** (see §3.2) | checkpoint `00d49a9` |
| The plan doc itself | `58d3056`, `9ae58ac`, `ea2dae3`, `08e1bc6`, `5ab8006` |

### Restart: confirmed, not merely armed

The deferred backend restart fired and landed at **2026-08-12 21:32 PDT
(04:32Z)**, verified three ways rather than by trusting the success line
(standing memory says never do that): the unit's `ActiveEnterTimestamp` is
after the arming, the running pid is that young, and the live process's
`chat_context.learnings_context` source contains both `if scope !=
learnings.GLOBAL` and `OPERATOR PREFERENCES`.

So everything in §3 is live, not pending.

### Not done, and not started

Everything in the plan's phases 0 to 5. Nothing turns autonomy on. The queue
runner is still absent, the autonomous cron lines are still commented out, and
that comment state is **the operator's decision, recorded in
`infra/cron/workshop.crontab`**. Do not uncomment them.

---

## 2. Where to pick up

The plan's own "if only three things get done" list, with the current state:

1. ~~Fix the preference path~~ **done** (`b65ed58`).
2. ~~Un-drop the Design System rule from the injected block~~ **done**
   (authorship is a sort tier in `learnings._entry_rank`; budget 700 to 900).
3. **Give `mcp__workshop__search` to the worker and to agents.** NOT done, and
   it is now the highest-value remaining item. Today exactly one surface in the
   system can search the workshop's own writing: the general chat
   (`chat_context.py:1724`). A ralph worker, a registry agent and an @-mentioned
   agent all cannot.

   **It is three different mechanisms, not one allowlist change**, and the spec
   breaks them out in §3.6: the mentioned agent goes through
   `agent_authority._READ_TOOLS` (which correctly excludes `broker`, whose whole
   contract is not reading what it adjudicates), project and build chats route
   to the full-agentic branch, and the ralph worker calls `claude -p` directly
   with no MCP flags at all, so it needs `--mcp-config`. The worker case also
   has an owner-pinning trap that would let it ship broken and pass a smoke
   test.

After that, the plan's phase order. Phase 0 is "stop lying" and is all
deletions of controls that do nothing.

**Phase 1 was reordered on 2026-08-13** after a second review re-measured the
queue. Its first two items are now "get the 14 SEC tickets out of the build
queue" and "one scope vocabulary", because until both land, `development` has
exactly ONE ticket that passes readiness and phase 2 cannot draw five watched
runs from it. The plan's new §1 subsections ("The queue is ordered worst-first"
and "There is almost no ready work in the scope phase 4 targets") are the
evidence; read those two before starting phase 1.

---

## 3. The two fixes most likely to be misunderstood or undone

### 3.1 Preferences were written to a store nothing read

`task_scope.apply_learning_note` defaults a ```learning-note``` fence to
`knowledge.scope_for_context("general")`, which is `workshop`.
`chat_context.learnings_context` then skipped exactly that scope, so the chat
replied "Captured a preference" and wrote somewhere no prompt ever read.
**Eleven records, three weeks, zero retrievals.**

Fixed by narrowing the exclusion to `global` alone (which is already the first
block) and labelling the second block `OPERATOR PREFERENCES` rather than
`AGENT MEMORY`, because the label is what tells the model how to weigh it.

Pinned by `backend/tests/test_learnings_reach_the_chat.py`, which fails against
the old condition.

**Caveat for whoever verifies this:** reading the store changes it.
`memory_block()` stamps `retrieval_count` and `last_retrieved`. The audit's own
verification call put `retrieval_count: 1` on ten of the eleven records, dated
2026-08-13T04:08:49Z. That timestamp is the audit, not the system.

### 3.2 The most important rule was silently over budget

The injected `global` block was capped at 700 tokens against a 795-token store,
and the entry it dropped was the longest: the Workshop Design System rule, 168
tokens, absent from every prompt for roughly 25 days. It lost on three terms at
once (old, one provenance entry, and never retrieved BECAUSE it was never
selected, which is self-reinforcing).

**Authorship is now a sort TIER, not a bonus.** The first attempt added 2.0 and
described it as "above the maximum any other term can reach"; the other four
terms sum to 3.5, so a fresh reinforced incident line scored 2.45 against the
rule's 2.2 and still won. If you touch `_entry_value` / `_entry_rank`, keep the
tier.

The regression test also failed the first time in the other direction: built
with `record()`, everything is stamped as new, so recency alone carried it and
the test passed whichever way the ranking was wired. It now writes rows by hand
with controlled timestamps. **A test that passes with the fix reverted is
worthless; check that explicitly.**

---

## 4. Four things an adversarial review caught in the plan's first draft

These are the ones a fresh agent is most likely to re-propose, because each is
the obvious next move:

1. **Do NOT split `awaiting` into two states.** `awaiting` is not a status; it
   is a structured field on a `blocked` ticket, and `tasks.py:57-89` records
   the decision that every stop reason is the SAME primitive widened in place,
   with `kind` deliberately an open slug set. Also `awaiting_input` is already
   a live **Job** status (`jobs.py:52`), so reusing the token collides across
   the Ticket/Run seam. What is wanted is an expiry policy keyed on
   `awaiting.kind`: questions may time out, `kind: permission` never does.
2. **Do NOT build an inbox producer for finished runs.** It exists and works
   (`inbox.sweep_finished_runs`, raises `notify` for autonomous-trigger jobs,
   runs on every inbox read). The inbox is nearly empty because no unattended
   run happens.
3. **Do NOT point a build sweep at `workshop` scope.** Operator decision
   2026-08-09: "`workshop` is not a build target... it has no code tree." Use
   `development`.
4. **Do NOT add an LLM reviewer as a gate.** `reviewer.sh` is deterministic and
   IS the external verifier. Self-critique without an external signal measures
   40% to 55% where a sound verifier measures 40% to 88%, and a single LLM
   verdict flips 13.6% of the time. A clean-context judging model is worth
   having as ADVISORY findings, never as the gate.

**Three more from the second pass (2026-08-13), all verified in code:**

5. **Do NOT give the queue triager an mtime watermark.** The note triager uses
   one (`triager.py:166-187`), and copying it is an infinite loop:
   `tasks.update` sets `updated = now()` on every write (`tasks.py:1808`), so
   the triager writing `awaiting` re-selects the ticket it just judged, forever.
   The note triager is safe only because it cannot write notes. Use a per-ticket
   content hash over title/body/`depends_on`/`effort`.
6. **Do NOT key `awaiting` expiry on a denylist of permanent kinds.**
   `_normalize_awaiting` defaults `kind` to `"input"`, and legacy
   `{question, since}` records read as `input` too. Since `input` is the kind
   that times out, a denylist means every unlabelled block silently becomes
   auto-resolvable. Allowlist the three expiring kinds; everything else waits.
7. **Do NOT assume readiness is the unattended-selection predicate.** It reads
   the ticket's TEXT. `[SEC-C3]` is well-formed and undoable, because the worker
   is not root. The missing field is `execution: code | judgement | external`,
   and it moved from phase 5 to phase 1 because phase 4's first selection needs
   it.

---

## 5. Environment traps that cost time this session

- **A checkpoint committer commits your working tree mid-session**, authored as
  `Workshop Checkpoints <checkpoints@workshop.local>`, message
  `checkpoint: a turn <ts>`. Your work will often already be committed under a
  generic message before you run `git commit`. Check `git log -- <file>` before
  concluding a change was lost.
- **Peer sessions are editing this tree at the same time** (chat transcript
  work, `services/paths.py`). Stage only your own files. `git status` showing
  someone else's modifications is normal.
- **`backend/tests/test_paths_are_portable.py::...[services.paths-chat_cwd]`
  fails, and it is not yours.** It was failing before this session started and
  belongs to another session's in-flight work. 1858 pass around it.
- **A test that writes a learning must redirect BOTH stores.** Patching
  `learnings._dir` is not enough: `record()` also mirrors through
  `_legacy_md()` into the TRACKED `shared/learnings.md`. The first run of the
  new test file put `A global guardrail. [src:test]` into that file AND into
  the live global store, where it would have ridden into every prompt. Both
  were removed by hand.
- **A deleted row in `.runtime/learnings/*.jsonl` can come back.** The running
  backend's retrieval tracking rewrites the file from its own earlier read, so
  a hand-deletion races it. Delete, then verify, then delete again if needed.
- **The egress guard blocks a shell command containing an external hostname**,
  even when the command only writes text into a file. Use Edit/Write for
  content with URLs in it, not a bash heredoc.
- **`git add <repo-relative-path>` fails silently-ish from a subdirectory.**
  Several commits "found nothing to commit" purely because the cwd was
  `backend/`.

---

## 6. Operator preferences confirmed or corrected this session

- **A chip is for something you scan a LIST for.** Plain text is for what you
  read once you are already looking. One record on a page means almost nothing
  earns chip treatment.
- **The status WORD still has to be visible**, in the list and on the ticket. A
  dot alone answers "blocked or just queued" only for someone who already knows
  the five hues. The complaint was the chip, not the word.
- **Properties belong high, not in a rail that stacks to the bottom on a
  phone.** `DetailLayout` puts the rail first in the DOM for that reason; do
  not reorder it.
- **A record page's title is content, not a page name.** 24px is a banner.
- **Back is chrome**: muted, named, above the title. Never a bare accent arrow
  inheriting the h1 size.
- **Long body text clamps.** A page should open on its own controls.
- **Stop re-litigating minor styling.** Stated 2026-08-12 after the padding
  pass: move on to the substance.

---

## 7. Open questions the operator has not answered

Carried forward, not invented here:

- Is autonomous ticket execution the goal, or is the goal an excellent attended
  path with narrow autonomy? The plan argues both readings are defensible and
  that phases 0 to 3 are worth doing under either.
- What happens to the 34 open tickets, several of which are stale July design
  work? Nothing in the model expresses "this ticket replaces that one".
- ~~Per-scope budget before any unattended running. The ledger exists; no budget
  is set.~~ **Corrected 2026-08-13.** A budget IS set: `app_total` 5,000,000,
  `nominal_task_tokens` 150,000, the ~0.65 interactive reserve enforced, and
  `can_admit('development')` returns `(True, 'ok')`. Only the per-scope `ceil`
  is missing, and it is now a phase-4 line item in the plan. The genuinely
  unmetered spend is the reviewer's and the distiller's.
- Execution isolation (SEC-A1 / SEC-A2, both open). This is the hard
  prerequisite for phase 5 and cannot be solved by design in the plan doc.
