# 0025. One owner per server entity in the client, and the cache is the owner

- Status: **proposed**
- Date: 2026-08-17
- Depends on: ADR 0023 (the HTTP contract and the generated client). The cache
  is keyed on the generated client, so 0023 lands first.
- Plan: `docs/platform-redesign/target-architecture-2026-08-17.md` §2.5, item 5
  in the §4 sequence.
- Generalises: the rule already stated and already proven in
  `frontend/src/components/chat/threadStore.ts`.

## Context

**The repo has already written this decision down once, for one entity, and it
worked.** `components/chat/threadStore.ts` opens with it:

> *"ONE store per thread. The client's whole model of a conversation lives here,
> and nothing else owns any part of it.*
>
> *This replaces five owners of the transcript that were concatenated by
> convention at render time, `messages`, `pendingSegments`, `sentDuringTurn`,
> the type-ahead queue and the voice settle. Every historical reorder,
> double-bubble and eaten-message report was a disagreement between two of the
> five, because there was no single answer to 'what is in this chat'."*

That last clause is the whole thesis and it is not a hypothesis: it is a
retrospective on shipped bug reports. The transcript had five owners, the bugs
were the owners disagreeing, and the fix was not better synchronisation between
them. It was deleting four of them.

**Every other entity in the app still has the five-owner shape.** The
2026-08-17 frontend audit measured it:

| Measured | Value |
|---|---|
| `fetch(` call sites / files containing them | **363** in **84** |
| Components fetching directly | **66** |
| Fetches behind a client module | **13%** |
| Distinct loading/error state shapes | **21** |
| `useState<T \| null>(null)` slots | **341** |
| Writes / hand-written reloaders after a write | **183** / **106** |
| Cross-component invalidation, of any kind | **0** |
| API paths fetched from more than one file | **50** of **188** |

I re-ran the first two and the fourth against the tree on 2026-08-17
(`grep -rno "fetch(" --include=*.ts --include=*.tsx frontend/src | wc -l`, the
same with `-l`, and `grep -rno "useState<[^>]*null>(null)"`) and got 364 in 84
files and 351 null slots. The audit numbers stand; the drift is two days of
commits, and it is in the wrong direction.

Three of those rows are the same defect at different magnifications.

**Zero cross-component invalidation, with 106 hand-written reloaders, means a
write is only visible to the component that performed it.** Every other view of
that entity is stale until something independently refetches. The 106 reloaders
are the manual, per-site, per-author reconstruction of one line of framework
behaviour.

**`useState<T | null>(null)` conflates three states into one value.** Loading,
empty and failed are all `null`. 341 times. Every one of the 21 distinct
loading/error shapes exists because some author needed to tell two of those
three apart and invented a way to do it locally.

**50 of 188 paths have more than one owner.** `/api/agents/autonomy/status` has
three independent owners with three different refresh policies, one of which
fetches once on mount and never again. Two of those can be on screen together
showing different answers to the same question. That is precisely the
"disagreement between two of the five" that `threadStore.ts` was written to end,
reproduced at the scale of the whole app.

**Polling.** `frontend/src/lib/usePoll.ts` already decided this one too, and its
header says why: Workshop is installed as a PWA and is never closed, so forty
`setInterval` sites each kept running on a backgrounded phone, waking the radio
and the CPU. Two of the forty checked visibility. The hook pauses while hidden
and runs immediately on return, and it is tested (`pollShouldRun` is pure for
exactly that reason). **25 of the 40 surfaces have still not migrated**,
including a 1.2s poll in the birds LiveFeed.

**Browser storage.** All **19 localStorage keys are global to the browser
profile**. Enumerating the key expressions in the tree gives 22 distinct ones
(three string literals and nineteen prefix or constant expressions:
`OUTBOX_PREFIX`, `SEL_PREFIX`, `PROVIDER_PREFIX`, `CACHE_PREFIX`, `TOKEN_KEY`,
`WORKBENCH_KEY`, and so on). None carries a user dimension.

