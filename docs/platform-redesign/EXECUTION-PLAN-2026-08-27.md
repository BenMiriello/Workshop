# The whole plan, ordered: what is built, what is inert, what is next

- Date: 2026-08-27
- Written because the operator asked a fair question: *"Have you planned out the
  whole thing? Are you considering the whole plan all together, or are you just
  rushing to do the most recent things that we discussed?"* The honest answer was
  the second one.
- This is an ORDERING, not a design. It names no new work that is not already
  filed. Detail lives in the two 2026-08-26 indexes and the design docs; this
  file exists to say what to do next and why, across everything at once.

---

## 0. The finding that reframes the question

**The worktree, branch, lease and lock system the operator asked about is
already built. All of it. It has been for four days.**

`workspace-ownership-and-leases-2026-08-22.md` defines stages B0 to B8. Verified
against the tree on 2026-08-27: **every one of them shipped.** The lease table
and module, the worktree record and its timeline, the read-only mount, presence,
the holder UI, Queue, Steer, and Preempt with snapshot-and-quiesce. Roughly
5,000 lines across `leases.py`, `lease_repo.py`, `worktree_records.py`,
`tree_access.py`, `presence.py`, `control.py`, `git_service.py`, plus
`RefPill.tsx`, `HolderControls.tsx`, `DirectoryView.tsx` and their tests.

**The doc's own header still says "B5 through B7 are not [built]".** So does the
handoff beside it. That is why nobody, including me, has been treating this as
done.

**But it is inert, and that is the real story.** Measured:

- `lease` table: **0 rows**
- `worktree` table: **0 rows**
- `change` table: **0 rows**

Nothing has ever used it. Not because it is broken, but because of three missing
connections, each small, each holding a large amount of finished code hostage.

This is the pattern across the whole platform right now, and it is the thing to
plan around: **the ratio of built-to-wired is bad.** Writing more stages makes it
worse. The next work is connections, not construction.

---

## 1. The three connections

Ordered by how much finished code each one turns on.

### C-1. The reviewer gate could not run inside a worktree. FIXED TODAY.

`check_no_hardcoding.sh` tested `[[ -d "$dir/.git" ]]`. A linked worktree's
`.git` is a FILE, so every worktree answered "not a repo", fell through to a
scan that ignores `.gitignore`, walked into `node_modules/` and `dist/`, and
reported a clean branch as 12 home paths and 809 email addresses.

Every ticket the sweep claims gets its own worktree. So **every autonomous run
would have been judged against its own dependencies.** Per-run worktrees were
unusable and nothing said so. Fixed in `7c241fb5`, both sites, with a regression
test that runs the real shell against a real worktree.

This was filed as `62e181fe` at priority 40 and no document mentions it.

### C-2. A send could not say who made it. FIXED TODAY.

`control.queue()` took an `actor`, passed it to the sweep branch, and dropped it
on the chat branch one line before it would have been stored. That is the whole
reason a thread cannot address a thread: the endpoint, the mailbox and the drain
all exist. Fixed in `b7d84d51`, carried from the queue entry to the stored
message.

This is the single buildable primitive named by both
`one-thread-many-senders-2026-08-27.md` and `entity-schema-2026-08-27.md`.

### C-3. Nothing gives an interactive chat a lease. NOT FIXED.

**This is the largest single piece of dead code in the platform.**
`WORKSHOP_TREE_LEASE_MOUNT` is off. It cannot be turned on, because
`tree_access.decide` returns read-only for anything that does not hold a lease,
and no chat can acquire one: `session_defaults.resolve()` and `.choose()` have
**zero non-test callers**, and `reader_staleness.py` has zero callers anywhere.

So: B1's enforcement, B8's whole module, the `write_blocked` client event, and
the branch-on-write rule in `branches-worktrees-and-environments-2026-08-20.md`
§14 are all waiting on one connection. Roughly 500 lines.

It is next. It is not a one-commit job and it ends in an attended verification
that is the operator's, the same posture as the uid layer.

---

## 2. The whole map: 19 workstreams, 175 live tickets

Ordered by leverage, not by size. Status verified against the tree, not against
the docs.

**Now unblocked or nearly so**

