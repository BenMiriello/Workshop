# Final plan: generalize the Agent primitive and fold the bridge agent in (2026-07-16)

Author: fable (design-review architect)
Status: plan of record. Merges tasks 5c9223c0 (generalize agent) and da801d52
(fold the bridge agent in, revisit clawd rename). Executes the operator's 2026-07-16
mandate: FULLY implement, nothing half-done, nothing hardcoded for appearances,
every overlapping subsystem PORTED or REMOVED, and testing left OFF at the end.

This doc is the plan a coding agent will follow after operator approval. Every
substantive claim about the current tree cites file:line. Phases are ordered so
each is independently reviewable and ships as its own merge unit. The plan does
not build anything explicitly rejected in the synthesis (part 6 of
`synthesis-agent-protocols-2026-07-16.md`); those exclusions are named again in
section 10 so silence is not mistaken for oversight.

Referenced docs (all in `docs/platform-redesign/`): the four 2026-07-16 thread
docs (`operator-asks`, `_brief`, `resource-access-protocol`,
`agent-roles-research`, `scopedness-and-escalation`, `synthesis`),
`unified-agent-model-2026-07-15.md`, `orchestration-and-autonomy.md`,
`concept-schema-2026-07-16.md`, `review-findings-2026-07-15.md` (F3).

---

## 1. Executive summary

- The operator implicitly answered the one gating decision from the 2026-07-16
  thread with "yes generalize agent": **an autonomous agent becomes a third
  `Identity.kind` (`agent`) with zero ambient authority**. Grants are minted at
  dispatch, not inherited from the operator's session.
- The four live defects in the shipped tree (D1-D4 in synthesis Part 1, plus
  F3 review-finding prereq) are repaired in P0 as one small merge unit. This
  unblocks the ask; it does not require any of the contested decisions.
- The `Agent` primitive keeps its current schema (`agents_registry.py`) and
  gains three fields the tree already half-anticipates: a closed-enum `role`
  (validated), a `latitude` string (cascades invocation to task to scope), and
  standardized `tool grants` (already there via `capabilities`, wired into
  `mint_grant`).
- Five agents currently register but never run because
  `dispatch_triggers.sh:329` hardcodes `role == "memory"`. P0 fixes that with a
  role-dispatch table. `workshop-overseer` is retired; `email-triager` and
  `plaid-watcher` stay registered and dispatch under the new table.
- The bridge agent folds IN as an Agent instance (`slug=the bridge agent`, event trigger from
  Discord, autonomy=continuous, grants for Discord + Plaid + google-docs
  tools). The Discord bridge stays as a THIN event source at `~/clawdbot/` (a
  three-line poster to the workshop event bus); the persistent `claude -p`
  loop, the tmux session, and the discord-handler subagent all move into the
  workshop's shared pipeline. This shrinks the the bridge agent surface from ~9k LoC of
  runtime + prose to a slug plus a persona plus one trigger.
- The custom Plaid MCP at `~/clawdbot/plaid-mcp/` MOVES into the workshop tree
  (`backend/mcp/plaid/`), registered as a Store, gated by the resource-grant
  path already in `permissions.py:616-728`.
- The `clawd` naming decision is DEFERRED again, but tightened: the plan
  identifies the exact surfaces that would change under any rename
  (`clawdbot/`, `the-bridge.service`, `~/.config/clawdbot/`) and locks the
  service names to the current values so nothing else names them.
- The block-and-review loop lands in P2 as a Review tab on the Work surface;
  rule minting comes out of `learnings.py` provenance (three independent blocks
  proposes a promotion). The prerequisite F3 fix (bare-slug learnings scope
  falling back to `global`) is included in P0.
- Testing at the end is light and leaves autonomy OFF: the
  `.runtime/AUTONOMY_DISABLED` flag is present, `the-bridge.service` is
  disabled, `crontab -l` still fires cron_agent but every dispatched sweep
  short-circuits on the flag.

---

## 2. Ground truth: what the current tree actually does

Every claim below was verified by reading the cited file. Nothing is quoted
from memory.

### 2.1 Identity model

- `backend/services/permissions.py:71-90`. `Identity.kind` is a bare string
  with two documented values: `operator` (default-permissive; `authorize()`
  always returns `allow` or `confirm`) and `guest` (the only gated caller).
- `permissions.py:96-129`. `resolve_identity()` defaults to
  `OPERATOR_IDENTITY` for `None`, unrecognized strings, or unrecognized
  dicts. The comment at :127 is explicit: "Bare unknown string: be permissive,
  treat as operator." This is the operator hard rule ("no defensive cruft for
  single-user") encoded in code, and it is correct for the human. It is wrong
  for an unattended background process ingesting hostile input, which today
  runs as `operator` and therefore holds ambient authority.
- `permissions.py:323-393`. `authorize(operator, ...)` short-circuits at :354
  to `allow`/`confirm` unconditionally. Guest is the only path with any real
  gating.
- `permissions.py:616-644`. `mint_grant()` writes a resource-scoped grant to
  `backend/resource-grants.json`. IS WIRED into `backend/routes/grants.py:64`.
- `permissions.py:647-677`. `attenuate()` narrows an existing grant. GREP FOR
  CALL SITES: zero. Dead code today.
- `permissions.py:710-728`. `check(token, uri, verb)` mechanically enforces a
  minted grant. No caller invokes it either; the resource-grant path is a
  facade waiting for the broker.

### 2.2 Agent primitive

- `backend/services/agents_registry.py`. One canonical `Agent` concept with
  fields: `slug`, `runtime` (`builder|assistant`), `name`, `purpose`,
  `capabilities`, `triggers`, `limits`, `role`, `depth`, `fanout`, `memory`,
  `project_slug`, `letta_agent_id`, `scope`, `definition`, `definition_path`,
  `retired`, `source`, `id` (see :147-173 for the record shape).
- `agents_registry.py:69-80`. Closed enums exist for `depth` and `fanout`
  with `_coerce_depth` and `_coerce_fanout` coercers. `role` is read raw at
  :160 with no coercer. This is D2 in the synthesis: `role: "memoy"` silently
  disables the memory-keeper.
- `agents_registry.py:118-127`. Explicit exclusion set: the bridge agent per-channel
  Discord agents (Letta-backed) are declared NOT registry members. This is a
  Letta-era carve-out; when bridge folds in, the exclusion becomes moot and
  can be simplified (see PM below).