**Testability.** `pages/` (excluding `pages/apps/`) plus non-chat `components/`
is **22,390 lines with zero pure modules and zero test files**. I verified both
halves: `find pages -path pages/apps -prune -o -name '*.ts*' -print` plus
`find components ... | grep -v '^components/chat/'` sums to exactly 22,390
non-test lines, and `find pages components -name '*.test.ts*' -not -path
'components/chat/*'` returns **0**. The same query over `components/chat` and
`lib` returns **57**. The repo's documented frontend test policy is pure logic
modules only, with no DOM harness. Under that policy those 22,390 lines are not
merely untested, they are untestable in principle. The testable ceiling is
**42%** of the frontend and **16%** is exercised.

**File size.** **22 files exceed 600 lines and hold 34% of non-test code.**
`ProjectDetail.tsx` is **1,785 lines** with 16 sections, 60 `useState` and 22
fetches (I confirmed the line count). It is not a page, it is sixteen pages
sharing a scope.

## Decision

**Every server entity has exactly one owner in the client: a cache entry. One
entity, one key. A write invalidates the key; it does not reload anything by
hand.**

This is `threadStore.ts`'s rule with the word "thread" replaced by "entity". The
transcript keeps its store, because a live turn is a reducer over a stream and
not a cached GET; everything that is a resource fetched by URL becomes a cache
entry.

Six clauses follow.

### 1. TanStack Query, not a bespoke cache

At 363 call sites the hand-rolled version is already more code than the library,
and it has been written 363 times without converging. §3 of the plan rules the
alternative out in one sentence and it is the right sentence: *"the repo has
already proven it can build the right thing once and not generalise it; a
hand-rolled cache would be that pattern again."* A bespoke cache would be the
sixth owner.

What adoption deletes, concretely: the 106 reloaders (invalidation replaces
them), the 21 state shapes (one discriminated status replaces them), and the 341
three-way-ambiguous nulls (`pending` / `success` with empty data / `error` are
distinct values, not one). The divergence class goes with them, because two
components asking for the same key get the same entry by construction rather
than by both authors happening to poll at the same rate.

### 2. The cache is keyed on the generated client

Query keys are derived from the ADR 0023 generated client's operation
identifiers and parameters, not from hand-written strings. That makes the cache
typed end to end: the key, the response type and the invalidation target all
come from the same schema, so a route whose response shape changes is a compile
error at every consumer rather than a runtime `undefined` at one of them.

Corollary, and this is the enforceable part: **`fetch(` outside the generated
client is the thing the check forbids.** Not "prefer the client". The escape
hatch is not a second fetch, it is an addition to the schema.

### 3. One polling policy: `usePoll`, and the exemptions are already written

Every recurring server read goes through `usePoll`. Default is pause-while-hidden
with a catch-up run on return. A caller that must keep ticking says
`whenHidden: "continue"` and, by saying so, documents why.

`usePoll`'s own header names what it does **not** cover, and this ADR adopts that
list unchanged so the rule cannot be read as banning something legitimate:
timers that are not polls. A bounded retry that gives up after N tries, an audio
meter, a debounce, a one-shot delay, a clock tick. Those are their own logic and
they stay where they are. The check keys on `setInterval` performing a server
read, not on `setInterval`.

Where a query is on a `refetchInterval`, that interval obeys the same rule:
paused while the document is hidden unless the call site declares otherwise.

### 4. Browser storage is namespaced by user

Every `localStorage` and `sessionStorage` key is prefixed with the acting user's
id: `ws:<user_id>:<key>`. Writing an un-namespaced key is a violation, and
reading one is how the migration shim finds legacy values once, on first load
after sign-in, and rewrites them under the namespace.

