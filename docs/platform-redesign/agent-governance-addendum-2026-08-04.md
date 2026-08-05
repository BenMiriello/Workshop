# Addendum: chat modes, permissions, containment, and who calls whom (2026-08-04)

> **STATUS: DESIGN, approved for build.** Extends
> `agent-definition-and-governance-2026-08-04.md` with the four things the
> operator asked for after approving it: two named chat modes, a reasoned
> permission model where autonomous agents are contained by default, a
> containment story grounded in what is actually enforced on this box, and an
> explicit call/trigger graph. Every claim about the current boundary was
> verified on 2026-08-04, not read from a ticket.

## 1. Two chat modes

A chat is in exactly one of two modes, shown in the toolbar, switchable per chat.

| Mode | What it means | Work goes to | Ticket created |
|---|---|---|---|
| **Direct** | The conversation does the work itself. It may spawn Claude Code native subagents for context isolation, and their results come back into this conversation. Nothing is tracked outside the chat. | this session + native subagents | no |
| **Delegated** | Work leaves the conversation as Tickets, executed by Workshop agents under the governance in this doc, reported back into the chat. | the queue + Workshop agents | yes |

Why these two and not a spectrum: the distinction that matters to the operator is
**does this work outlive the conversation**. In Direct mode the chat is the record
and the transcript is the whole artifact. In Delegated mode the Ticket is the
record and the chat links to it. That is a binary, so the control is a binary.

`Direct` is the default for a new chat. Nothing about Direct mode is degraded:
native subagents are the same context-isolation mechanism Delegated mode uses
underneath, minus the tracking. The reason to switch to Delegated is that you
want a receipt, a reviewer gate, and work that survives the tab closing.

(If `Delegated` reads too passive in the UI, `Orchestrated` is the alternative.
`Direct` has no good alternative and should stay.)

## 2. What is actually a boundary on this box, today

This section exists because the system has previously described advisory fields
as if they confined an agent, and the standing remediation ticket (SEC-A2) is to
stop doing that. Verified 2026-08-04:

**Real, OS-enforced:**
- `clawd-a` is **not** in the docker group (`id -nG clawd-a` → `users`). This
  closed the critical escalation: one `docker run -v /:/host` used to be host root.
- Egress guard hook on Bash and WebFetch.
- Container network isolation for generated apps; `0600` env files and secrets,
  against *other unix users*.
- Recursive `claude` spawns are clamped (turns, model, depth).

**Not a boundary, and must never be described as one:**
- `scope`, `touches`, `latitude`, `grants`, `stores`. `touches` is a *post-hoc*
  check on the committed diff — it blocks marking a ticket done, not the writes,
  and is blind to non-repo writes, subprocesses and network. `latitude` is
  explicitly advisory. `grants` bind only code that voluntarily calls
  `services.stores`; a Bash `cat` walks past them.
- The per-owner data partition (a contextvar).
- **The autonomy loop still runs as `clawd-a`** — the same uid as the interactive
  session. Verified: `queue_runner.sh` runs as `clawd-a`. So an autonomous turn
  can rewrite the very files that describe its own permissions, and read every
  owner's personal data. SEC-A1 is marked done because the docker half shipped;
  the dedicated lower-privilege identity did not.

**Therefore the permission model in §3 is coordination, not confinement.** It
prevents accidents, makes intent explicit and reviewable, and gives the operator
something to configure. It does not stop a determined or confused agent. The
thing that would is finishing A1: a separate uid for the autonomy loop. That is
listed in the plan, and until it lands, no part of §3 may be presented in the UI
as security.

## 3. Permissions: contained by default, opted in

Three concentric limits, each answering a different question.

### 3.1 What may it write (role)

From the parent doc: `role_may_write(role, target)` over
`{tree, tasks, notes, learnings, verdict, nothing}`. This is the **upper bound**,
set by the role, and it is the only thing that can grant. Everything below can
only narrow.

### 3.2 What may it touch (scope + touches)

Narrows the upper bound to a place: an agent's own scope, and the file globs its
current Ticket declares. Default `touches: []` currently means *unrestricted*,
which is the wrong default for a containment field and inverts on build:
**`[]` becomes "nothing outside the ticket's declared files"**, and a ticket with
no declared touches may not write the tree at all.