- On-disk agents (verified `ls $WORKSHOP/agents/`): 15 markdown
  files. Real ones: `memory-keeper`, `workshop-overseer`, `email-triager`,
  `plaid-watcher`, `the bridge agent`, `the bridge agent-commute`, `workshop-builder`,
  `workshop-builder-2`, `blank-project-1`, `blank-project-2`, `probe-free`,
  `probe-git`, `smoke-fix`, `test1234`, `test2`. Directory is gitignored.

### 2.3 Task substrate

- `backend/services/tasks.py:1-50`. Canonical Task v2 schema. Includes
  `touches: [str]` (:40) documented as "path globs this task writes (fan-out
  safety)".
- `touches` reference sweep (verified):
  - `tasks.py:40` (doc), `:311` (normalize round-trip), `:760` (add param),
    `:787` (persist).
  - `task_dedup.py:344`, `:391`, `:414` (accept + persist through dedup).
  - `task_scope.py:152` (thread through scope reader).
  - **Zero read sites that GATE anything**. `grep -rn touches
    scripts/ralph.sh` is empty. D3 in the synthesis.
- `tasks.py:69`. Terminal statuses: `done`, `cancelled`. Blockable statuses
  include `blocked`. `blocked_reason` is stored; nothing reads it beyond
  echo-back to the UI. There is no `awaiting_input` primitive tied to a review
  loop; today's block path just parks work.

### 2.4 Dispatcher

- `scripts/dispatch_triggers.sh:401-424`. The role hardcode. Verified: the
  branch at :408 fires memory_keeper_run.sh only when `a.get("role") ==
  "memory"`; :423 prints `skip-assistant` and continues. Every other
  assistant-runtime agent with a live trigger dies here.
- Consequence, verified by reading each agent file:
  - `memory-keeper.md`: `role: memory`, cron trigger. RUNS.
  - `workshop-overseer.md`: no role, loop trigger. NEVER RUNS.
  - `email-triager.md`: no role, event trigger. NEVER RUNS via
    dispatch_triggers (event path is separate at `event_dispatch.py`, verify).
  - `plaid-watcher.md`: no role, loop trigger. NEVER RUNS.
  - `the bridge agent.md`, `the bridge agent-commute.md`: no role, loop trigger. Not driven by
    this dispatcher today; the bridge agent is driven by its own systemd tmux loop.

### 2.5 Learnings (F3)

- `backend/services/learnings.py:108-116`. `_valid_scope()` rejects bare
  slugs. Only `global`, `workshop`, and `project:<slug>` etc.
- `scripts/project_ralph.sh:529`. Sets `BILL_SCOPE="${SLUG:-$ASSIGNEE}"`
  (bare slug for a project).
- `scripts/ralph.sh` uses `BILL_PROJECT` as the learnings scope and rejection
  provenance. When the scope is a bare slug, `memory_block` catches the
  ValueError (chat_context.py:618-620) and the entry silently falls back to
  `global` (or is written under a valid `project:<slug>` scope only when the
  code paths remember the prefix; per project_ralph :529 they DO NOT).
- `chat_context.py:597-623`. `learnings_context` injects `global` into every
  scope's chat plus the scope's own store. When per-project learnings leak to
  `global`, they leak into every agent everywhere.

### 2.6 The bridge agent source tree

Verified `find $HOME/<bridge> -type f -not -path '*/.git/*'`:

- `~/clawdbot/CLAUDE.md` (17k). The the bridge agent persona + dispatch protocol +
  Discord playbook + Plaid onboarding + security posture. Mostly duplicative
  of the discord-handler subagent + operator profile.
- `~/clawdbot/run.sh` (2.8k). The persistent `claude -p --channels` wrapper
  with auto-accept-trust sidekick, --continue loop, systemd-friendly exit.
- `~/clawdbot/plaid-mcp/` (~1500 files including node_modules). The custom
  Node/hono Plaid MCP wrapper. Source files: `server.js`, `onboard.js`,
  `balances.js`, `transactions.js`, `package.json`. `node_modules/` is
  vendored.
- `~/clawdbot/security/` (2 files). `audit.sh` + install-cron.txt. Nightly
  security audit, per the memory doc.
- `~/clawdbot/docs/` (3 files). Bringup playbook + security invariants +
  audit history. Documentation, not runtime.
- `~/clawdbot/archive/`. Pre-migration artifacts.
- `~/clawdbot/commute-diagnostic.png`. A stray screenshot; delete.

Systemd unit: `~/.config/systemd/user/the-bridge.service` (verified). Loads
`~/.config/clawdbot/auth.env` (long-lived `CLAUDE_CODE_OAUTH_TOKEN`), forks a
tmux session named `clawd-claude` that runs `run.sh`.

Discord-handler subagent: `~/.claude/agents/discord-handler.md` (14k).
The operational playbook for a Discord turn (dispatched from `~/clawdbot/CLAUDE.md`
per its :19-37 protocol).

### 2.7 Frontend

- `frontend/src/pages/Agents.tsx:29-34`. Already renders `role` and `depth`
  chips on agent cards; the UI vocabulary exists. `latitude` is not yet a
  column.
- `frontend/src/pages/Work.tsx:44-54`. Two-tab Segmented (Tickets, Runs) plus
  a third (Autonomy) already; adding a Review tab is one entry.
- `frontend/src/components/AutonomyPanel.tsx`, `TaskPanel.tsx`. Present.
  Preset control (off/on-demand/scheduled/continuous) is shipped; depth and
  fanout are shipped. Latitude is not.

---

## 3. The identity-model decision: DECIDED YES

The operator's message today ("yes generalize agent... FULLY implement this
so its FULLY working technically") implicitly answers the one gating question
from `synthesis-agent-protocols-2026-07-16.md` Part 3. An autonomous agent
becomes a third `Identity.kind` (`agent`) with zero ambient authority.

Rationale, reproduced from synthesis so the reader does not have to
cross-reference: `operator` in `permissions.py` is not the human's identity,
it is an authority assignment on a running process. Forbidding gates on the
human (the hard rule) does not forbid bounding a program that ingests hostile
input (an inbound Discord message, a scraped web page, a note body). The
dispatcher mints a generous in-scope grant at job start so the agent starts
holding read/write over its own scope; the gate is on the perimeter, not the
work itself. This preserves the "single-operator, no defensive cruft" posture
without regressing to confused-deputy behavior.

