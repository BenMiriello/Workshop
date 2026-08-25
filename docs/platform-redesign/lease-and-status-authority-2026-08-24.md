# The lease, and who may write a ticket's status

**Status: plan, 2026-08-24.** Measured against the tree at `10018a9e`.

This plans the work that `backend/services/work_review.py` names as its blocker,
which it calls "the lease refactor (B3)". The headline finding is that **B3 is
already built**, and the remaining work is smaller and more specific than the
name suggests. It is written out below rather than assumed, because a plan that
misreads the current locking would produce a worse system than the one it
replaces.

Companion docs, all still current:
[`changes-and-review-2026-08-22.md`](changes-and-review-2026-08-22.md) (C2 and
C5), [`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
(B3 as specified, and section 13.2's layering rule),
[`work-status-contract-2026-08-24.md`](work-status-contract-2026-08-24.md)
(section 3.2's refusal), and
[`merge-policy-implementation-plan-2026-08-24.md`](merge-policy-implementation-plan-2026-08-24.md)
(G3).

---

## 0. What was measured

| Question | Answer | Evidence |
| --- | --- | --- |
| Is B3 built? | Yes, 2026-08-23 | `backend/services/leases.py`, `backend/db/lease_repo.py`, `scripts/lease.py`, migration `f2a6d0c31b78`, commit `d4c82846` |
| Is the fence a real compare-and-swap? | Yes | `db/lease_repo.py` acquire is `INSERT ... ON CONFLICT (subject_kind, subject_id) DO UPDATE ... WHERE lease.expires_at < now()`; renew and release are `UPDATE ... WHERE generation = :g` |
| Is the fence monotonic? | Yes | `tasks._acquire_ticket_lease` passes `min_generation = record.claim_token + 1`, so a fresh lease row can never make the generation go backwards |
| Does anything validate the fence? | Yes | `tasks.update()`'s `expect_claim_token` at `backend/services/tasks.py:2110`, and `tasks.heartbeat()` at `:2650` |
| Does the sweep pass the fence? | Yes, at all 14 status-write sites | `scripts/project_ralph.sh`, every `ptasks_tx ... --expect-claim-token "${CLAIM_TOKEN:-}"` |
| Has reclaim ever fired? | It could not, until 2026-08-23 | `audit-what-actually-runs-2026-08-23.md` section 3.4: two tickets sat `doing` with dead pids for 41 hours because the prescan read the retired file store. Fixed; `backend/tests/test_reclaim_reads_the_live_store.py` pins it |
| Is there a real reclaim path now? | Yes | `tasks.reclaim` at `backend/services/tasks.py:1770`, called from `next_ready` at `:1899` |
| Is the autonomous loop on? | **No** | `.runtime/autonomy-override.json` is `{"engaged": true, ...}`, the autonomy lines in `infra/cron/workshop.crontab` are commented out, and `.runtime/queue-runner.log` is a wall of "autonomy disabled ... idling" |

### The stale docstring

`backend/services/work_review.py`'s "THE HONEST LIMIT ON DERIVED" paragraph was
written in commit `865e2197` on 2026-08-22 at 18:06. B3 shipped in `d4c82846` on
2026-08-23 at 02:34, eight hours later. `git log -L 38,48` on that file shows the
paragraph has never been edited since. So the sentence "Doing it properly means
the lease refactor (B3)" was true when written and has been false for a day, and
it has since been quoted forward into four other places as a live blocker:

* `docs/platform-redesign/work-status-contract-2026-08-24.md:61`
* `docs/platform-redesign/agent-context-plan-2026-08-24.md:616` and `:680`, where
  it is the stated reason decision D1 ("what does `done` claim?") is left unmade
* `docs/platform-redesign/merge-policy-implementation-plan-2026-08-24.md:535`
  and `:811`, where G3 is deferred on it

One stale comment has been blocking five things for a day. Correcting it is part
of ticket T3 below, not a separate errand.

### Every writer of a ticket's status, classified

| Writer | Class | Path | Fenced? |
| --- | --- | --- | --- |
| `tasks.claim` (`todo` to `doing`) | system, on a worker's behalf | `tasks.py:2428` | yes, by the atomic lease acquire plus the `status == "todo"` compare-and-set |
| The sweep's 14 terminal writes (`done`, `in_review`, `blocked`, `parked`, `todo`) | worker | `project_ralph.sh` via `ptasks_tx` | yes, `--expect-claim-token` |
| `tasks.enforce_touches` (writes `blocked`) | system | `tasks.py`, called from `check-touches` | yes, the CLI forwards the token |
| `tasks.reclaim` (`doing` to `todo` or `parked`) | system | `tasks.py:1770` | not applicable, it is the authority; **but see FM1** |
| `tasks.apply_decision` (`done`, `cancelled`, `todo`) | operator | `tasks.py:2716`, reached from `routes/tasks_all.py` and `routes/jobs.py` | no token, by design: an operator write displaces a worker rather than being displaced by one |
| Panel / chat / API `tasks.update` | operator | various | no token, same rule, and a material edit bumps the generation (`tasks.py:2273`) |
| `work_review.merge_change` | system | `services/work_review.py` | **it does not write status at all. That is the gap.** |

There is no fourth class. The design already distinguishes worker writes (must
carry a matching generation) from operator writes (need none, and invalidate the
worker's). That distinction is sound and this plan keeps it.

---

## 1. What is actually wrong

Six things. Two are proven defects, one is a proven design hole, three are the
missing half of C2. None of them is "the lease is wrong".

### FM1. A zombie worker can write `done` in the window between reclaim and re-claim. PROVEN.

`tasks.reclaim` deliberately leaves `claim_token` intact
(`backend/services/tasks.py:1813`, "`claim_token` is deliberately left intact
here exactly as it always has been, the next acquire bumps it"). It releases the
lease row but the record keeps its number, and `tasks.update`'s fence reads the
**record**, not the lease. So between the reclaim and the next `claim()`, a
crashed worker that comes back holds a token that still matches.

Reproduced against the real module on a file-backed store:

```
claimed: token=1 status=doing
reclaim: [{'id': 'f635a5e8...', 'action': 'released'}]
after reclaim: status=todo claim_token=1
ZOMBIE LATE WRITE: ('done', 1)
```

Code path: `tasks.reclaim:1813` leaves the token, `tasks.update:2110` compares
equal, `tasks.update:2122` writes the status. A ticket reads `done` with its work
on an abandoned branch and nothing merged. **That is exactly the defect
`e48268d0` exists to end, and it is reachable today without any operator
involvement.** The comment's premise, that the next acquire bumps it, is true and
does not cover the window before the next acquire.

`workspace-ownership-and-leases-2026-08-22.md:60` predicted this in prose
("between reclaim and re-claim a zombie's token still matches. It is not fenced
in that window"). No test covers it. `backend/tests/test_leases.py:567` tests
that reclaim frees the lease, not that it fences the record.

### FM2. A supervised run whose change fails to open still marks the ticket finished.

`work_review.open_for_task` returns `None` rather than raising, on purpose, so a
bookkeeping failure never loses committed work. But
`scripts/project_ralph.sh:487` treats `None` as a non-event: it logs "no change
opened for wt/... ; branch left unmerged with its commits intact", **returns 0**,
and the caller at `:1417` / `:1442` then writes `in_review` or `done`.

The result is a branch with real commits, no change record, and a ticket that
reads finished. Nothing will ever merge it, because merging is driven from the
change. This is the exact shape of the ten-unmerged-branches incident, and it is
still reachable on any scope where the relational store is briefly unreachable or
where `changes.open_change` raises for any other reason.

### FM3. A merged change never completes its ticket.

`work_review.merge_change` merges the branch and calls `changes.mark_merged`. It
does not touch the ticket. Completion happens only because
`routes/tasks_all.py:1193` and `routes/jobs.py:352` each call
`tasks.apply_decision(... ACCEPT ...)` immediately afterwards, in the operator
path. `services/auto_merge.py:571` calls the same `merge_change` and has no such
follow-up, so **a change that auto-merges leaves its ticket `in_review`
forever**. That is G3 in
`merge-policy-implementation-plan-2026-08-24.md:535`, deferred there "on the
lease refactor". The prerequisite it was waiting for does not exist.

C2's table says `change merged` implies ticket `done`. Nothing in the tree
implements that edge. A repo-wide grep confirms no code reads a change's status
to decide a ticket's.

### FM4. The `in_review` write is a second act that can be lost.

Opening the change and moving the ticket are two separate calls with nothing
joining them. `open_change_for_task` succeeds, the sweep is then killed, and the
ticket stays `doing` with a `ready` change against it. Reclaim later returns it
to `todo`, a later agent picks it up, and now there are two changes for one
ticket. This is the same class of duplication the incident produced, arrived at
from the other direction.

### FM5. A displaced worker keeps working, and nothing stops it writing to the tree.

`scripts/ralph.sh:186` detects a fenced heartbeat and logs
`heartbeat no-op for task=... (claim displaced/reclaimed?); continuing
best-effort`. It does not abort. So a worker that lost its claim keeps burning up
to an hour of wall clock and tokens, and keeps committing into a worktree a new
claimant may now own. The read-only mount that would prevent the tree half (B1)
is built but its flag `WORKSHOP_TREE_LEASE_MOUNT` is off. Only the final status
write is refused, which is the cheapest thing that was going to happen anyway.

### FM6. An empty `--expect-claim-token` is silently promoted to operator authority.

`scripts/project_tasks.py:166` maps `""` to `None`, and `tasks.update` reads
`expect_claim_token is None` as "this is an operator write". So a worker call
that passes an empty token is not refused: it is **escalated**. Worse, at
`tasks.py:2273` an unfenced material edit to a held ticket bumps the generation,
so the call fences out the very run that made it, and the status still lands.

Every current bash call site interpolates `"${CLAIM_TOKEN:-}"`, which is set at
`project_ralph.sh:1201` right after the claim, so this is not firing today. It is
a landmine under every future caller, and the failure is silent in both
directions.

### What is NOT wrong

Stated plainly, because it is most of the surface area:

* The lease table, its atomic upsert, its server-computed `expires_at`, its
  generation-checked renew and release, and its typed subject are correct and
  built.
* The monotonic fence is real, is validated by `tasks.update` and
  `tasks.heartbeat`, and carries its floor across the file-backed / relational
  split via `min_generation`.
* Keeping `claim_token` on the record was right. Deleting it would leave a
  file-backed project sweep with no fence at all, not a degraded one.
* Keeping the `status == "todo"` compare-and-set in `claim()` was right, and
  revision 1's proposal to delete it would have let two sweeps work one ticket.
* The lease TTL of 3600s against a heartbeat once per ralph iteration (at most
  1500s) is correctly sized. LLM turns take minutes and a short lease would cause
  reclaim storms.
* The worker-versus-operator authority split is correct and should not be
  touched.

---

## 2. The target design

### The rule

> **A ticket's status is written by exactly one of three authorities, and each
> proves itself differently.**
>
> * A **worker** proves a matching fencing generation. Its write is refused if
>   the generation does not match, and it never advances the generation.
> * An **operator** proves nothing and needs to prove nothing. An operator write
>   is never refused for concurrency reasons, and a material one advances the
>   generation, which displaces any worker holding the ticket.
> * The **system** (reclaim, and change-driven completion) is the authority of
>   last resort. It does not present a generation; it **sets** the next one, and
>   every other holder is fenced out by that act.

The first two are today's design and are kept verbatim. The third is where the
change is: reclaim currently behaves like a caller with no authority (leaves the
number alone) when it is in fact the authority, and change-driven completion does
not exist at all.

### How a displaced or zombie worker is prevented from writing

The known hazard is stated in
`docs/platform-redesign/research-findings-2026-07-15.md:48`: a held-but-unchecked
token does not stop a resurrected worker's late write, and the standard answer is
a monotonic fence validated by compare-and-swap. That answer is already the
mechanism here. The defect is that **one authority path forgets to advance it**.

So: **any act that ends a worker's right to a ticket must advance the
generation, not merely release the lease.** Three such acts exist.

| Act | Today | Target |
| --- | --- | --- |
| Operator materially edits a held ticket | `_fence_ticket_lease` bumps. Correct. | unchanged |
| Reclaim of a dead worker | `_release_ticket_lease` at the current generation, token left intact | `_fence_ticket_lease`, and the returned generation is written to the record |
| A change merging and completing the ticket | does not happen | system write, which advances the generation for the same reason |

`lease_repo.fence` already does exactly the right thing for the reclaim case in
one statement: `UPDATE lease SET generation = generation + 1, expires_at = <past>`.
It bumps and expires together, so it both fences the zombie and frees the subject
for the next acquire. Where there is no lease store, `_fence_ticket_lease` falls
back to `current + 1` on the record, which is the same guarantee for a
file-backed scope.

### Who may write `done`

One more rule, and it is the one `e48268d0` will need:

> **A ticket may not reach a terminal status while it has an open, unmerged
> change.** The refusal lives at the write, in `tasks.update`, so it holds for
> every caller: the sweep, the API, a chat, the CLI, and anything written later.

`tasks` may not import `changes` (section 13.2: nothing below knows about the
layer above it). So the check is **registered into** `tasks` by `work_review`,
which is the module whose entire purpose is to be the one place that knows both.
`tasks` holds a nullable callable; unregistered means "not applicable", which is
the correct and only safe answer on a file-backed scope with no change store.
This is the same shape as the existing `_lease_module()` idiom in `tasks.py`, and
it inverts the import so the layering rule holds in the letter and not only the
spirit.

"Not applicable" and "not merged" must read differently at every reader. That
distinction is `e48268d0`'s to build; this plan only guarantees it is
expressible.

---

## 3. How the status write moves into `work_review` without losing the fence

The docstring says moving the write "would take the fence with it". Working
through it, the fence moves cleanly, because a fence is a value and not a
location.

Today, on the supervised success leg:

```
project_ralph.sh publish_or_park
  -> open_change_for_task (subprocess) -> work_review.open_for_task(slug, branch, task_id, title)
  -> returns to bash
  -> ptasks_tx set-status $DIR $TASK_ID in_review --expect-claim-token $CLAIM_TOKEN
```

Two acts, two processes, one of which can be lost (FM4), and one of which fires
even when the other silently failed (FM2).

Target:

```
project_ralph.sh publish_or_park
  -> open_change_for_task (subprocess)
     -> work_review.open_for_task(slug, branch, task_id=..., title=...,
                                  root=$DIR, expect_claim_token=$CLAIM_TOKEN)
        1. changes.open_change(...)                     # unchanged
        2. if it opened: tasks.update(root, task_id, status="in_review",
                                      expect_claim_token=...)
        3. return {change_id, status_written, refused_reason}
  -> bash writes NO status on this leg
```

The three things that make this work, none of which is a concurrency change:

1. **The token is already in the caller's hand.** `CLAIM_TOKEN` is a shell
   variable at `project_ralph.sh:1201` and is passed to every other status write.
   The bridge heredoc at `:385` gains two `sys.argv` entries. The fence is
   forwarded, not recreated.
2. **The root is already in the caller's hand.** `$DIR` is the ticket store root
   and is the first argument to every `project_tasks.py` call in the script.
   `work_review` takes it as a parameter rather than resolving a slug, which
   keeps it a thin orchestration layer and avoids giving it a dependency on
   `task_scope`.
3. **`work_review` is allowed to import `tasks`.** Its own docstring says it is
   "the ONLY module that imports both", and it currently imports `changes` and
   `worktrees`. Adding `tasks` is the module doing the job it was created for.

The ordering matters and is the opposite of the sweep's: **open the change
first, move the ticket second.** If the change fails to open, no status is
written and the ticket stays `doing`, which reclaim will later return to `todo`
with a real branch still on disk. That is a recoverable state. The reverse order
produces FM2, which is not.

Whether the fenced write is refused is now reported back to the sweep, which logs
it and cancels the child job exactly as it does today at
`project_ralph.sh:1422`. Nothing about the refusal's meaning changes; only where
the call is made from.

The same argument runs in reverse for completion: `work_review.merge_change`
already knows the change and can look up the ticket, so it writes `done` there,
as a **system** authority write with no token, which is what the merge is. Both
operator accept paths and `auto_merge` then get the completion for free and stop
being two implementations of the same rule.

---

## 4. Migration

**The autonomous loop is off, and this is the cheapest possible moment to do
this.** `.runtime/autonomy-override.json` reads
`{"engaged": true, "reason": "Disabled from Settings", "engaged_by":
"operator"}`, the autonomy lines in `infra/cron/workshop.crontab` have been
commented out since 2026-08-10, and `.runtime/queue-runner.log` shows nothing but
"autonomy disabled ... idling" through 2026-08-24T18:02. The resident
`queue_runner.sh` is alive but short-circuiting at its gate. So there is no
in-flight sweep to strand, and no window where a half-migrated status contract is
in effect on live work.

Concretely that means:

1. **No online migration is needed and none should be written.** No dual-write
   period, no compatibility shim on the status write, no feature flag on the move
   itself. A flag here would be pure cost: it would need both code paths kept
   alive and tested, to protect a loop that is not running.
2. **No schema change.** The lease table, its migration and its regenerated
   `schema.gen.ts` all landed with B3. Nothing in this plan adds a column.
   `field_occupancy_baseline.json` does not move.
3. **The one in-flight hazard is stale ticket state, not stale code.** Before the
   first ticket lands, check for tickets left `doing` from the era of the audit:
   `python3 scripts/project_tasks.py list-json . | jq '[.[] | select(.status ==
   "doing")]'`. Any such row is a crashed sweep's remains; reclaim now works and
   will clear it on the next `next_ready`, but confirming it is empty first means
   T1's fence change cannot be blamed for a pre-existing row.
