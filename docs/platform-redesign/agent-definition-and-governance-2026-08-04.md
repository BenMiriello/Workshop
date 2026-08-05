# One way to define an agent, and a governance layer that means something (2026-08-04)

> **STATUS: DESIGN. NOT BUILT.** Supersedes nothing; it is the missing layer under
> `primitive-roles-2026-07-18.md` (roles) and `agent-interaction-model-2026-07-31.md`
> (interaction), both of which are still NOT BUILT. Written against the live tree
> on 2026-08-04, with every count verified rather than recalled.

## 0. The correction this starts from

An earlier explanation in chat said an agent's detail page "is history-driven, so
an agent that has never run shows nothing." That describes the implementation and
should not be mistaken for a defence of it. **An agent is defined by its contract,
not discovered by running it.** Role, scope, responsibilities, activation
conditions and limits are declared up front; history is *evidence about* an agent,
never the source of what it is. Any surface that inverts this is wrong, and the
one we have does.

## 1. The problem, counted

There are **seven** ways an agent is defined in this tree:

| # | Mechanism | Count | Shape |
|---|---|---|---|
| 1 | `agents/<slug>.md`, `projects/*/agents/<slug>.md` | 10 | frontmatter + body |
| 2 | `.runtime/build_agents.json` synthesis (`_record_from_build_state`) | 5 of 12 builders | JSON runtime state promoted to identity |
| 3 | `~/.claude/agents/*.md` (Claude Code native subagents) | 2 | frontmatter + body, different field names |
| 4 | Templates (`list_templates`) | 3 | a fourth shape |
| 5 | Groups (`list_groups`) | 2 | a fifth |
| 6 | App personas (`ContextProvider` subclasses) | 4 | Python classes: persona, tools, model, live context |
| 7 | `per_channel_agents.py` (masini Discord) | n/a | explicitly excluded from the registry by rule |

Mechanism 2 is how five "agents" existed without anyone declaring them: a process
ran, wrote runtime state, and the registry synthesised an identity from it. That
is the "let it run and find out" model, and it is the direct cause of the twelve
leftover builders.

Mechanism 6 is agent-like in every functional sense (persona, tool allowlist,
model, injected context) and is invisible to the agent registry.

And what the enum actually buys today: **`role` gates exactly one expression in
the entire codebase** — `tools_active = role == "maker"` (`routes/chat.py:1484`).
`reviewer`, `triager`, `keeper` and `broker` are behaviourally identical to each
other and to `None`. `participants` has no behaviour at all (audited 2026-07-31).

## 2. What the research says

**2.1 The declaration shape has converged, and we already have it twice.**
Google's A2A reached v1.0 in 2026 under the Linux Foundation with 150+
organisations: an **Agent Card** is a declarative manifest naming the agent, its
*skills* (each with purpose, expected inputs, output format), endpoints, auth, and
capabilities. Claude Code's native subagent is the same idea in the format we
already use: markdown, YAML frontmatter (`name`, `description` = *when to use
this*, `tools`, `disallowedTools`, `model`), body as system prompt. The supervisor
reads `description` to decide when to delegate, exactly as it reads a tool
description to decide when to call a tool.

**2.2 No protocol and no framework gives us governance.** This is the finding that
decides the framework question. "Governance Gaps in Agent Interoperability
Protocols" (arXiv 2606.31498) shows MCP, A2A and ACP **cannot express**:
permissions and role-based access, autonomy levels, delegation limits (depth,
re-delegation, scope), accountability and provenance, or escalation procedures.
Its recommendation is a **dedicated governance layer above the transport
protocols**. The part we are missing is precisely the part nothing off the shelf
supplies.

**2.3 Autonomy is per action class and gated on reversibility, not a switch.**
Current practice: an agent runs autonomously through reversible steps and pauses
before anything that cannot be undone (send, delete, publish, charge, push).
Three levels are described: L1 per-action approval, **L2 plan-level approval**
(approve a plan, the agent then executes the many steps inside it without asking),
L3 machine-readable boundaries that permit, block, or route to escalation. Gate
strength is calibrated on consequence severity, reversibility, and the agent's
demonstrated reliability (arXiv 2606.15563 makes the same argument from
uncertainty: governance intensity should scale continuously, not switch).

**L2 is what a Ticket already is.** Approving a ticket is approving a plan; the
worker executes its many steps autonomously inside that scope. Our existing model
is the recommended one; it has just never been *named* as the approval unit.

**2.4 Frameworks.** LangGraph: stateful directed graphs, conditional edges,
checkpoints, human-in-the-loop interrupts, the largest production footprint.
CrewAI: role/goal/backstory crews where the framework *infers* coordination.
AutoGen: research and multi-agent debate. Swarm: narrow handoffs.

## 3. Framework decision: no runtime adoption, deliberate theft

**Do not adopt an orchestration framework as the runtime.** Three reasons:

1. Our runtime is already Claude Code sessions, which give context isolation,
   automatic compaction, tool scoping and native subagents. Adopting LangGraph
   means replacing the execution substrate, including the session model fixed on
   2026-08-04 (ADR 0011).
2. The gap we actually have is governance, and per 2.2 no framework or protocol
   provides it. We would take on a dependency and still write the layer.
3. CrewAI *infers* coordination. We want coordination stated, because the recurring
   failure here is systems that do something implicit and unobservable.

**What to take:** A2A's card shape for declaration (2.1). Claude Code's
`description`-drives-delegation. LangGraph's interrupt/checkpoint concept as the
model for approval gates. CrewAI's insistence that a role is declared, not inferred.

