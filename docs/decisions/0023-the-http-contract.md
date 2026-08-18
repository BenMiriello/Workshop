# 0023. The HTTP surface has one contract

- Status: **proposed**
- Date: 2026-08-17
- Plan: `docs/platform-redesign/target-architecture-2026-08-17.md` §2.4, item 1 of
  the §4 sequence. That document's own status line still reads PROPOSAL, so this
  record is `proposed` and moves to `accepted` with it.
- Related: ADR 0024 (a domain is a package), which owns the rule that one
  resource has one router. The addressing section below depends on it.
- Supersedes: nothing. There has never been a decision record about the HTTP
  surface, and that absence is the thing this record is about.

## Context

There are **22 ADRs in this repo and not one of them is about the HTTP
contract.** The four audits run on 2026-08-17 found the same shape in every
layer: the code is coherent exactly where a decision was written down, and
incoherent exactly where one never was. The HTTP surface is the largest place a
decision was never written down, and it is measurably the most incoherent thing
in the tree.

The measurements, all from the plan unless marked otherwise:

**415 routes, 4 with `response_model=`**, all four in one 165-line module. **281
routes carry no return annotation at all.** Request bodies are well typed (113
pydantic models); responses are essentially undescribed. The frontend therefore
hand-writes **528 types** and casts at **232 `.json()` sites, 54 of them with no
annotation**. The drift is real and confirmed on 3 of 4 spot-checked endpoints:
archiving a chat returns the entire transcript, and a sibling route measured
**4.77 MB of `messages` inside a 4.79 MB response** and strips it, while these
three do not. The client type gives no hint, because it declares two fields the
endpoint does not return.

**80 collection GETs return 19 bare arrays and 61 envelopes across 52 distinct
top-level key names, 44 used exactly once.** That is one decision made 52 times
by 39 routers, which is what happens when it is not made once.

**Pagination exists on 20 of 80 collection GETs, with 8 different default
limits.** `GET /tasks` declares `limit: int | None = None`
(`routes/tasks_all.py:272`, verified) and returns the entire task set when the
caller omits it. Verified separately with
`grep -rEn 'limit: int = [0-9]+' backend/routes/`: **8 route signatures across 7
routers take a bare integer limit with no `le=` ceiling of any kind** (`events`,
`projects`, `deploys`, `jobs`, `auth`, `agents` twice, and an `autonomy`
helper), plus two pydantic body fields in `routes/knowledge.py`.

**Errors: 348 `raise HTTPException` sites and zero registered exception
handlers.** Ten routes return failures at HTTP 200 in three further shapes, and
four different error-message key names are live (`detail`, `error`, `reason`,
`hint`). `routes/tools.py:74-83` is the whole problem in one handler, verified:
an unknown tool raises 404, and a server exception two lines later `return`s
`{"error": ...}` at HTTP 200. A client cannot tell success from failure by
status code on that route, so it must read the body, so every client reads the
body, so the status code stops meaning anything anywhere.

**Three timestamp encodings.** ISO-8601 strings; epoch floats shipped raw in the
voice and relay bodies; and a pre-formatted human-local string from
`services/birds_analyze.py:1416` (verified) whose own docstring gives the format
as `'Jun 8, 10:55 PM'`. No client can parse or sort that, and it encodes the
server's timezone, not the reader's. **Eight or more field names for "when"**:
`ts`, `created_at`, `created`, `updated`, `updated_at`, `time`, `started_at`,
`at`.

**Ids: 173 `str` path params against 7 `int`.** `note_id` is `str` in
`routes/knowledge.py:60` and `int` in `routes/birds.py:103` (both verified). The
second is a SQLite rowid reaching the wire.

**Mutations: 7 response patterns across 219 routes.** `{"ok": True, ...}` with
seven different key-tuples. **Zero 204s. Exactly one 201.**

