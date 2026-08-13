# Implementation spec: phases 0 to 4 of the automation loop

**Status: SPEC. Companion to `autonomy-turn-on-2026-08-12.md`, which is the
authority for WHY. This document is only HOW, and it is binding on the code.**

Every path, line number, symbol and constant here was read from the live tree on
2026-08-13. Where this spec and the plan disagree, the plan wins on intent and
this document is wrong about mechanism. Where either disagrees with a ratified
decision (`entity-model-2026-07-18.md`, the ADRs), the ratified decision wins.

**Read the plan's §1 first.** Two of its subsections ("The queue is ordered
worst-first", "There is almost no ready work in the scope phase 4 targets") are
the reason phase 1 is shaped the way it is below, and the work reads as
arbitrary without them.

**Scope of this document.** Phases 0 to 4. Phase 5 is an entry condition gated on
an execution boundary that does not exist (SEC-A1), so there is nothing to
specify.

---

## 1. Schema: two new ticket fields, and nothing else

Every phase below depends on these, so they land first, in one change, with
their tests. No other schema change is required by phases 0 to 4.

### 1.1 `execution`: can a machine do this at all

```
execution: "code" | "judgement" | "external" | None
```

| value | meaning | unattended sweep |
|---|---|---|
| `code` | a change to a tree this worker can write | eligible |
| `judgement` | the output is a decision, a design or an audit | never |
| `external` | needs root, another host user, a vendor console, or an operator secret | never |
| `None` | not yet classified | never (fail closed) |

`None` is not `code`. An unclassified ticket is not eligible for unattended
selection, because the whole point is that the sweep should not guess.

**Storage.** A SPEC field, stored flat, alongside `effort` and `priority`. It is
NOT added to `EXEC_FIELDS` (`tasks.py:174`): a tracker sync would legitimately
want it, and it is a thing the operator sets rather than a thing a run writes.

**Where it changes:**

| file | change |
|---|---|
| `backend/services/tasks.py` | add to `_normalize()` near `effort` (`:757-763` is the pattern); coerce to the closed set or `None`; add `execution` param to `add()` (`:1484`) and `update()` (`:1583`) |
| `backend/routes/tasks_all.py` | accept in the create and patch bodies; it already rides `_detail()` once it is on the record |
| `frontend/src/lib/tasks.ts` | `execution?: TaskExecution` on the `Task` type; `EXECUTION_LABEL` map |
| `frontend/src/lib/ticketEdit.ts` | add to `TicketDraft`, `draftFromTask`, `ticketDirty`, `ticketPatch` |

**Why a third value rather than a boolean.** `judgement` and `external` are both
"not for the sweep" but they route differently for a human: a judgement ticket
wants a chat, an external one wants a terminal and a password. Collapsing them
loses the only information the tag adds.

### 1.2 `triage`: the mark that stops the queue triager re-judging

```
triage: { at: iso, decision: "ok"|"split"|"underspecified"|"none", spec_hash: str } | None
```

**Storage.** An EXEC field. Add `"triage"` to `EXEC_FIELDS` (`tasks.py:174`) so
it projects under `exec` on read and is never sent to an external tracker. It is
run metadata, not spec.

**`spec_hash` is the entire safety mechanism, so it is specified exactly.**

```python
# backend/services/tasks.py
def spec_hash(task: dict) -> str:
    """Hash of the fields a triage decision depends on, and only those.

    Deliberately NOT `updated`: `update()` sets it on every write (:1808), so a
    frontier keyed on it re-selects every ticket the triager itself wrote to,
    forever. The note triager's mtime watermark is safe only because that role
    cannot write notes; a queue triager writes the store it reads.
    """
    payload = json.dumps({
        "title": str(task.get("title") or "").strip(),
        "body": str(task.get("body") or "").strip(),
        "depends_on": sorted(str(d) for d in (task.get("depends_on") or [])),
        "effort": task.get("effort") or "",
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

Excluded on purpose, each for a reason:

| excluded | why |
|---|---|
| `updated` | the loop described above |
| `status` | a ticket going `todo` to `doing` to `todo` is not a new question |
| `awaiting` | the triager's own `underspecified` write |
| `evidence`, `job_ids`, `attempts` | run history, not spec |
| `execution`, `priority` | change routing, not whether the spec is adequate |

**Due predicate**, which is what replaces the watermark:

```python
def triage_due(task: dict) -> bool:
    t = (task.get("triage") or {})
    return t.get("spec_hash") != spec_hash(task)
