# Task management exploration: durable tasks, goal autonomy, and external trackers

Status: exploration, 2026-07-12. **This is a research synthesis to be scheduled as its own effort. It is NOT part of the current platform-redesign implementation.** It exists so the redesign (see `proposal.md`) can reserve the right seams: Task is a first-class noun scoped to Project, and the current scattered task machinery (`services/tasks.py`, root-level `.tasks.json`, per-agent-cwd task files) is known-wrong and will be replaced by this design when scheduled.

## 1. The scoping decision

**Durable tasks are per-project. Chats hold transient plans that can be promoted. Agents are delegates with queue views, never owners.**

Rationale, from how every serious shipping tool splits this:

- Nobody scopes durable tasks to a chat. Claude Code's todo list is in-session working memory that evaporates at session end; Devin's task state dies with the sandbox; Cursor and Codex bind a task to a branch and PR, not a conversation. Chats and sessions everywhere hold transient plans; durable work items live at repo/project or issue level. This confirms the operator's lean: per-chat durable tasks would be the confusing option, and we do not build them.
- The project is the only container that outlives both chats and agents, survives context compaction, and travels with the code when the project is forked or ejected. It is where the current system already rolls tasks up, minus the scatter.
- Per-agent task stores are how we got today's mess (tasks split across root, each project, and each agent cwd, with operator/agent owner mixing). The Linear model is the correction: assigning work to an agent makes the agent the **delegate** while a human retains ownership. An agent's "task list" is a filtered query over project backlogs, not a store.

The one boundary that needs an explicit gesture: **promotion**. A plan line in a chat or an in-run todo becomes durable only when promoted into a project Task (a one-click "promote to task" on plan items, mirroring the chat-to-project promotion gesture in the main proposal). Everything not promoted is working memory and is allowed to vanish. Defining this gesture crisply is most of the UX design; the ephemeral-vs-durable boundary is the whole system.

## 2. One Task model for tickets and goals

The operator wants both: discrete tickets a builder picks up "whenever", and Ralph/Codex-style goals an agent works until met. These are not two systems. A goal is a task whose acceptance criteria are a verifier rather than a static checklist. One object, a `type` discriminator, shared lifecycle.

```jsonc
Task {
  id: string,
  project_id: string,          // scope: always project-owned
  title: string,
  body: string,

  type: "checklist" | "goal",
  status: "backlog" | "ready" | "in_progress" | "blocked"
        | "in_review" | "done" | "cancelled",

  owner_id: string,            // human; always retained
  assignee: null | "agent:<id>" | "human:<id>",   // agent = delegate, not owner

  checklist: [ { text, done } ],   // optional sub-items
  depends_on: [task_id],           // gates pickup
  priority: number,
  labels: [string],

  // goal support
  acceptance: {                // REQUIRED when type = "goal"
    kind: "tests" | "eval" | "binary_checklist",
    spec: string               // command, eval id, or checklist ref
  },
  max_iterations: number,      // hard loop cap
  plan_ref: string,            // path to persisted PLAN/TODO file the loop reads (Ralph-style disk state)

  // external tracker linkage
  source: "native" | "github" | "jira" | "linear",
  external_id: string | null,  // idempotent write-back key
  external_url: string | null,
  sync_origin: "user" | "sync",// last-writer tag, echo guard

  // execution linkage
  run_ids: [string],           // Runs that worked this task (proposal's Run object)
  evidence: [ { kind, ref } ], // test results, eval output, PR links, logs

  created_at, updated_at, updated_by
}
```

Notes on the goal half:

- **Explicit lists and open goals are reconciled by the acceptance gate.** The AutoGPT failure modes (infinite "always something to improve" loops, goal drift by iteration 50, subjective completion judged by the same model doing the work, runaway bills) all trace to completion being a vibe. Here completion is a gate: `acceptance` must pass, `max_iterations` is a hard stop, and the loop's working state persists to `plan_ref` on disk so every iteration starts with fresh context reading a durable plan (the existing Ralph pattern, kept deliberately). Caps and budgets here are correctness and cost bounds on unattended loops, not multi-user guardrails.
- **Drift re-grounding**: the loop re-injects the original acceptance criteria into each fresh context, so iteration 30 is still solving the stated problem.

## 3. How an agent picks up work, and the completion gate

**Queue semantics.** An agent's queue is a query, not a store:

```
WHERE project_id IN (projects whose runs I am bound to)
  AND status = "ready"
  AND (assignee IS NULL OR assignee = me)
  AND all depends_on are done
ORDER BY priority
```

