<!-- author: self -->
Recovered 2026-09-01 from the settled docs, measured against the live tree, and
checked against outside evidence. This is the short version; the long ones are
`docs/platform-redesign/threads-branches-and-pages-2026-08-29.md` (model of
record), `primitive-roles-2026-07-18.md`, `agent-roles-research-2026-07-16.md`
section 3, `work-loop-core-design-2026-08-21.md` and
`WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md`.

## What the plan decided, and why

- **A role is a set of WRITES, not a job title.** maker | reviewer | triager |
  keeper | broker, closed, because output authority is the only axis a
  deterministic shell can enforce and the only one that makes a failure mode
  structural instead of behavioural. A triager that cannot write notes cannot
  re-trigger itself; that is a guarantee, not a prompt instruction.
- **There is no orchestrator agent, and there should not be one.** The conductor
  is deterministic code, about twenty lines. The overseer function that needs
  judgement is a triager at a higher scope, which is the same role with a
  different scope key. A named orchestrator was rejected twice, once as a
  conductor and once as a persistent process, both times because it duplicates
  the dispatcher.
- **There is no planner agent either.** Decomposition happens in the main chat
  with the operator, which is where he gets to steer. The background roles are
  exactly three: triager (intake), worker (execute), reviewer (verify). The
  judgement pass that scopes a ticket is `ticket_scope_review` plus
  `triager.run_queue_triage`, a pass over the queue, not a role.
- **The human-facing name is free text.** Planner, Builder, Researcher,
  Orchestrator are names on a role record `{name, prompt, tools, writes}`;
  `writes` stays the closed enum. That split is what the five-value enum was
  always missing.
- **No agent addresses another agent.** Stigmergy: the ticket store and the note
  store are the whole coordination surface. No inbox, no queue, no handoff
  protocol, no leader election.
- **Escalate, never re-roll.** A role at the edge of its authority files a
  ticket or blocks; it never acts across the edge and never handles its own
  failure. A re-rolled verdict turns a gate into a slot machine.
- **Blocked ends the run.** A block is a ticket state, not a run state, so
  nothing holds a worker slot waiting for a person.
- **armed and role are independent fields on a thread.** armed is the only field
  that decides whether a thread works unasked. Neither implies the other.
- **The ticket is the primary axis**, because it is the only object that spans
  the whole life of the work. A branch is a place, a thread is an actor, and
  delegation is a call graph link (who asked whom), not a tree.

## What exists, what is dead

Wired and live: the ticket store; `next_ready(unattended=True)` and its
fail-closed predicate; `reviewer.sh` (deterministic, and it deliberately never
sees the worker's self-report); `project_ralph.sh eval_criteria`, which EXECUTES
a ticket's criteria; `change_gates.CRITERIA_GATE`; `chat_drain.continuation_loop`
plus `wakeups`, the durable seed-and-resident-loop that can start a turn with
nobody watching; `agent_authority._ROLE_WRITES`.

Dead: `services/triager.py` (1004 lines; its state dir did not exist until
today); `ticket_scope_review`; `scripts/dispatch_triggers.sh`, the only
role-to-runner switch in the tree, with zero callers, which orphans
`triager_run.sh`, `memory_keeper_run.sh` and `dispatch_grants.mint_in_scope_grant`;
`cron_agent.sh` (crontab line commented out); the agents/*.md roster (four files,
one run between them since 2026-07-18); `latitude` (null on every ticket).

The gap that matters: **`chat_thread.armed` landed today and nothing reads it.**
The whole executable-acceptance machine is built and was starved: the field is
null on every one of the 195 tickets it was measured over.

## What the outside evidence changes

1. **Serialize writes, parallelize reads.** Extra roles may add intelligence,
   never actions. Cognition's own 2026 revision of "Don't Build Multi-Agents".
   The branch lease being a queue is the right shape.
2. **The reviewer must not inherit the generator's context.** Reviewing from the
   artifact alone beat reviewing as a subagent that inherited the generation
   prompt; reviewing twice in one session does not help at all.
3. **Self-verification is an activation failure, not an effort failure.** A
   64.5 percent blind spot across 14 models on errors attributed to self versus
   to another; self-preference in LLM-as-judge persists even when authorship is
   hidden. So "verify more carefully" cannot work, and neither can a second
   in-session pass. Fresh context is the floor; a different model is stronger.
4. **Split on context boundaries, not job titles.** If the next role needs
   everything the last role read, it is the same role. Multi-agent costs 3-15x
   the tokens.
5. **Ambiguity, not weak graders, is what gets exploited.** Reward hacking was
   0.7-2 percent on unambiguous tasks and 22-44 percent on ambiguous ones, and
   held-out tests were the WEAKER detector. So: unambiguous acceptance criteria
   first; executable checks plus a cheap behavioural monitor (did the change
   edit its own test?) second; rubrics last. This is the one finding that
   corrects the "executable checks are sufficient" instinct.
6. **A plan buys termination and tokens, not accuracy.** Full-horizon planning
   reached accuracy parity with step-by-step while cutting input tokens 2-3x and
   cutting repetitive tool loops from 30-45 percent to 1.9 percent. So do not
   put a human gate on a plan for quality reasons; gate it where it writes.
7. **Spec-driven development has no measured defect benefit** (100k PRs,
   within-author fixed effects, all five vendor hypotheses unsupported; the
   author names the complexity confound himself). Do not mandate a spec artifact
   as a quality gate. Spend the effort on acceptance criteria instead.
8. **Human-in-the-loop converges on monitor-and-interrupt, not approve-each-step.**
   Experienced users pre-approve more and interrupt more; agents pause for
   clarification twice as often as humans interrupt. Reserve mandatory human
   authority for the irreversible class. That is the escalation channel this
   design already has (`awaiting`, blocked, the six operator verbs) and has
   never used.

## The disease, stated once

Design completes and build does not. 668,795 words of design prose, 223 new docs
in six weeks against 87 feature commits, 1 of 22 retirement commitments landed,
188 of 195 tickets never attempted, 0 with executable acceptance. The rule that
follows is the one in the model of record: a change deletes the surface it
replaces in the same commit that adds the new one. Every invariant in this repo
that actually holds is enforced by something that fails the build.