**Addressing: 12 sites where a static path segment shadows a path-param slot in
the same position.** Verified for the worst of them: seven literal words occupy
the slot `/agents/{agent_id}` also claims. Six come from `routes/agents.py`
(`adopt-group`, `build`, `delegates`, `from-template`, `templates`, `tools`) and
the seventh from `routes/autonomy.py`, which mounts with no prefix and writes
`/agents/autonomy/...` as a literal. Any agent whose id is one of those seven
words is unreachable. Separately, a ticket is addressable four ways across four
routers.

## Decision

**One contract, stated below, on every route. It is enforced by
`scripts/check_http_contract.py` on new and changed routes, and the existing
surface is a measured backlog that may only shrink.**

### 1. Collections have one envelope

Every collection response is exactly:

```json
{"items": [...], "page": {"limit": 50, "next_cursor": "...", "total": 1042}}
```

`items` is always present, always a list, empty for no results, never null,
never absent. `page` is always present and is one shared pydantic model:
`limit: int`, `next_cursor: str | None` (null means this is the last page),
`total: int | None` (null means the route declined to count, which is legal and
must not be confused with zero).

**Bare arrays are banned, including for small fixed lists.** A bare array has no
place to put a cursor, so each of the 19 that exist today would need a breaking
change the first time its collection grows.

**A single-resource response is the resource, unwrapped.** No `{"data": ...}`
around a singleton. Wrapping buys nothing and the generated client would only
strip it again.

**`tasks_all.py:400` is superseded, and it was the closest thing in the tree to
right.** Its `{tasks, count, total, scopes, offset, sort}` is the only response
in 415 routes that separates the page from the total, and that separation is
carried forward verbatim as `page.total`. Three of its six keys do not
generalise. `tasks` is an entity-named key, and entity-named keys are precisely
the mechanism that produced 52 distinct names; they also make a generic
`Page<T>` impossible in the generated client, which is the point of doing this
at all. `count` is `len(items)` and a derivable field is a field that can
disagree. `sort` echoes back what the caller sent.

`scopes` is the interesting one, because it is real data the caller wants. It is
not a member of the collection, and the rule is that **anything that is not a
member of the collection is a different resource with its own endpoint.**
`GET /tasks/scopes` already exists at `tasks_all.py:404`, which is the proof that
the split is available and cheap.

### 2. Pagination is keyset, and the ceiling is mandatory

**New paginated routes use a cursor, not an offset.** Almost every collection
here is time-ordered and append-heavy: runs, events, chat messages, bird
detections, usage records. Offset paging over an append-heavy list silently
skips and repeats rows between page fetches, which is the same class of defect
ADR 0018 and ADR 0020 exist to eliminate one layer up. The cursor is opaque:
base64 of the sort key plus the id tiebreaker. No client parses, constructs or
increments one.

**Offset survives only where it already is.** Removing it from 20 routes at once
would break the pagers reading them. Existing offset routes keep `offset` and
must additionally return `page.total`; no new route gets one.

**Every route that returns a collection declares
`limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)`.** Both constants live in
one module (`wsplatform/http.py`) so a router cannot invent the ninth
default. **The package is `wsplatform/`, never `platform/`**: a top-level
package called `platform` shadows the standard library module of that name
process-wide and the backend dies before a single test is collected. See ADR
0024, "The name".
`DEFAULT_PAGE` is 50 and `MAX_PAGE` is 200.

A route with a genuinely different ceiling declares its own `le=` and says why
in a comment on the line. `routes/birds.py:510` (`le=20000`, an atlas clip
fetch) is a legitimate case. **The check enforces that `le=` is present, not
what its value is.** What is banned is the absence of a ceiling, because that is
what turns one bad client into an outage.

**No route returns an entire store.** `limit: int | None = None` is the concrete
violation and it disappears.

### 3. One error model, and failure never arrives at 200

**The status code is the only signal of success or failure.** A client decides
from the status line and never from the body. This is not a style preference:
`routes/tools.py` proves that as soon as one route breaks it, every client of
every route has to start reading bodies defensively.