Pickup is an atomic claim (`assignee: null -> agent:<id>`, `status -> in_progress`) so two loop runs cannot double-claim, and a Run is opened and linked via `run_ids`. This is the Linear AgentSession shape mapped onto the proposal's Run object: the Run is the bounded lifecycle, the Task is the durable work item.

**The completion gate: an agent never self-marks done.** When the agent believes it is finished it writes `evidence` (test output, eval results, PR link, logs) and moves the task to `in_review`. Admission to `done` requires the gate to pass:

- `type: goal`: the `acceptance` verifier runs (tests or eval). Pass admits; fail returns to `in_progress` or `blocked` with the failure attached as evidence.
- `type: checklist` with no runnable verifier (docs, research, design): a fresh-context reviewer (the `work-reviewer` pattern already in the stack) checks the diff and evidence against the checklist, binary yes/no per item. This is the fallback verifier for unverifiable-by-test work.
- Operator review remains available for anything, but is never the required path for routine work; the point of the gate is that autonomy does not depend on the operator being awake.

This division of authority is the single most load-bearing rule in the design. Every documented multi-agent failure mode worsens when the worker judges its own completion; the fresh-context gate is cheap and already proven here.

## 4. External trackers: GitHub Issues, Jira, Linear

Optional integration, designed so pulling it never orphans work.

**Shadow-task model.** An imported ticket becomes a native Task with `source` and `external_id` set. All Workshop logic (queues, claims, gates, Runs) operates only on the native object. Removing an integration leaves fully functional native tasks behind.

**Direction: one-way import by default, write-back on explicit action.** Continuous field-level bidirectional sync is the known trap: each synced change triggers the other side, echo loops amplify, and conflicts are silent. The default posture:

- Import: poll or webhook brings external issues in as shadow tasks.
- Write-back happens only on discrete agent/user actions: a comment posted, a PR opened, a status transition. Each write-back is idempotent, keyed on `external_id` (create once, update thereafter).
- Every change carries `sync_origin`; the sync process ignores changes it originated. Origin tagging plus idempotency are the two non-negotiables.
- Full two-way sync exists only behind a per-integration opt-in flag, with the same origin tags.

**Delegate semantics carried outward.** Linear's agent model is the reference: agents are first-class assignable actors, but assignment makes them delegates while humans keep ownership; a bounded session is created per delegation and the agent must show liveness quickly or be marked unresponsive. GitHub's assign-issue-to-agent -> draft PR -> iterate-on-review-comments flow maps cleanly onto our claim -> Run -> evidence -> in_review lifecycle, with the PR as the primary evidence artifact.

## 5. Risks and open questions

1. **The promote gesture is the design.** Where exactly can a transient plan line be promoted from (chat message, in-run todo, plan file), and what context travels with it? Under-designing this leaves backlog living in chats again by habit.
2. **Verifiers for unverifiable work.** The binary-checklist reviewer fallback works but is weaker than tests. Is there a class of task (research memos, design docs) where the operator wants to be the standing gate instead? Needs a call.
3. **Cost governance defaults.** `max_iterations` and a budget cap are mandatory on `type: goal`; the defaults matter (too low: goals stall silently; too high: overnight bill surprises). Propose conservative defaults plus per-task override, tune from real runs.
4. **Queue contention beyond claims.** Atomic claim prevents double-pickup, but `depends_on` chains plus multiple loop triggers can starve low-priority tasks or deadlock on a cancelled dependency. Needs a small scheduler policy, not just a query.
5. **Migration of the current scatter.** Root-level `.tasks.json`/TODO.md and per-agent-cwd task files need a one-time consolidation into per-project stores, with owner-vs-delegate fields corrected. TODO.md stays as the git-friendly regenerated export (that part of `tasks.py` is right and kept).
6. **How much tracker surface to build natively.** With Linear/GitHub import available, the native UI can stay minimal (list, board optional, task detail). Recommendation: minimal native surface first; a kanban is a later luxury.
7. **Interaction with the Run object.** This doc assumes the proposal's Run lands first. If task work is scheduled before Phase 1 of the redesign, `run_ids` degrades to today's run-log references; acceptable but worth avoiding.

## References

- Claude Code session todos and Plan Mode; Devin session/coordinator model; Cursor background agents; OpenAI Codex cloud tasks; GitHub Copilot coding agent (issue-scoped)
- Ralph loop pattern (fresh context per iteration, disk-persisted plan as source of truth)
- AutoGPT-class failure analyses (unbounded goals, self-judged completion)
- Linear agent/AgentSession/delegate model; two-way sync echo-loop prevention (origin tagging, idempotent keys)
- Existing stack: `services/tasks.py`, `project_ralph.sh`, the fresh-context `work-reviewer` gate