**This is a defect today, not only under multi-user.** The plan states it as a
present-tense defect and it is right: a second signed-in user on one machine
inherits the first user's chat drafts, outbox, scroll positions and device
tokens. The browser profile is shared even when the account is not, and the
outbox in particular is queued content that would be sent under the wrong
identity. The un-namespaced keys are also why "sign out" cannot mean anything:
there is no set of keys that belongs to the session that ended.

Keys that genuinely describe the device rather than the person (a station label
on a shared kiosk, a theme preference the operator wants to survive account
switches) may stay global, but only by being listed in the namespacing module
with a reason. The default is namespaced.

### 5. The rule comes out of the page as a pure module

**A page composes; it does not compute.** Any logic with a testable answer,
which is to say a function from data to data with no React and no DOM, moves to
a `.ts` module beside the component or into `lib/`, and arrives with a test.
Rendering, layout and event wiring stay in the `.tsx`.

This is the move `components/chat/` already made, and it is why that directory
is 58 to 98 percent pure with 57 test files while the other 22,390 lines have
zero of each. `transcriptBlocks.ts` (what renders, given a log), `branchable.ts`
(whether a bubble carries the branch control), `sendPlan.ts`, `pollShouldRun`:
every one of those was a decision buried in a component before it was a tested
function. The extraction is what raised chat's ceiling, and it is the only thing
that raises the app's.

The target is stated as the ceiling, not as coverage: **coverage is capped by
purity, so raising coverage means extracting, not writing more tests against the
same untestable surface.**

### 6. A file over 600 lines is a reviewable event

600 lines is the threshold because 22 files are over it and they hold 34% of
non-test code, so the line is drawn exactly where the mass is. Crossing it means
either splitting the file or writing down, in the file's header, why the concern
is genuinely that large. This mirrors the ~800-line rule ADR 0024 sets for
backend modules; the number is lower here because a component that large is
almost always several components and a page that large is several pages.

`ProjectDetail.tsx`'s 16 sections are 16 candidate route children with 16
independent cache keys, which is the shape it should have had.

## Migration posture

**363 call sites do not move in one commit, and pretending otherwise is how the
three previous target documents in this repo decayed.** So the rule is stated in
two halves, and only one of them is a gate.

**The moment anyone touches a component, that component must leave satisfying
all six clauses**, for the entities it reads and writes:

1. no `fetch(` in it, only the generated client through a query or mutation
2. every entity it reads has one key, and it does not own a second copy in state
3. every write it performs invalidates, and reloads nothing by hand
4. every recurring read is a `usePoll` or a `refetchInterval` that pauses hidden
5. every storage key it uses is namespaced
6. any pure rule it contains has moved out with a test, and it is under 600 lines

"Touches" means changes behaviour. A rename, a token swap or a copy edit does
not drag a 1,700-line page into scope; adding a fetch, a `useState` of server
data, or a new section does.

**Everything else is standing backlog**, worked in priority order and measured
rather than estimated. The three ordered by damage:

- the **50 multi-owner paths**, worst first, starting with the three owners of
  `/api/agents/autonomy/status`, because those are live disagreements on screen
  rather than latent duplication
- the **25 unmigrated polling surfaces**, starting with the 1.2s birds LiveFeed
  poll, because that one is a battery cost on a device in a pocket
- the **19 storage keys**, all at once, because namespacing is a single
  mechanical change plus one migration shim and splitting it would leave two
  conventions live

The gate is what stops the numbers growing. The backlog is what shrinks them.
Neither works alone: this is the fourth document in this repo to describe a
correct frontend target, and the previous three had no gate.

## Consequences

**Gained.** One answer to "what is this entity", per entity, in the client.
Writes that are visible everywhere the entity is shown, without any component
knowing about any other. Loading, empty and failed as three distinct values
instead of one `null`. A typed path from route handler to render, since the
cache key and the response type both come from the schema. A polling policy that
does not run a PWA's timers in someone's pocket. A frontend whose testable
ceiling can rise, which today it structurally cannot.

