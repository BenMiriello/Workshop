---
name: Notes and Docs Policy
description: "Where every durable note goes: scratch (ephemeral), the scoped knowledge Notes store (durable facts), or docs/ (plans and ADR decisions). Never a flat per-project NOTES.md. Read before writing any note, memory, or decision."
---

# Where notes go: the three-tier policy

Pick by lifespan and audience, not convenience. Every durable thing an agent or the
operator writes lands in exactly one of three tiers.

## Tier 1: ephemeral scratch

Agent thinking, smoke tests, throwaway probes, scraped output, ad-hoc working notes.
Put them in a `scratch/` or `tmp/` folder anywhere in the tree (both gitignored).
Never durable, never a source of truth. Do not drop stray `test_*` or `tmp_*` files
into source dirs.

## Tier 2: durable scoped Notes (the knowledge store)

Durable scoped facts and running notes go into the knowledge Notes store
(`services/knowledge.py`) at the right scope. Per-scope `MEMORY.md` index plus one
`<id>.md` per note, injected into that scope's chat context and searchable on recall.

- `workshop` -> shared cross-cutting facts (`shared/knowledge/`, committed)
- `project:<slug>` -> per-project notes (`projects/<slug>/knowledge/`, gitignored)
- `agent:<slug>` -> per-agent running notes (`.runtime/agents/<slug>/knowledge/`, gitignored)
- `app:<name>` -> per-app notes (`apps/<name>/knowledge/`, committed)

Commit-or-not is decided by scope, not by hand: `workshop` and `app` are committed
(shared, want history); `project` and `agent` stay gitignored (instance-local, a
fresh clone starts blank). Write with `knowledge.put_note(scope, id, title, content,
hook=...)` or the Notes UI. This is where agent-written and operator-written notes
meet; use it instead of a write-only log nobody re-reads.

## Tier 3: long-form documents, plans, decisions (docs/)

Plans, designs, and architecture decisions go in committed markdown under `docs/`.
Git versioning, diff review, and editor-openability are the point. Architecture
decisions follow the ADR convention: `docs/decisions/NNNN-short-title.md` (dated,
status proposed|accepted|superseded), from `docs/decisions/0000-template.md`.
Consult accepted and superseded decisions before re-opening settled ground; open a
new record for a choice that will outlive the chat.

## The one hard rule

Never create a flat per-project `NOTES.md` (or any single write-only markdown log per
scope). That is the failure mode this design retires: agents append but never
re-read, stale facts silently lie after a refactor, and every conversation pays the
attention cost of irrelevant rules. Scoped, retrieved Notes replace it.
