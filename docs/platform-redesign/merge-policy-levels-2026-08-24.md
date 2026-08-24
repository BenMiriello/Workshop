# What happens to finished work: the levels, and the contract

**Status: current. 2026-08-24.** Extends
`changes-and-review-2026-08-22.md` §6, which decided the two-knob model and is
still right. This adds the tier that was missing, the outside research the
operator asked for, one simplification that removes a policy value, and the
contract sentence the settings screen has to be able to say.

## 1. The question

> "What we do with work once finished in a worktree or by an agent ... how we
> set the policy, what levels we set the policy at, if we have a global default,
> if we have a project default, if we can change it per ticket or agent or any
> combination ... a simple clear contract with the user ... but also wanting to
> have configurability, and that's a tough balance."

## 2. The state, measured, and it is further along than the question assumes

The two-knob model is not just designed, it is **built and wired end to end**:

| piece | file | size | state |
|---|---|---|---|
| scope policy | `services/merge_policy.py` | 157 | built, fail-closed |
| per-change flag | `db.models.Change.auto_merge` | migration landed | built |
| gate results | `services/change_gates.py` | 470 | built AND wired |
| claim-to-diff | `services/claim_check.py` | 590 | built |
| the decision | `services/auto_merge.py` | 593 | built |
| routes | `GET/POST /agents/autonomy/merge-policy` | | built |

`change_gates.py:423` calls `auto_merge.consider(...)` on **every reviewer
run**, and all 3 live changes carry recorded gates. So the evaluation is already
happening on every completed piece of work. It answers `manual` every time,
because no scope has a policy and no change carries the flag.

**Two things are missing, and neither is the mechanism.** There is no UI (only
`schema.gen.ts` knows the endpoint exists, so the operator cannot see or set
it), and there is no tier above the scope. Plus one decision that is his.

Also found: ticket cb311b87, blocked on "attach the machine gate results to the
change", is **stale** — that work is done and running.

## 3. What everyone else does

**Agent authorship does not lower the bar; it raises the burden of proof.**
GitHub's coding agent always works on its own branch and opens a PR, and its own
review posts as a *Comment*, never an *Approve* — so it is structurally incapable
of satisfying a required approval. That is a deliberate design choice worth
copying: the agent may do the work and may attest to it, but its attestation is
not an authority to land.

**The standard graduated pattern is classify-then-auto-merge, not trust levels.**
Dependabot's widely-used config auto-merges `semver-patch` and holds everything
else, gated on green CI. The lesson repeated everywhere it is written up: *it is
only as safe as your CI*, and the classification, not the author, is what makes
it safe.

**Correctness and authority are separate signals.** Kubernetes' Prow splits
`/lgtm` ("this code is right") from `/approve` ("this may land here", authority
derived from OWNERS), and tide merges only when both hold and no hold-label is
set. Conflating them is how "approve" came to mean two opposite things in this
repo's own routers, which was the 2026-08-22 defect.

**Two levels, answering different questions, is the norm.** GitHub has repo
branch protection (what must be true) and per-PR auto-merge (merge when it is).
They compose without a precedence puzzle precisely because they are different
kinds of setting.

## 4. The design

### 4.1 The distinction that makes multiple levels intelligible

The trap in "global default, project default, per-ticket, per-agent" is that it
sounds like one setting at four levels, which forces a precedence puzzle nobody
can hold in their head. It is not one setting. It is two different kinds:

> **Green gates decide whether work is CORRECT. The policy decides only WHO is
> allowed to say "land it" without reading it.**

Nothing merges on a red gate, at any level, ever. That is not configurable and
it should never appear as a setting. What the levels configure is authority.

### 4.2 The three tiers

**Tier 1 — deployment default.** `manual`. What a scope's authority is when it
has not been given one. Today this is a constant, `merge_policy.DEFAULT_POLICY`.
Making it configuration is the missing tier and it is a small change.