**The wire shape stays `{"detail": ...}`**, which is what FastAPI already
produces for `HTTPException` and for 422 validation errors. Rewriting 348 raise
sites to reach a new shape would be a large diff with no behaviour change and a
long tail of missed sites.

**`detail` becomes an object**, and it becomes one from a single place:

```json
{"detail": {"code": "ticket_not_found", "message": "no ticket 0f8c", "hint": null}}
```

A registered handler on `HTTPException` upgrades a plain-string `detail` into
that object, deriving `code` from the status when the raiser did not give one.
**All 348 existing raise sites therefore become compliant without being
touched**, and the raise sites that want a stable machine-readable `code` opt in
one at a time. This is the cheapest item in this ADR and it is the one with the
highest ratio of coverage to diff.

**`error`, `reason` and `hint` are banned as top-level keys.** `hint` moves
inside `detail` where it already has a slot. The other two were only ever there
because the response was a 200 that meant failure.

### 4. One timestamp encoding, one field-name convention

**Every instant on the wire is an RFC3339 UTC string** with an explicit offset
(`2026-08-17T19:04:11Z`). Epoch floats are converted at the serialization
boundary, not shipped raw. **Pre-formatted human-local strings are banned
outright.** `_fmt_dt`'s `'Jun 8, 10:55 PM'` is a rendering decision made by the
only layer that does not know the reader's timezone, and it destroys the
information a client needs to make it correctly.

**An instant field is named `<verb>_at`, past participle**: `created_at`,
`updated_at`, `started_at`, `ended_at`, `deleted_at`. `ts`, `time`, `at`,
`created` and `updated` are banned. **A duration is an integer of milliseconds
named `<thing>_ms`**, never a float of seconds, so the unit is in the name and
cannot be guessed wrong.

### 5. Every id on the wire is a string

173 of 180 path params already are, and §2.3 of the plan targets UUIDv7 keys
everywhere, which are strings on the wire regardless of their storage
representation. **An id is opaque**: no client parses, ranges over, compares or
increments one. `birds.py:103`'s `note_id: int` is a rowid leaking through the
HTTP surface; it stays an integer in the database and becomes a string in the
path.

**A resource's own id is `id`; a reference to another resource is
`<entity>_id`.** And because a slug is a label and never an identity (plan
§2.3 item 3), **a path segment carrying a slug is named `<entity>_slug`**, never
`<entity>_id`. Where a slug is still the addressing key today, as it is for
projects, it keeps working and it stops lying about what it is.

### 6. `response_model=` on every route

4 of 415 today. **A return annotation of `dict`, `list`, `Any` or nothing is not
a response model and does not count**, because none of them describes anything
in the OpenAPI schema and the generated client would emit `unknown` for all of
them. The declared model must be a pydantic model or a `Page[T]` of one.

This is the clause that makes the schema real, and therefore the clause that
plan §4 item 2 depends on: the generated TypeScript client replaces 528
hand-written types and turns drift into a compile error.

### 7. Mutations: status codes that mean what they say

| Operation | Status | Body |
|---|---|---|
| `POST` that creates | **201** | the created resource, plus a `Location` header |
| `PATCH` / `PUT` | **200** | the full updated resource |
| `DELETE` | **204** | none |
| `POST` that acts (start, cancel, retry) and completes | **200** | the resource in its new state |
| `POST` that acts and does not complete | **202** | the resource, or the run that will do it |

**`{"ok": true}` is banned.** A 2xx already says ok. The body's job is to say
what the new state is, which is the thing the client actually needs and the
thing that currently forces 106 hand-written reloaders in the frontend. This
one rule collapses seven key-tuples across 219 mutation routes.

**A `DELETE` of an already-absent resource returns 204, not 404.** Delete is
idempotent, and the alternative makes every double-click a logged error.

### 8. Versioning: not now, and here is the trigger

