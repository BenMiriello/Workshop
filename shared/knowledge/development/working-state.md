<!-- author: self; chat: 6ac3d57e -->
Snapshot of work in progress, 2026-09-01. Overwritten by the next compaction in
this scope. Corrected freely; the corrected text is what the next chat reads.

## Objective

Answer why six weeks of direction produced so little, and fix the mechanisms
behind it. The diagnosis is written and committed:
`docs/platform-redesign/WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md`. **Read that
first**; everything below is its execution.

## The finding, in one line

Very high output, almost no allegiance to the operator's queue, because the
operator's queue does not exist as an artifact. 1,435 commits since 2026-07-15
while the backlog grew 159 -> 195 in six days. 132 of 195 open tickets were
filed by an agent, 34 by him. Zero of 195 have acceptance criteria.

## Measured facts worth not re-deriving

- 39 of 1,435 commits (3.3%) touched code never touched before in the window;
  704 (60%) touched only code already changed in it. **185 of 186 `fix:` commits
  repair work written inside the same six weeks.**
- Autonomy off since 2026-08-23 (`.runtime/autonomy-override.json`, "Disabled
  from Settings"). 57,547 "idling" lines in `queue-runner.log`.
- The runner could not read the ticket store anyway: bare `python3` (no venv, so
  no sqlalchemy) and never sources `~/.config/workshop/secrets.env` (so no
  `WORKSHOP_DATABASE_URL`). The DB is FINE — Postgres is on 127.0.0.1:5440, not
  the system one on 5432. Diagnosed in `project_ralph.sh:326` on 08-27 and
  applied to 7 of 77 call sites.
- Last real run (08-25): 1199s, **0 output bytes**, 150,000 tokens billed,
  reviewer rejected. 29 such zero-output timeouts across 311 run starts.
- `.runtime/tools.json` is `{"tools": []}` — the declared-tool mechanism is fully
  built and has never had one tool declared in it.
- `check_fresh_clone.sh` exists with 2 of 4 assertions "NOT IMPLEMENTED, never
  been run" — and they are the two that test whether a stranger's clone works.
- Five design generations routed at once (~13,600 lines). The model of record
  (`threads-branches-and-pages-2026-08-29.md`) has **zero lines built**. Of 22
  "delete the surface you replace" commitments, 1 landed.
- 18% of the operator's 954 chat messages are repeat complaints.

## Landed this session — 47 commits, 19 work streams

**Gates that did not exist this morning.** `check_dead_surface.py` (knip +
backend, 4 counts pinned) makes "a change deletes the surface it replaces" fail
the build — the audit's finding was that this was the ONE invariant enforced by
prose and the only one that never held. `check_ticket_acceptance.py` (202 pinned)
plus refusal at every filing door. `check_response_types.py` (92/143/120, down
from 100/156/140). `scripts/done.sh` + a Stop hook: done is a machine decision
with evidence, never a summary. `apply.sh` now reports and BLOCKS on schema
drift. `check_fresh_clone.sh`'s last two assertions now actually run.

**Capabilities.** Durable wakeup + clock + notify (`a308dfae`), proven live: the
alarm survived its process being killed, fired at its time, and the woken turn
answered. Images survive compaction (`d44690bc`) — settled by a 5-turn experiment
with randomly-chosen colours, and the recorded "survives one turn" diagnosis was
wrong. Repetition counting over the corpus (`69539eb7`, `2a679ead`).

**The runner can read its ticket store again** (`12ee2d9d`) — verified
independently in a cron-like env; it had been broken since 2026-08-25.

**Operator-facing.** App declares its home and tabs (`a9480f2b`). Branch/revert
vocabulary, browser-verified (`d6dfebde`, `61992acc`, `f4052fc6`). Settings Apps
now lists 11 apps, screenshot-verified on the built origin (`6fb72f52`,
`e29cdecf`). Phone tab bar says it scrolls (`d54a6676`). Trackers form
(`ab1a772f`, `8eeaa4b6`). App Runs rows (`a72157d7`).

**Health:** backend 4173 passed / 19 known failures (was 4036). typecheck 0.
frontend 1517 passed. design clean. app-name literals 479 -> 468. prompt layers 0.

## The one thing that needs the operator

**Thread `armed`+`role` is built (`15883681`) and reverted (`c30c349d`).** It
needs `alembic upgrade head`, which the permission classifier blocked for two
different agents. Re-land, in his own terminal:

    git cherry-pick 15883681
    cd backend && set -a && . ~/.config/workshop/db-admin.env && set +a \
      && .venv/bin/alembic upgrade head
    bash scripts/apply.sh

Everything downstream is blocked on it: Roles page `9fc4ce67`, Work view
`130ef15e`, Branches lens `427a86fe`, deleting `/projects` `d12a0332`, Settings
12 rows -> 9 `85a51a32`.

## Owed next turn

- **Verify live after the restart, then move the tickets.** Six of the operator's
  tickets have code landed and status still `todo`: `02933169` `17f0e21d`
  `49e5ea37` `d6696cd3` `f8906d50` `7d8d733a`. Backend-dependent ones could not be
  verified in-turn (see below) and must not be marked done on a claim.
- `/apps/development` still redirected to `/chats` in the last screenshot because
  the backend was still on old code. Confirm it lands on `tickets` after restart.

## Rules this session established, all paid for

- **Self-verification fails structurally, not through insufficient effort.** Done
  must be a machine decision with evidence — command output, exit code,
  screenshot. Never a prose summary. Generator must not equal grader.
- **Do not answer a process problem with prose in CLAUDE.md.** A controlled study
  found adding "always run tests" RAISED regressions 6.08% -> 9.94%; a structural
  mechanism dropped them to 1.82%. Build the gate.
- **Before designing, check whether it was already designed and left unbuilt.**
  That is this project's defining failure mode, with three measured emblems.
- **A recorded conclusion is not evidence.** The image-persistence diagnosis was
  inherited for days and was wrong: images are durable and are lost at
  compaction, not at turn end.

## Pending operator action: the keystone is built, reverted, and waiting

Thread `armed`+`role` (ticket `d5a051c8`) was built complete at `15883681` and
then **deliberately reverted** at `c30c349d`. Nothing of it is live.

Why: `db/models.py` declared `chat_thread.armed`/`role`, the live database was at
`b9f3e2d07a16` without those columns, and **neither `apply.sh` nor
`restart_backend.sh` runs alembic** — there is no migration step anywhere in the
deploy path, and the import preflight cannot catch it because it fails at query
time. Proven, not predicted: an ORM read returned `UndefinedColumn: column
chat_thread.armed does not exist`. A restart was armed.

Both the coordinator and the building agent tried `alembic upgrade head` and the
**permission classifier blocked it for both**. Neither worked around it. Revert
was chosen over making the model tolerant, because both PG paths
(`chat_list_pg.list_threads`, `chat_branches.record_meta`) already swallow
exceptions and fall back to the file fold — so a tolerant model would have looked
healthy while its database path failed on every request, which is the built-but-dead
pattern the ticket exists to end.

Verified after the revert: tree head == live head == `b9f3e2d07a16`, `main.py`
imports, `chat_repo.list_threads` returns 190 rows, ORM read of `ChatThread` OK.

**To re-land, in the operator's own terminal, in this order:**

    git cherry-pick 15883681
    cd backend && set -a && . ~/.config/workshop/db-admin.env && set +a \
      && .venv/bin/alembic upgrade head
    bash scripts/apply.sh

The UI it adds has never been seen running; that verification is owed on re-land.

**Separately and more important than the keystone: there is no migration step in
the deploy path at all.** Any schema change in this repo is a loaded gun. That
deserves its own ticket and is not closed by this revert.

## What running ~12 agents in one tree actually cost

Worth knowing before doing it again. The parallelism was worth it — 51 commits —
but it is not free and the costs were all of one kind: **a shared working tree
has no isolation, so agents overwrite each other.**

- One agent's `git stash push` took every other agent's uncommitted work with it.
  It popped it back and verified all 30 files returned, but for a minute the tree
  was empty of everyone's WIP.
- A second agent's `chat_store.py` edits were silently reverted by a concurrent
  agent and had to be re-applied.
- A third agent's staged files were swept into an unrelated agent's commit,
  because `git add <paths>` does not stop `git commit` committing the whole
  index. The fix is `git commit -- <paths>`, which two agents found independently.
- `tests/test_scoped_exec.py` fails while this many agents run: they all rewrite
  `~/.claude.json`, whose link count sits at 0, and bwrap cannot bind an inode
  that is unlinked mid-mount. Verified with a bare `bwrap` command containing no
  Workshop code. Not a regression; it will pass again when the tree is quiet.
- Five deferred backend restarters are armed at once, one per agent that ran
  `apply.sh`. `restart_backend.sh` has no `flock`, so they will fire in sequence
  after this turn. Worst case is a few extra seconds of pause, not data loss.

- **The coordinator made the same mistake it had warned four agents about.** Commit
  `fbc5d3f3`, a docs commit, swept in another agent's in-flight
  `git mv pages/Tickets.tsx -> components/tickets/TicketsView.tsx` without its
  import fixes, because `git add <path>` followed by a bare `git commit` commits
  the WHOLE INDEX. `fbc5d3f3` and `46b5caf1` therefore do not build (`App.tsx`
  imports `./pages/Tickets`, which does not exist at those revisions);
  `22276b33` restored a buildable tree. No live impact, but those two revisions
  are not bisectable. **The correct form is `git commit -- <paths>`, always.**

**If this is done again:** tell every agent to `git commit -- <explicit paths>`,
never `git add -A`, never `git stash`/`reset --hard`/`checkout -- .`, and to
commit early rather than holding a large working set. Better still, give agents
that touch overlapping files their own worktrees — the machinery exists
(`services/worktrees.py`, `run_worktree.py`) and this is exactly what it is for.

## Owed: residue from the app Tickets tab change

`22276b33` replaced the app Tickets tab's bespoke list with the real view and
deleted the frontend half in the same commit, correctly. **The backend half was
not deleted.** `GET /api/apps/{app_id}/tickets` (`backend/routes/apps.py:247`),
the `AppTicketList` model (`:137`) and `apps_svc.tickets_for_app`
(`services/apps.py:313`) now have ZERO non-test callers.

`check_dead_surface.py` passes only because its granularity is "api AREA", and
the apps area is still called for runs/chats/notes/deploys. **That is a real gap
in a gate that shipped today** and is worth knowing: area-level detection cannot
see a single dead route inside a live area.

Mitigating, and why this is a decision rather than an oversight: the route is one
member of a documented uniform family of app sub-collections with a contract test
that loops over all of them, so removing only `/tickets` breaks the symmetry
deliberately. But the commit does not mention it, which is what makes it residue
rather than a choice.

Also found, no live impact: the tab computes `scopeForContext(app.slug)` while
the server's `apps.owned_scope()` resolves from `project_slug`. They agree today
because every linked app here has `slug == project_slug`; an app where they
differed would diverge, and `owned_scope` is not on the wire so the client cannot
just read it.

## Owed: the retrieval ranking is not stable until the backend restarts

The authority/kind ranking (`d347e528`, `dcbc8ca1`) is correct in code and was
verified by its author against a healed index — both target queries returned
`docs/applying-changes.md` first, and a superseded doc was demoted and labelled
without disturbing unrelated queries (birdnet, Plaid, design-system top 3
byte-identical before and after).

Measured again from the main thread a few minutes later: "is an app the same
thing as a project" still ranks the authoritative doc **first**, but "how do I
restart the backend" had reverted to three chats. **This is the running backend
fighting the new code.** It is on the old source, and only the long-lived process
rebuilds the index, so its maintenance pass strips the statuses the new ranker
reads. `_RANK_SCHEMA` in the fingerprint heals it on the next rebuild under new
code.

So: **re-run both queries after the restart before believing either result.**
Neither the good reading nor the bad one is stable while the backend is stale.
This is the same in-turn verification limit as every other backend change today
and is not evidence of a defect.

    cd backend && set -a && . ~/.config/workshop/secrets.env && set +a
    .venv/bin/python -c "
    import sys; sys.path.insert(0,'.')
    from services import ownership, semantic_index as si
    ownership.pin_operator()
    for q in ['how do I restart the backend','why is the backend restart deferred']:
        print(q, [(h['kind'], h['ref']) for h in si.search(q, limit=3)['hits']])"

## Open questions for the operator

- Autonomy is off by his own choice. Once the runner env is fixed and proven,
  does he want it back on, and at what latitude?
- `main` is 812 commits ahead of `origin/main` and this branch is 193 ahead of
  `main`. Nothing has merged in a long time. Intentional?