```

No record means due. A rewritten spec means due again, which is correct: the
question genuinely changed. The triager answering its own question does not,
which is the point.

### 1.3 Tests that must exist before anything uses these

`backend/tests/test_triage_mark.py`:

1. `spec_hash` is stable across an `update()` that only touches `status`.
2. `spec_hash` is stable across an `update()` that only sets `awaiting`.
   **This is the regression test for the infinite loop. It must fail if
   `updated` is ever added to the hashed payload.**
3. `spec_hash` changes when `body` changes.
4. `triage_due` is True for a record with no `triage`.
5. `execution` coerces an unknown string to `None`, not to `code`.

Test 2 is the one that matters. Write it first and watch it fail against a
naive `updated`-based implementation.

---

## 2. Phase 0: stop lying

No new capability. Every item removes a control that does nothing or a label
that is false. Nothing here needs the schema above.

### 2.1 Delete `depth` and `fanout` from the registry

Already gone from the UI; the parse still echoes them to the API.

| file | change |
|---|---|
| `backend/services/agents_registry.py:216-220` | delete `_coerce_depth`, `_coerce_fanout` |
| `:475-476` | delete the two record keys |
| `:819-820, :843-846` | drop the `depth` / `fanout` params from the write path |
| `backend/routes/agents.py:178-179` | delete the two response keys |
| `:983-984` | drop from the create body |
| `:103-104` | the comment already says project autonomy has no depth; keep it, it explains the absence |

Grep `depth`/`fanout` across `frontend/src` afterwards and expect zero
non-comment hits. A definition file that still carries `depth:` in frontmatter
keeps working: unknown frontmatter keys are ignored.

### 2.2 Split the brake from the runner

Today `POST /api/queue/runner/start` (`routes/autonomy.py:257`) does two
unrelated things and one of them cannot work:

```python
autonomy_override.clear()                  # moves the GLOBAL brake
subprocess.Popen(["bash", str(QUEUE_WATCHDOG)])   # no DIR argument
```

`queue_runner_watchdog.sh` reads `DIR="${1:-}"` and calls
`assert_run_target "$DIR"`, which refuses an empty target under ADR 0014 and
exits 2. So the button clears the brake and starts nothing.

**Backend.**

```python
class RunnerStart(BaseModel):
    dir: str        # required. No default: ADR 0014, a target must be named.

@router.post("/queue/runner/start")
async def queue_runner_start(body: RunnerStart):
    # The brake is NOT touched here. Starting a runner and permitting
    # unattended work are two decisions; one button moving both is how the
    # global brake got cleared by someone starting a runner.
    target = _validated_run_target(body.dir)   # 400 on refusal, with the reason
    subprocess.Popen(["bash", str(QUEUE_WATCHDOG), target], ...)