**Cost.** A dependency the frontend did not have, with its own concepts
(`staleTime`, `gcTime`, query keys) that every contributor must learn. A period
where both patterns are live, which is unavoidable at 363 sites and is why the
touch rule exists. Extracting pure modules makes the file count grow, and some
of those modules will be thin at first.

**Explicitly not decided here.** Whether writes use optimistic updates or wait
for the server. That is per-mutation and depends on how bad a rollback looks;
the chat send path already answers it one way (pending sends are modelled
explicitly in the store) and a settings toggle answers it another. Also not
decided: whether `threadStore.ts` itself eventually folds into the cache. It
should not until there is a reason, because a live turn is a stream fold, not a
cached resource, and the store is the one part of this that already works.

**Not re-litigated.** Building our own cache. §3 of the plan and this record both
close it. If the library is ever the wrong answer, the argument has to be a
measurement, not a preference.

## The check

`scripts/check_frontend_state.sh` (contract; a sibling agent implements it, and
it belongs in the reviewer gate beside `check_design.sh`).

It runs over `frontend/src/**/*.{ts,tsx}` and `mobile/`, ignoring `*.test.ts*`,
and fails on any of the following. Each failure names the file, the line and the
clause.

1. **`fetch(` outside the generated client.** Any `fetch(`, `window.fetch(`,
   `XMLHttpRequest` or `axios` reference outside the generated client directory
   and its one hand-written transport wrapper (a single allowed path, listed in
   the script; it is where auth headers are attached and where the existing
   `components/chat/httpError.ts` decoding moves to). This is the load-bearing
   rule; the rest are narrower.

2. **An un-namespaced storage key.** Any `localStorage` or `sessionStorage`
   access outside one new module, `frontend/src/lib/storage.ts`. That module
   becomes the only caller of the browser API and it applies the
   `ws:<user_id>:` prefix; a device-scoped exemption is an entry in a list
   inside it, so an exemption is a diff someone reviews.

3. **A polling `setInterval` outside `usePoll`.** A `setInterval` or a recursive
   `setTimeout` whose callback transitively reaches the generated client. The
   non-poll timers `usePoll`'s header exempts (retry, audio meter, debounce,
   one-shot, clock tick) do not reach it and so do not trip this.

   **Built 2026-08-25, as `scripts/check_frontend_state.py` (Python, not the
   `.sh` this section names), and it counts `usePoll` ITSELF as well.** That
   widening is not scope creep, it is this clause read after the fact: when the
   clause was written `usePoll` was the DESTINATION, so a hand-rolled interval
   was the only wrong answer. It is not the destination any more, and the six
   days between the query layer landing and the dashboard adding four new
   `usePoll` sites are the proof that leaving the old mechanism un-gated is the
   same defect one step up. Two counts, pinned at 43 and 4, and they only go
   down. Timer classification is by hand, in an allowlist inside the script with
   a reason per entry, rather than by chasing callbacks through the import
   graph — see the script's header for why an approximate reachability walk is
   wrong in both directions on this tree.

4. **A file over 600 lines** without a `@oversize:` header line giving the
   reason. Report-only for the 22 files that are over it on the day the check
   lands: the script carries them as a frozen baseline list, fails on any new
   entrant, and fails on any baseline file that grows. The list only shrinks.

5. **Advisory, reported and not failed:** count of
   `useState<...| null>(null)` slots, count of distinct paths fetched from more
   than one query-key owner, and the pure-module ratio per directory. These are
   the backlog's progress meter. `field_occupancy.py` and `doc_status.py` set
   the precedent: measure first, wire the threshold into the gate separately,
   once the number has somewhere to go.

The baseline mechanism in (4) is the design for all five: the check must be
addable to the gate on day one, with the existing violations frozen rather than
fixed, or it will not be added at all. `check_one_definition.py` went from 4 to
0 in a day because it could fail immediately. This one cannot, so it ratchets.
