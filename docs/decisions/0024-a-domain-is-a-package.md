# 0024. A domain is a package, and the import graph is a DAG

- Status: **proposed**
- Date: 2026-08-17
- Plan: `docs/platform-redesign/target-architecture-2026-08-17.md` §2.2, item 0
  of the §4 sequence. That document's own status line still reads PROPOSAL, so
  this record is `proposed` and moves to `accepted` with it.
- Related: ADR 0023 (the HTTP contract), which depends on the rule below that
  one resource is owned by one domain and therefore has one router.
- Amends in practice: the 2026-08-17 consolidation that moved `EFFORTS` and
  `STATE_TYPES` into `db/models.py`'s import path under
  `scripts/check_one_definition.py`. That was correct against the rule that
  existed and is wrong under this one. See "Where a shared vocabulary lives".

## Context

**The flat `services/` namespace is the single largest structural defect in the
backend.** 116 files, **22 obvious prefix clusters** (`chat_*` eleven times,
`ticket_*` four, `deploy*` four, `app_*` four, `scope_*` three), and no
directories to match any of them. The prefixes are the packages the author
wanted and the filesystem would not give.

Four measurements make the case, all from the plan except where noted.

**438 function-local imports against 430 module-scope ones.** A near 1:1 ratio,
which no design produces on purpose. **In 22 of the 24 mutual A-B module pairs,
exactly one direction is a function-local import**, which is the signature of
someone hitting an `ImportError` and pushing the import into a function body to
make it go away. Every one of those is a real edge in the dependency graph that
is invisible to anything reading the top of a file.

**One strongly connected component of 58 modules, 27% of the backend.** And the
number that determines the whole shape of this record: **removing every one of
the 40 layering violations still leaves a 32-module SCC entirely inside
`services/`.** The layering violations are a symptom. The flat namespace is the
disease.

**40 upward dependency edges**, the worst of which are in the bottom layer.
Verified with `grep -n '^from services' backend/db/models.py`: the schema module
imports `services.latitude.LATITUDES` at line 54, `services.chat_store.CHAT_MODES`
at 62, and `services.tasks.EFFORTS, STATE_TYPES` at 63, all at module scope. The
lowest layer in the system imports three modules from the highest-churn one.

**11 modules over 1,200 lines carry 30% of all backend code.**
`backend/routes/chat.py` is 4,096 lines holding **22 distinct concerns, 7 of
which have no HTTP surface at all**: prompt construction, a compaction
summariser with its own LLM call, a ticket distiller with a second LLM prompt,
the context-window table, the send/claim state machine, plan-mode policy, and
cut-coordinate math. None of that is a route. It lives in a router because there
was no package to put it in.

And the thing that makes all of this fixable rather than merely diagnosed:
**this repo has already built the right answer once.** `services/commute/` is a
package, and its coupling stays internal at **0.76**, against `chat` at **0.27**
across 17 flat files and 15,892 lines, and `app` at **0.11**.

## Decision

**Every domain is a Python package whose `__init__.py` is its public API, the
backend has four layers, and the acceptance criterion is that the import graph
is a DAG.**

### The tree

```
backend/
  domains/
    chat/        # was 17 flat files + 7 concerns buried in routes/chat.py
    tickets/     # was 13 flat files
    agents/
    memory/      # knowledge, learnings, semantic index
    autonomy/
    deploy/      # collapses the TWO parallel deploy stacks
    apps/        # the app runner and registry
    commute/     # already correct, becomes the template
    voice/       # already partly correct
  wsplatform/    # paths, ownership, tenancy, ids, config, shared vocabulary
                 # NOT `platform/`: see "The name" below
  api/           # routers only, thin
  db/            # models, migrations, repositories
```

**A package's `__init__.py` IS its public API.** It declares `__all__`, and
nothing outside the package imports past it. `from domains.chat import
send_message` is legal. `from domains.chat.turn_machine import _claim` is not,
whether or not the symbol is private by name. Modules inside a package import
each other freely by full path; that freedom is the reason the boundary is worth
having.

