# 0004. Agent identity is a third kind, separate from operator and guest

- Status: proposed
- Date: 2026-07-18
- Supersedes / superseded by: none

## Context

`backend/services/permissions.py:71-93` defines `Identity.kind` as one of two values:
`operator` or `guest`. The behavior of each is fixed and deliberate:

- `authorize(operator, ...)` always returns `allow`, never `deny` (`:354-356`). The
  module docstring (`:6-27`) calls this out explicitly: the operator is never gated.
  Gating the operator is anti-cruft for a single-user system.
- Guests are gated by app sensitivity: `private` apps are never guest-readable
  regardless of grants (`:380-381`). Guest models an external person with standing.

The operator's resource-access asks (A1-A12, `operator-asks-2026-07-16.md`) require
that autonomous agents be scopeable: agents must be holdable to a resource boundary
(A6), a smart LLM must review cross-boundary requests (A7), and agent identity must
carry job provenance for escalation (B1). These asks are unimplementable if agents
are `operator` identity, because `authorize(operator, ...)` is always allow and the
gate cannot be added without gating the operator themselves.

At the same time, `guest` is the wrong answer. Guests are gated by app sensitivity,
so private apps are never guest-readable, but the operator's own background agents
must reach private apps: that is the point of running agents at all. And `guest`
models a person with external standing, with its own persistence and revocation
semantics; an agent is a process acting for the operator for the duration of one job.

The confused deputy (Hardy 1988) is the exact failure `operator`-identity agents
create: a program tricked by a less-privileged party (text in a note body, a web
page) into misusing authority it holds ambiently. An autonomous agent that reads
note bodies and web pages and then acts with the operator's ambient authority IS the
confused deputy. The object-capability literature's answer is that authority must be
carried by the invoker as tokens, not held ambiently by the program.

This decision is not about gating the human. The operator is never gated and this
record does not touch that. It is about bounding a program that processes hostile
input.

## Decision

Add `Identity.kind == "agent"` to `permissions.py:71-93`.

An agent identity carries:

```python
Identity(
    kind="agent",
    id="<agent-slug>",           # e.g. "notes-triage"
    on_behalf_of="operator",     # claim, not proof; for escalation provenance
    job_id="<job-attempt-id>",
    task_id="<task-id>",         # optional, for blocked-task linkage
)
```

The defining rule:

> An agent identity has no ambient authority whatsoever. Its authority is exactly the
> set of resource grants it holds. `authorize()` is not consulted for an agent at
> all; `check(token, uri, verb)` is the only question.

`resolve_identity` (`:96-129`) parses `"agent:<slug>"` into the new kind. The
operator fallback (`:107-109`) is unchanged: an unrecognized caller resolves to
`operator`, never to `agent`.

`authorize()` (`:323-393`) gets an agent branch that returns `deny` unconditionally,
with a docstring stating that agents are authorized by `check()` only. The guest
ladder (`:379-393`) is not extended to agents.

The dispatcher mints an agent's initial grant set at job start, from the job's own
scope, generous by default. A job on `project:acme` starts holding a subtree grant
over `workshop://store/knowledge/project-acme` with verbs `[read, list, search,
append, write]`. That is precisely what it could do today under ambient authority.
Nothing regresses; nobody is prompted.

What changes is only what happens at the edge of the job's scope. When the acme job
reaches for `workshop://store/finance/plaid-items`, it holds no token, so it does
not silently succeed. It gets a broker request or a block. That edge is exactly what
A1, A2, A5, A6, and A9 are about.

An attended chat session's agent is minted the grant set of its chat scope at
session start, same as above. Attendedness adds nothing extra: the operator can
always read the thing themselves through the normal route, which is not gated.
Attendedness is therefore not a permission concept.

## Consequences

What becomes easier:

- A6 and A7 are implementable: the broker gates agents, not the operator. The
  resource-access protocol (`resource-access-protocol-2026-07-16.md`) depends on
  this decision; it collapses without it.
- The confused deputy attack surface is structurally bounded: a fully compromised
  agent's worst-case action is inside the programmatic ceiling declared in the
  policy record, which the operator controls and the LLM cannot touch.
- Escalation has clean provenance: `on_behalf_of` and `task_id` let the operator
  see which job triggered a blocked request without reading prose.

What becomes harder:

- `resolve_identity` must parse the new kind and the dispatcher must mint tokens at
  job start. This is two new code paths, both small.
- Any background job that currently relies on implicit operator authority to cross
  scope boundaries will stop working until it is given an explicit grant or its scope
  is widened. This is a one-time migration, not ongoing friction.

What is now settled:

- Agents are NOT operator. Proposals to give an autonomous agent ambient operator
  authority should cite this record and argue why the confused deputy concern does
  not apply to the specific case.
- Agents are NOT guest. Guest is for external persons with standing; do not reuse
  it for internal processes.
- A third kind is not a slippery slope toward a fourth. The three-way split covers
  the actual distinct cases: a human acting directly, an external party with
  standing, and a process acting on behalf of the human for a bounded job.

Deferred:

- How `on_behalf_of` is verified (today it is a claim). For a single-operator system
  the claim is sufficient; proof would require signing. Do not add signing.
- Whether a future agent-to-agent delegation path requires a nested `on_behalf_of`
  chain. Leave unspecified until a concrete case appears.