Full call-site audit for `is_operator` / `authorize`:

| file:line | context | change |
| --- | --- | --- |
| `permissions.py:71-90` | `Identity` class + `is_operator` property | add `is_agent` sibling property |
| `permissions.py:96-129` | `resolve_identity` | accept `"agent:<slug>"` and `{"kind":"agent","id":slug}`; unchanged for None/operator/guest |
| `permissions.py:287-293` | `capability_permitted` operator shortcut | leave as-is (operator still permitted); add explicit `agent` branch that consults minted grants only |
| `permissions.py:323-393` | `authorize` | add `is_agent` branch: NO ambient allow. Read = allow only if within scope; write = requires an explicit grant; unmatched = deny |
| `permissions.py:407-436` | `reachable_apps` | agent identity: itself plus any minted grant `to`-side; operator unchanged |
| `permissions.py:442-478` | `assistant_capabilities` | pass through; agent identity yields the same summary shape guest does today, driven by minted grants not by app manifest defaults |
| `backend/routes/base_apps.py:120` | passes `OPERATOR_IDENTITY` explicitly | leave as-is (this is the HTTP surface; UI is the operator) |

Invariants (mirrors synthesis I1/I2):

- **The LLM can only attenuate, never amplify.** Any broker-mediated grant is
  narrower than what the operator declared programmatically. This is why
  `attenuate()` moves from dead code to load-bearing.
- **The broker never reads the data it adjudicates.** Inputs are identity,
  URI, selector, purpose, policy; the response mode determines whether the
  called tool returns verbatim, filtered, or redacted data downstream. Wire
  point: `llm_router.py:387+444` `tools_disabled=True` path already exists.

---

## 4. Phase plan

Five phases, ordered by dependency and by daily value. Each is one merge unit.

### P0. Repair (small, unblocks everything, no contested decisions)

Goal: fix the four defects and one prereq. Ships without any of the new
concepts. Merge unit is small and reviewable.

| file | change |
| --- | --- |
| `backend/services/agents_registry.py:75-80` | add `_coerce_role(v)` beside `_coerce_depth`/`_coerce_fanout`. Closed enum: `("maker","reviewer","triager","keeper","broker")` plus `None`. Wire it in at :160. Reject unknown roles at write time (`create`/`update`, :389+414) with a 400-friendly `ValueError`. |
| `scripts/dispatch_triggers.sh:401-424` | replace the `if a.get("role") == "memory":` branch with a Python-side dispatch table. Signature: `ROLE_HANDLERS = {"keeper": _dispatch_keeper, "triager": _dispatch_triager, "reviewer": _dispatch_reviewer, "maker": _dispatch_maker}`. The default (no role) falls to `_dispatch_maker` for a builder-runtime agent and `_dispatch_assistant_loop` for assistant. `broker` never dispatches (demand-only). Each handler returns `(type, scope, assignee)` or None. Preserves the memory-consolidation launch path (calls `memory_keeper_run.sh`). |
| `agents/workshop-overseer.md` | delete (retire per synthesis Part 5 P0 + Part 6 rejects). Not just `retired: true`; the file itself goes because its function is the workshop-scope `triager`, not a distinct role. |
| `backend/services/permissions.py` | add `NOTES_VAULT_ROOT = Path("~/notes").expanduser()` constant. Register in a new `services/stores.py` (or extend `manifests.py`) as `store_uri="notes://vault"`, backed by the on-disk `~/notes` directory. This is the A3 precondition: the vault becomes addressable, so per-folder policy is expressible. |
| `scripts/project_ralph.sh:529` | replace `BILL_SCOPE="${SLUG:-$ASSIGNEE}"` with a helper that emits `project:<slug>` when SLUG is set and non-empty (else keep the assignee form, which already carries the `agent:` prefix). Fixes F3. |
| `scripts/ralph.sh` | no change: it reads BILL_PROJECT unchanged. The prefixing happens upstream. |
| `backend/services/tasks.py:*` | enforce `touches`: add a `_check_touches(root, tid, path)` helper called from the write path (task_scope committer + the reviewer receipt writer). A task with a non-empty `touches` may only cause file writes matching one of its globs; violations record an evidence line and block the task. A task with `touches=[]` is unrestricted (the default; today's behavior). Wired at the `reviewer.sh` receipt-consumer callsite so no worker path is bypassed. |
| `backend/services/permissions.py` | ensure `attenuate()` is exported and callable. Add ONE call site in P0 as a smoke check: the dispatcher, when minting the job's in-scope grant (added in P0's grant-mint helper), calls `attenuate()` from the workshop-root superset. This retires D4. |

Bootstrap the P0 grant-mint helper (used by the dispatcher and by ralph.sh):

- Add `services/dispatch_grants.py` with one function
  `mint_in_scope_grant(agent_slug, scope_key, verbs=("read","write"),
  expiry_s=3600)`. It calls `permissions.mint_grant()` with `to="agent:<slug>"`,
  `store_uri=<scope-root-uri>`, `subtree=True`, `verbs=verbs`, and an expiry
  caveat sized to the sweep budget. Returns the grant. The dispatcher calls it
  right before appending `(type, scope, assignee)` to `plan` in
  `dispatch_triggers.sh` and passes the resulting token to `project_ralph.sh`
  via a new `--grant-token` arg.

P0 merge units: 3 commits (repair-role-dispatch, repair-F3-learnings-scope,
enforce-touches). `attenuate()` wiring rides with repair-role-dispatch since
they share the dispatcher edit.

### P1. Latitude (one string, cascades)

Goal: implement the scopedness axis. One field. Cascades invocation to task
to scope through the mechanism `orchestration-and-autonomy.md` §7.1 already
uses for other overrides.

