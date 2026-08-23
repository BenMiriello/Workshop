# The Change: what gets reviewed, and how work becomes merged

- Date: 2026-08-22
- Status: **C1 through C5 are BUILT** (C3 on 2026-08-23; the rest 2026-08-22
  evening). C6 (the claim-to-diff check) and C7 (auto-merge) are not.
  Section 8 carries what each step turned out to need.
- Answers the question left open by
  [`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
  §12 ("does accepting merge?"), which turned out to be the wrong shape of question.
- Depends on branch isolation (that doc, and
  [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)).

---

## 1. Where "accept" came from, and what is wrong with it

It was not invented for the workspace design. `entity-model-2026-07-18.md:113-127`
ratified the ticket lifecycle `todo -> doing -> in_review -> done`, with
`in_review -> done: reviewer approves`. `services/task_decision.py` implemented it on
2026-08-22 to fix a real bug: the word "approve" meant two opposite things in two
routers, and answering "Approve" to finished work sent it back to `todo`.

So `accept` is a legitimate, pre-existing, ratified transition. The problem is not the
verb. It is that **the thing being accepted is a status, not an artifact.**

Today `in_review` means a worker said it was finished. The operator is asked to accept
work he cannot see: no diff, no test result, no preview, no record of what was
actually touched versus what the ticket asked for. `accept` flips a field. Every
mature system solved this by making the reviewable thing a **change**, and this design
has been treating a ticket as though it were one.

## 2. The standard model: intent and change are two records

Every system that has run this at scale separates them, under different names:

- **Gerrit**: an issue is elsewhere; the reviewable unit is a *change* (one commit,
  amended in place, reviewed pre-submit).
- **Phabricator**: *Maniphest* task for intent, *Differential* revision for the diff.
- **GitHub / GitLab / Bitbucket**: issue for intent, pull or merge request for the diff.
- **Jira / Linear**: the issue is the intent, and its **status is driven by the change**:
  a PR opening moves the issue to In Review, a PR merging moves it to Done.

The transferable rule, and the one this system is missing:

> **A ticket says what should be true. A change says here is the code that makes it
> true. The ticket's status is DERIVED from its changes; nobody flips it by hand.**

That single inversion removes the deepest problem in the current loop, which is that
the worker declares its own completion and the reviewer gate judges a claim rather
than an artifact. If `in_review` means "a change is open" and `done` means "its change
merged", then completion is a fact about the repository rather than an assertion.

## 3. The Change, defined

Local-first, remote-optional. The same record whether or not GitHub is involved, which
is the operator's explicit requirement.

```
change
  id, org_id                       -- RLS per the Ticket model
  app_id
  worktree_id                      -- the branch it is built from
  base_ref                         -- what it merges into
  head_sha
  title, summary                   -- authored by whoever produced it
  ticket_ids     [uuid]            -- 1..N; a change may satisfy several
  status         enum(draft, ready, changes_requested, approved, merged, closed)
  gates          [{name, status, detail, at}]      -- machine checks (§5)
  remote         {kind, url, number} | null        -- a mirrored GitHub PR, if bound
  merged_sha, merged_at, closed_reason
```

**Naming.** "Change" rather than "pull request" or "merge request": both of those
presuppose a remote and a request to someone else, and here the common case is
neither. It is also the word Gerrit and Google use for exactly this artifact. The UI
verb stays ordinary: **Review**, then **Merge**.

**Relationship to the branch.** One branch produces one change. A change may carry
several tickets, and then it merges as one unit, which is why
`workspace-ownership-and-leases` §10 defaults a new session to a **new** branch rather
than joining an existing one.

**Relationship to GitHub.** When the app is bound to a remote, the change **mirrors**
to a PR: `worktrees.publish_branch` already does this. When it is not, `merge_branch`
merges locally. Both paths already exist and are already first-class (ticket C3). The
change record is what makes them one concept instead of two code paths with different
affordances.

## 4. Status flows from the change, not from a self-report

| event | ticket becomes |
| --- | --- |
| change opened `ready` | `in_review` |
| change `changes_requested` | `doing` |
| change merged | `done` |
| change closed unmerged | back to `todo`, or `parked` at the attempt cap |

This is the Jira/Linear convention and it is what makes `accept` honest: accepting is
approving a change, and merging is what closes the ticket. `task_decision`'s verbs
survive, but `accept` now means "approve this change" and merge is its consequence,
rather than a status flip with nothing behind it.

## 5. Giving the reviewer enough to review well

The operator's requirement, and the research says precisely where the danger is.

**The measured failure mode of agent-authored changes is the description not matching
the diff.** In a 2026 study of AI coding-agent PRs, descriptions claiming
unimplemented changes were the single most common defect (45.4% of inconsistent PRs),
and high message-code inconsistency correlated with 51.7% lower acceptance and 3.5x
longer time to merge (§9.3). This system has exactly that hazard, amplified: the
worker writes both the code and the completion claim, and today the operator sees only
the claim.

So the review surface is built around distrusting the summary:

1. **The diff is the primary object, and it is shown first.** The summary is secondary
   and explicitly labelled as the author's claim.
2. **A claim-to-diff check.** Each assertion in the summary is mapped to the files
   that would substantiate it, and any claim with nothing behind it is flagged. This
   is cheap to approximate and attacks the number-one measured defect directly.
3. **Machine gates first, human attention last.** The recommended order is
   dependency and provenance, then secrets and static analysis, then a second-model
   review, then a person (§9.3). This repo already has the machine layer:
   `reviewer.sh`, the ratchets, `check_design.sh`, `npm run typecheck`, the test
   suites, `check_routes_answer.py`. The change record carries their results as
   `gates` so a person never spends attention on what a checker already answered.
4. **Intent beside outcome.** The ticket's Objective, Output and Boundaries next to
   what was actually touched, with anything outside the declared `touches` flagged.
   The three questions a human reviewer should actually be answering are: does this
   match an architecture we chose, did the trust boundaries hold, and can I
   reconstruct why it is this way.
5. **A running preview.** `branches-worktrees-and-environments` §6 already designs a
   per-branch preview deployment on its own port and URL. Review is testing, not only
   reading, and this is the piece that makes that true.
6. **Risk signals, surfaced not buried:** new dependencies, schema migrations, deleted
   files, touched secrets or auth paths, and diff size. Size matters because reviewer
   accuracy collapses with it, which is an argument for many small changes rather than
   one large one.

## 6. Pre-approval, which is a standard mechanism with a standard name

The operator's "pre-approve mechanic" exists in every system as **auto-merge**:
approve now, and merge automatically when the checks pass. GitLab renamed
merge-when-pipeline-succeeds to Auto-merge in 16.0 and widened it to
merge-when-checks-pass in 16.9; GitHub has per-PR auto-merge; merge queues are the
same idea with serialization added (§9.4).

Two knobs, both standard, and deliberately no more:

- **Per change: auto-merge.** "Merge this as soon as the gates are green." Set by the
  operator on a change he has read but does not want to babysit.
- **Per scope: `merge_policy`.** `manual` (default), `auto_when_green`, or
  `auto_when_green_and_low_risk`.

**Where it lives, since the operator asked whether it belongs to the ticket, the agent
or the chat: the scope, with a per-change override.** A scope already carries
`execution` (reactive / supervised / autonomous) via `services/scope_execution.py`.
Merge policy is the next notch on that same dial and belongs beside it rather than on
a new axis. Putting it on an agent would mean the same work is reviewed differently
depending on who picked it up, which is the wrong invariant; putting it on a chat
would make it a property of a disposable session.

**Low-risk must be defined, not vibed.** The Ona result (auto-approving low-risk PRs
cut lead time 74%) depends entirely on the classifier being conservative. Proposed
definition, all of which must hold: every gate green; the diff stays inside the
ticket's declared `touches`; no dependency change, no migration, no secret or auth
path; and a size ceiling. Anything else is not low-risk, and the classifier fails
closed.

## 7. What already exists

Most of the local half is built and unwired, which is the same pattern found across
this effort.

- `worktrees.diff_branch` (`:310`) already returns a shortstat summary plus per-file
  unified diffs. That is the review payload.
- `worktrees.merge_branch` (`:345`) merges locally, fast-forward where possible.
- `worktrees.publish_branch` (`:449`) opens the GitHub PR for a bound app.
- `worktrees.discard_branch` (`:429`), `cleanup_merged` (`:500`), `repo_state` (`:534`).
- `scripts/reviewer.sh` is the machine gate and already produces per-check results.
- `services/task_decision.py` owns what an answer means and both routers call it.

**What is missing is the record and the surface**, not the plumbing: a `change` row
tying a branch to its tickets and gate results, a review view, and the status
derivation in §4.

## 8. Plan

Slots into the branch-isolation plan after B2 (the worktree record). Each step is one
ticket.

Built: C1 (`9febe9d`), C2 + C5 (`865e219`, `bcc1f5b`, `50fc367`), C4 (`1ccf888`).
Two things the plan did not anticipate, both found by running it: a merged
change loses its diff unless the base SHA is pinned BEFORE the merge (diffing
against the base REF afterwards is guaranteed empty, since it now contains the
change), and the sweep helper that opens a change must call
`ownership.pin_operator` or it dies silently on OwnerUnset.

- **C1. The change record**, created when a branch first has commits ahead of base.
  `diff_branch` becomes its payload. No UI yet.
- **C2. Status derivation** (§4): ticket status follows its change's lifecycle, and
  the worker stops setting `in_review` itself. This is the step that removes
  self-reported completion.
- **C3. Gate results on the change**: `reviewer.sh` and the test and typecheck runs
  write their outcomes to `gates` instead of only to a log. BUILT (2026-08-23).
  `services/change_gates.py` translates a reviewer run into gate entries and
  writes them; `reviewer.sh` calls it whenever `REVIEWER_CHANGE_ID` or
  `REVIEWER_GATE_PROJECT`+`REVIEWER_GATE_BRANCH` is set, and `project_ralph.sh`
  routes the receipt's verdict onto the change the moment it opens one. Two
  things this step turned out to need. First, a SKIPPED check has to be
  recorded with its reason: most of the reviewer's checks are gated on whether
  the diff touches the paths they inspect, and an absent gate reads to a person
  exactly like one that did not apply. Second, the write has to resolve the
  backend venv rather than PATH's `python3` — the store is unreachable from the
  bare interpreter, so a bare call logs an import error forever and writes
  nothing, which is the same silent death `open_change_for_task` had.
- **C4. The review surface**: diff first, summary labelled as a claim, gates, intent
  beside outcome, risk signals. To the design system, on the existing sheet host.
- **C5. Merge on approve**, local or via the mirrored PR, with the existing
  park-on-conflict behaviour as the failure path.
- **C6. The claim-to-diff check** (§5.2). Its own step because it is the novel part
  and should not delay C1 to C5.
- **C7. Auto-merge per change, then `merge_policy` per scope** (§6), with the
  fail-closed low-risk classifier.

**Open, and genuinely the operator's:** whether `auto_when_green_and_low_risk` should
exist at all in a single-operator system, or whether per-change auto-merge is enough.
The argument for the scope policy is that it is the only thing that makes a long
unattended run useful overnight. The argument against is that it is the one place in
this design where the system merges code nobody looked at.

## 9. Research appendix

### 9.1 Intent versus change as separate records
Gerrit reviews a *change* (one commit, amended in place) pre-submit; Phabricator pairs
Maniphest tasks with Differential revisions; the PR model has been essentially
unchanged in principle since 2008 and GitLab's MR and Bitbucket's PR are the same
concept. In all of them the issue and the diff are different records with different
lifecycles.
- graphite.com/guides/gerrit-vs-phabricator-key-differences ; stackshare.io/stackups/gerrit-code-review-vs-phabricator

### 9.2 Status derived from the change
Jira workflow triggers and Jira/GitLab automation transition an issue to In Review
when a PR is opened against it and to Done when that PR merges, explicitly so that
developers do not hand-maintain status. Same pattern in Linear's git integration.
- help.gitkraken.com/git-integration-for-jira-cloud/automatic-workflow-triggers-gij-cloud/
- atlassian.com/devops/automation-tutorials/jira-automation-rule-on-pullrequest-merge

### 9.3 Reviewing agent-authored changes
Message-code inconsistency is the dominant defect in AI-agent PRs: descriptions
claiming unimplemented changes were 45.4% of cases, and high-inconsistency PRs saw
51.7% lower acceptance and 3.5x longer merge times. The recommended pipeline is
machine gates first (dependency and provenance, secrets and SAST, a second-model
pass) so human attention lands on judgment: architectural fit, trust boundaries, and
whether the reasoning can be reconstructed. Reviewer accuracy degrades with diff size,
and agent-written tests deserve the same suspicion as the code.
- arxiv.org/pdf/2601.04886 (message-code inconsistency)
- github.blog/ai-and-ml/generative-ai/agent-pull-requests-are-everywhere-heres-how-to-review-them/
- augmentcode.com/guides/ai-agent-pre-merge-verification ; arxiv.org/pdf/2604.03196

### 9.4 Auto-merge and risk-classified pre-approval
GitLab's merge-when-pipeline-succeeds became Auto-merge in 16.0 and
merge-when-checks-pass in 16.9, requiring a green pipeline plus all required approvals
plus resolved threads; merge trains serialize it. GitHub has per-PR auto-merge.
Risk-classified auto-approval of low-risk PRs is reported to cut lead time
substantially, and depends entirely on a conservative classifier.
- docs.gitlab.com auto_merge / merge_trains ; ona.com/stories/auto-approving-low-risk-prs
