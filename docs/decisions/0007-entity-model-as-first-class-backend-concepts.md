# 0007. The entity model is the single authoritative design reference

- Status: accepted
- Date: 2026-07-18
- Supersedes: concept-schema-2026-07-16.md, synthesis-agent-protocols-2026-07-16.md,
  _brief-agent-protocols-2026-07-16.md (vocabulary claims only)

## Context

By 2026-07-18 the platform design lived across eight documents produced in the
2026-07-16 research thread plus three operator addenda. These docs shared no single
vocabulary: "Chat" and "session" were used interchangeably; "task" and "ticket" meant
different things to different docs; the latitude axis had two competing value sets
("low/medium/high" vs "strict/standard/open"); Agent's identity status was settled
only in ADR 0004 but not propagated; dependency edges between tickets were described
only in an addendum and not in any schema. Every implementation ticket produced
against this diffuse base inherited the ambiguity and required per-ticket resolution.

This ADR resolves the fragmentation. The entity model document
(`docs/platform-redesign/entity-model-2026-07-18.md`) was produced by synthesizing
all eight research docs, the three addenda, the workshop-v1 overview, and the
existing ADRs 0001 through 0006. It is the output of that synthesis; this ADR is its
ratification.

Prior ADRs that remain authoritative: ADR 0001 (task store is canonical), ADR 0002
(knowledge store), ADR 0003 (GitHub binding), ADR 0004 (Agent is a third identity
kind), ADR 0005 (policy rules engine), ADR 0006 (learning mechanism). None of those
are superseded; the entity model builds on top of them.

## Decision

`docs/platform-redesign/entity-model-2026-07-18.md` is the single authoritative
reference for:

1. Canonical vocabulary: one word per concept, with code-identifier mappings and a
   list of aliases to retire.
2. Scope addressing: the four-level address string and its access-control semantics.
3. Identity kinds: the three-way operator/guest/agent split and its authority model.
4. The four entity schemas: Ticket (code: task), Run (code: job), Agent, Chat.
5. Cross-cutting concepts: grants, latitude (canonical values: strict/standard/open),
   Store tiers, autonomy modes, and the four live defects (D1-D4).
6. The integration map: which 2026-07-16 docs are superseded, folded in, or preserved,
   and why.

Any implementation ticket, ADR, or design document that introduces a new term,
modifies a schema, or adds a cross-entity relationship must first reconcile with this
document. If reconciliation requires a change to the entity model, the change goes
there (with a date annotation) and this ADR is superseded by a new one that points
to the updated model.

The naming decision that was contested: latitude uses `strict/standard/open` (not
`low/medium/high`). The three-value form from `scopedness-and-escalation-2026-07-16.md`
is canonical because it maps cleanly to the DEPTHS cardinality and was the more fully
argued source. The final-plan doc's naming is overridden.

## Consequences

What is now settled:
- All new code that creates, updates, or reads Tickets must use the state machine in
  section 4.1 of the entity model. No states outside that set.
- All new code that creates Agents must validate the role field against the closed
  five-value enum (plus None). A role string not in that set is rejected at write
  time, not silently ignored (fixing defect D2).
- Ticket-to-ticket dependency edges (`depends_on`, `blocks`, `relates_to`) are
  first-class schema fields. The scheduler enforces the eligibility rule (section 4.4).
  Cycle detection is mandatory on every edge mutation.
- `attenuate()` at `permissions.py:647` is the only valid path for Grant narrowing
  (fixing defect D4). No new Grant creation or narrowing may bypass it.

What this ADR does not change:
- Code identifiers: `task` stays `task`, `job` stays `job` (ADR 0001 is in force).
- The `tasks.py` or `knowledge.py` schemas are not migrated by this ADR; schema
  migrations come in the P0-P4 implementation tickets.
- The `workshop-v1/00-overview.md` platform concepts (Project, App, Deployment) are
  not changed by this ADR.
- Voice-mode files are not touched.

Defects D1-D4 are enumerated in the entity model (section 8.5) and must be repaired
in phase P0 before any of the P1-P4 work lands. They are not fixed by this ADR.

The 2026-07-16 research docs that are SUPERSEDED by the entity model
(concept-schema, synthesis, and the brief) are kept on disk as audit history.
Do not update them; all future updates go to the entity model.

The 2026-07-16 research docs that are PRESERVED (operator-asks, final-plan) remain
authoritative for their stated purpose: the ask record and the execution plan
respectively. When a design choice traces to an operator ask, the ask ID (A1, B3,
etc.) from `operator-asks-2026-07-16.md` is the citation.