```

`/queue/runner/stop` similarly stops signalling the brake. It keeps killing the
resident runner, which is what it is for. The brake keeps its own existing
endpoints at `:123` (engage) and `:132` (clear).

`_validated_run_target` shells the same gate the watchdog uses
(`scripts/dev_mode_gate.sh`) so the refusal reason is identical in both places
rather than reimplemented in Python.

**Frontend, `frontend/src/components/AutonomyControls.tsx:132-157`.**

Two controls where there is one. Per the design system, a switch that is a
policy is a `Toggle`; a thing that starts a process is a `Button`.

| control | primitive | label | state shown |
|---|---|---|---|
| unattended work permitted | `Toggle` | "Allow unattended work" | brake state, `ok` / `stopped` |
| the resident runner | `Button` + a scope `Select` | "Start runner" / "Stop runner" | `running` from the flock probe |

The existing `describeAutonomy(pair)` already models the two independently
(`running` and `enabled`); keep it. The Start button is disabled with a hint
when no scope is selected, and per the design system a disabled control dims,
it never sets `cursor: not-allowed`.

Stopping the runner keeps its `ConfirmDialog`. Toggling the brake does not: it
is reversible and instant.

### 2.3 Relabel latitude, and stop calling anything a boundary

`frontend/src/lib/tasks.ts` `latitudeDesc`, rendered at
`frontend/src/pages/Agents.tsx:481`. Rewrite the three descriptions so each says
what the worker DOES when the ticket is silent, and none implies permission:

| value | text |
|---|---|
| `strict` | "Asks when the ticket does not say." |
| `standard` | "Decides reversible things, asks about the rest." |
| `open` | "Decides and reports at the end." |

`services/latitude.py:20-41` already retracts the "enforced floor" language in
the docstring; this makes the UI agree with it. Anywhere `touches` or grants are
described as a boundary, say "audited after the run" instead. There is currently
no such string in `frontend/src`, so this is a rule for new copy rather than an
edit.

### 2.4 Leave `ticket_scope_review.py` alone

Its fate is phase 3's decision, and deleting it here means rewriting it there.

---

## 3. Phase 1: readiness visible, and a work supply

The plan's §1 measured that `development` has exactly one ready ticket, so the
first two items are what make phases 2 and 4 possible at all. They are data
work, not features, and they come first.

### 3.1 Classify the 34 open tickets, and park what no worker can do

A scripted pass the operator reviews, not an autonomous one.

`scripts/classify_open_tickets.py` (new, one-shot, lives in `scripts/` because
it is an operation rather than a service):

1. Read every open ticket in every non-scratch store.
2. Propose `execution` per ticket with a deterministic first pass:
   - body matches `\b(sudo|root)\b` or the title ends in `(root)` to `external`
   - `effort: deep` or a title starting with a judgement verb (audit, design,
     define, revisit, decide, research) to `judgement`
   - everything else to `code`
3. Print the proposal as a table and write nothing without `--apply`.
4. With `--apply`, set `execution`, and additionally set `status: parked` with
   `blocked_reason: "host administration; not agent-executable"` for the
   `external` ones.

Expected on today's data: 14 SEC tickets classified, at least 6 `external`,
and the head of `next_ready` in the platform repo stops being a root ticket.

**Why park rather than move to another scope.** A move is a delete plus an add
across two stores with no transaction and no `move` verb in `tasks.py`. Parking
is one field, it is the existing dead-letter semantics, `next_ready` already
ignores it, `state_type` already maps it to `backlog`, and it is reversible with
one click. The tickets stay visible and stay findable.

**The operator reviews the table before `--apply`.** A misclassification here is
cheap in one direction (a `code` ticket wrongly marked `judgement` waits for a
human, which is the status quo) and not in the other, so the deterministic pass
is deliberately biased toward `external` and `judgement`.

### 3.2 Re-file the misfiled platform tickets

8 of the 9 readiness-passing tickets are in `shared/queue` (`workshop` scope,
no code tree) and several are plainly platform code work: "A sent message should
not look already-read", "Reduce empty turns: 26% of prompts return no assistant
text", "Point `LETTA_URL` at the right instance".

**This is deliberately NOT the full scope-vocabulary migration.** The plan's §5
item 4 wants one vocabulary across tickets, notes and chats, touching four
independent parsers (`knowledge._parse_scope`, `task_scope.root_for_context`,
`chatScope.ts`, `lib/tasks.ts`). That is a migration and it is not a
prerequisite for phase 2. What IS a prerequisite is that some buildable tickets
exist in a buildable scope.

So, split:

| | what | when |
|---|---|---|
| 1b-i | re-file the platform-code tickets from `shared/queue` into the platform store, by hand, reviewing each | now, blocks phase 2 |
| 1b-ii | unify the scope enum and delete the translators | its own ticket, after phase 3 |

1b-i is an afternoon and gives phase 2 its five candidates. 1b-ii stays on the
list because three key spaces still means three of everything, but nothing in
phases 0 to 4 blocks on it.

### 3.3 The readiness line

The six checks already run on every ticket read (`tasks_all.py:520` attaches
`rec["readiness"]`). The data is on the wire; only the display is missing.

**It is one line of prose. It is not a checklist.** The operator deleted a
readiness checklist from this page on 2026-08-12 as a nag panel, and rebuilding
it with different markup would be the same mistake.

`frontend/src/pages/TicketDetail.tsx`, immediately above the Run button:

```tsx
{gaps.length > 0 && (
  <Notice tone="warn">{readinessLine(gaps)}</Notice>
)}
```

The prop is `tone`, not `kind`: `Notice` takes `"info" | "warn" | "error"`
(`packages/ui/src/feedback.tsx:55`) and the five status KINDS are a different
vocabulary belonging to `StatusPill` and `StatusDot`. Getting this wrong is a
typecheck failure, not a visual one, so it will be caught, but the two
vocabularies are easy to conflate.

`readinessLine` lives in `frontend/src/lib/tasks.ts` and is pure, so it is unit
testable without a DOM:

| gaps | text |
|---|---|
| 1 | "Does not say what done looks like." |
| 2 | "Does not say what done looks like, or which files it may change." |
| 3+ | "Three things are unspecified: what done looks like, which files it may change, and where to start." |
| 4+ | first two, then "and N more." |

Phrasing comes from the `why` strings already returned by
`ticket_readiness.check` (`services/ticket_readiness.py:62-86`), lowercased and
joined. Do not invent a second set of wordings; the checker owns them.

`Notice`, not `StateBlock`: a dashed region is for an empty or error state, and
this is an inline advisory on a populated page.

**Same line in the chat's file-a-ticket card**, which is where it is cheapest to
act on, and in the ticket list row only as a single `warn` `StatusDot` with the
line as its `title`. A full sentence per row would wreck the row rhythm the
list was just rebuilt around.

**It never blocks.** `launch_task_run` keeps refusing only
`ticket_readiness.blocking()` (title, dependencies). See the plan's §3.1: the
line informs a human, and readiness becomes a selection predicate only for
unattended dispatch in phase 4.

### 3.4 The `execution` control on the ticket page

- **Rail row** (`Props` / `Prop`), always visible, showing the label or "not
  classified".
- **Editor** in Edit mode, a `Select` with the three values plus empty.

This is a new field with an editor, which is the pattern the 2026-08-12 pass
deleted for latitude and attempt-cap. It earns its place on a rule the deleted
ones failed: **phase 4 selects on it.** A field with a consumer is not
decoration. If phase 4 is abandoned, delete this control with it.

### 3.5 Make the ticket-maker fill acceptance and boundaries

74% and 68% of open tickets fail these two checks. Both are prompt changes, and
the format module already exists so no new vocabulary is introduced.

| file | change |
|---|---|
| `backend/services/ticket_drafts.py` | the draft-proposal prompt must emit all four `ticket_format.ORDER` fields and compose through `ticket_format.compose()` |
| `backend/services/chat_context.py:171` | `TASK_PROPOSAL_BUILD_RULE`: require Output and Boundaries, and say what they are for (a worker sees only title and body) |
| `backend/routes/chat.py` (distiller, `_distill_task_from_thread` at `:608`) | same four fields |

Boundaries has a machine-checkable escape hatch already: `ticket_readiness`
accepts a non-empty `touches` OR `latitude: open` OR a stated Boundaries label
(`ticket_readiness.py:78-80`). Producers should state the label; it is the one
a human reads.

**Target: newly filed tickets pass at 80%.** Measure with the same one-liner
the plan's §1 used, filtered to `created >= <date of this change>`. Do not
compare against the 26% figure directly: that is a different population, which
is the mistake the plan's own §1 flags about the earlier 0-of-40 number.

### 3.6 Give the search tool to the worker and to agents

The highest-value item in the plan's "if only three things" list, and the last
one outstanding. Three surfaces, three different mechanisms.

**(a) The @-mentioned agent.** Tools come from `agent_authority.narrow_tools`
(`routes/chat.py:2257`) and `mcp_servers=[]` is passed explicitly at `:2323` and
`:2375`. Add `mcp__workshop__search` to `_READ_TOOLS` in `agent_authority` and
pass `mcp_servers=["workshop"]`.

**`_READ_TOOLS`, specifically, and not "every role".** `tools_for_role`
(`:109-132`) grants reads only to a role that writes SOMETHING:

```python
writes = writes_for_role(role)
if not writes:
    return []