| file | change |
| --- | --- |
| `backend/services/tasks.py:8-44` (schema doc) | add `"latitude": "low"|"medium"|"high"|None` to the schema. Cascade rule: task > project autonomy > agent default > "medium". |
| `backend/services/tasks.py:271-320` | round-trip the new field in `_normalize`. |
| `backend/services/tasks.py:760+787` | accept and persist in `add`/`update`. |
| `backend/services/agents_registry.py:147-173` | expose `latitude` on the agent record (default None). |
| `backend/routes/tasks_all.py` | expose latitude on the Task API. |
| `backend/routes/projects.py` (autonomy patch) | expose latitude on the project autonomy record. |
| `scripts/ralph.sh` | resolve effective latitude at claim time (task -> project -> agent -> "medium") and export as `LATITUDE=<value>` into the `claude -p` env. |
| `scripts/reviewer.sh` | consume `LATITUDE`; a low-latitude sweep BLOCKS rather than deciding when the reviewer would have said "I don't know". Wires a new reviewer verdict `block-for-decision` that flips the task to `blocked` with `blocked_reason="latitude:low-and-ambiguous"`. |
| `services/chat_context.py` (persona injection) | one paragraph appended to the persona block: "You are running at LATITUDE=X. LOW: when the ticket does not say, ask; do not decide. MEDIUM: decide and note the choice. HIGH: decide and report the outcome." |
| `frontend/src/components/AutonomyPanel.tsx` | add a Segmented control (low/medium/high) beside depth. |
| `frontend/src/components/TaskPanel.tsx` | show the effective latitude on the ticket detail (with source: task/project/agent/default). |
| `frontend/src/pages/Agents.tsx:34+367` | render a `latitude` chip beside role/depth. |

P1 merge unit: one commit. Adds a field, threads it, adds a persona paragraph
and two UI chips. Zero mechanism changes.

### P2. Block loop + rule minting (Review tab)

Prereq: P0's F3 fix is already merged.

Goal: turn `blocked` into a first-class review surface with structured request
envelopes and a promotion path from block to rule.

| file | change |
| --- | --- |
| `backend/services/tasks.py` | extend `blocked_reason` with an optional `blocked_request` shape: `{"kind": "permission"|"resource"|"input"|"other", "ask": str, "context": {...}}`. Structured, not free-text. Persisted alongside `blocked_reason` for back-compat. |
| `backend/routes/tasks_all.py` | new POST `/api/tasks/{id}/review` that accepts one of `{approve|deny|grant-and-continue|mint-rule|dismiss}` plus optional grant params (verbs + ttl) or rule text. `approve`: releases the block, sets status back to `todo`. `deny`: cancels the task. `grant-and-continue`: calls `permissions.mint_grant()` with the requested verbs, records the grant id on the task, and releases. `mint-rule`: records a learning in the SCOPE store (not global) via `learnings.record()` with source=`review`. `dismiss`: leaves the block in place, hides it from the queue. |
| `backend/services/learnings.py:317-367` | add a helper `promote_when_stable(scope, hash) -> bool` that checks a learning's provenance length. Three independent block-origin provenance entries proposes a promotion candidate. Promotion writes to a new `learnings/promoted.jsonl` and surfaces as an operator prompt in the Review tab (never auto-mints a grant; that would violate the "operator owns rules" invariant). |
| `frontend/src/pages/Work.tsx:47-56` | add a `review` tab entry beside `tickets`/`runs`/`autonomy`. |
| `frontend/src/pages/Work.tsx` (new component) | `ReviewTab`: lists blocked tasks across scopes, with the structured request rendered as an action row (Approve/Deny/Grant+Continue/Mint-Rule). Also lists Promotion Candidates from the learnings promote-when-stable check. Each action posts to the /review endpoint above. |
| `frontend/src/lib/tasks.ts` | add `blocked_request` to the Task type. |

Job resume is REJECTED per synthesis Part 5 P2. The task is the durable state;
the LLM's context is stale by construction at unblock time; a released task
simply becomes `todo` again and the next sweep tick claims it fresh.

P2 merge units: 2 commits (block-request + review-endpoint, review-tab-ui).

### P3. Mediated broker (the expensive part)

Depends on section 3 (identity kind). Ships last because it is the only part
that requires a live cloud model.

| file | change |
| --- | --- |
| `backend/services/broker.py` (new) | one class `MediatedBroker`. Method: `adjudicate(identity, uri, verbs, selector, purpose, policy) -> Verdict`. Verdict shape: `{"decision":"allow"|"deny", "grant":<mint_grant result or None>, "mode":"verbatim"|"filtered"|"redacted", "reason": str}`. The call sets `tools_disabled=True` on the underlying `llm_router.call_cloud` invocation (`llm_router.py:427+444`) so the broker cannot fetch the resource itself. Model floor: Sonnet or better; on Sonnet unavailability the broker returns deny (fail-closed). |
| `backend/services/policy.py` (new) | one dataclass `StorePolicy` living OUTSIDE any store folder: `{store_uri, tier: "open"|"gated"|"mediated", topics_allow: [str], topics_deny: [str], response_modes: ["verbatim"|"filtered"|"redacted"]}`. Loaded from `backend/store-policies.json` (mirrors `resource-grants.json` shape). Not `summarized`; see section 10. |
| `backend/services/stores.py` (new) | store registry. `~/notes` registers as one entry from P0. Every store carries a policy and (optionally) a reader function. Store reads route through: `check(token, uri, verb)` (mechanical), then if the token names a mediated tier and no in-scope grant exists yet, invoke `broker.adjudicate` to mint or deny. |
| `backend/routes/stores.py` (new) | `GET /api/stores`, `POST /api/stores/{id}/read?selector=...` (broker-mediated), `POST /api/stores/{id}/write` (grant-checked). |
| `backend/services/permissions.py:697-728` | already has `check()`. No change. |
| `frontend/src/pages/Notes.tsx` | wire to `/api/stores/notes:vault` for the notes case. The notes app stops owning its own gate; policy lives in `store-policies.json`. |

Finance as a generality proof (per resource-access-protocol design): a second
store `plaid:transactions` registers with tier=`mediated`, topics_allow=[]
(nothing implicit), and a policy requiring an explicit purpose per request.
This validates the substrate on a second resource without adding a new
mechanism.

P3 merge units: 3 commits (broker+policy service, stores service+routes,
notes-and-plaid wiring).

### P4. Resident agents + templates + zero-agent default

Goal: templates fork rather than link; a new project starts with zero
resident agents.

