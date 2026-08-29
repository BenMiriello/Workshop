<!-- author: derived:claude-haiku-4-5-20251001; type: reference; pinned: true; chat: 50cf71c1e6564676b908b6f33ddf9fe1 -->
Written automatically when a chat in this scope compacts, and overwritten by
the next compaction here. It is the handoff a new chat in this scope reads to
continue the work without the old transcript.

Edit it freely to correct what is wrong: the corrected text is what the next
chat reads. But it is a snapshot of work in progress, not a durable record, so
anything that must outlive this piece of work belongs in a note of its own.

## Objective

Complete the platform redesign workstreams into end-to-end working functionality. Verify all planned work is actually implemented, not just proposed. Ship working worktrees/branches, search/embeddings, and the unified threading model so the system is usable, not perpetually in progress.

## Decisions and why

- **Mermaid is optional, not hard dependency.** Verified by measurement: 3.4 MB of chunks removed when absent. Why: every install shouldn't pay for diagrams they don't need.
- **Chat modes: 3 → 2.** Collapsed orchestrated/auto into auto; direct stays. Why: orchestrated and auto differed only in prompt text, no code gate between them.
- **Retrieval: author-prose-only, not code.** Grep for code (it's exact and current), embeddings keyed on `workshop:` refs (durable). Local ONNX model, two flat files, brute-force numpy. Why: paths are fragile, refs are portable, grep is the right tool for code.
- **Multi-chat coordination restored.** Each of five chats gets explicit list of what others shipped to prevent duplication. Why: keeps threads focused, prevents duplicate work.
- **Branches UI maps 6 lease states onto 5 status kinds.** No sixth type invented. Queue and Steer as actions; Preempt deliberately not offered from a page (needs a session). Why: consistency with design system, honest about what a monitoring page can do.
## Done

- Mermaid + file rendering (images/PDF/text viewer, mermaid lazy-loaded)
- Chat modes collapsed 3 → 2, DB migrated, legacy values map forward
- Personal data purged from git (audio, screenshots, ticket dirs, `.gitignore` fixed)
- Veritus leak closed: `grep -ri veritus` returns nothing, routed through config
- Gate improvements: `check_no_hardcoding` learned `~/`, `$HOME`, `Path.home()`; scope widened; docs sweep added; `check_fresh_clone.sh` written
- 14 installation facts removed from L0/L1 prompt layers
- Entity schema documented: 9 entities, 3-axis thread model (what advances / who attends / what it may do), state machines, all with mermaid diagrams
- Five redirected chats with explicit non-overlapping work lists
- Memory/Files UX chat (Opus 5) launched
- Branches page UI (who holds each worktree, how long, dirty/clean, ahead/behind, lease states mapped to 5 status kinds)
- Drift banner (build-blocking-only, appears on every page)
- Retrieval-in-production-2026-08-27.md: five decisions on keying, indexing, storage, cost
## Not done

- Birds WIP still uncommitted (blocking builds, needs commit or stash)
- Ratchets_not_loosened re-pin (gate correctly refuses worker; counts went 291 → 479 and 20 → 24, needs operator approval)
- 6 lines for `~/.config/workshop/instance.env` (read-only from here)
- Worktree system end-to-end: Branches UI built, but heartbeat / lease acquisition / concurrent agent splits untested
- Branches page visibility: designed and built separately, unclear if linked from project view or discoverable
- Out-of-order message bug unfixed (diagnosed: `threadStore.ts:552` routes `meta.during_turn` arrivals to separate path; needs server investigation before fix)
- Link styling on mobile (centered when shouldn't be; noted for later)
- Multi-chat coordination completion (re-established but not verified across all 5)
- Full decoupling (417 gate-visible app references, 84 vendor couplings, 124 tracked knowledge notes)
- Embeddings implementation (plan exists, storage/indexing/search tool unbuilt)
## Shortcuts taken and what they owe

- Mermaid optional but verified only by moving node_modules and building; needs real-world test on clean install
- Branches page is separate UI, not integrated into main app navigation or project view
- Retrieval plan on paper, implementation unstarted
- Multi-chat coordination via explicit lists in each message, not via a system (still ad-hoc)
- Five chats given work but no health/heartbeat monitoring on them
- Yield-to-dangling-ref tested on test data only, not on real operator file recovery
- Gates widened but upper ratchets need re-pinning; worker cannot approve its own gate

## Open questions

- Are the five chats actually running and making progress, or idle waiting for responses? (Owner: operator to check and engage)
- Is Branches page visible and linked from projects, or orphan? (Owner: needs integration audit)
- Should extra work be gated by prompt rule, checklist, or something else? (Owner: noted as design goal, too subtle for now)
- Is worktree/lease/heartbeat actually working end-to-end with real concurrent work? (Owner: needs testing)
- Birds WIP: commit it or stash it? (Owner: blocking decision)
- Are the instance.env 6 lines provided, or does operator authorize write access? (Owner: decision)
[trimmed to fit the working-state budget]