**Tier 2 — scope policy.** Built. The scope is the right home and the existing
argument holds: a scope already carries `execution` (reactive / supervised /
autonomous) and merge policy is the next notch on that same dial. On an **agent**
it would mean the same work is reviewed differently depending on who picked it
up, which is the wrong invariant. On a **chat** it would be a property of a
disposable session.

**Tier 3 — per change: the operator pre-approves one diff he has read.** Built.
This is the answer to "can I set it per ticket": yes, and it already exists, but
it is *not a policy* — it is a person taking responsibility for one specific
change in advance so he does not have to babysit the gates. That is why it may
act where the scope policy is `manual`: a person read it.

So the two authorities are a **person in advance** (`merged_by:
change_auto_merge`) and the **scope as a standing rule** (`merged_by:
scope_merge_policy`), and the record already distinguishes them.

### 4.3 Rejected, with reasons

- **Per-agent policy.** Wrong invariant, as above.
- **Per-chat policy.** A disposable session should not carry durable authority.
- **A deployment CEILING that scopes may not exceed.** This is the natural
  multi-user extension and it is named here so it is not lost, but building it
  now is a multi-tenant guardrail in a single-operator system: the operator
  constraining himself against himself. Not built, deliberately.
- **Priorities or weights between levels.** The resolution is one lookup.

### 4.4 Resolution, which is one sentence

    A change merges when every non-advisory gate is green AND either
    the operator pre-approved that change, or the scope's policy allows it.
    A scope's policy is its own if set, otherwise the deployment default.

## 5. One simplification: delete `auto_when_green`

The enum today is `manual | auto_when_green | auto_when_green_and_low_risk`.
The middle value should go, and removing it is what makes the contract
explainable.

Ask what `auto_when_green` is for. If a person read the diff, that is per-change
pre-approval, which is Tier 3 and already exists. If nobody read it, then the
only thing standing between a green build and a 40-file unreviewed refactor
landing on main is nothing at all. `auto_when_green` is the one value with no
defensible use: it is strictly more dangerous than the risk-classified option
and strictly less useful than pre-approval.

Delete it and the setting becomes **two states**, which a person can hold in
their head and a settings screen can state honestly:

- **I review everything.** (`manual`, the default)
- **I review anything that is not small and boring.**
  (`auto_when_green_and_low_risk`)

Where "small and boring" is not a vibe. It is the existing classifier, all of
which must hold: every gate green; the diff inside the ticket's declared
`touches`; no dependency change, no migration, no secret or auth path; under the
file and line ceilings; and anything it cannot classify is not low risk.

## 6. The answer to the open question

`changes-and-review-2026-08-22.md` §8 left this genuinely open: should a scope
policy exist at all in a single-operator system, or is per-change pre-approval
enough?

**Recommendation: keep it, delete `auto_when_green`, and do not arm any scope
until the loop runs again.**

The argument for a standing authority is entirely about unattended time. With
the loop off, it buys nothing, and `manual` everywhere is correct today. When
the loop is back on, the failure it prevents is real and already measured: work
that lands `in_review` and waits accumulates, and a review queue that grows
faster than it drains turns the supervised ceiling from a safety property into
the bottleneck. Three branches are already waiting.

The argument against is that it merges code nobody read. The risk classifier is
the answer to that objection, which is why the value that keeps it is the one
that keeps the classifier, and the value that goes is the one that does not.

## 7. What to build

1. **The Settings surface.** The endpoint has existed with no UI, which is this
   repo's most repeated defect class. It shows the deployment default, every
   scope's policy, which scopes differ from the default, and the low-risk
   thresholds as the policy numbers they are.
2. **The deployment default tier**, so `manual` is a configured value rather
   than a constant.
3. **Delete `auto_when_green`** and its branch.
4. **Close cb311b87**, which is done.

Not now: the ceiling, per-agent policy, notification on auto-merge (the merge
record already says who and why), and arming any scope.