```

So an unroled agent and `broker` get nothing, and that is deliberate on two
grounds the docstring states: an agent with no declared role should be inert
rather than quietly able to read the tree, and **a broker's contract is
specifically that it never reads the data it adjudicates**. Adding search to
`_READ_TOOLS` inherits both exclusions for free. An earlier draft of this spec
said "all five roles including broker", which would have handed the one role
defined by not reading a search tool over the whole corpus.

Note also that reads are not free here: same-uid reads reach every owner's
personal data (SEC-A5). That is the reason the surface is narrow, and it is an
argument for putting search behind the same derivation rather than beside it.

**(b) Project and build chats.** `chat_context.py:1460` and `:1571` set
`allowed_tools = []`, which routes them to the full-agentic branch. Full agentic
inherits the cwd's MCP configuration, and there is no `.mcp.json` in the tree,
so they get nothing. Give them the same treatment as (c).

**(c) The ralph worker.** `scripts/ralph.sh` invokes `claude -p` directly rather
than through `llm_router`, with `--disallowedTools` (`:431`) and no MCP flags.
The mechanism `llm_router` uses is `strict_mcp_flags(servers_subset)`, emitting
`--strict-mcp-config --mcp-config <json>`.

So: add a config emitter next to the existing module CLIs (`learnings.py:964`
is the precedent for a `__main__` argparse in a service), and have ralph pass
it.

```bash
# scripts/ralph.sh, near the DENY_FLAGS block at :431
MCP_CFG="$(cd "$WORKSHOP/backend" && .venv/bin/python -m services.llm_router \
            mcp-config --server workshop 2>/dev/null || true)"