| file | change |
| --- | --- |
| `agents/_templates/` (new dir, tracked) | shipped templates: `note-triager.md` (D5 target), `daily-digester.md`, `budget-watcher.md`, `pr-reviewer.md`. Each is a plain Agent definition with `role`, `triggers`, and a persona body. |
| `backend/services/agents_registry.py` | new `fork_from_template(template_slug, target_slug, project_slug=None)` that copies the definition file into the target scope and stamps `template: <slug>@<version>` in the frontmatter. NO SYNC ENGINE. |
| `backend/routes/agents.py` | `POST /api/agents/from-template` with body `{template, slug, project?, overrides}`. |
| `frontend/src/pages/Agents.tsx` | add "Create from template" split-button; new modal with the template list. |
| project bootstrap (`backend/services/projects.py` or equivalent create path) | default resident agent set for a NEW project: `[]`. Explicit. |

P4 merge unit: one commit (templates + fork + UI).

---

## PM. The bridge agent fold-in (dedicated phase, largest scope item)

Order: PM runs AFTER P0 and CAN run in parallel with P1-P4, but the operator
never loses Discord access mid-migration. The migration is done in three
strict sub-steps.

### PM.1 File inventory (verified by reading, not summarizing)

| path | classification | rationale |
| --- | --- | --- |
| `~/clawdbot/CLAUDE.md` | REMOVE after PM.3 | Most content duplicates the discord-handler subagent + operator profile. The unique bits (Plaid onboarding, Google Docs quirk notes, completion contract) migrate to the workshop `agents/the bridge agent.md` body during PM.2. |
| `~/clawdbot/run.sh` | REMOVE after PM.3 | Once the bridge agent rides the workshop's `claude -p` dispatch, the persistent tmux loop is superfluous. |
| `~/clawdbot/plaid-mcp/{server.js, onboard.js, balances.js, transactions.js, package.json}` | PORT to `backend/mcp/plaid/` | The MCP is genuinely useful. Moves into workshop as a first-class MCP server registered in `llm_router._MCP_SERVER_SPECS`. `plaid-tokens.json` stays in `~/.config/clawdbot/` (host-level secret; KEEP path). |
| `~/clawdbot/plaid-mcp/node_modules/` | REMOVE from source (regen on install) | Vendored deps; `npm install` at the new location. |
| `~/clawdbot/security/audit.sh` + `install-cron.txt` | PORT to `scripts/security_audit.sh` | Nightly security check; keep it running, from the workshop repo. |
| `~/clawdbot/docs/` (bringup, security-invariants, audit history) | PORT to `docs/the bridge agent/` | Historical value; keep in-tree. |
| `~/clawdbot/archive/` | KEEP in place | Pre-migration junk. Do not delete; do not import. |
| `~/clawdbot/commute-diagnostic.png` | REMOVE | Stray file. |
| `~/clawdbot/.git`, `.gitignore` | REMOVE with the tree | Once emptied. |
| `~/.claude/agents/discord-handler.md` | PORT into `agents/the bridge agent.md` body | This IS the bridge agent's persona. It moves in verbatim, minus the outdated Letta-era bits. |
| `~/.claude/agents/work-reviewer.md` | KEEP as `agents/work-reviewer.md` in workshop scope | Verified `ls ~/.claude/agents`. Genuine reviewer subagent; belongs in the workshop registry now. |
| `~/.config/systemd/user/the-bridge.service` | REPLACE with `the bridge agent-event-bridge.service` (PM.3) | New service is thin: a tiny Node/Python process that reads Discord events and POSTs to workshop's event bus. No persistent `claude -p` loop, no tmux. |
| `~/.config/clawdbot/auth.env` | KEEP | Long-lived OAuth token. Still loaded via `EnvironmentFile=` by the new service AND by the workshop backend. |
| `~/.config/clawdbot/plaid-auth.env` | KEEP path | Plaid client_id/secret. |
| `~/.config/clawdbot/plaid-tokens.json` | KEEP path | Per-bank access tokens, mode 0600. Read by the ported MCP. |
| `~/.config/clawdbot/google-auth.env`, `~/.config/google-docs-mcp/token.json` | KEEP | OAuth for the google-docs MCP. Already reachable from workshop. |
| `~/.claude/channels/discord/{.env, access.json}` | KEEP | Discord channel state. Still owned by whatever process posts. |
| `~/.claude/plugins/cache/claude-plugins-official/discord/0.0.4/server.ts` | KEEP (local patch documented in operator memory) | The plugin still runs, just under the new event-bridge service. |
| `~/workshop/agents/the bridge agent.md` | REWRITE (PM.2) | Currently a 20-line stub with a Letta agent id. Becomes the real the bridge agent definition. |
| `~/workshop/agents/the bridge agent-commute.md` | KEEP as a distinct Agent | Verified 166-line commute-specific persona. Refactor to reuse the bridge agent's base rules via `include:` (a new registry feature in PM.2) so we don't duplicate the persona. Trigger stays `cron`. |

### PM.2 Migration order (do NOT break Discord mid-migration)

1. **Ship P0 and PM prerequisites first.** The role dispatch table (P0) is
   required for the bridge agent to run under the new pipeline at all.
2. **Add the Discord event trigger** to `backend/services/triggers.py`. New
   trigger kind: `discord`, subscribes to inbound-message events on named
   channels. Delivery: the event lands in `.runtime/events.jsonl` and the
   subscriptions writer (`agents_registry.regenerate_subscriptions`) wires it
   to any agent that declares a matching trigger.
3. **Register a `the bridge agent` Agent** with the ported persona,
   `role: triager`, `trigger: discord`, `autonomy: continuous`, and a granted
   toolset (Discord + Plaid + google-docs MCP names). Test with autonomy OFF
   (the flag is present, dispatcher exits early).
4. **Build the event bridge** (`scripts/chat_event_bridge.py`, ~50 lines):
   subscribes to Discord via the existing `discord-mcp` npm plugin or (if
   simpler) reuses the existing `--channels plugin:discord` transport, but the
   process's ONLY job is to post inbound envelopes to the workshop event bus
   (`POST http://127.0.0.1:8000/events` with a `discord` source). No `claude
   -p` loop, no LLM in this process. A crash of the bridge only pauses inbound
   Discord; the bridge auto-restarts under systemd.