**Agreed with plan §2.4 item 4, with the trigger sharpened.**

Every consumer of this API today is built from this repo and deployed in the
same commit: `frontend/`, the mobile WebView, the shell workers, the CLI shims,
the agent tools. A version prefix on a surface whose only clients ship
atomically with it buys nothing, costs a rewrite of 415 paths, and creates a
permanent second code path that will be maintained by nobody.

The plan states the trigger as "a consumer not built from this repo". That is
close but it is the wrong test, and the counterexample is already in the tree:
**a native Expo build sitting in an app-store review queue is built from this
repo and still cannot be redeployed with the server.** The correct trigger is
therefore:

> **Version the surface the first time a consumer exists that cannot be
> redeployed in the same commit as the server.**

A public API, a third-party integration, and a shipped native binary all trip
it. The WebView does not. When it trips, `/api/v1` is applied to the whole
surface in one commit, not per route, and the versioned artifact is the OpenAPI
schema.

### 9. Addressing: one slot, one meaning; one resource, one address

**A position in a path is either always a literal or always an identifier, for a
given prefix.** Mixing them is what makes seven agent ids unreachable, and no
amount of route-ordering care fixes it, because the collision is in the address
space and not in the router.

Two rules produce that:

1. **A collection-level operation gets its own collection. A member-level
   operation goes under the member id.** `/agents/templates` becomes
   `/agent-templates`; `/agents/build` becomes `/agent-builds`;
   `/agents/delegates` becomes `/agents/{agent_id}/delegates`, which is what it
   always meant. `/agents/autonomy` is a different concept that happened to be
   spelled underneath this one, and it becomes `/autonomy`.
2. **One resource has exactly one canonical address.** A ticket is addressable
   four ways across four routers; one of those is canonical and the other three
   are deleted or answer 308. The canonical form is `/tickets/{ticket_id}`, with
   scope as a query filter on the collection and never a path segment, because
   **scope is a mutable attribute and a mutable attribute must not be part of an
   address.** Moving a ticket between scopes would otherwise change its URL,
   which is the same defect as a project rename being a fork.

Rule 2 is held in place structurally by ADR 0024: one resource is owned by one
domain package, and one domain package has one router.

## Migration posture

**415 routes cannot change in one commit, and pretending otherwise is how the
three previous documents of this kind decayed.**

**The touch rule.** The moment anyone edits a route handler for any reason, that
route comes to full compliance before the edit lands. The unit is the route, not
the module and not the router. Compliance is the whole list: `response_model`,
envelope, capped limit, string ids, RFC3339 `*_at`, correct status code, no
failure at 200.

**One exception, and it is not optional.** A wire-shape change to a route the
frontend reads is not complete until the frontend reads the new shape in the
same commit. Until the generated client lands, that is a hand edit. After it, it
is a compile error, which is the entire argument for doing plan §4 item 2 early.

**The check gates the diff and reports the backlog.** It fails only on routes
that are new or changed, exactly as `check_one_definition.py --only-changed`
does, for the reason that script's own header gives: a checker that fires
constantly gets disabled, which is how we got here. It prints every violation in
the tree on every run regardless, so the debt stays visible and countable.

**The backlog is a set of numbers that may only go down**, held in
`scripts/http_contract_baseline.json`: 411 routes without a response model, 19
bare arrays, 52 envelope keys, 10 failures at 200, 8 uncapped limits, 3
timestamp encodings, 7 integer path params, 12 shadowed path slots, 1 unbounded
collection. Each is burned down a router at a time under plan §4 item 2, and the
ratchet refuses an increase in any of them even in files the diff does not
touch.

## The check

`scripts/check_http_contract.py`. Contract, precise enough to implement:

**Input.** An AST scan of `backend/routes/**/*.py` plus any other module
declaring an `APIRouter`. It does not boot the app, because a check that
requires the app to start cannot run in the reviewer gate. It reads
`backend/main.py`'s `include_router` calls to compose full paths from router
prefixes, which the shadowing finding needs and a per-file scan cannot have.

