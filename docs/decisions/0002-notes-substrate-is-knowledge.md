# 0002. The Notes substrate is the scoped knowledge store

- Status: accepted
- Date: 2026-07-15

## Context

Notes and memory scattered across at least six substrates and several formats:
project `INTERVENTIONS.md`, build-agent `INTERVENTIONS.md`, the knowledge store, the
learnings mirror, an out-of-tree notes vault, plus true strays like
`projects/<slug>/NOTES.md` and `shared/global-memory.md`. The scoped knowledge store
(`services/knowledge.py`) is already a scope-generic markdown note store with exactly
the scopes the operator asked for (workshop, project, agent, app), a per-scope
`MEMORY.md` index, REST CRUD, and chat-context injection. The Project "Notes" section
posted to `INTERVENTIONS.md`, which no context injection ever reads and which agents
never write, so it was a dead-end log on a different substrate from the notes agents
actually read. Building a new note store would repeat the write-only-markdown failure
the research diagnosed. See
`docs/platform-redesign/notes-system-plan-2026-07-15.md` (Parts 2 and 3).

## Decision

Adopt `services/knowledge.py` as the canonical Notes substrate; do not build a new
store. Notes are scoped and retrieved, never concatenated into one flat file. The
module identity stays `knowledge` in code while the UI noun becomes "Notes". Retire
`INTERVENTIONS.md` as the Notes surface: the Project Notes section renders the
project's `project:<slug>` knowledge scope, so operator writes and agent writes share
one substrate. Commit-or-not is decided by scope: `workshop` and `app` notes are
committed, `project` and `agent` notes stay gitignored (instance-local). A flat
per-project `NOTES.md` or any single write-only log per scope is disallowed. The
three-tier policy (scratch, scoped Notes, docs) is written once as a `workshop`
knowledge note and the `notes-and-docs` SKILL so it is both injected and
discoverable.

## Consequences

Agent-written notes finally appear to the operator and operator notes land where
agents already read. Learnings stay a distinct concept (deduped corrections and
guardrails) and are cross-linked, not merged. The out-of-tree `$WORKSHOP_NOTES_VAULT` vault stays
separate and is only linked from the Notes UI. The two `INTERVENTIONS.md` routes are
kept alive but unlinked for one release as an escape hatch, then deleted; the
build-agent `INTERVENTIONS.md` injection is a separate working mechanism and is left
alone. Long-form plans and decisions stay committed markdown in `docs/`, indexed and
cross-linked from the Notes UI, not swallowed into the store.
