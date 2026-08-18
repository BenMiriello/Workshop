# Handoff: execute item 2 (the API contract), 2026-08-18

The plan is `target-architecture-2026-08-17.md`. Items 0, 0b and 7 landed, plus
two clauses of item 5 and the chat half of item 1. This document is what the
next agent needs to start item 2 without re-deriving anything.

## Read these three, in this order

1. `docs/decisions/0023-*.md`, the HTTP contract. It is the spec you are
   implementing. The envelope is `{items, page}`, the error body is one shape
   from one handler, and both are already live.
2. `docs/platform-redesign/target-architecture-2026-08-17.md` §2.4 and §6.
3. `frontend/src/api/README.md` if present, else `scripts/gen_openapi.py`'s
   docstring: it explains the generator and the worked example.

## State, measured 2026-08-18

- Backend suite 2927, frontend 966, typecheck clean.
- Seven gates green: module_layout, no_process_globals, response_model,
  no_raw_storage, duplicate_helpers, one_definition, generated_client.
- `response_model`: 90 of 411 routes have one, 321 do not.
- `frontend/src/api/schema.gen.ts` is generated and current. Exactly ONE
  frontend file consumes it so far (the worked example). 157 `.json()` call
  sites remain hand-typed.

## The work, in order

### 2a. Annotate routers, one router per commit

For each file in `backend/routes/`, give every route a real `response_model`.
"Real" means a Pydantic model or a generic over one. `response_model=dict`,
`list[dict]` and `dict[str, Any]` are rejected by `check_response_model.py`
BY DESIGN: they satisfy the keyword while producing `unknown` in the generated
client, which is the exact failure the rule exists to prevent. `None` is
allowed only on a 204.

Model placement: beside the router, or in a shared module when two routers
return the same thing. Do not invent a new shape for a route: read what it
actually returns today and describe THAT. If the current return is wrong,
that is a separate finding to report, not to fix silently in the same commit.

After each router: `npm run gen:api-types`, then commit the router, the models
and `schema.gen.ts` TOGETHER. The generated file is not optional and not a
follow-up.

### 2b. Adopt the generated types on the frontend

Replace hand-written response types with the generated ones, file by file,
starting where the drift was already measured: the chat thread endpoints. The
worked example in the tree shows the import form. Delete the hand-written type
when it is replaced; leaving both is how the drift comes back.

### 2c. Re-pin

`python3 scripts/check_response_model.py --write-baseline` after review, so the
number ratchets down and cannot climb back.

## Rules that are not optional

**Commit before your turn ends.** Subagents die with the turn. On 2026-08-17
two agents' work was left uncommitted in the shared tree and one of them had
ALREADY applied a database migration, so the live backend spent 3.3 hours
querying a column that no longer existed. Every Postgres chat write failed,
swallowed by a fire-and-forget except, and the mirror drifted from 2 threads to
4. Nothing on screen showed it.

**Never split a migration from the code that matches it.** Uncommitted code can
be reverted. An applied `ALTER TABLE` cannot. If your change has both halves,
they land in one commit or they do not land.

**Regenerate `schema.gen.ts` before any `apply.sh`.** `apply.sh` builds from
disk. A stale generated file ships a bundle that disagrees with the backend.

**Do not re-pin a ratchet to make it green.** If a number moves the wrong way,
find the cause. If it is a deliberate tradeoff, re-pin AND write down why, in
the commit message. Two baselines have moved that way already and both say why.

## Do not do these

- Do not start `task -> ticket`. It is deferred, and §5 of the plan has the
  measurement showing why a mechanical rename corrupts the asyncio code.
- Do not touch the chat transcript migration (stages 2 to 4) or the branching
  rebuild. Both are open tickets with their own staging requirements.
- Do not repair the chat mirror. `db/chat_verify.run(rebuild)` has no
  per-thread targeting and one drifted thread is usually live. It has a ticket.

## Known open, not yours unless told

- 4 threads drift in the Postgres chat mirror, 5 messages missing. The JSONL
  log is authoritative for reads, so nothing is lost on screen.
- ADR 0016 says a durable work item's code identifier is `task`. That is true
  today and becomes wrong the day the deferred rename lands. Amend it as part
  of that work, not before.
