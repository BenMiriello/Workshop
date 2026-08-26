# What a ticket's status claims, and how an agent learns it

**Status: proposal, 2026-08-24.** Answers the operator's question: why a ticket
said "fixed" while `main` did not contain the fix, and how the vocabulary should
reach agents without being hardcoded into every system prompt. Grounded in
`research-agent-context-and-tool-docs-2026-08-24.md`.

## 1. The defect, precisely

Ten branches held committed work that was never merged. Tickets were closed
against that work. So the ticket store said done and `main` did not contain it.

The cause is not a labelling accident. It is a **missing derivation**, and this
repo already ratified the fix and then built half of it.
`changes-and-review-2026-08-22.md` C2 says a ticket's status is derived from its
change and nobody flips it by hand. `services/work_review.py` states, in its own
docstring, which half shipped:

> Half of that is true here: accepting a ticket merges its change, and the merge
> is what completes it. The other half is not yet: the sweep still writes
> `in_review` itself.

So a worker writes its own status, and nothing checks that status against where
the work actually is. Measured: a repo-wide grep for "merged into main" or "not
yet merged" returns **nothing**. The distinction does not exist in the codebase.

## 2. What GitHub does, which is the answer

GitHub never overloads one status field with three facts:

- Issues and PRs carry `state: open | closed`, and that is all it means.
- Whether a closed PR **shipped** is a separate orthogonal field: `merged`
  (bool) plus `merged_at` (timestamp, null if never merged). There is no
  `state=merged` in the REST API at all; callers fetch closed and partition.
- **Released** is a third concept again: a tag plus a Release object, wholly
  independent. A repo can carry months of merged-but-unreleased commits.

Three orthogonal facts, three fields. **The lesson is not to add a ninth status.**
A `merged` status would collide with `done` the first time work is merged but not
accepted, or accepted but not merged, both of which happen here.

## 3. The proposal

**3.1 Finish the derivation, do not add a status.** A ticket's landed-ness is a
question about its CHANGE, and the change record already carries the branch and
the merge. So it is derived, never stored twice, and the answer is always
computed from the thing that knows.

**3.2 Refuse the contradiction rather than displaying it.** A ticket may not read
`done` while its change exists and is unmerged. That state is what produced this
whole incident, and it should be a refusal at the write, not a badge in the UI.
Where there is no change at all (a file-backed scope, a non-worktree sweep) the
answer is "not applicable", which must read differently from "not merged".

**3.3 Surface the three states the operator actually needs to tell apart:**
committed, merged, and accepted. Those are the three that were conflated.

**3.4 Complete C2's other half.** The blocker is named in the code: moving the
`in_review` write into `work_review` would take the claim-token fence with it,
and the fence is what stops a displaced worker clobbering an operator's edit.
The prerequisite is the lease refactor (B3). That is a dependency to express,
not a reason to leave it.

## 4. How the vocabulary reaches an agent

The operator's three constraints, which pull against each other: each surface
carries its own explainer; it is not re-read every turn; the agent knows about it
**before** calling. The reconciliation is standard and is called **progressive
disclosure**, in two tiers:

- **Always loaded, tiny.** A name and a one-line description, 30 to 50 tokens.
  Enough to know the thing exists and when it is relevant. This satisfies "aware
  before calling".
- **Fetched on demand.** The full contract, retrieved only when the short
  description makes the agent decide it matters. This satisfies "not reloaded".

This is not theoretical. Anthropic's Tool Search Tool with `defer_loading`
measured a **55,000 token** reduction in upfront tool definitions, an 85% context
reduction, and **accuracy went UP**, not down: Opus 4 from 49% to 74%, Opus 4.5
from 79.5% to 88.1%. A smaller relevant tool set beats a large one crammed into
every prompt.

**One correction to the operator's reasoning, which strengthens his conclusion.**
He wanted the explainer kept out of the always-loaded layer partly to avoid
re-reading it every turn. On **cost** that concern does not hold: with prompt
caching, cache reads bill at 0.1x, so a stable 300-token vocabulary block sitting
in the cached prefix costs roughly 30 token-equivalents per turn after the first.
Economically it is nearly free.

The real argument is the one the accuracy numbers show: **irrelevant fixed
context measurably degrades the model**. So the reason to keep it out is
signal-to-noise, not price. That is exactly his "entangles things" instinct, and
it is right for a better reason than the one he gave.

**Where it lands here.** The measured gap: `services/tasks.py` defines the
vocabulary once, in comments, and the six MCP tool descriptions in
`backend/mcp/workshop_query_server.py` (255 to 661 characters each) never mention
what any state means. So:

1. The ticket-reading tools' descriptions gain **one sentence** naming the state
   vocabulary and pointing at where the contract lives. Tens of tokens, always
   loaded.