4. **Order is by risk, not by dependency depth.** T1 (the proven fence hole)
   lands first and alone, because it is the only item that changes concurrency
   behaviour and it should be observable in isolation. T3 through T5 are module
   plumbing and can follow in sequence. T2 and T6 are independent and can land at
   any time.
5. **Turning the loop back on is not part of this work** and must not be bundled
   into any of these tickets. The gate is the operator's.

The rollback story is ordinary git: each ticket is one branch, one change, and
none of them is a data migration, so reverting one reverts it completely.

---

## 5. What this unblocks

| Blocked thing | Where it is recorded | What unblocks it |
| --- | --- | --- |
| **`e48268d0bf5c4e0d9923480bb982ad24`** "A ticket may not read done while its work sits unmerged on a branch" | the ticket store, priority 80 | T3, T4 and T5. Its part 2 ("the contradiction is refused at the write") needs a place to hang the refusal that every writer passes through, which is T5. Its part 1 (landed-ness derived from the change) needs the change to be the thing that moves the ticket, which is T3 and T4. **T1 is what makes the refusal worth having**, since a fence with FM1's hole can be walked around by the exact actor the refusal targets. This plan deliberately does NOT implement the refusal, so `e48268d0` keeps its whole scope. |
| **G3**, an auto-merged change leaves its ticket in review forever | `merge-policy-implementation-plan-2026-08-24.md:535` and `:811`, deferred "on the lease refactor" | T4, directly and completely. The follow-up ticket that document recommends filing was never filed; T4 is it. |
| **Decision D1**, "what does `done` claim?", which leaves `STATUS_MEANING["done"]` a placeholder in the agent-context work | `agent-context-plan-2026-08-24.md:616` | T3 plus T4 plus T5. Once completion is derived and the contradiction refusable, `done` has an answer the platform enforces, so the L1 vocabulary can state it without documenting a rule the system does not keep. |
| **The stale blocker itself**, quoted forward into four documents | `work_review.py:38-48` | T3, which rewrites the paragraph to say what is actually true. |
| An hour of a displaced worker's wall clock and tokens per incident | `ralph.sh:186` | T2. |

