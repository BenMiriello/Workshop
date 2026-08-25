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