### 3.3 What may it do without asking (autonomy × reversibility)

`autonomy` says how much an agent may do unattended:

- `reactive` — manual invocation only. **The default for every new agent.**
- `supervised` — may run on its triggers; results land in `in_review`.
- `autonomous` — may run and commit, subject to the reviewer gate.

And crossing it, the rule that makes `autonomous` safe enough to enable:
**an irreversible action always requires approval, whatever the autonomy mode.**

| Reversible (no approval) | Irreversible (always approval) |
|---|---|
| edit files in a worktree, commit locally, write notes/tickets, run tests | push, open/merge a PR, deploy, delete outside the worktree, send anything off-box, spend money, rotate a secret |

This is the pattern current practice converged on: run freely through reversible
steps, stop before anything that cannot be undone. It also means the operator can
turn autonomy on without the decision being all-or-nothing.

**A Ticket is the unit of approval.** Approving a ticket authorises the reversible
work inside its declared touches and nothing else — plan-level approval, which the
research identifies as the sweet spot, and which this system already had without
naming it.

### 3.4 Opt-in, and visible

Every one of the above is declared in the agent's one definition file and rendered
on its page in words ("may write notes and learnings; may not touch the tree").
Nothing is granted implicitly, nothing is inherited silently, and an agent with no
declaration is `reactive` with the empty write set.

## 4. Who calls whom

Explicit, hierarchical, and acyclic. Fully-connected peer messaging is rejected:
at 10 agents a mesh has 45 edges, past what can be reasoned about or tested, and
the research treats hierarchy as a reliability requirement above ~8 agents.

```
Operator
  └─ Chat (Direct)         → native subagents (isolated context, return a summary)
  └─ Chat (Delegated)      → files Tickets
        └─ Queue/dispatcher → starts a Run for one Ticket
              └─ maker      → does the work in a worktree
              └─ reviewer   → verdict on the maker's diff (never edits)
        └─ triager          → files/links/escalates Tickets (never edits the tree)
        └─ keeper           → writes notes/learnings (never edits the tree, never files)
```

Rules:

1. **Only the queue starts an agent.** A chat files a Ticket; it does not spawn an
   agent directly. One entry point means one place to enforce §3.
2. **Delegation is off by default** (`delegatable: false`), matching what CrewAI
   now defaults to after loop problems in the wild. An agent that may delegate
   declares it, and declares to whom.
3. **Depth cap of 2**, consistent with the entity model's sub-ticket cap. A maker
   may not spawn a maker that spawns a maker.
4. **No cycles.** A reviewer may not call the maker it reviewed.
5. **Every run carries a budget** (iterations, wall clock) — the stall/wander
   guard, already present in `limits`.
6. **Agent→chat messages do not enter the transcript uninvited** (interaction
   model D4): they surface as a dismissible item.

## 5. What "autonomy is on" will mean in practice

Today it means nothing, because two global brakes are engaged and no agent
declares a mode. After this ships, for a given agent, work happens only when all
of these hold:

1. the global brake is clear (**one** brake after this, not two),
2. the agent's `autonomy` is `supervised` or `autonomous`,
3. a trigger fired (cron, event, or a queued Ticket),
4. the action is within its role's write set and the ticket's touches,
5. and if the action is irreversible, the operator approved it.

That chain is renderable, which is the point: the agent page can say exactly which
link is currently open and which is blocking.

## 6. Plan (supersedes §5 of the parent doc)

**P0 — Truth.** Reject an invalid `role` instead of silently coercing it. Agent
page leads with the contract; explicit "never run, because X" state.

**P1 — One definition.** The seven mechanisms collapse to one file format.

**P2 — Authority.** `role_may_write` + tools derived from it.

**P3 — Activation.** Reversibility classification; `touches: []` inverts to
deny; collapse the two global brakes into one; delete the flag file.

**P4 — Chat modes.** Direct/Delegated as a per-chat field and toolbar control.

**P5 — Finish SEC-A1.** A dedicated lower-privilege uid for the autonomy loop.
**Until this lands, §3 is coordination only and the UI must say so.**

**P6 — Only then** consider `triager`/`broker` as new roles.