Two things this explicitly does **not** unblock, named so nobody expects them:
B1's read-only mount is still behind an off flag and this plan does not turn it
on, and B5 through B7 (show the holder, queue and steer, preempt with quiesce)
are untouched.

---

## 6. Is this a refactor?

No, and it should not be scoped as one.

B3, the thing the docstring names, was planned in full in
`workspace-ownership-and-leases-2026-08-22.md` section 15 and
`HANDOFF-branch-isolation-2026-08-22.md`, was built on 2026-08-23, and is
correct. What remains is one genuine concurrency defect of about ten lines plus a
test (T1), one guardrail against a silent authority promotion (T6), one shell
behaviour change (T2), and three pieces of ordinary module plumbing that move a
write from bash into the module that already exists to hold it (T3, T4, T5).

The only decision in it that deserves real thought is T5's shape, and that is a
layering question, not a locking one.

---

## 7. The tickets

Filed 2026-08-24, all `--execution code --source agent`.

| ref | id | pri | depends on | one line |
| --- | --- | --- | --- | --- |
| T1 | `0fb57b31260e4e7c9bbe313eee637aa7` | 88 | none | Reclaim advances the fence instead of leaving a zombie's token valid |
| T3 | `791f334ef0c74d70adfd9695b40d42f9` | 86 | T1 | Opening a change is what moves the ticket to in_review |
| T4 | `2e026c69fe1546f29a68f1b5c9e8a6c4` | 84 | T3 | Merging a change is what completes its ticket, on every merge path |
| T5 | `91a477a5569d4e9dabec16ad10abfc3f` | 82 | T4 | One authority for a terminal status, registered rather than imported |
| T2 | `4c59ee016ce34553a84da20b38ab90b4` | 80 | none | A displaced worker stops working instead of continuing best-effort |
| T6 | `e9a313c330ad40bca7713100a3b27476` | 78 | none | An empty claim token is refused, never promoted to operator authority |