5. **Install `~/.config/systemd/user/the bridge agent-event-bridge.service`.** Verify
   for one operator round trip (a manual Discord message with autonomy off
   yields an event on the bus, visible in the Events page but NOT dispatched).
6. **Flip the switch.** Enable the new bridge; disable
   `the-bridge.service`. Enable autonomy (or leave off for testing at the end
   of the plan, per operator ask). The the bridge agent Agent is now driven by the
   dispatcher.
7. **Wait ONE FULL OPERATOR DAY** with both services present but only the new
   one enabled, before removing the old tree. This is the safety window.
8. **Remove `~/clawdbot/`** except for `~/clawdbot/archive/`.
   `the-bridge.service` is deleted (`systemctl --user disable --now`).

### PM.3 Discord bridge design decision

Verified from operator memory + `~/clawdbot/CLAUDE.md`: the Discord plugin
today runs INSIDE the persistent `claude -p --channels` process. The plugin's
`server.ts` receives Discord events and passes them into the claude session as
`<channel source="discord">` tool-envelopes; the session then dispatches to
the `discord-handler` subagent.

Two options for the new world:

- **Option A (recommended, less cruft).** The event bridge stays a thin
  poster: it hosts the plugin's Node MCP server itself (already Node/hono) but
  captures each inbound message as an HTTP POST to workshop's event bus
  instead of feeding it into a resident LLM. The reply path also flows through
  workshop: the bridge agent's dispatched turn writes its reply, and the same bridge
  process reads a `discord.reply` outbound event from the bus and issues the
  Discord API call. This decouples the LLM life-cycle from the network
  life-cycle; a dispatcher restart never drops a Discord connection.
- **Option B.** Keep the persistent `claude -p --channels` loop, add a
  trigger kind that consumes its stdout. This preserves the existing
  auto-accept-trust ergonomics but keeps the tmux tie-in and does not
  actually fold the bridge agent in; it just adds a workshop-side wrapper around it.

**Recommendation: Option A.** It is the actual fold-in; Option B is a
half-measure that keeps two loops running.

### PM.4 Custom Plaid MCP

Verified `ls ~/clawdbot/plaid-mcp/`. Source: `server.js`, `onboard.js`,
`balances.js`, `transactions.js`, `package.json`. Moves to
`backend/mcp/plaid/`. Register in `llm_router._MCP_SERVER_SPECS` under key
`plaid` with spawn args `node backend/mcp/plaid/server.js`. Grant path uses
the same resource-grant machinery: `plaid:transactions`,
`plaid:balances` register as stores in P3, with `store-policies.json`
requiring mediated tier and mode=verbatim (Plaid data is not paraphrasable).

### PM.5 Supervisor cron

Verified `crontab -l`: today runs `cron_agent.sh` every 20 minutes. The doc
`~/workshop/CLAUDE.md` and `cron_agent.sh` header say Phase 0 is retired and
Phase 0.5 (dispatch_triggers.sh) plus Phase 1 (event drain) are what tick.
This IS the "the bridge agent supervisor cron" referenced in memory
(`project_bridge_supervision.md`). After PM: no change to `cron_agent.sh`
mechanics; the new the bridge agent Agent gets dispatched by the same tick that
dispatches every other Agent.

### PM.6 `the bridge agent-commute` variant

Verified 166 lines of commute-specific persona and cron trigger. Refactor
target: the bridge agent-commute becomes an Agent whose body `include: the bridge agent` pulls
the bridge agent's base persona plus a delta. If the include feature is more work than
inlining, INLINE the current 166 lines under a new `agents/the bridge agent-commute.md`
that duplicates the shared prelude. Better duplication than a broken include.

---

## 5. Clawd rename (deferred decision, tightened)

Recommendation: **defer the rename**, but lock down what a future rename
would touch.

Rationale:
- The `the service user` Linux user is fine to keep. Renaming a Linux user is
  disruptive and does not add capability.
- `clawdbot/` empties out under PM. Once empty (post PM.3 safety window),
  delete it. The name goes away by virtue of removal, not renaming.
- `the-bridge.service` gets replaced by `the bridge agent-event-bridge.service` during
  PM.3. That is a name change already; the "clawd" naming does not survive
  PM.
- `~/.config/clawdbot/` is a config directory holding auth secrets. Renaming
  it churns every service unit's `EnvironmentFile=` path for zero user-facing
  benefit. Defer.

Surfaces that would need to change under a full rename (documented so a
future decision is not blind):
- `~/.config/clawdbot/` -> `~/.config/the bridge agent/` (or `~/.config/workshop/`)
- `~/.config/systemd/user/*bot*` -> new names
- `/tmp/workshop-cron-agent.lock` (already workshop-named, fine)
- Documentation references in `~/.claude/CLAUDE.md`, workshop memory docs,
  and this doc.

Estimated churn: 40-60 file paths, one migration script, one operator log
out/log in cycle. Defer until the folded system is stable in production for a
week.

---

## 6. Old-system audit (port/remove/keep, systematic)

Operator's specific mandate: "any other old systems or UI's or code, port it
over or remove it so the whole thing works and makes sense."