2. The contract itself is **fetchable**. NOTE, corrected 2026-08-24: `open_ref`
   exists only in design documents and has never been built, so a plan that
   depends on it depends on unbuilt work. The implementation plan
   (`agent-context-plan-2026-08-24.md`) instead extends the EXISTING `where_is`
   tool with a topic argument, which is the right call: reuse the carrier that
   already ships rather than block on a designed-but-absent one.
3. **The data explains itself.** A ticket row returned to an agent carries not
   just `status: done` but where the work is. This is the cheapest of the three
   and the most reliable, because it arrives exactly when it is relevant and
   cannot be forgotten.

Nothing goes into any agent's system prompt, which is what he asked for.

## 5. Why the work was done twice

The operator asked whether the duplication is a temporary artifact. It is, and
the mechanism is specific: a branch never merged, the ticket was closed anyway,
a later agent read the closed ticket, found `main` without the fix, and rebuilt
it. Seven of ten branches were reimplemented this way.

Every link in that chain is closed by section 3: the ticket could not have read
done with its change unmerged, so the later agent would have seen unlanded work
rather than a contradiction. No separate mechanism is needed, and the
worktree-per-run and reaper work removes the branches that are the raw material.

## 6. Operator ruling, 2026-08-25: done means merged

Asked to choose between "done means accepted and landed on the default branch,
refused at the write if the change is not merged" and "done means the worker
finished, with landed-ness derived separately", the operator answered plainly:
**"I mean done should be merged, right?"**

So this document's §3 is now settled rather than proposed on that point:

- **`done` is refused when the ticket's change is not merged into the default
  branch.** It is a write-time refusal, not a warning and not a later reconcile.
  The refusal names the change and its state, because the whole failure being
  fixed is a status that asserted something nobody had checked.
- This does NOT add a ninth status, and §2's reading of GitHub still holds: the
  merged fact stays its own field on the change, and `done` is refused by
  consulting it. One fact, one home, one reader.
- A ticket whose work legitimately has no change (a decision, an operator-only
  action, an external dependency) is not caught by this, because there is no
  change to be unmerged. That is the correct behaviour and not a loophole:
  `execution` already distinguishes those.

This closes the first of the eight decisions listed in
`work-system-coverage-2026-08-25.md` §4, and unblocks the three tickets that
section names as waiting on it: `46b07a2b` (the state vocabulary), `e48268d0`
(no done while unmerged) and `91a477a5` (the landed-ness guard). `e48268d0`'s
Boundaries line, which forbids the move that `791f334e` mandates, is stale
against this ruling and should be corrected when it is picked up.

Two related decisions were also settled the same day and are recorded here so a
reader of this file does not have to hunt them:

- **`latitude` is deleted.** Set on zero of 111 tickets, and its stated job
  (decision rights) is already carried in words by the Boundaries line every
  ticket must have. The operator: "I don't know what latitude is either, so
  yeah, if you're saying it's not essential and it's not serving a purpose...
  remove it."
- **The autonomous loop stays off, deliberately.** The operator turned it off
  because it does not yet work correctly, not because arming it is an open
  question. Nothing in this document should be read as proposing to arm it.

## 7. Configurable done, 2026-08-25: a small closed set of conditions

The operator, after the §6 ruling: "the 'done' definition should be
configurable. but must have some structure. merged can be one condition but
must say into what branch."

This section sits ON TOP of §6. `merged` stays the default and the only
default; it becomes one named condition among a closed few rather than the
sole hardcoded one.

### 7.1 It is the existing `acceptance` field

`acceptance` is already every ticket's record of what done means for it. It is
declared in `services/tasks.py` (the field table at line 40), normalized by
`_normalize_acceptance`, persisted as `ticket.acceptance` JSONB
(`db/models.py:507`), mirrored by `db/ticket_sync.py`, preserved across
backfills (`db/backfill.py:_PRESERVED`) and read by `ticket_readiness.check`
as the answer to "does this ticket say what done looks like". A second
completion-criteria field beside it would be two answers to one question.

**Correction to the prior audit: the acceptance gate is not inert.**
`scripts/project_ralph.sh` reads `acceptance.kind` (1151), `acceptance.spec`
(1152), `acceptance.allow_no_diff` (1244) and `acceptance.criteria` (1281),
executes the criteria in the work tree (`eval_criteria`) and refuses the
terminal transition when one fails (1406). The measured problem is adoption,
not dead code: 7 of 259 tickets set the field, so nearly every ticket takes
the `reviewer` default path.

**`conditions` is a new KEY on that field, and it does not duplicate `kind`,**
because the two answer different questions at different moments. `kind`,
`spec`, `criteria` and `allow_no_diff` answer "may the sweep write a terminal
status at all", asked at the end of a run, inside the sweep. `conditions`
answers "may that terminal status be `done`", asked at the write, by whoever
writes it. §6 answered the second question with a hardcoded rule and no field.
This is that rule given a home.

    acceptance: {
      "conditions": ["merged", ...] | [{"kind": "merged", "branch": "main"}],
      "kind": ..., "spec": ..., "criteria": [...], "allow_no_diff": ...
    }