**Interface.** `--json`; `--only-changed <REV>` for gate mode, restricting
failure to routes in files changed since `REV`; `--fail-on-increase` against
`scripts/http_contract_baseline.json`. Exit 1 on any in-scope violation, 0
otherwise. Always prints the full tree report.

**Findings, one code each.** `no_response_model`; `weak_response_model`
(`dict`, `list`, `Any`); `bare_array_response`; `envelope_keys` (a collection
GET returning a top-level dict whose keys are not exactly `items` and `page`);
`uncapped_limit` (a `limit` parameter with no `le=`); `nonstandard_page_default`
(a limit default that is not `DEFAULT_PAGE`); `new_offset_param`; `int_path_param`;
`failure_at_200` (a `return` inside a handler of a dict literal containing
`error`, `reason`, or `ok: False`); `banned_error_key` (top-level `error`,
`reason`, `hint`); `banned_time_field` (a response field named `ts`, `time`,
`at`, `created`, `updated`); `raw_epoch_timestamp` (a `time.time()` or
`.timestamp()` result placed into a response dict); `ok_true_body`;
`wrong_mutation_status` (a create handler without `status_code=201`, a delete
handler without `204`); `path_shadowing` (two composed routes with a literal and
a parameter in the same position under the same prefix).

**Wiring.** `scripts/reviewer.sh`, beside `check_one_definition.py`, gated on
the diff touching `backend/routes/` or `backend/main.py`, emitting the reason
`http_contract_fail` and, when the script is missing or errors,
`http_contract_check_unavailable` in the ambiguous list rather than a silent
pass.

## Consequences

**Gained.** The OpenAPI schema becomes true, which is the precondition for
generating the TypeScript client and deleting 528 hand-written types and 363
hand-rolled fetch calls. An entire class of bug moves from "someone notices on
screen" to "the build fails". One envelope means one `Page<T>` and one pager
component. One error model means `httpError.ts` has one shape to parse instead
of four. A capped limit means one bad client cannot fetch the whole ticket
store.

**Cost.** 415 routes of backlog, burned down under a rule that adds work to
every unrelated task that touches a route. That is deliberate: the alternative
is a flag day, and a flag day across 415 routes and 232 client call sites is not
a thing this repo can safely do. Cursor pagination is more work per route than
offset and is harder for an ad-hoc `curl` to walk.

**Settled, and not to be re-litigated.** The envelope key is `items`. The
timestamp is RFC3339 UTC. Ids are strings. Failure never arrives at 200. If a
route needs something this contract does not have, the contract is amended here,
in this file, once, rather than in that router.

## Alternatives considered

**Generalise `{tasks, count, total, scopes, offset, sort}` as it stands.** It is
the best envelope in the tree and it is already written. Rejected: an
entity-named collection key cannot be typed generically, which forfeits the main
prize (one `Page<T>` in the generated client), and two of its six fields are
derivable or echoes.

**JSON:API or HAL.** Both solve exactly this and both are specified and
tooled. Rejected: they solve considerably more than this (relationship graphs,
sparse fieldsets, hypermedia link discovery) and every one of those features
would arrive as ceremony on 415 routes with no consumer asking for it. The
envelope above is the subset that is load-bearing here.

**Leave bare arrays for small fixed lists.** Tempting, and it is what 19 routes
do. Rejected: it makes "is this collection paginated" a per-route fact a client
must remember, and the first time one of those lists grows past a page, the fix
is a breaking change instead of a new field.

**Normalise all 348 raise sites to a new error shape.** Rejected in favour of
the exception handler, which achieves the same wire shape from one place, with
no diff, and no tail of sites that were missed.

**Version now, at `/api/v1`.** Rejected. It is a rewrite of every path to buy
optionality for a consumer that does not exist, and the trigger in §8 is
concrete enough that we will know the day it does.