| subsystem | disposition | detail |
| --- | --- | --- |
| `agents/workshop-overseer.md` | REMOVE | Verified never runs (D1). Function is a workshop-scope triager, not a distinct role. Delete file in P0. |
| `agents/the bridge agent.md` (workshop stub, 20 lines) | REWRITE in PM.2 | Currently a Letta-era stub with an inline `letta_agent_id`. Becomes the real the bridge agent definition (persona + triggers + grants). |
| `agents/the bridge agent-commute.md` | REWRITE in PM.6 | Currently duplicates the bridge agent's persona and adds commute logic. Refactor to reuse via include or explicit inline. |
| `agents/plaid-watcher.md` + `email-triager.md` | KEEP, correct dispatch | After P0's role table, both dispatch cleanly. Both are event-triggered; verify their event source is wired in `regenerate_subscriptions` (`agents_registry.py:506-548`). Bodies are short (18-20 lines); leave as-is. |
| `agents/blank-project-*`, `probe-*`, `smoke-fix`, `test1234`, `test2` | REMOVE | Verified: all are test/probe stubs left in the gitignored agents/ dir. `test1234` gives it away. Retire in P0. |
| `agents/workshop-builder{,-2}.md` | KEEP | These are the two active builder agents. Not touched. |
| `~/clawdbot/` | REMOVE post-PM safety window | See PM inventory. |
| `the-bridge.service` | REMOVE post-PM safety window | Replaced by `the bridge agent-event-bridge.service`. |
| `~/.claude/agents/discord-handler.md` | REMOVE post-PM.2 | Its content moves into `agents/the bridge agent.md`. The `~/.claude/agents/` file was a Letta-era subagent that the persistent claude session dispatched to; under the new model, the bridge agent IS an agent and the dispatch is the workshop dispatcher. |
| `~/.claude/agents/work-reviewer.md` | PORT to `agents/work-reviewer.md` | Genuine reviewer subagent; brings the reviewer path into the registry. |
| Letta references in code | AUDIT + REMOVE | Verified `agents_registry.py` still speaks Letta (see `per_channel_ids`, `letta_agent_id` field, `memory: letta`). Stage 9 already removed Letta as a runtime dependency (per operator memory + `unified-agent-model-2026-07-15.md`), but the SCHEMA still carries Letta ids. Sweep: remove `letta_agent_id` from `_record_from_def` (:150-172), remove `per_channel_ids` and the exclusion path (:118-127, :294), stop honoring `memory: letta` as a valid value. This is a P4 tail cleanup (post-bridge fold) because until the bridge agent is folded we cannot be sure no runtime path still names them. |
| `_TURNS_DIR` liveness scan in `agents_registry.py:216-262` | KEEP | Real liveness detection for builders. Not Letta-specific. |
| Old-style `authorize()` operator-only gates | KEEP | `permissions.py:354` operator branch stays default-permissive by the hard rule. The `agent` branch is additive. |
| `.tasks.json` per-tree lock, TODO.md export, reconcile | KEEP | Section 2 of `tasks.py` confirms these are the canonical durable path. No changes. |
| `~/notes` vault | REGISTER as store in P0 | Not "old system" per se, but unregistered today. A3 precondition. |
| `services/memory_keeper.py` + `memory_keeper_run.sh` | KEEP, generalize invocation | Under P0's role table, memory-keeper dispatches via `role: keeper` (renamed from `role: memory` in the enum, back-compat shim reads `memory` -> `keeper`). |

---

## 7. Testing plan (light, ends with autonomy OFF)

Operator ask: "test it out lightly just to make sure but leave it off at the
end." Design:

1. **Unit tests (already-present, must be extended or added):**
   - `backend/tests/test_usage_ledger.py` (verified exists). Extend with a
     scope-key test that exercises `project:<slug>` end-to-end (F3 regression).
   - `backend/tests/test_permissions.py` (create if missing; verified none
     today). Cases: minted grant + `check()`, `attenuate()` narrows,
     `attenuate()` refuses widening, `Identity(kind="agent")` denies without a
     grant, `Identity(kind="operator")` allows.
   - `backend/tests/test_agents_registry.py` (create if missing). Cases:
     `_coerce_role` rejects `"memoy"`, closed-enum values round-trip on
     write, unknown role at write time raises.
2. **Smoke: note-triage ant registers and dispatches (D5, PM.1
   verification).** Add `agents/_templates/note-triager.md`. Fork into user
   scope via the P4 endpoint. Verify: `dispatch_triggers.sh` names the agent
   in its next tick's dry-run print. NO ACTUAL RUN. Autonomy stays off.
3. **Smoke: latitude cascades.** Create a task with no `latitude`, in a
   project with `autonomy.latitude="low"`. Read `/api/tasks/{id}`, verify the
   effective_latitude in the response resolves to `low` and its source is
   `project`.
4. **Smoke: block-and-review round trip.** Create a task with
   `latitude="low"`, force a reviewer verdict of `block-for-decision` via a
   canned reviewer stub. Verify the task hits the Review tab. POST
   `/api/tasks/{id}/review` with `approve`; verify the task returns to `todo`
   and the block is cleared. NO ACTUAL RALPH SWEEP.
5. **Smoke: the bridge agent responds to a Discord message via the new path.** With
   the event bridge live and autonomy ON for the bridge agent only (`autonomy: on-demand`
   preset so it does not sweep, only fires on the trigger): send one Discord
   message to the bot. Verify the event lands in the bus, dispatch fires,
   reply lands in Discord. Then flip the bridge agent back to `autonomy: off`.
6. **End state:** `.runtime/AUTONOMY_DISABLED` present, `the bridge agent` preset =
   `off`, `the bridge agent-event-bridge.service` disabled, `the-bridge.service`
   disabled (or removed). `crontab -l` unchanged (cron_agent still fires and
   short-circuits on the flag).

Tests are run manually by the executing agent; they are not automated in CI
in this plan (the operator has no CI on this repo).

---

## 8. Sequencing (merge units and order)

Ship in this order. Each merge unit is independently reviewable; nothing
depends on a later unit.

| # | merge | contents |
| --- | --- | --- |
| 1 | P0.a repair-role-dispatch | `agents_registry.py` `_coerce_role`, `dispatch_triggers.sh` role table, delete `workshop-overseer.md`, delete probe/test agent stubs, `dispatch_grants.py` + first `attenuate()` call site |
| 2 | P0.b repair-F3-learnings-scope | `project_ralph.sh` prefix helper |
| 3 | P0.c enforce-touches | `tasks.py` `_check_touches` + reviewer wire |
| 4 | P0.d register-notes-vault | `services/stores.py` seed + `notes://vault` |
| 5 | P1 latitude | field + cascade + persona paragraph + UI chips |
| 6 | P2.a block-request-shape + review-endpoint | `tasks.py` + `tasks_all.py` |
| 7 | P2.b review-tab-ui | `Work.tsx` Review tab |
| 8 | P2.c promote-when-stable | `learnings.py` helper + surfacing |
| 9 | PM.1 ported-plaid-mcp | `backend/mcp/plaid/` + `llm_router` registration |
| 10 | PM.2 the bridge agent-agent-def | rewrite `agents/the bridge agent.md`, new discord trigger kind, event bridge script |
| 11 | PM.3 event-bridge-service | new systemd unit; the-bridge.service DISABLED |
| 12 | PM.4 the bridge agent-commute-refactor | reuse or inline |
| 13 | PM.5 remove-clawdbot | after 24h safety window |
| 14 | P3.a broker + policy | `broker.py`, `policy.py`, tests |
| 15 | P3.b stores + notes wiring | `stores.py`, `routes/stores.py`, `Notes.tsx` |
| 16 | P3.c plaid-store wiring | second store as generality proof |
| 17 | P4 templates + fork + UI | `_templates/`, `fork_from_template`, Agents.tsx button |
| 18 | tail cleanup: retire Letta schema | `agents_registry.py` letta_agent_id, per_channel_ids removal |