## 4. The design: one definition, three layers

### Layer 1 — Identity and contract (declarative, one file, one format)

**One format: `agents/<slug>.md`** (user scope) or `projects/<p>/agents/<slug>.md`
(project scope). Frontmatter plus body. It adopts Claude Code's field names where
they overlap, so **a Workshop agent is a valid Claude Code subagent** and the two
stop being separate species:

```yaml
name:         memory-keeper
description:  When to use this agent, in one sentence. Drives delegation.
role:         keeper                 # closed enum; defines what it MAY WRITE
scope:        workshop               # workshop | project:<slug>
purpose:      One line, human-facing.
tools:        [...]                  # derived from role by default; explicit overrides narrow only
disallowedTools: [...]
model:        <tier>
triggers:     [{kind: cron, cron: "0 */6 * * *"}]
limits:       {max_iterations: 1, budget: {max_wall_s: 900}}
autonomy:     supervised             # see Layer 3
```

The body is the system prompt. Nothing else defines an agent:

- **(2) dies**: `build_agents.json` becomes state-only. It may never synthesise an
  identity. An undeclared process is a Run, not an Agent.
- **(4)(5) become emitters**: a template or group *writes one of these files* and
  then has no further existence.
- **(6) migrates**: each app persona gets a definition file; the `ContextProvider`
  subclass keeps only the code that cannot be declared (the live snapshot function).
- **(3) unifies**: native subagents live under the same schema.
- **(7) declares or is not an agent.**

### Layer 2 — Authority: a role is the set of writes

Make the claim from `primitive-roles` real, in one function:

```
role_may_write(role, target) -> bool
   target ∈ {tree, tasks, notes, learnings, verdict, nothing}
```

| role | may write |
|---|---|
| maker | tree, commits, task transitions on its own claim |
| reviewer | verdict, learnings |
| triager | tasks |
| keeper | notes, learnings |
| broker | nothing |

Every write path asks it: `tasks._persist`, `knowledge.put_note`, the reviewer
verdict, tree writes. One choke point each, the pattern already proven when nine
ticket call sites collapsed to a single scope-version bump.

**Tools are derived from permitted writes**, replacing `tools_active = role ==
"maker"`. A keeper gets note tools; a triager gets ticket tools; a broker gets
none. An explicit `tools:` list may only *narrow* what the role permits, never
widen it — least privilege with the role as the upper bound, which is the RBAC
shape 2.2 describes.

### Layer 3 — Activation: trigger × autonomy × reversibility

Three independent questions, currently conflated into one switch plus two brakes:

1. **May it start?** `triggers` (manual, cron, event, discord, loop).
2. **How much may it do unasked?** `autonomy`: `reactive` (manual only),
   `supervised` (runs, lands results in `in_review`), `autonomous` (commits,
   subject to the reviewer gate).
3. **Is this particular action reversible?** A classification on the action, not
   the agent. Irreversible actions — push, publish, delete, charge, send outside
   the system, deploy — **always** require approval, whatever the autonomy mode.

That third axis is what makes `autonomous` safe enough to actually turn on, and it
is missing today. The ticket is the L2 approval unit: approving it authorises the
reversible work inside its `touches`, and nothing else.

**And one global brake, not two.** Today the JSON overlay gates trigger dispatch
while `.runtime/AUTONOMY_DISABLED` gates the shell worker loop, and the shell
scripts have never read the JSON. The read side was unified on 2026-08-04; the
gates still need to be, and the flag file deleted.

### Layer 4 — The page follows the definition

Contract first, evidence second:

1. **What it is**: role, and *what that role may write*, in words.
2. **When it runs**: triggers, autonomy mode, and whether a brake is suppressing it.
3. **Bounds**: tools, limits, scope.
4. **Then** evidence: runs, tickets, notes it has produced.

"Never run" is a first-class state naming its cause ("reactive, and the global
brake has been engaged since 2026-07-18"), not four empty panels.

## 5. Plan

**P0 — Truth (small, no behaviour change).**
- Agent page leads with the contract, and renders the *activation chain* with the
  link that is currently blocking. Add the explicit "never run, because X" state.

  (An earlier draft listed "reject an invalid role" here, on the belief that
  `memory-keeper`'s `role: memory` was a typo being silently coerced. It is not:
  `ROLE_ALIASES = {"memory": "keeper"}` is a declared alias. Item withdrawn.)

**P1 — One definition.**
- `build_agents.json` stops synthesising identities.
- Templates and groups emit definition files.
- App personas gain definition files; keep only live-snapshot code in Python.
- Native subagents adopt the same schema.

**P2 — Authority means something.** `role_may_write` + derived tools. Ships as one
unit: a role that gates writes but not tools is still a label.

**P3 — Activation.** Reversibility classification and the always-approve rule;
collapse the two global brakes into one; delete the flag file.

**P4 — Only then**, consider `triager` and `broker`. A fifth and sixth role before
the two in use mean anything is more vocabulary.

## 6. Rejected

- **Adopting LangGraph/CrewAI/AutoGen as the runtime** (§3).
- **A per-agent autonomy switch as the only gate.** Ignores that the same agent
  doing a reversible and an irreversible thing warrants different treatment.
- **Inferring roles from behaviour.** It is how twelve builders appeared.
- **Keeping `participants` as a tag with no behaviour.** Either it changes recall
  and addressability, or it goes.
