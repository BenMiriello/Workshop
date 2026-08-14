# Testing the autonomous loop without it being a headache

Status: **plan, not built.** Written 2026-08-14 at the operator's request.

The operator's position, which sets the requirements: willing to test on something
imperfect, wants the work recoverable if it goes wrong, and will not turn the
runner on until there is reason to think it will work. So the goal is not "prove
it is correct" but "make the blast radius small enough that being wrong is
cheap".

## There is already a test environment, and it is not the repo

`projects/<slug>/` is the isolation that exists:

- each is **its own git repo** (`projects/birds/.git`),
- the whole directory is **gitignored** by the platform repo (`.gitignore:67`,
  `/projects/*`), so nothing a worker does there can reach the platform tree,
- each carries its own `.tasks.json` and `TODO.md`,
- `scripts/project_ralph.sh --dir <path>` targets an arbitrary tree, and
  `scripts/reviewer.sh` is shared rather than per-project,
- `projects/_templates/` already holds four scaffolds.

So the test environment is a **disposable project**, and recovery is
`rm -rf projects/<slug>`. Nothing else is touched. That is the whole design;
building a separate sandbox would be inventing a second mechanism to do what
this one already does.

Worktrees are the second layer, for when the target has to be the real repo:
`worktrees.allocate_task_worktree` locks a tree per task, and `merge_branch`
already has a local (no remote, no PR) path for unbound projects, so isolation
works here despite the platform repo having no remote.

## Three tiers, in order, each answering a different question

**Tier 1 — does the harness work?** A disposable project, tickets whose work is
trivial and whose acceptance is a shell command. Exactly what
`queue-drain-alpha/bravo/charlie` did in July: "create this file with this one
line". No judgement involved, so a failure is unambiguously the harness.

What it proves: claim, lease and heartbeat; the reviewer gate; the commit
requirement; the status transition; the receipt; the queue draining more than
one ticket per sweep.

**Tier 2 — does a worker do real work?** Same disposable project, a genuine
small code change with a test. Proves the worker can read a spec, change code,
and satisfy a gate it cannot see the answer to.

**Tier 3 — one real ticket, in the real repo, supervised.**

    bash scripts/project_ralph.sh --dir /home/clawd-a/workshop \
      --task-id <id> --max-tasks 1 --supervised

`--supervised` lands a passing ticket in `in_review` rather than `done`, so
nothing is auto-accepted. Bounded to one named ticket, so it cannot wander into
the queue.

## The tests worth running are the failure tests

This is the part that decides whether the loop is safe to leave on, and it is
the part that gets skipped. "It worked" on a happy path says nothing about what
happens at 3am. Tier 1 should deliberately provoke each of these:

- **A ticket the worker cannot finish.** Does it park at the attempt cap, or
  loop? The no-progress detector exists; it has never been tested against a
  task designed to defeat it.
- **A stale claim.** Start a worker, kill it, let the lease expire, let another
  claim it, then let the first one write. The claim fence must reject the late
  write. This is the exact Kleppmann fencing case the counter was built for.
- **A reviewer that should say no.** Feed it a ticket whose acceptance cannot
  pass and confirm the gate refuses rather than marking done. Ungrounded
  self-verification approving wrong work is the failure mode the gate exists
  for.
- **A dirty tree.** The one that would bite today: a worker commits at the end
  of a run, and with another session's uncommitted files present it can sweep
  them into the commit. Confirm it stages only its own work, or confirm it does
  not and fix that before tier 3.

## Preconditions for tier 3

1. **A clean tree.** Otherwise the worker commits someone else's in-flight work.
   This is not hypothetical: the checkpointer did exactly that twice on
   2026-08-14.
2. `--max-tasks 1` and an explicit `--task-id`.
3. `--supervised`, so the result lands in review.
4. Know the recovery: `git worktree remove`, delete the branch, or
   `git reset --hard <sha>` on a named commit. Write the sha down before
   starting.

## Turning the runner on, afterwards

The resident runner needs a NAMED tree (ADR 0014), so the commented crontab line
does not work as written:

    */5 * * * * /home/clawd-a/workshop/scripts/queue_runner_watchdog.sh /home/clawd-a/workshop

Do not enable it until tier 1's failure tests pass, because a resident loop
turns a single bad run into a repeating one.

## Separately: why chats stopped filing tickets

Measured, and it is a dated regression rather than a design gap.

Chats filed **25 tickets in July and 0 in August**. The only August ticket with
`source.kind = chat` is a test artifact titled "Should not be filed". The last
real one was 2026-07-31.

**Cause: `chat_store.DEFAULT_CHAT_MODE = "direct"`**, and the operator
preference resolves to `direct` too. The Direct/Orchestrated mode feature landed
in early August (`0b69ba3`), and direct mode's prompt says, flatly:

> Do NOT file tickets, do NOT emit a task-proposal card, and do NOT emit a
> task-patch block.

So every chat created since then starts in the one mode that cannot file, which
is why "orchestrated chats are not making tickets" looked true: most of the
recent chats are not orchestrated. Only 8 threads have ever contained a card.

Two things follow, and they are separable:

1. **The default.** One setting (`chat_store.set_default_mode`) decides whether
   a new chat can file. This is an operator preference, not a bug, but the
   current value makes the whole ticket surface inert by default.
2. **The escape hatch is missing.** The mode's own specification says direct is
   "a traditional chat, no auto-ticketing and no cards, **but an explicit 'file
   this' STILL works**." The implemented prompt has no such carve-out: it
   forbids filing outright. So in a direct chat, asking for a ticket in words
   does not produce one. That is an implementation/spec divergence, and it is
   the smaller and safer of the two fixes.

Neither is built. (2) is the one worth doing first: it restores the promised
behaviour without changing what happens by default.