PM (steps 9-13) can proceed in parallel with P1/P2 (steps 5-8) once P0 lands
because they touch disjoint files (except for `agents_registry.py`, which
gets minor edits from both). Merge order is what matters; concurrent
development is fine as long as steps 1-4 land first.

---

## 9. Testing plan wiring detail (for the executing agent)

For each merge unit above, the executing agent must:
- run `bash scripts/check_design.sh` (design lint gate)
- run `cd frontend && npx tsc --noEmit` when UI changes
- run `python3 -m pytest backend/tests/ -x` for backend changes
- run `scripts/reviewer.sh` at the end of each unit

Only after a unit passes those gates does it move to the next. When
integration test 5 (the bridge agent Discord round-trip) runs, do it manually and
DELETE any test messages from Discord after verification (operator prefers
clean channel history).

---

## 10. What this plan deliberately does NOT build

Copied from `synthesis-agent-protocols-2026-07-16.md` Part 6, re-stated so
silence is not mistaken for oversight:

- No conductor agent. Conductor is ~20 lines of Python in the dispatcher.
  Overseer function is a `triager` at workshop scope.
- No durable-execution engine, no job resume. The Task is the durable state.
- No policy DSL (Rego, Cedar). `mint_grant` + `check` are the policy engine.
- No approval queues that GATE work. The Review tab sits BEHIND work that
  already stopped on its own. It is a dashboard, not a gate.
- No audit journals of block decisions. `tasks.py:37` `evidence` records
  what is needed; an audit journal is the multi-tenant cruft the hard rule
  forbids.
- No trust ladders, agent performance scoring, or zero-trust defaults.
- No `summarized` response mode in the seed set. Only verbatim/filtered/
  redacted (each satisfies output subset input and is deterministically
  assertable).
- No population-scale ant/response-threshold coordination model. Stigmergy
  proper (agents coordinate through shared state, the task store) survives
  and is already the posture.

If any of these emerge in the executor's plan as "we could also...", strike
them explicitly.

---

## 11. Open questions for operator

Two only. Both are low-blast-radius and the plan can proceed to a defensible
default if unanswered.

1. **Option A vs Option B for the Discord bridge (PM.3).** Recommended: A
   (thin poster to event bus, LLM loop hosted by dispatcher). B is a
   half-measure. Proceeding on A unless the operator says otherwise.
2. **Discord event trigger transport.** The current Discord plugin lives in
   the claude-code plugins cache; can we run it as a standalone Node process
   from the workshop tree without vendoring the plugin? If not, the event
   bridge shells out to `claude --channels` in headless mode and consumes its
   NDJSON output. Executor to verify at PM.2 build time; if it complicates
   the bridge past ~200 LoC, revert to Option B for the bridge only.

Everything else was decided in-plan or has a synthesis-owned default.

---

## Appendix A: file-touch matrix

For the executor: this is every file the plan modifies or creates, grouped by
merge unit.

- P0.a: `backend/services/agents_registry.py`,
  `scripts/dispatch_triggers.sh`, `backend/services/dispatch_grants.py`
  (new), `agents/workshop-overseer.md` (delete), `agents/blank-project-*.md`
  (delete), `agents/probe-*.md` (delete), `agents/smoke-fix.md` (delete),
  `agents/test1234.md` (delete), `agents/test2.md` (delete),
  `backend/routes/agents.py` (role validation surface).
- P0.b: `scripts/project_ralph.sh`.
- P0.c: `backend/services/tasks.py`, `scripts/reviewer.sh`,
  `backend/services/task_scope.py`.
- P0.d: `backend/services/stores.py` (new), `backend/services/permissions.py`
  (NOTES_VAULT_ROOT const).
- P1: `backend/services/tasks.py`, `backend/services/agents_registry.py`,
  `backend/routes/tasks_all.py`, `backend/routes/projects.py`,
  `scripts/ralph.sh`, `scripts/reviewer.sh`, `backend/services/chat_context.py`,
  `frontend/src/components/AutonomyPanel.tsx`,
  `frontend/src/components/TaskPanel.tsx`,
  `frontend/src/pages/Agents.tsx`, `frontend/src/lib/tasks.ts`.
- P2.a: `backend/services/tasks.py`, `backend/routes/tasks_all.py`.
- P2.b: `frontend/src/pages/Work.tsx`.
- P2.c: `backend/services/learnings.py`, `frontend/src/pages/Work.tsx`
  (promotion candidates section).
- PM.1: `backend/mcp/plaid/` (moved), `backend/services/llm_router.py`
  (`_MCP_SERVER_SPECS` entry).
- PM.2: `agents/the bridge agent.md` (rewrite), `backend/services/triggers.py`
  (discord trigger kind), `backend/services/agents_registry.py`
  (regenerate_subscriptions honors it), `scripts/chat_event_bridge.py`
  (new).
- PM.3: `~/.config/systemd/user/the bridge agent-event-bridge.service` (new).
- PM.4: `agents/the bridge agent-commute.md`.
- PM.5: delete `~/clawdbot/` (except archive/), delete
  `~/.config/systemd/user/the-bridge.service`.
- P3: `backend/services/broker.py` (new), `backend/services/policy.py`
  (new), `backend/services/stores.py`, `backend/routes/stores.py` (new),
  `backend/store-policies.json` (new), `frontend/src/pages/Notes.tsx`.
- P4: `agents/_templates/note-triager.md` (new),
  `agents/_templates/daily-digester.md` (new),
  `agents/_templates/budget-watcher.md` (new),
  `agents/_templates/pr-reviewer.md` (new),
  `backend/services/agents_registry.py` (`fork_from_template`),
  `backend/routes/agents.py`, `frontend/src/pages/Agents.tsx`,
  `backend/services/projects.py` (default agent set = []).
- tail: `backend/services/agents_registry.py` (Letta schema retire).

---

End of plan.