A bare string is shorthand for `{"kind": <string>}`, normalized in
`_normalize_acceptance` the way `criteria` already is: an unknown kind is
dropped, an empty list removes the key, and `acceptance` with no `conditions`
round-trips exactly as it does today.

### 7.2 The kinds

Measured against GitHub branch protection, GitHub rulesets, GitLab's merge
checks and approval rules, Jira and the Scrum Guide. Two things hold across
all of them. Automated checks passing and a review approving are universal.
Merge is not modelled as a condition anywhere: it is the action the conditions
gate. Jira has no first-class definition-of-done object at all (Atlassian's
own guidance is to build one from a custom field), and the Scrum Guide defines
Definition of Done without naming a single item, so neither supplies structure
to copy. Only one further item is a first-class toggle in both vendors:
conversation resolution.

Three kinds, all evaluable from records this repo already keeps.

**`merged`** `{"kind": "merged", "branch": str|None}`. The ticket's change has
`status == "merged"` into the branch named. Evaluated from `Change.status`,
which a database check constraint ties to `merged_sha` and `merged_at` being
non-null (`db/models.py:1327`), and `Change.base_ref`, which is
`nullable=False` and so always names the branch. `branch` null means the
scope's default branch (§7.4). ON BY DEFAULT.

**`gates`** `{"kind": "gates"}`. Every required gate on the change passed.
Evaluated from `Change.gates`, written by `change_gates.record_reviewer_run`
from `reviewer.sh`'s JSON. The required set is `change_gates.GATE_NAMES` minus
`claim_check.ADVISORY_GATES`; `skipped` counts as satisfied, matching GitHub,
where a required check is met by `successful`, `skipped` or `neutral`. The
evaluator exists: `auto_merge._gates_complete` and `_gates_green`.

**`operator`** `{"kind": "operator"}`. A person accepted it, rather than a
policy. Evaluated from `Change.merged_by == "operator"`, one of
`db.models.MERGE_ACTORS`. On top of `merged` this narrows rather than adds: it
says the merge must not have been an auto-merge.

`acceptance.criteria` and `acceptance.spec` are deliberately NOT condition
kinds. They are already hard pre-transition gates in the sweep, and the
criteria results already land on the change as the `acceptance_criteria` gate,
so requiring them again would be the same fact checked twice.

Considered and excluded, with what each would take:

- **Threads resolved.** GitHub's "Require conversation resolution", GitLab's
  "All discussions are resolved". Nothing in this tree stores a comment on a
  change, so this needs a comment record, a resolved flag and a surface to
  resolve on before it can be a condition.
- **Released.** §2's third orthogonal fact. No tag or release object exists
  here.
- **Up to date with base.** `worktrees.branch_state` computes `ahead`/`behind`
  but stores nothing, and a clean merge already subsumes it.
- **Documentation updated, acceptance criteria met, deployed to staging.** The
  recurring agile items that no vendor enforces, because no machine can check
  them. They stay prose on the ticket's `Output:` line.

### 7.3 Unevaluable is unmet, and it blocks

A condition that cannot be evaluated (no change record, no database, an
unreadable gates payload, an exception anywhere) is a condition that failed,
so the `done` write is refused. This is `auto_merge`'s existing rule and its
existing mechanism: `_safely` turns any exception into a failed `Condition`,
and `assess` evaluates every condition rather than short-circuiting so the
refusal can say what else was true.

### 7.4 Where the default lives

`services/done_policy.py` over `.runtime/done-policy.json`, the third instance
of the idiom `scope_execution.py` established and `merge_policy.py` already
follows: keyed by scope key and never by a path, an unknown value clamps to
the default, and setting a scope back to the default deletes its key so the
file is only ever the list of scopes the operator deliberately changed.

`DEFAULT_CONDITIONS = ("merged",)`. Nothing else is on by default. It is the
condition the operator ruled on in §6 and the one whose absence produced the
original defect; every other kind is opt-in.

The branch that `merged` means: `worktrees._canonical_branch` already answers
this, preferring the project record's tracked `repo.default_branch` and
falling back to `git_service.main_branch`. Measured 2026-08-25: no project in
`.runtime/projects.json` carries a `repo` binding, so every scope in this
checkout resolves by the fallback today. There is no per-scope branch override
and none is added here; `done_policy` stores the condition set, and the branch
comes from the project record, which is where a branch name already lives.

### 7.5 How a per-ticket set composes with the scope default

Union. A ticket's `acceptance.conditions` ADD to its scope's set and cannot
subtract from it, so a ticket cannot weaken the §6 ruling. The exemption for
work that legitimately has no change is unchanged and stays where §6 put it:
there is no change, so there is nothing unmerged to refuse.

### 7.6 The refusal

Unchanged in kind from §6, wider in content: it names every unmet condition
and why each is unmet, in the `Condition.detail` sentence `auto_merge` already
writes for the merge basis. A refusal that only says "not done" is the same
unbacked assertion this whole document exists to remove.