Re-exporting submodules (`from . import config, poller, scoring`) satisfies this
as well as re-exporting symbols does, and it is the cheaper migration. The rule
the check enforces is the same either way: an import from outside names the
package, and takes something `__all__` names.

### The name: `wsplatform/`, not `platform/`

Amended 2026-08-17, the same day this record was written, after the first two
things that belonged in it were built.

This ADR originally named the package `platform/`. That name is a landmine.
`backend/` is on `sys.path` for every way this app runs (uvicorn resolving
`main:app`, pytest's rootdir), so a top-level package literally called
`platform` **shadows the standard library's `platform` module process-wide**.
The failure is not local to anything that imports it: the backend died before
pytest could collect a single test, because `uuid` calls `platform.system()` at
import time and got an empty package instead.

Reproduced directly:

```
$ mkdir -p /tmp/shadowtest/platform && touch /tmp/shadowtest/platform/__init__.py
$ cd /tmp/shadowtest && python -c "import sys; sys.path.insert(0,'.'); import platform; print(platform.__file__)"
/tmp/shadowtest/platform/__init__.py
```

So the package is `wsplatform/`. This is worth recording rather than quietly
renaming, for two reasons. It is the kind of defect that gets rediscovered by
whoever next reads the target tree in the plan doc and types the obvious thing.
And it is a small instance of this document's own thesis: the tree diagram was a
decision written down, someone built against it, and the writing down is what
made the collision a five-minute correction instead of a mystery.

If a later migration changes how the app is invoked so that `backend/` is no
longer a `sys.path` root, this becomes a one-line rename. Until then the name
carries the constraint.

### The four rules

**R1. A route handler contains no business logic.** Measured today: `birds.py`
is **8.9 lines per route** (thin, delegating, correct), `tasks_all.py` is
**102.8**, `chat.py` is **83.6**. The thin routers are exactly the ones whose
logic already lives in a service. Two mechanical facts stand in for the rule: a
handler body is at most **20 statements**, and **no module under `api/` imports
`db/` or `sqlalchemy`.** The second is the substantive half. A handler that
cannot reach the database cannot hold the business logic even if it wants to.

**R2. Zero function-local imports.** Any `import` whose nearest enclosing scope
is a function or method is a violation. Two carve-outs, named here so they are
not re-argued per site: an import under `if TYPE_CHECKING:` is module scope and
is fine; and a genuinely optional heavy dependency deferred to keep process
start cheap is allowed **only** with an inline `# lazy: <reason>` marker, which
the check reads and counts, and the count may not exceed its baseline. The 438
that exist are overwhelmingly not lazy loads. They are cycle escapes, and 22 of
the 24 mutual pairs say so.

**R3. Dependencies point one way.** The order is `api → domains → platform →
db`, and the arrow notation in the plan is ambiguous, so state it plainly: **a
layer may import anything strictly below it.** `api` may import `domains`,
`platform` and `db`. `domains` may import `platform` and `db`. `platform` may
import `db`. **`db` imports nothing from any layer above it**, which is the rule
`db/models.py` breaks three times today.

A cross-domain import (`domains/chat` importing `domains/tickets`) is a
same-layer edge. It is allowed, through the other package's `__init__` only, and
only if it does not create a cycle.

**R4. A god module is a reviewable event.** No new file over **800 lines**
without an ADR saying why the concern is genuinely that large. Mechanically: a
file that is new in the diff, or that crosses 800 lines in the diff, fails
unless its module docstring carries a line reading `Size: exempt by ADR NNNN`
naming a real record in `docs/decisions/`. The 11 modules already over 1,200
lines are grandfathered in the baseline and may only shrink.

### Where a shared vocabulary lives

**A closed vocabulary is platform, not domain.** `LATITUDES`, `CHAT_MODES`,
`EFFORTS`, `STATE_TYPES` and every other value set that both a table definition
and a service need moves to `wsplatform/vocabulary.py`. Both `db/models.py` and
`domains/*` then import downward from it.

This has to be said explicitly because it looks like a conflict between two
rules that are both correct. On 2026-08-17, `EFFORTS` and `STATE_TYPES` were
deliberately moved into `db/models.py`'s import path to kill a duplicate enum
under `scripts/check_one_definition.py`, whose own header records what it was
built for: the autonomy enum declared four times, agent roles twice, `EFFORTS`
twice, and `CHAT_MODES` twice **with a different element order in each copy**.
That fix was right against the rule that existed. Under R3 the same edit reads
as an upward import from the bottom layer, which is the worst direction in the
graph because everything imports `db`.

**Both rules are right and neither is weakened.** There is still exactly one
declaration of each value set, so `check_one_definition` stays satisfied; the
edge now points down, so the layering check is satisfied too. The conflict was
never between the rules, it was the absence of a layer beneath both of them.

The general test, so this is decidable without a debate each time: **if a symbol
has no behaviour and is imported by two layers, it is platform.** Constants,
enums, type aliases, the id minter, the path resolver, the ownership contextvar.
If it has behaviour that belongs to one domain, it stays in that domain.
`services/paths.py` and `services/ownership.py` are already the correct shape
for this (in-degree 49 and 44, ~148 lines each, near-zero fan-out) and are the
first two things to move.

### The reference implementation

**`services/commute/` is the template, and new packages copy its shape.** Its
cohesion is **0.76** against chat's 0.27 and app's 0.11, and the specifics are
worth naming because the number alone is not instructions. Verified by reading
it:

- It is a directory, not a prefix. Fifteen submodules split by concern: `config`,
  `poller`, `scoring`, `alerter`, `llm`, `timefmt`, `geo`, `journeys`,
  `messages`, `patterns`, `places`, `reason`, `segment`, `transit_match`.
- Its `__init__.py` docstring lists the public surface **function by function**,
  one line each, with what each returns.
- It declares `__all__` explicitly.
- It states its own invariant in the docstring: "Everything is read fresh from
  disk on each call, no caches across requests." A package that says what is
  true of all of it is a package with a boundary.

That is the whole template. It is not elaborate and it took no framework.

### The acceptance criterion is zero SCCs

**Not zero layering violations. Zero strongly connected components of size
greater than one.**

This is the load-bearing choice in the record. A layering check alone would
report green on a graph that still contains a 32-module knot, because that knot
is entirely inside one layer. The 58-module SCC is 27% of the backend and it is
the thing that makes the codebase hard to change: no module in it can be read,
tested or moved alone.

Two consequences follow.

**The graph is built from all imports, function-local ones included.** A cycle
check that only reads module-scope imports would report a DAG that exists only
because 438 edges were hidden inside function bodies. R2 and the SCC criterion
are therefore one check on one graph, not two checks.

**The largest SCC size is the progress metric**, and it is printed on every run.
58 today. 32 after the layering violations are fixed. 1 is done.

## The check

`scripts/check_module_layout.py`. Contract, precise enough to implement:

**Input.** An AST walk of every `.py` file under `backend/`, excluding `.venv`,
`__pycache__`, `alembic/versions` and tests. Absolute and relative imports are
both resolved to module paths. Each import edge is tagged with whether its
enclosing scope was module or function.

**Layer assignment.** By top-level directory: `api` = 3, `domains` = 2,
`platform` = 1, `db` = 0. During the migration, `routes/` maps to `api` and
`services/` maps to `domains`. **Anything unmapped is reported as `unlayered`
and counted, not passed by default**, so a new top-level directory cannot slip
through by being unrecognised.

**Interface.** `--json`; `--only-changed <REV>` for gate mode, restricting
failure to findings whose source file changed since `REV`; `--fail-on-increase`
against `scripts/module_layout_baseline.json`, which records per-code counts and
the largest SCC size and may only go down. Exit 1 on any in-scope violation.
Always prints the full tree report and the member list of the largest SCC.

**Findings, one code each.** `local_import` (function-scope import with no
`# lazy:` marker); `lazy_marker_over_baseline`; `upward_edge` (source layer
lower than destination layer, reported with both module names);
`deep_import` (an import naming a module inside another package rather than the
package, or naming a symbol the target `__init__` does not export in
`__all__`); `cycle` (membership in an SCC of size > 1, reported once per SCC
with its members and size); `oversize_module` (over 800 lines, new or newly
crossing, with no `Size: exempt by ADR NNNN` in the module docstring);
`fat_handler` (a function decorated with `@router.*` whose body exceeds 20
statements, reported with the count); `api_touches_db` (a module under `api/`
or `routes/` importing `db` or `sqlalchemy`); `unlayered_module`.

**Wiring.** `scripts/reviewer.sh`, beside `check_one_definition.py`, gated on
the diff touching `backend/**/*.py`, emitting the reason `module_layout_fail`,
and `module_layout_check_unavailable` in the ambiguous list rather than a silent
pass when the script is missing or errors.

## Consequences

**Gained.** A module you can read alone, because it is not in a 58-module knot.
A boundary a reviewer can see without running a tool, because it is a directory.
The 7 non-HTTP concerns in `routes/chat.py` get somewhere to go, which is the
precondition for testing any of them. Deleting a domain becomes deleting a
directory. And the two specific extractions the plan calls out become
straightforward: `services/app_runner.py` is 1,426 lines imported by 10 modules
of which **only 2 use its actual purpose**, the other 8 pulling in a process
supervisor to read a ~40-line developer-mode boolean, which becomes its own
module in `wsplatform/`.

**Cost.** A large mechanical diff across ~116 files that touches nearly every
import statement in the backend, and it must land after the `task→ticket` and
`job→run` rename (plan §4 item 1) or the same identifiers get moved twice. Every
in-flight branch rebases through it. A package boundary also adds a real
friction to reaching one function in another domain, which is the point and will
still be irritating.

**Not decided here.** `services/birds_analyze.py` is 3,443 lines containing its
own auth system with `password_hash` and `invites` tables for a separate app,
two independent storage-eviction engines, and one test file. It is a product,
not a module, and whether it becomes `domains/birds/` or leaves this repo is a
separate call. Under this record it is a grandfathered oversize module either
way.

**Settled, and not to be re-litigated.** A domain is a directory. `__init__.py`
is the API. `db` imports nothing above it. Shared vocabulary is platform. The
target is a DAG, not a smaller number of violations.

## Alternatives considered

**Fix the 40 layering violations and stop.** Cheaper, mechanical, and it would
show a green layering check. Rejected on the measurement that motivated this
record: it leaves a 32-module SCC entirely inside `services/`, so it buys a
passing check and not a readable codebase.

**Enforce boundaries by naming convention on the flat namespace**, keeping
`chat_*` and adding a lint. Rejected: the 22 prefix clusters are already that
convention, and the ratio of 438 function-local imports to 430 module-scope ones
is what it produced. A convention with no filesystem behind it cannot make an
`__init__` mean anything.

**Microservices, one process per domain.** Rejected in plan §3 and reaffirmed
here: the problem is intra-process module boundaries. A package fixes that. A
network hop between packages adds serialization, partial failure and deployment
coupling, and fixes nothing this record is about.

**Move `EFFORTS` and `STATE_TYPES` back to a duplicate declaration to clear the
upward edge.** Rejected outright. It trades a layering violation for a
correctness one, and `CHAT_MODES` already demonstrated the failure mode by
drifting in element order between its two copies. `wsplatform/` gets both rules at
once.

**Allow function-local imports where they break a cycle.** This is the status
quo stated as a policy, and it is what the 438 are. Rejected because it makes
the dependency graph unreadable from the source: an edge inside a function body
is invisible to a reviewer, to a tool that reads module headers, and to anyone
estimating whether a module can be moved.