MCP_FLAGS=()
[ -n "$MCP_CFG" ] && MCP_FLAGS=(--mcp-config "$MCP_CFG")
```

Emit to a temp file, print the path, and let the caller clean up. Do not use
`--strict-mcp-config` here: the worker is full-agentic by design and strict mode
would silently drop every other server the tree legitimately configures.

**The server is owner-scoped**, via `WORKSHOP_QUERY_OWNER`
(`backend/mcp/workshop_query_server.py:85-108`). The worker runs as the service
user, so the emitter must pin the owner the tickets belong to, or the worker
searches an empty index. This is the single most likely way this item ships
broken and passes a smoke test.

**Verification that actually proves it:** run one ticket whose body asks a
question only answerable from a knowledge note, and confirm the run log contains
an `mcp__workshop__search` call. A run that merely succeeds proves nothing.

### 3.7 Rebuild the index, and index every knowledge directory

`semantic_index.collect_notes()` (`:231-253`) builds its roots from
`knowledge._shared_knowledge_root()` plus that directory's immediate
subdirectories, non-recursively. So `projects/*/knowledge`, `apps/*/knowledge`,
`.runtime/agents/*/knowledge` and `roles/<role>/` are never indexed: 21 of 21
indexed note refs come from one directory.

- Extend `collect_notes` to walk every scope directory `knowledge.scope_dir()`
  can produce.
- Add a rebuild trigger. A cron entry is wrong here (it is the thing the
  operator turned off); a post-write hook in `knowledge.put_note`
  (`knowledge.py:509`, and note it is `async def`) that marks the index stale,
  plus a rebuild on the next search when stale, is self-healing and needs no
  scheduler.
- `numpy` is imported at `semantic_index.py:51` and declared only in
  `backend/requirements-voice.txt:17`. Move it to `requirements.txt`. A backend
  installed without voice extras currently raises on the first search call.

The plan puts this in phase 3 because the triager is the first consumer that
depends on retrieval. The numpy line is a one-word fix and should go in phase 1
with (3.6), because it is a latent crash on the tool we are about to hand out.

---

## 4. Phase 2: K watched runs, and a number

Not a build. A procedure, plus the instrumentation to make it produce evidence.

**Procedure.** For each of K=5 tickets, all `execution: code`, all in
`development`, chosen by the operator:

1. `POST /api/tasks/development/{id}/run`
2. Watch the run page. Record: did the reviewer pass, did a real commit land,
   does the ticket's stated acceptance hold, did it raise a block.
3. Record wall time and token spend from the usage ledger.

**Instrumentation needed first**, none of it large:

| what | where |
|---|---|
| the run page shows the live worker step | already shipped |
| `task_id` and `chat_id` on the job record | shipped 2026-08-12 |
| a per-run token total visible without reading the ledger | `usage_ledger` has the rows; the run page does not show them |

**The gate.** From the plan's §6, restated here because it is the number
everything after depends on:

| K=5 result | action |
|---|---|
| 4-5 pass | proceed to phase 3 |
| 2-3 pass | fix what the failures show, re-run K, do not proceed on the same code |
| 0-1 pass | stop; the attended path is the product |

"Pass" means reviewer passed AND a real commit landed AND acceptance holds. **A
run that raised a sensible block counts as a pass**: asking was correct, and
scoring it as failure is what teaches a worker to guess.

---

## 5. Phase 3: the queue triager

The largest build in this spec. It reuses two existing things whole and adds one
new decision surface.

### 5.1 Rewire `ticket_scope_review.py` as the engine

This answers "decide its fate", which the plan defers to here. `review(task)`
already returns `{verdict: ok|split|underspecified, reason, subtickets,
blockers, stages}`, already downgrades a `split` with fewer than 2 children
(`:156-158`), already refuses to split a ticket that has a `parent` (`:182-188`),
and already composes child bodies through `ticket_format.compose()`
(`:146-151`). It writes nothing.

Keep it. Delete the dangling import at `routes/tasks_all.py:38` and import it
from the triager instead. Restore its deleted test file.

### 5.2 `triager.run_queue_triage(scope_key)`

New function in `backend/services/triager.py`, beside `run_triage` (`:452`).
Shares the module's admission and cap discipline; shares nothing else.

```python
QUEUE_DECISIONS = ("ok", "split", "underspecified", "none")
MAX_QUEUE_TICKETS_PER_PASS = 20     # how many to LOOK at
MAX_SPLIT_PER_PASS = 5              # how many may file children
MAX_UNDERSPECIFIED_PER_PASS = 5     # how many may raise a question
```

Both write caps matter. 25 of 34 open tickets fail the acceptance check, so an
uncapped first pass produces roughly 25 newly blocked tickets and 25 questions
in one morning. That is not a review, it is a wall.

**Selection.** `[t for t in tasks if t["status"] == "todo" and triage_due(t)]`,
ordered oldest first. Not the mtime watermark. See §1.2.

**Per ticket:**

| verdict | write |
|---|---|
| `ok` | `triage` mark only |
| `none` | `triage` mark only, with the reason |
| `underspecified` | `tasks.update(awaiting={...})`, then the mark |
| `split` | file children as `draft` with `parent`, then the mark on the parent |

Every path writes the mark, including the ones that change nothing. That is what
makes the pass terminate.

**Children are `draft`.** A split is a proposal like any other, and the operator
approves it. Depth cap 2, enforced by the engine's existing `parent` refusal.
`scoped_by: "agent:queue-triager"`.

### 5.3 The `awaiting` record the triager writes

```python
awaiting = {
    "kind": "missing-info",          # explicit. never rely on the default.
    "ask": "<the one specific question>",
    "why": "<which readiness check this resolves>",
    "unblocks_if": "<what an answer would let the worker do>",
    "raised_by": "agent:queue-triager",
    "dedupe_key": f"triage:{scope_key}:{check_name}",
}
```

`dedupe_key` groups a mass-block so ten tickets missing acceptance can be
answered as one gesture, which `tasks.py:57-89` already designed for.

**ONE question per ticket.** The engine returns a list of blockers; take the
one that resolves the highest-value readiness gap and discard the rest. A
five-part question is not answerable in the inbox.

### 5.4 Expiry keyed on `kind`, as an allowlist

```python
# backend/services/tasks.py
EXPIRING_AWAITING_KINDS = frozenset({"input", "decision", "missing-info"})
```

**An allowlist, not a denylist, and this is the load-bearing detail.**
`_normalize_awaiting` defaults `kind` to `"input"`
(`"kind": _slug_or(payload.get("kind"), "input")`), and its own docstring notes
that a legacy `{question, since}` record "reads as `input`". `input` is an
expiring kind. So a denylist of permanent kinds would make every unlabelled and
every pre-existing block silently auto-resolvable on the day expiry ships.

Anything not in the set waits for a human, forever, including `permission` and
including any future slug. `kind` stays an open set, which is why the default
has to be safe.

Auto-resolve additionally requires all of: resolved latitude is `standard` or
`open`, `awaiting.default` is non-null, the decision is reversible, and the
block was not raised by the reviewer. That ladder is already ratified in
`latitude-model-2026-07-18.md:158-167`, including "hard gates never auto-resolve
at any latitude". This spec adds no new policy, only the kind filter.

### 5.5 Do not refund an attempt for a triager-raised block

`POST /review` releases with `restore_attempt=True` (`tasks_all.py:998`), on the
sound reasoning that an answered question is not a failed attempt. But a
triager-raised `underspecified` block is written with no run having been
claimed, so answering it decrements an attempt the ticket never spent. It floors
at zero, so nothing breaks, but a ticket triaged and answered twice quietly
earns back two real attempts against its park cap.

```python
raised_by = (task.get("awaiting") or {}).get("raised_by") or ""
restore = not raised_by.startswith("agent:queue-triager")
```

### 5.6 Trigger

Manual first. `scripts/triager_run.sh <scope> --queue --dry-run` prints every
decision and writes nothing. **Read a week of these before giving it a cron
trigger**, which is the plan's instruction and the reason this phase is measured
in weeks.

---

## 6. Phase 4: supervised unattended execution

### 6.1 Selection

The one new rule. Unattended dispatch selects on:

```
status == "todo"
AND depends_on satisfied          (existing)
AND execution == "code"           (new, §1.1)
AND ticket_readiness.check(t)["ok"]   (new)
```

ordered by the existing `priority DESC, created ASC`.

`next_ready` currently applies only the first two (`tasks.py:1457-1479`). Add an
optional predicate rather than changing its meaning, because `next_ready` is
also what a MANUAL run path consults and readiness must never refuse a human:

```python
def next_ready(root, *, assignee=None, unattended: bool = False):
```

`unattended=True` is passed by the sweep and by nothing else. This keeps the
ratified "readiness never vetoes" true for every human path, while making
"tickets marked ready may run" true for the machine, which is the distinction
the plan's §3.1 draws.

### 6.2 Per-scope budget ceiling

Set before the first unattended run, not after the first surprising week.

A budget already exists and works: `app_total` 5,000,000,
`nominal_task_tokens` 150,000, a ~0.65 interactive reserve, and `can_admit`
refusing a sweep that would start on a sliver (`usage_ledger.py:442-467`). The
gap is only the per-scope `ceil` that caps how much of the shared pool one scope
may borrow. It is a config value, not a feature. Set it in the same change that
starts the runner.

Genuinely unmetered and worth fixing here: the reviewer's and the distiller's
spend. `reviewer.sh` makes zero model calls so it is free, but the distiller is
a real call and is billed to nobody.

### 6.3 Supervised means `in_review`

A run in a `supervised` scope lands the ticket in `in_review` rather than
`done`. The reviewer gate and the commit gate are unchanged. This is the
`autonomy` enum's only machine-visible consequence and it is what makes phase 4
safe to try.

### 6.4 Stuck detection

An unattended run that loops is the failure this phase invents.

Copy OpenHands' published rule set, whose thresholds are the only ones anyone
has published:

| rule | threshold |
|---|---|
| same action, same observation | 4 |
| same action, same error | 3 |
| alternating A,B,A,B | 6 |
| steps without a file write | 8 before the first write, 20 after |

Compare by SEMANTIC equality: strip ids, timestamps and metrics before
comparing, or nothing ever matches. Anchor the window at the last human message.
Reset every counter on a real file write.

**Advisory first, blocking second.** And the documented failure of every
implementation of this is killing an agent that was legitimately polling
something slow, so:

> A stuck run ends `parked`, with a reason and a resumable id. Never `failed`.

`parked` is already the dead-letter for attempt-cap exhaustion, already maps to
`backlog`, and is already visible. Reuse it.

### 6.5 The runner itself is the operator's call

`infra/cron/workshop.crontab:72` is commented out, is missing the `--dir`
argument it now requires, and carries an explicit instruction above it: "THIS
COMMENT STATE IS THE OPERATOR'S DECISION, NOT AN ACCIDENT OF EDITING... Do not
uncomment them to 'restore' anything."

**Fix the missing `--dir`. Leave the comment.** The watchdog takes the tree as
`$1`, positionally, so the corrected line is
`*/5 * * * * <workshop>/scripts/queue_runner_watchdog.sh /path/to/tree`.

### 6.6 What already exists and must not be rebuilt

`inbox.sweep_finished_runs` raises `kind="notify"` for every terminal job whose
trigger is autonomous, runs on every inbox read, and its watermark is current.
The inbox is nearly empty because no unattended run happens, which this phase
changes by itself. Nothing to build.

---

## 7. UI/UX rules for everything above

Binding: `frontend/CLAUDE.md`. This section adds only what is specific to these
surfaces.

**New or changed surfaces, and their primitives:**

| surface | primitives | phase |
|---|---|---|
| readiness line, ticket page | `Notice kind="warn"` | 1 |
| readiness dot, list row | `StatusDot` with `title` | 1 |
| `execution` rail row | `Prop` | 1 |
| `execution` editor | `Select` inside Edit mode | 1 |
| brake toggle | `Toggle` | 0 |
| runner control | `Button` + scope `Select` | 0 |
| triage decision on a ticket | one line of `Prop` text | 3 |
| a blocked ticket's question | existing Stuck section | 3 |

**Rules this work is most likely to break, each learned the expensive way:**

1. **A chip is for something you scan a LIST for.** One record on a page means
   almost nothing earns chip treatment. The `execution` value is a `Prop`, not
   a `Badge`.
2. **No checklist for readiness.** It was deleted as a nag panel on 2026-08-12.
   One line of prose, or a dot.
3. **Status commits on change; a spec commits on Save.** `execution` is part of
   the spec a worker executes, so it goes behind Edit mode with title and body.
   Readiness is a report and has no control at all.
4. **Properties belong high.** `DetailLayout` puts the rail first in the DOM so
   it does not stack to the bottom on a phone. Do not reorder it to fit a new
   row in.
5. **Four list states.** Any new list (the triage decisions review, if it gets
   one) needs loading, empty, error and overflow.
6. **No new inline hex, no emoji, lucide only.** `bash scripts/check_design.sh
   --added HEAD` gates it and `reviewer.sh` fails on `design_violations`.
7. **Disabled dims. Never `cursor: not-allowed`.**

**Before claiming any UI item done:** `npm run typecheck` from the repo root
(NOT `npx tsc`, which resolves a deprecated stub package and exits 0 having
checked nothing), `bash scripts/check_design.sh`, and `cd frontend && npm test`.
Then rebuild the public bundle, because the operator is on the built origin:
`bash scripts/build_workshop_public.sh`, proved with `bash scripts/ui_sync.sh`.

---

## 8. Test plan

Backend tests run with the venv: `cd backend && .venv/bin/python -m pytest`.
System python breaks collection on three files.

| area | test | asserts |
|---|---|---|
| schema | `test_triage_mark.py` | §1.3, especially that `awaiting` does not move `spec_hash` |
| schema | `test_execution_field.py` | unknown value coerces to `None`, not `code` |
| readiness | extend `test_ticket_readiness.py` | `readinessLine` wording for 1, 2, 3 and 4+ gaps |
| selection | `test_next_ready_unattended.py` | `unattended=True` skips unready and non-`code`; `unattended=False` returns them unchanged |
| expiry | `test_awaiting_expiry.py` | a record with no `kind` does NOT expire; `permission` does not; `input` does |
| triager | `test_queue_triage.py` | a pass over a ticket it just marked `underspecified` selects nothing on the second run |
| triager | same | caps hold: 20 looked at, 5 split, 5 questions |
| attempts | extend `test_question_roundtrip.py` | answering a triager-raised block does not decrement `attempts` |
| search | manual | a worker run log contains an `mcp__workshop__search` call |

The triager termination test is the one that matters most. Assert on the second
pass returning zero decisions, not on the mark being written, because writing
the mark and still re-selecting is exactly the bug.

**One environment trap for whoever writes these.** A test that writes a learning
must redirect BOTH stores: patching `learnings._dir` is not enough, because
`record()` also mirrors through `_legacy_md()` into the tracked
`shared/learnings.md`. The same class of mistake applies to tickets: a test that
auths as a real owner must redirect `ownership._RUNTIME` to `tmp_path` or it
writes into live stores.

---

## 9. What this spec does not cover

- **Phase 5 and autonomous commits.** Gated on SEC-A1 (a lower-privilege
  execution identity or a sandbox), which cannot be solved by design in this
  file.
- **`supersedes`.** Nothing expresses "this ticket replaced that one", so the
  July cohort still needs a human read before a runner sees it. Phase 1's
  classification pass is a partial substitute and not a replacement.
- **The full scope-vocabulary migration** (1b-ii). Named, deferred, not
  blocking.
- **The dry run (`--plan`).** A phase 5 prerequisite in the plan; genuinely
  useful earlier, but not required by anything in phases 0 to 4.
- **Concurrency and worktrees.** Whenever it comes, it arrives as worktree
  isolation, not a smarter lock. C3 (the local-merge completion path) is the
  prerequisite and is already ticketed.
- **Mid-run steering.** The plan flags the field as genuinely divided on it.
  Nothing here builds it.
