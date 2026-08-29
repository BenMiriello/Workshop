# Where notes go: the three-tier policy

Every durable thing an agent or the operator writes lands in exactly one of three
places. Pick by lifespan and audience, not by convenience. This note is mirrored
as the `notes-and-docs` SKILL so builders auto-discover it and assistants can load
it on demand.

## Tier 1: ephemeral scratch

Agent thinking, smoke tests, throwaway probes, scraped output, ad-hoc working
notes. These go in a `scratch/` or `tmp/` folder anywhere in the tree (both are
gitignored). They are never durable and never a source of truth. Do not drop stray
`test_*` or `tmp_*` files into source dirs; use scratch.

## Tier 2: durable scoped Notes (the knowledge store)

Durable scoped facts and running notes go into the knowledge Notes store
(`services/knowledge.py`) at the right scope. The store is the Notes substrate: a
per-scope `MEMORY.md` index plus one `<id>.md` per note, injected into that scope's
chat context and searchable on recall. Scopes:

- `workshop` -> shared, cross-cutting facts (`shared/knowledge/`, committed)
- `project:<slug>` -> per-project notes (`projects/<slug>/knowledge/`, gitignored)
- `agent:<slug>` -> per-agent running notes (`.runtime/agents/<slug>/knowledge/`, gitignored)
- `app:<name>` -> per-app notes (`apps/<name>/knowledge/`, committed)

Commit-or-not is decided by scope, not by hand: `workshop` and `app` notes are
committed (shared, want history); `project` and `agent` notes stay gitignored
(instance-local, a fresh clone starts blank). Write via `knowledge.put_note(scope,
id, title, content, hook=...)` or the Notes UI. This is where agent-written and
operator-written notes meet, so use it instead of a write-only log nobody re-reads.

## Tier 3: long-form documents, plans, decisions (docs/)

Plans, designs, and architecture decisions go in committed markdown under `docs/`.
Git versioning, diff review, and editor-openability are the reason these are
documents and not notes. Architecture decisions follow the ADR convention:
`docs/decisions/NNNN-short-title.md` (dated, status proposed|accepted|superseded),
seeded from `docs/decisions/0000-template.md`. Consult accepted and superseded
decisions before re-opening settled ground; open a new record for a choice that
will outlive the chat.

## The one hard rule

Never create a flat per-project `NOTES.md` (or a single write-only markdown log per
scope). That is the failure mode the whole design retires: agents append but never
re-read, stale facts silently lie after a refactor, and every conversation pays the
attention cost of irrelevant rules. Scoped, retrieved Notes replace it.
