# Implementation plan: how an agent learns the platform's vocabulary

**Status: plan, 2026-08-24.** Turns section 4 of
`work-status-contract-2026-08-24.md` into work a worker can execute. The shape
is already settled by `research-agent-context-and-tool-docs-2026-08-24.md` (two
tier progressive disclosure; the justification is signal to noise, not cost,
because cache reads bill at 0.1x). This document does not relitigate that. It
says which files change, what the text is, what it costs, and how anyone would
tell whether it worked.

The operator's four requirements, which every section below is measured against:

- **R1** each surface, endpoint or tool carries its own explainer or reference
- **R2** it is NOT re-read and reloaded into context every turn
- **R3** the agent is aware of the tool and how it works BEFORE it calls it
- **R4** it is NOT hardcoded into every agent's system prompt

---

## 0. Measurements

All figures read from the tree on 2026-08-24. Token estimates use the repo's own
4-chars-per-token convention (`routes/chat._CHARS_PER_TOKEN`), so they are
comparable to the numbers the prompt ceiling already uses.

### 0.1 The fixed prompt a tools-active chat carries every turn

Assembled by `routes/chat._build_prompt` (line 1508 onward), in this order.
Everything down to the custom instructions is described in-tree as the
"cache-stable prefix".

| Block | Source | chars | ~tokens |
|---|---|---:|---:|
| L0 kernel | `chat_context.PLATFORM_KERNEL` | 928 | 232 |
| Role preamble (whole) | `chat_context.role_preamble(tools_active=True)` | 8,801 | 2,200 |
| ... of which delegation | `chat_context.delegation_block(True)` | 4,295 | 1,074 |
| ... of which UI cards | `chat_context.UI_CARD_PROTOCOL` | 3,220 | 805 |
| ... of which style | `chat_context._STYLE_RULE` | 623 | 156 |
| ... of which capabilities + egress | inline in `role_preamble` | 663 | 166 |
| L1 orientation | `orientation.manifest_text("workshop")` | 1,039 | 260 |
| **Platform-owned fixed total** | | **10,768** | **2,692** |

A tools-off chat pays 4,207 chars (~1,052 tokens) for the role preamble instead
and gets no orientation block at all.

On top of that, and varying per chat rather than per turn: `provider.persona()`
(L2), `learnings_context`, `ambient_digest`, `working_state_block` (capped at
~1,200 tokens), the layered knowledge index (500 token budget), the skills
name+description block, and `custom_prompt()` (L3). The research's "1,500 to
3,000+ tokens" estimate was low for the platform half: the measured platform
half alone is 2,692.

**There is no explicit cache breakpoint in this repo.** Grep for
`cache_control` / `ephemeral` across `backend/` returns only edge HTTP headers
and vendored packages. The prompt is a single joined string handed to the
`claude` CLI, and cache stability is achieved by ORDERING alone: stable blocks
first, then the conversation, then a "volatile tail" (`live_context`, then the
`=== NOW REPLY ===` user turn) that is deliberately placed last so it cannot
invalidate anything above it. `workspace_digest` goes as far as dropping each
task's status tag from the ambient block specifically because a status flip
would reshuffle bytes in the cached prefix. So "which blocks sit before and
after the breakpoint" has a real answer even though no breakpoint is declared:
everything through `=== CONVERSATION SO FAR ===` is intended to be stable,
everything after it is not.

### 0.2 The MCP tool block

`backend/mcp/workshop_query_server.py` assembles `tools/list` in `_tool_list()`
(line 490). Five built-ins are always listed; `spawn_background` is appended
only when `WORKSHOP_QUERY_THREAD` is bound; then every declared `command` tool
from `services/command_tools`.

| Tool | description chars | ~tokens |
|---|---:|---:|
| `query` | 289 | 72 |
| `search` | 555 | 139 |
| `spawn_background` | 661 | 165 |
| `where_is` | 255 | 64 |
| `list_attachments` | 309 | 77 |
| `get_file` | 333 | 83 |

Serialized whole (descriptions plus JSON Schemas, which is what the model
actually pays for): **3,293 chars / ~823 tokens** for the five-tool list,
**4,383 chars / ~1,096 tokens** with `spawn_background` bound.

None of the six mentions ticket state. `query`'s description says it summarizes
"open tasks" and never says what open excludes.