`e48268d0bf5c4e0d9923480bb982ad24` depends on T3, T4 and T5. That ticket is not
edited by this plan; the relationship is recorded here and in T5's body.

**The `pri` column above is what was requested, not what is stored.** A relational
scope keeps priority as a named band, not an integer: `db/legacy_map.PRIORITY_BANDS`
maps 100 and up to `urgent`, 80 and up to `high`, 40 and up to `medium`. So the
whole 75 to 88 range collapses to `high` (which reads back as 80), except 75 to 79,
which fall into `medium`. T6 was filed at 78, landed as `medium`, and was raised to
80 so all six sit in the intended band. The consequence worth knowing: **the
88/86/84/82/80/78 ordering is not representable in this store.** Sequence between
these tickets is carried by `depends_on` and by section 4's ordering note, and by
nothing else.

---

## 8. Open for the operator

1. **T5's mechanism: a registered guard, or caller discipline?** This plan
   proposes that `work_review` registers a landed-ness check into `tasks`, so the
   refusal holds for every writer of `done` including ones not yet written. The
   cheaper alternative is to make `work_review` the only sanctioned caller and
   rely on review to keep it that way. The registry costs one nullable module
   global and an inverted import; caller discipline costs nothing now and fails
   silently the first time somebody writes a new path. The registry is
   recommended, and it is the one piece here that adds a mechanism rather than
   moving one.
2. **Does an auto-merge complete a ticket, or only make it completable?** T4 as
   written says a merge completes the ticket, which is C2's table read literally
   and closes G3. The alternative reading is that autonomous merge should still
   leave the ticket for a human to close. C2 answers this, so T4 follows C2, but
   it is a product decision and reversing it later is a one-line change.
3. **Turning the loop back on.** Nothing here does that, and nothing here should.
   Worth saying that after T1 through T5 the loop is meaningfully safer to run
   than it was on 2026-08-10, since the two ways a ticket could read finished
   over unlanded work are both closed.