1. **W5 Work loop, claims, leases, changes** (17 live). Built B0-B8, inert.
   C-3 above is its head. Its status chain (`791f334e` → `2e026c69` →
   `91a477a5` → `3df11386`) has never executed, because no `change` row has
   ever existed.
2. **W6 Gates and ratchet hygiene** (6 live). Small, and it gates W5: C-1 lived
   here. `0f2f3fa6` (nothing runs the bare ratchets) is open and has already had
   two live instances in one day.
3. **W14 Schema and addressing** (2 live). Five live addressing grammars. The
   sender primitive (C-2) is the first thing landed against it.

**The long poles**

4. **W7 App separation** (24 live). Largest. `git ls-files apps/` is still 0.
   Blocked behind W9 (kit CSS) for its middle, and blocks W8 entirely.
5. **W8 Hosting, builds, releases** (16 live). Entirely unbuilt. No `Artifact`,
   `Release` or `Deployment` class exists. Rides on W7.
6. **W1 Token accounting and limits** (16 live). `token_usage.py` landed
   2026-08-27; the price table, the components column and the cap have not.
7. **W2 Model routing and providers** (14 live). Provider stage 1 shipped; there
   is no `resolve_model` in services and no `jobs.plan()`.

**Everything else**

8. W13 Security (14, six parked, operator/root only) · W3 Ticket provenance (13)
   · W4b Memory store fold (10) · W10b Chat surface (9, needs reconciliation
   against the MEM wave) · W4a Prompt budget (6) · W9 Styling kit (5) ·
   W12 Navigation shell (5) · W10a Chat storage migration (4, stalled behind a
   parked ticket) · W16 noise (4) · W4c Scheduled maintenance (3) ·
   W10c Narration truth (3) · W11 Client state (3) · W15 Retrieval (1).

### Dependency edges between workstreams

The ones that change the order:

- **W6 → W5.** The gate had to work in a worktree before per-run worktrees meant
  anything. Stated in no doc. Done today.
- **W9 → W7.** App CSS cannot leave the kit until the kit classes land; that one
  ticket transitively unblocks nine.
- **W7 → W8.** Hosting assumes an app is a separable artifact.
- **W1 → W2.** Routing policy cannot be judged without a model on the ledger row.
- **W4b → W4a → W1.** A three-workstream chain through indexing PROJECT.md.
- **W3 → W13.** Deleting `latitude` removes half the subject of SEC-A2.
- **W13 → W5.** SEC-A1's execution boundary is the one the loop needs.

---

## 3. What to do next, in order

1. **C-3, the chat lease-acquire path.** Turns on ~500 lines and lets the mount
   flag be armed. Ends in an attended verification that is the operator's.
2. **Make a `change` row exist.** W5's entire status chain is built and has never
   run against real data. Until one exists, "built" there means "compiles".
3. **`0f2f3fa6`, run the bare ratchets on a schedule.** Two silent drifts in one
   day. Cheap, and it protects everything above.

Then the long poles, in the order the edges above force: W9's kit classes, then
W7's extraction chain, then W8.

---

## 4. The process defect underneath all of this

Ten design docs currently claim a state the tree contradicts, and they all err in
the same direction: **they say unbuilt when the code exists.**

- `workspace-ownership-and-leases-2026-08-22.md`: "B5 through B7 are not". All are.
- `counting-tokens-2026-08-25.md`: "Nothing changed yet". `token_usage.py` shipped.
- `work-loop-core-design-2026-08-21.md`: "no `--permission-mode`". It is set.
- `client-state-management-2026-08-25.md`: "not TanStack Query". 49 files use it.
- `providers-credentials-and-limits-2026-08-23.md`: "nothing built". Stage 1 shipped.
- Four app-separation docs quote gate numbers that are three pins out of date.
- `transcript-read-from-rows-2026-08-14.md`: "being implemented". Untouched 13 days.

This has already cost real work once: a stale docstring in `work_review.py`
deferred to a refactor that had shipped eight hours earlier, was quoted forward
into four other docs, and gave two tickets opposite instructions.

It cost it again today, in the most expensive possible way: **the operator asked
whether we were building the worktree and lock system, and it was already built.**
A status line is load-bearing infrastructure here, and nothing gates it.

That is worth its own ticket, and it is filed. The rule it should enforce is the
one this document tried to follow: a claim about what is built is not evidence,
and the tree is.