### 0.3 The state vocabulary today

`backend/services/tasks.py` lines 141 to 196:

```python
STATUSES = {"draft", "todo", "doing", "in_review", "blocked", "parked", "done",
            "cancelled"}
TERMINAL = {"done", "cancelled"}
STATE_TYPES = ("backlog", "unstarted", "started", "completed", "canceled")
_STATE_TYPE = {...}          # the eight onto Linear's five
def state_type(status) -> str
```

The MEANINGS exist only as a four-line comment above `STATUSES` (draft,
in_review, parked, cancelled are glossed; todo, doing, blocked and **done** are
not). Nothing agent-facing reads any of it.

**The token list is already in more than one place**, which matters for the
"exactly one source" requirement:

1. `services/tasks.py` `STATUSES` (the JSON store's authority).
2. `db/legacy_map.py` `STATES`, eight `(token, name, category, position)`
   tuples, imported by `db/backfill.py`, `db/repo.py` and `db/ticket_sync.py`.
   Its own docstring says three copies would be the drift this project exists
   to remove, and it is the fix for that. It is a fourth copy relative to
   `tasks.py`, and nothing asserts the two agree.
3. Migration `966ddcaa35ae` seeds the same eight `workflow_state` rows.
   `legacy_map`'s docstring says the migration "must agree with this file";
   only the backfill's fidelity check enforces it, and only when a backfill
   runs.

`db/models.py` line 63 already does `from services.tasks import EFFORTS,
STATE_TYPES as STATE_CATEGORIES`, so a `db` module importing `services.tasks`
for a vocabulary is established precedent, not a new dependency direction.

### 0.4 The committed / merged / accepted axis

It does not exist as text anywhere. It exists as DATA:

- `services/changes.py` `_shape()` already returns `branch_slug`, `head_sha`,
  `ticket_ids`, `status` (`draft|ready|changes_requested|approved|merged|closed`
  from `db/models.CHANGE_STATUSES`), `merged_sha`, `merged_at`, `merged_by`,
  `merge_basis`. This is GitHub's shape already, orthogonal fields rather than
  an overloaded enum.
- `db/change_repo.list_changes(ticket_id=...)` filters by array containment on
  `ticket_ids` and rides a GIN index. `db/models.py` line 1348 calls this "the
  query C2's status derivation runs", so the index was built for exactly this.
- `services/work_review.py` is the only module allowed to import both tickets
  and changes, by the layering rule it cites
  (`workspace-ownership-and-leases-2026-08-22.md` sec 13.2). It already has
  `change_for_task(task_id)`.

So the derivation is buildable today. What is missing is prose and a carrier.

### 0.5 Where an agent can currently see a ticket

| Surface | Carries `status`? | Carries landed-ness? |
|---|---|---|
| `query` digest (`workspace_digest`) | **No.** Dropped on purpose for cache stability | No |
| `search` kind=ticket (`semantic_index.collect_tickets`) | No, chunks are title + body | No |
| `where_is` / `orientation.manifest_text` | No | No |
| `scripts/project_tasks.py` (`_slim`, line 144) | Yes | No |
| `routes/tasks_all` (`_with_triage`, line 134) | Yes, plus derived `state_type`, `ready`, `blocked_by`, `readiness` | No |
| `services/refs._resolve_ticket` | Yes, for the UI chip | No |

### 0.6 `refs.py` and the fetch-a-document call

`services/refs.py` is the one parser/serializer for `workshop:<kind>/<key>` and
`resolve_ref()` returns `{kind, title, href, status}`. It **resolves** a ref to
a title and a link. It does not return content.

**`open_ref` does not exist.** Grep for it across `.py`, `.ts`, `.tsx` and `.md`
returns only design docs: `the-plan-2026-08-23.md` line 166 and
`finding-and-surfacing-memory-2026-08-23.md` lines 220, 247, 373, 520 to 523,
579, where it is proposed as `open_ref(ref, part=None)` and named a precondition
for measuring retrieval. Section 4 of the work-status-contract doc says the
contract is fetchable "through the `open_ref` style call already designed",
which is true of the design and not of the tree. **Any plan whose tier two
depends on `open_ref` is blocked on unbuilt work.** Section 3 below does not.

### 0.7 Prior art in this repo for exactly this pattern

`services/skills.py` already implements two-tier progressive disclosure, and
says so in its module docstring: `descriptions_block()` puts each skill's
name+description into the cache-stable prefix, `get_skill()` loads the full
body on demand when the model emits the `use_skill` block described by
`chat_context.USE_SKILL_PROTOCOL` (362 chars / ~90 tokens). The plan below is
the same pattern with a different fetch verb, so it is not a new idea in this
codebase, it is an existing one applied to a second thing.

`services/orientation.py` (62 lines) is the other half of the precedent. Its
docstring already states the property this plan needs: "This module is the
single source of truth shared by the prompt injection
(`chat_context.orientation_manifest`) and the `where_is` MCP tool". One string,
two carriers, one of them always loaded and one of them fetched. Nobody has
said out loud that this is the right home for tier one. It is.

### 0.8 The one surface orientation does not reach

The unattended sweep. `scripts/project_ralph.sh` line 1297 fires
`scripts/ralph.sh "$TASK_TEXT"`, and the worker's fixed prompt is
`RALPH_INSTRUCTIONS` (2,450 chars / ~612 tokens, hardcoded in `ralph.sh` line
351). It never goes through `chat_context._build_prompt`, so it gets NO L0
kernel, NO role preamble and NO orientation manifest. What it does get is the
full workshop MCP server: `ralph.sh` line 573 shells `services.llm_router
mcp-config --server workshop --stdout` and passes it as `--mcp-config`, so the
worker's `tools/list` is the same six-tool list measured in 0.2.

This is the sweep that produced the original defect. It means **tier one must
ride the MCP tool descriptions, not only the injected manifest**, or the one
agent that most needs the contract is the one agent that cannot see it.

---

## 1. Where the contract lives

**One fact, one owner module. Assembly is not duplication.**

The contract has two halves that cannot honestly live in the same module,
because `services/tasks.py` may not learn what a change is (the layering rule in
`work_review.py`'s docstring, and `tasks.py` importing `changes` would invert
the dependency that `db/models.py` already relies on).

**W1. The eight states and what each means: `backend/services/tasks.py`.**
Beside `STATUSES`, add one dict and one guard:

```python
# What each state CLAIMS. One line each, and this is the only place any of it
# is written: db/models.py already imports STATE_TYPES from here, and
# services/orientation.py imports this to build the agent-facing contract.
STATUS_MEANING = {
    "draft":     "filed, not yet ready to run. Nothing will pick it up.",
    "todo":      "ready to run, unclaimed.",
    "doing":     "claimed by a worker that holds its lease.",
    "in_review": "the worker says it finished. The operator is the gate. "
                 "This says nothing about where the code is.",
    "blocked":   "started and stuck on something outside the ticket.",
    "parked":    "dead-letter: exhausted its attempt cap. Still open work, "
                 "but next_ready will never pick it.",
    "done":      "<decided by D1, see section 8>",
    "cancelled": "dropped. Nobody is going to do it.",
}
assert set(STATUS_MEANING) == STATUSES, "STATUS_MEANING must cover STATUSES exactly"
```

The `assert` is the whole point: a ninth status cannot be added without writing
what it means, and a meaning cannot outlive its status. Format is a plain dict
of str to str, not a doc, not YAML, not a database row, because the CHECK
constraint and the JSON store both already read Python from this file.

Delete nothing. The existing four-line comment above `STATUSES` becomes
redundant prose and should be replaced by a pointer to `STATUS_MEANING`, so
there is not a comment and a dict saying the same thing differently.

**W2. Committed vs merged vs accepted: `backend/services/work_review.py`.**
A module-level string constant, no database access at import:

```python
LANDED_CONTRACT = (
    "A ticket's state and where its code is are two different questions.\n"
    "- COMMITTED: a worker wrote and committed the change on its own branch. "
    "The branch exists, the trunk does not have it.\n"
    "- MERGED: that branch reached the trunk. This is recorded on the CHANGE "
    "(merged_sha, merged_at), never on the ticket.\n"
    "- ACCEPTED: a person agreed the work is right. Accepting is what merges.\n"
    "An unsupervised sweep merges on success, so accepted and merged coincide "
    "there. A supervised run leaves the branch unmerged and the ticket "
    "in_review, so the work can be committed and complete and NOT on the "
    "trunk. That combination is what produced the ten unmerged branches.\n"
    "Where a ticket has no change at all (work committed straight to the tree, "
    "or a scope with no change store) the answer is NOT APPLICABLE, which is a "
    "different fact from NOT MERGED and must never be shown as one."
)
```

This module is already the only one that imports both `changes` and ticket
concepts, and it already documents the supervised/unsupervised asymmetry in its
own docstring. Putting the prose anywhere else creates a second place that has
to know it.

**W3. Assembly: `backend/services/orientation.py`.** It imports both, lazily and
best-effort (importing `work_review` pulls `changes` which pulls SQLAlchemy; a
file-backed checkout must still render a manifest, exactly as
`chat_context.orientation_manifest` already swallows failures). It exposes:

```python
def contract_text(topic: str) -> str      # the full contract for one topic
def contract_line(topic: str) -> str      # the one-line tier-one reference
TOPICS: dict[str, Callable[[], str]]      # {"ticket-state": _ticket_state_contract}
```

Nothing else in the tree may write the words "in_review" or "merged" into
agent-facing text. That is checkable (section 7).

**What this plan does NOT unify, and why.** `db/legacy_map.STATES` keeps its own
eight tuples. Making it import `tasks.STATUSES` is a real improvement and it is
listed as decision D2, not done here: a migration is frozen against those exact
literals, and changing the seed source in the same change as the vocabulary
work would mix a schema question into a prompt question. What this plan DOES
add is a test that the two agree (section 7, A2), which is the check that was
missing.

---

## 2. Tier one, always loaded

**Home: `services/orientation.py`, and from there into two carriers.** It is the
right home for the reason its own docstring gives: it is already the one string
shared by the always-injected prompt block and the on-demand `where_is` result.
Tier one needs to be in both places and must not be written twice.

**W4. Carrier A, the injected manifest.** `orientation.manifest_text()` gains
one bullet, from `contract_line("ticket-state")`:

```
- Ticket states (draft/todo/doing/in_review/blocked/parked/done/cancelled):
  `done` describes the TICKET, not the trunk. A ticket you are handed carries
  `work_location` for where its code actually is. Full contract: `where_is`
  topic "ticket-state".
```

**Measured: 247 chars / ~62 tokens.** The manifest goes from 1,039 to 1,286
chars (~260 to ~322 tokens). The platform-owned fixed prefix goes from 10,768
to 11,015 chars, **2,692 to 2,754 tokens, +2.3%**.

The status tokens in that line are rendered from `sorted(tasks.STATUSES)` in the
store's own order, not typed as a literal, or the line becomes copy number five.

**W5. Carrier B, the MCP tool descriptions.** In `_tool_list()`, append
`contract_line("ticket-state")` in its one-sentence form to `TOOL_DESC` (the
`query` tool) and to `WHERE_IS_TOOL_DESC`:

```
States: draft/todo/doing/in_review/blocked/parked/done/cancelled. `done`
describes the ticket, not the trunk; call `where_is` topic "ticket-state" for
what each claims.
```

**Measured: 169 chars / ~42 tokens**, times two carriers, plus ~58 tokens for
the new `topic` property on `WHERE_IS_TOOL_SCHEMA`. The six-tool block goes from
4,383 to about 4,950 chars, **~1,096 to ~1,238 tokens, +13.0%**.

Build it inside `_tool_list()` with a try/except falling back to the static
description, matching how that function already tolerates a broken
`command_tools` catalog. Do not import `services.orientation` at module scope:
this server is a per-turn stdio subprocess and its every other service import is
lazy on purpose.

**Total tier-one cost, measured not guessed: ~204 tokens per turn** for a chat
that has both carriers (62 injected + 84 in two descriptions + 58 schema),
against a ~3,788-token platform floor (2,692 fixed prompt + 1,096 tools).
**5.4%.** For a ralph sweep worker, which has no injected manifest, it is ~142
tokens against a ~1,708-token floor (612 instructions + 1,096 tools).

R3 is satisfied for both surfaces. R4 is satisfied: no persona, no
`RALPH_INSTRUCTIONS`, no per-agent text changes.

---

## 3. Tier two, on demand

**Use the call that exists. `where_is` gains one optional argument.**

```
where_is(topic: str | None = None) -> str
```

- `where_is()` returns the orientation map, byte-identical to today plus the
  W4 line. No caller changes, no regression.
- `where_is(topic="ticket-state")` returns `orientation.contract_text(
  "ticket-state")`: the eight states with `STATUS_MEANING`'s line for each, then
  `work_review.LANDED_CONTRACT`, then one line on how to read `work_location`.
- An unknown topic returns the list of known topics and never raises. A tool
  that errors on a near-miss spends the agent a turn; one that answers with its
  own index spends it nothing.

Schema addition:

```python
WHERE_IS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": ("Optional. Return ONE contract in full instead of "
                            "the map. Topics are listed in the map; "
                            "'ticket-state' is what each ticket state claims "
                            "and what it does not claim about the trunk."),
        },
    },
    "additionalProperties": False,
}
```

`_run_where_is(args)` takes the args dict it is currently called without
(`workshop_query_server.py` line 597).

**Why not `open_ref`.** It does not exist (0.6). Adopting it here would make a
two-file change into a dependency on `finding-and-surfacing-memory-2026-08-23`
step 2. When `open_ref` does land it should resolve
`workshop:contract/ticket-state` to the same `orientation.contract_text` call,
which is one line in its resolver and costs nothing to defer.

**Why not a new tool.** The MCP server's own comment at line 448 argues against
a second server for a reason that applies just as well to a seventh tool: every
tool added is paid in the fixed `tools` block on every turn by every agent. A
new `ticket_contract` tool would cost roughly as much always-loaded description
as the contract line itself, to buy nothing `where_is` does not already do.

**Why not an MCP resource.** The research's option F is the spec-correct answer
and this server declares no `resources` capability and implements tools by hand
over stdio. Adding the primitive is more protocol surface than the whole
feature. If the server ever grows resources, the contract should move; nothing
here would need rewriting because the text lives in `orientation.py` either way.

**Measured cost of tier two: zero on turns that do not fetch it**, and one tool
result on turns that do (`contract_text("ticket-state")` will land around 1,600
to 2,000 chars, ~400 to 500 tokens, once D1 fixes the `done` line). That is R2,
literally.

---

## 4. The data explains itself

The cheapest and most reliable of the three, because it arrives exactly when it
is relevant and cannot be forgotten. GitHub's shape, not a ninth status.

**W6. `work_review.work_locations(task_ids) -> dict[str, dict]`.** One
`changes.list_changes(limit=1000)` call, indexed by each row's `ticket_ids`
(rows come back newest-activity-first, so the first match for a ticket wins).
Returns `{}` on `ChangesUnavailable` rather than raising, matching
`change_for_task`'s existing treatment of "no relational store" as "there is
nothing to merge". A single-ticket `work_location(task_id)` wraps it.

Payload, attached to the outgoing ticket record and **never stored**, exactly
the discipline `routes/tasks_all._with_triage` already documents for
`state_type` ("HERE, not in the store"):

```json
"work_location": {
  "change_id": "018f...",
  "branch": "wt/workshop/fix-the-thing",
  "merged": false,
  "merged_at": null,
  "note": "committed on a branch, NOT merged into the trunk"
}
```

- `merged: null` means **not applicable**: no change record exists for this
  ticket. `note` says which kind of not-applicable it is (no change store in
  this scope, or the work was committed straight to the tree). Section 3.2 of
  the work-status-contract doc requires that this read differently from "not
  merged"; a null plus its own sentence is how.
- `note` is generated from the other fields, never hand-written per caller, so
  it cannot drift from them.
- No `status_meaning` string per row. The state's meaning is tier two and does
  not belong repeated on every row of a list; only the fact that is per-ticket
  goes per-ticket.

**Serializers that change (two, both single-hook):**

1. `scripts/project_tasks.py::_slim` (line 144). One added key. This covers
   `next-open`, `next-ready`, `add`, `set-status`, `claim`, `heartbeat`, `note`
   and `block` in one edit, which is every verb `project_ralph.sh` and a maker
   agent's Bash use. `list-json` (line 271) prints raw records and needs the
   batched call applied to the list.
2. `backend/routes/tasks_all.py`. Apply `work_locations()` once per request over
   the assembled rows, then stamp, beside where `_with_triage` is applied (the
   list route around line 390 and the patch/detail path at line 718). Do NOT put
   it inside `_with_triage` itself: that runs per row, and a per-row
   `list_changes` is an N+1 on a polled endpoint.

**Serializers that deliberately do NOT change:**

- `services/workspace_digest.py`. Its module docstring forbids adding volatile
  per-task state to the ambient block, and it already dropped the status tag for
  that reason. A merge flipping would reshuffle the cached prefix. The digest
  answers "what work exists"; `work_location` answers "where is this one",
  which is a question you ask about a ticket you already have.
- `services/semantic_index.collect_tickets`. Chunks are title plus body, built
  offline. A database lookup per hit at search time is a real cost for a field
  the agent can get from the ticket itself.
- `services/refs._resolve_ticket`. It feeds UI chips, not agents. Adding
  `merged` there is a reasonable follow-up for the frontend and is not part of
  this plan.

---

## 5. How it generalises

The rule, stated so a later engineer can apply it without reading this document:

> **The two-tier rule.** Any platform surface an agent has to understand before
> using it carries its explainer in two tiers.
>
> 1. **The full contract is prose owned by the module that owns the fact.** Not
>    a doc, not a prompt constant: a module-level string or dict in the same
>    file as the data it describes, so it cannot be changed without the change
>    being in the same diff. If a contract spans two modules, each owns its own
>    half and neither restates the other's.
> 2. **`services/orientation.py` assembles and serves it.** Register the topic
>    in `orientation.TOPICS`; write ONE line for
>    `orientation.contract_line(<topic>)`, under 70 tokens, which names the
>    vocabulary, names the single most common misreading, and names the fetch.
>    The line goes into `manifest_text()` and into the description of whichever
>    tools touch the surface. The full text is reached by `where_is(topic=...)`.
> 3. **If the surface returns records, the record carries the per-record fact.**
>    Derived on the way out in the route or CLI layer, never stored, with a
>    generated one-line `note`. Per-record facts go on records; vocabulary does
>    not.
> 4. **Nothing goes into `chat_context.py`, a persona, `AGENTS.md`, or
>    `RALPH_INSTRUCTIONS`.** Those are L0 to L3 prompt layers. A surface's
>    explainer is not a prompt layer, and CLAUDE.md's four-layer contract
>    already forbids installation-specific facts in L0 to L2.

Cost of adding a surface under this rule: about 60 tokens on every tools-active
turn, once, plus zero until fetched. Budget it: if `manifest_text()` ever passes
roughly 600 tokens (it is 260 today, 322 after this change), the index itself
has become the noise the pattern exists to avoid, and topics should move behind
a listing call rather than being enumerated inline.

---

## 6. What not to do

- **Do not add the vocabulary to `PLATFORM_KERNEL` or any system prompt.** It is
  research option E. It is nearly free on cost (0.1x cache reads) and wrong on
  relevance: it pays attention budget on every turn system-wide, and most turns
  never touch a ticket. It also violates CLAUDE.md's rule that L0 to L2 carry
  only generalized product doctrine, and R1 and R4 directly.
- **Do not paste the contract into each tool description.** Research option B.
  It satisfies R3 and breaks R1 in spirit: N copies of one explainer is the
  drift `db/legacy_map.py` was written to end. One SENTENCE per tool that points
  at one contract is not the same thing as N copies of the contract.
- **Do not write the eight tokens as a literal anywhere new.** Render from
  `tasks.STATUSES`. There are already four copies (0.3) and
  `scripts/check_one_definition.py` exists specifically because "six mappers
  with two already drifted is not a discipline problem, it is a missing test".
- **Do not add a ninth status.** `merged` as a status collides with `done` the
  first time work is merged but not accepted, or accepted but not merged, and
  both happen here. Orthogonal facts get orthogonal fields (research section 6,
  work-status-contract section 2).
- **Do not add `work_location` to the ambient digest.** Section 4 says why: it
  is the one block whose bytes are engineered to not change.
- **Do not depend on `open_ref`.** It is designed and unbuilt (0.6).
- **Do not touch `RALPH_INSTRUCTIONS`.** Adding the vocabulary there is
  per-agent hardcoding by another name, and it would be a fifth copy that only
  one of the several worker paths sees.
- **Do not let the short line get vague.** The research's honest tradeoff: a bad
  short description means the model never asks for the long one and the contract
  is invisible again. The line must name the concrete misreading ("`done`
  describes the ticket, not the trunk"), not gesture at "state semantics".

---

## 7. Acceptance

Deterministic first, because this repo can run those.

- **A1. One definition holds.**
  `python3 scripts/check_one_definition.py --only-changed HEAD` reports no new
  duplicate group. This gate finds duplicate value-sets BY VALUE across Python
  and TypeScript, so a new literal list of the eight tokens fails it wherever it
  is written.
- **A2. The two existing copies are pinned together.** New test
  `backend/tests/test_status_vocabulary_is_one.py`:
  `{t for t, _, _, _ in legacy_map.STATES} == tasks.STATUSES`, and
  `{c for _, _, c, _ in legacy_map.STATES} <= set(tasks.STATE_TYPES)`, and
  `set(tasks.STATUS_MEANING) == tasks.STATUSES`. This assertion does not exist
  today and its absence is why the copies could drift silently.
- **A3. Nothing else writes the vocabulary.**
  `grep -rn "in_review" backend/services/chat_context.py backend/mcp/ scripts/ralph.sh`
  returns only `orientation`-derived text. A one-line addition to
  `scripts/check_no_app_names.py`'s sibling family, or a plain test asserting
  `"in_review" not in chat_context.PLATFORM_KERNEL + chat_context.role_preamble(
  tools_active=True)`, keeps it that way.
- **A4. The measured budget.** Test asserting
  `len(orientation.manifest_text("workshop")) <= 1,350` (baseline 1,039, this
  change 1,286) and that `chat_context.PLATFORM_KERNEL` is byte-identical to
  today. If a later surface pushes the manifest past the cap, the test says so
  before the prompt does.
- **A5. Both tiers answer.**
  `where_is()` output contains the tier-one line and every one of the eight
  tokens; `where_is(topic="ticket-state")` output contains all eight tokens AND
  the words committed, merged and accepted; `where_is(topic="nonsense")`
  returns a topic list and does not raise.
- **A6. The data explains itself.** Against a fixture ticket with a merged
  change: `project_tasks.py next-ready` JSON has
  `work_location.merged is True` and a non-null `merged_at`. Against one with an
  open change: `merged is False` and a `note` containing "NOT merged". Against a
  file-backed scope: `merged is None` and a `note` that does not contain the
  word "not merged".

Then the one that answers the operator's actual question.

- **A7. The behavioural check.** Open a tools-active chat in a scope with a
  ticket whose change is committed and unmerged. Ask: "is ticket X shipped?"
  Correct answer names the branch and says it has not reached the trunk.
  Precondition, asserted by A3 before the run: the vocabulary is in no persona
  and no system prompt, so a correct answer can only have come from the tier-one
  line, the `where_is` fetch, or the `work_location` field. Record which of the
  three the transcript shows, because that is the number that tells you whether
  tier one's wording is good enough to trigger a fetch, which is the pattern's
  one real failure mode.

  **Honest limit:** there is no eval harness in this repo. A7 is a manual
  one-shot with a recorded transcript, not a test. A6 plus A5 are the automatable
  proxy, and they prove the information is REACHABLE, not that it was used.

---

## 8. Decisions I cannot make

Each of these blocks a specific line of the plan. None of them blocks the rest.

- **D1. What does `done` claim?** This is the one that blocks writing the actual
  contract text, and it is the operator's call, not an engineering one. Today
  `done` is reached by two paths with different trunk implications: an
  unsupervised sweep merges on success and then completes the ticket, while a
  supervised run leaves the branch unmerged (`work_review.py` docstring). So
  `done` currently means "finished" in one mode and "merged" in the other.
  Options: (a) `done` = the operator accepted it, and the change merged or there
  was never one, which makes the current unmerged-branch state a contradiction
  to be refused; (b) `done` = the worker's work is finished, with landed-ness
  read only from `work_location`, which is cheaper and leaves "done but not on
  main" a legal, visible state. My reading of the work-status-contract doc's
  section 3.2 is that the operator wants (a), but it says so about a REFUSAL
  that is blocked on the B3 lease refactor, and writing (a) into the contract
  before the refusal exists documents a rule the system does not enforce. I will
  not guess. `STATUS_MEANING["done"]` is a placeholder until this is answered.
- **D2. Should `db/legacy_map.STATES` import its tokens from
  `tasks.STATUSES`?** Precedent exists (`db/models.py` line 63). Against: a
  migration is frozen against those literals and a seed that moves under a
  migration is its own hazard. This plan ships the assertion (A2) either way,
  which is strictly better than today; making it one literal is a follow-up
  with a schema question attached.
- **D3. `where_is(topic=...)` or wait for `open_ref`?** I recommend `where_is`
  and have specified it that way, because it ships in two files against zero
  unbuilt dependencies. If the operator wants one universal fetch verb for
  everything addressable, tier two becomes a dependency on
  `finding-and-surfacing-memory-2026-08-23` step 2 and this plan waits.
- **D4. Does the sweep worker get more than the tool description?** It has no
  injected manifest (0.8). I have deliberately not touched `RALPH_INSTRUCTIONS`
  because that is the hardcoding the operator forbade, which leaves the sweep
  worker's awareness thinner than a chat's: the tool description plus
  `work_location` on the ticket JSON it is handed. The principled fix is for
  `ralph.sh` to inject `orientation.manifest_text()` the same way it already
  injects the search paragraph, which is one more shell block and a ~322-token
  addition to a 612-token prompt. That is a real cost and a real change to a
  path that runs unattended, so it is a decision rather than a default.
- **D5. Is `work_location` worth it before the change store is universal?** See
  the self-assessment. It answers `merged: null` for most tickets today.

---

## 9. Is this implementable today

**Sections 2, 3 and 5: yes, fully, now.** Five files, no unbuilt dependencies,
no schema change: `services/orientation.py`, `services/tasks.py`,
`services/work_review.py`, `mcp/workshop_query_server.py`, plus tests. The
numbers in section 2 are measured against the current tree. `where_is` already
exists, is already read-on-demand, and is already backed by the module that
already claims to be the single source shared between prompt and tool. This is
a small change that mostly consists of connecting two things that were built to
connect.

**Section 1: yes, with a hole where D1 goes.** Everything except one dict value
can be written today. `STATUS_MEANING["done"]` cannot, and it happens to be the
exact state the operator asked about. A worker can land W1 to W5 with a
placeholder and a `[~] BLOCKED` sub-task for the `done` line, or wait. I would
land the mechanism and block on the sentence, since the mechanism is what makes
the sentence reachable once it exists.

**Section 4: yes to build, thin in effect, and this is the honest caveat.**
`work_location` is a real derivation over real columns with a real index. But it
will return `merged: null` for most tickets in this tree today, for two
compounding reasons. First, only two scopes read tickets from Postgres at all
(`project_tasks.py`'s own re-exec comment); every other scope is file-backed and
has no change store. Second, even in a relational scope, `work_review.py` says a
change is opened only for a SUPERVISED sweep in WORKTREE mode. An unsupervised
sweep merges and opens nothing; a non-worktree run has nothing to open. So the
field is correct, honest and mostly "not applicable" until the change store
covers more of the system. That is not a reason to skip it, since "not
applicable" stated clearly is exactly what the ten-unmerged-branches incident
lacked, but anyone expecting it to light up across the ticket list will be
disappointed, and it should not be sold as the fix on its own.

**What this plan does NOT fix, and must not be described as fixing.** The
original defect was a ticket reading `done` while `main` lacked the fix. This
plan makes that contradiction VISIBLE and makes the vocabulary REACHABLE. It
does not refuse the write. The refusal is section 3.2 of
`work-status-contract-2026-08-24.md` and depends on the B3 lease refactor, which
is named as a blocker in `work_review.py`'s own docstring and is not scoped
here. A worker who lands all of W1 to W6 has closed the "agents cannot learn the
vocabulary" gap and has not closed the "a worker can write a false status" gap.
Two problems, two changes, and conflating them is how the first one gets marked
done while the second is still true, which is the same failure mode this
document is about.

**Overall: a competent worker could execute W1 to W6 in one sitting**, minus
D1's sentence. The plan's weakest point is not the code, it is A7: nothing in
this repo can automatically prove that an agent USED the contract rather than
merely being able to reach it, and the whole argument for progressive disclosure
over always-loading rests on that distinction.
