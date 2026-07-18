# 0010. The `~/notes` vault is read-only to agents

- Status: accepted
- Date: 2026-07-18
- Supersedes / superseded by: none
- Related: 0004 (agent identity), 0002 (notes substrate), 0005 (policy engine)

## Context

`~/notes` (env-overridable via `WORKSHOP_NOTES_VAULT`) is the operator's
personal notes vault: their thinking, journal, drafts, reference material.
The workshop registers it as a Store at `workshop://store/notes/vault`
(`backend/services/stores.py:261`) so agents can look things up in it, but
never on the terms of an ambient "everyone has read/write". The operator's
directive during the 2026-07-18 coherence audit was concrete: *"idk if
the agents are using the notes app directly for their own notes which
would not be good."*

Agent-internal notes belong in the per-scope knowledge substrate:

- `workshop` → `shared/knowledge/`
- `project:<slug>` → `projects/<slug>/knowledge/`
- `app:<name>` → `apps/<name>/knowledge/`
- `agent:<slug>` → `.runtime/agents/<slug>/knowledge/`

The vault is a separate surface with a separate role. Reading it is
sometimes useful; writing to it should never be an ambient agent
operation.

## Decision

The vault is READ-ONLY to any identity of kind `agent` (ADR 0004). Write
verbs against the vault Store are refused before the adapter is
reached. This is enforced by THREE independent layers, in the order
they run:

1. **Identity layer** — Agents hold zero ambient authority
   (`backend/services/permissions.py:100-108`). A write requires a
   matching Grant. `grants.json` and `resource-grants.json` are both
   empty at rest; no agent identity carries a write grant to the vault.

2. **Store policy** — `backend/store-policies.json` caps the vault entry
   at `verbs_max: ["read","list","search"]`, `requesters_allow:
   ["agent:*"]`, `response_modes: ["filtered","redacted"]`, tier
   `mediated`. The prose rule states *"No verb writes, ever, through
   this record."* Even a hand-minted write grant would be rejected here
   before any adapter is asked to touch a file.

3. **Adapter refusal** — The vault adapter's write door
   (`backend/services/stores.py:687-700`) refuses `write` on knowledge
   scopes and requires a matching grant token for the vault URI itself.
   The operator identity is trivially permitted at the identity layer;
   any non-operator caller must present the token that the policy above
   would never mint.

Cross-store recall (an agent needs a specific note from the vault)
happens through the mediated broker (ADR 0005). Broker verdicts can
allow `read`/`list`/`search` on the URI subtree the operator's rules
permit; they cannot amplify to a write. `attenuate()` in the
permissions module is the primitive; it only narrows, never widens.

Writes into the vault happen from ONE surface: the operator's own
`/notes-app` HTTP routes (`backend/routes/notes.py:84-122`). Nothing
autonomous writes there.

## Consequences

- An agent that thinks it should "leave a note" writes into its
  per-scope knowledge dir (`agent:<slug>` → `.runtime/agents/<slug>/
  knowledge/`) via `knowledge.put_note`. That is the operator-safe
  scratch surface; not the vault.
- `memory-keeper` and other keeper-role agents write ONLY through
  `knowledge.put_note` into the per-scope knowledge dirs
  (`memory_keeper.py:455-458`, `:569-573`). They never touch the vault.
- A future request from an agent to read a specific vault note goes
  through the mediated broker: the broker adjudicates the URI + purpose
  against the topic allowlist, returns a narrowed verdict, and the
  adapter serves the filtered or redacted response. The broker never
  sees the note's body itself.
- If we ever wanted the vault writable by agents (we do not), it would
  require: a Grant minted to the agent identity, the store policy
  loosening `verbs_max`, and the adapter refusal check softened. That
  three-part change is the intentional friction; it is not a
  configuration we can trip into by accident.

## Alternatives considered

- **Register `~/notes` as just another knowledge scope.** Rejected. The
  vault is not a shared substrate; it is the operator's own thinking.
  Confusing "the operator's notes" with "the workshop's notes"
  invites both accidental leakage and incorrect consolidation.
- **Broker-mediate every vault read but permit ambient writes.**
  Rejected. Writes are the failure mode the operator specifically
  named. Reading a note carries much lower risk than a hallucinated
  scribble ending up in the operator's own vault.
- **Have the memory-keeper's consolidations land in the vault as
  operator-facing summaries.** Rejected. Consolidation output is
  workshop state; it belongs in `shared/knowledge/consolidated-memory`.
  The operator writes their own vault entries.

## Verification

Coherence audit 2026-07-18:

- `grants.json` empty at rest.
- `store-policies.json` vault entry caps verbs at read/list/search,
  requesters_allow includes `agent:*`, response_modes exclude anything
  that could produce novel tokens beyond redaction.
- No code path in `backend/services/memory_keeper.py`,
  `backend/services/chat_context.py`, `backend/services/knowledge.py`,
  `scripts/dispatch_triggers.sh`, `scripts/project_ralph.sh`,
  `scripts/ralph.sh` calls `notes_vault.create_note`,
  `update_note`, or `delete_note`. Verified with grep.
- The only callers of the vault write functions are the operator-
  authenticated `/notes-app` routes.
