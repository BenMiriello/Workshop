# Client state: implementation plan (2026-08-26)

Status: APPROVED by the operator 2026-08-26, implemented on branch
`wt/development/client-state-v1`.

Companion to [`client-state-management-2026-08-25.md`](client-state-management-2026-08-25.md),
which is the audit and the *why*. This doc is the *what*, precise enough to
build from. Where the two disagree, this one is newer and wins; §1 records
exactly what changed and on what evidence.

---

## 1. What a second measurement pass changed

The 08-25 proposal was written from a state-surface count. A full inventory of
the chat's turn state and server reads then found four things that reshape it.

**1.1 `stream.attached` is a dead field.** `useTurnStream` maintains it through
four setters and returns it, and *no consumer reads it* — `Chat.tsx` destructures
`stream` twice and never takes `attached`. It still re-renders `Chat` on every
attach and detach. The 08-25 doc counted it as one of the seven answers to "is a
turn running". It is not an answer; it is dead weight, and it is deleted here.

**1.2 `is-streaming` has no CSS rule.** The class is applied to the composer at
`ChatComposer.tsx:121` and defined nowhere in `frontend/src/styles.css` or the
kit. So the composer's documented "streaming appearance" does not exist. The
`streaming` prop itself is real and does three useful things (two tooltips and
parking a refetch), so the prop stays and the dead class fragment goes.

**1.3 A real disagreement the audit missed.** `transcriptBlocks.ts:463` computes
`liveTurnId` and suppresses the live block when the committed log already holds
a reply tagged with the active turn's id — a deliberate correction of a
non-atomic backend read. So **`turnActive === true` does not imply an indicator
is on screen.** But `Chat.tsx` derives the 1 Hz clock, `inFlight` and the
composer's `streaming` from `turnActive` *without* that override. Consequences
are individually small (a timer running with nothing to animate; a tooltip
saying a message will queue behind a turn that has finished), which is exactly
why it survived. It is the same class of defect as the three fixed in
`2277cc60`: two derivations of one fact that can disagree.

**1.4 The verification constraint.** There is no DOM/render harness, no jsdom,
no testing-library, and `frontend/CLAUDE.md` states none is planned: *"the target
is the PURE RULE behind a behaviour, extracted into its own module so it can be
executed."* Neither `useThreadLog` nor `useTurnStream` has any test at all.

That last point is the load-bearing one. **A hook cannot be unit tested in this
tree.** Anything whose correctness lives inside a hook is verifiable only by
driving the built app in a browser. That is a real cost, and it is what splits
the old Stage 2 in two.

---

## 2. Revised staging

| Stage | Scope | Risk | How it is verified |
|---|---|---|---|
| 1 | One turn lifecycle, pure module | Low | Unit tests + browser probe |
| 2a | TanStack Query + migrate the simple reads | Low-med | Typecheck, browser probe, network counts |
| 2b | Move `threadEvents` onto Query | **High** | Ticket only, not built here |
| 3 | The other 33 `usePoll` sites | Med | Tickets only, not built here |

**2b is deferred deliberately, and this is a change from 08-25.** The events
path has three call sites, roughly twenty `sync()` triggers, a cursor/branch/cut
protocol, a commit-settle retry loop, and an SSE stream folding into the same
reducer. It is the operator's primary interface, five previous passes have
broken it, and per 1.4 none of it can be unit tested. Rewriting its fetch
orchestration is a real improvement and it deserves its own ticket, its own
spec and its own verification session — not a subagent pass bundled behind two
other stages. The measurable win it carries (3-4 identical tail reads per
new-chat send collapsing to 1) is recorded on that ticket.

---

## 3. Stage 1 — one turn lifecycle

### 3.1 The problem, stated exactly

Nine pieces of state answer "is a turn running / what is it doing", in six
modules, three server-derived and six client-local. They are combined ad hoc at
the point of use, and the two combination sites in `Chat.tsx` (`:1292` and
`:1303`) compute the same expression twice — and `ChatLog.tsx:151-153` then runs
`logPlaceholder` a *third* time on the same inputs.

### 3.2 The design

A new pure module `frontend/src/components/chat/turnLifecycle.ts`. One
discriminated union, one derivation, and selectors. No React, no I/O.

```ts
/** How closely this tab is following the run. Only ever meaningful while a
 *  run exists, which is why it is nested rather than a sibling field. */
export type WatchState = "attached" | "reconnecting" | "detached";

export type TurnLifecycle =
  | { kind: "idle" }
  /** A send is out; the server has not named a turn yet. Client-local. */
  | { kind: "starting" }
  | { kind: "running"; turnId: string; watching: WatchState;
      segments: ChatSegment[]; startedMs: number | null;
      /** The transcript is showing a live block for this run. False when the
       *  committed log already holds this turn's reply (transcriptBlocks'
       *  `liveShown` correction). */
      shown: boolean }
  | { kind: "stopping"; turnId: string };
```

`deriveTurnLifecycle(input)` is the ONLY place the union is constructed:

```ts
export function deriveTurnLifecycle(input: {
  activeTurn: { turnId: string; segments: ChatSegment[]; startedMs: number | null } | null;
  liveShown: boolean;
  starting: boolean;    // client-local, the POST is out
  stopping: boolean;    // client-local, stop request in flight
  reconnecting: boolean;
  detached: boolean;
}): TurnLifecycle;
```

Precedence, and it must be exactly this, because it is the current behaviour
made explicit rather than a new policy:

1. `stopping && activeTurn` → `{kind:"stopping"}`
2. `activeTurn` → `{kind:"running", watching: detached ? "detached" : reconnecting ? "reconnecting" : "attached", shown: liveShown}`
   — note `reconnecting` outranks `detached` in the LABEL (see 3.3) but both are
   representable here; the label selector applies the precedence.
3. `starting` → `{kind:"starting"}`
4. otherwise → `{kind:"idle"}`

Selectors, each replacing a scattered expression:

- `isBusy(lc): boolean` — `kind !== "idle"`. Replaces `turnActive || pipeline.starting`.
- `showIndicator(lc): boolean` — running-and-`shown`, or stopping. This is the
  value that folds in 1.3.
- `indicatorLabel(lc): string` — replaces the current `turnLabel`. Precedence:
  stopping → "Stopping"; watching "reconnecting" → "Reconnecting"; watching
  "detached" → "Still running"; else `currentActivity(segments)`.
- `runningTurnId(lc): string | null`.

`turnStatus.ts` keeps `currentActivity`, `TOOL_VERB`, `toolVerb`,
`elapsedSeconds` and `indicatorElapsed` — they are about wording and time, not
lifecycle. `turnPhase` and `turnLabel` are REPLACED by the selectors and
deleted, along with their tests in `sendPlan.test.ts` (moved, not dropped).

### 3.3 Two behaviours that must NOT change

- **`planSend` keeps reading the server's `activeTurn` directly.** It decides
  whether a message starts a new turn or is injected into a live one. That is a
  correctness decision about the server's state, not a display decision, and
  folding `liveShown` into it would change send semantics. `sendPlan.ts` and
  `useSendPipeline.ts:355-360` are OUT OF SCOPE. Say so in a comment where the
  lifecycle is derived, so the next reader does not "finish the job".
- **The label precedence** above is today's precedence from `turnPhase`. Keep it
  identical; this stage is a representation change, not a wording change.

### 3.4 Deletions

- `attached` state, its four setters, and its entry in the `TurnStream` type
  (§1.1). Confirm with a grep that no consumer reads it before deleting.
- The `is-streaming` class fragment in `ChatComposer.tsx:121` (§1.2). The
  `streaming` PROP stays.
- `turnPhase`, `turnLabel`, `TurnPhase`, `TURN_LABEL` from `turnStatus.ts`.

### 3.5 Single computation

`Chat.tsx` computes the lifecycle ONCE and passes what is needed down.
`logPlaceholder` must be called exactly once per render: today `Chat.tsx:1288`
computes it for `logShowsProblem` and `ChatLog.tsx:151` computes it again for
rendering. Pass the result down instead of the inputs.

### 3.6 Tests (`turnLifecycle.test.ts`)

Pure module, so this is ordinary vitest. Must include, as named regression
cases, the three defects fixed in `2277cc60`:

- a run that exists but has not been attached to yet is **"Working"**, never
  "Still running" (the flash);
- `isBusy` is true continuously across starting → running, with no false gap
  (the message-disappears frame);
- `showIndicator` is false when `liveShown` is false even though a run is
  active (1.3), and `isBusy` remains true.

Plus: every union arm constructible, precedence order, and that an idle
lifecycle carries no turn id.

---

## 4. Stage 2a — TanStack Query for the simple reads

### 4.1 Dependency

`@tanstack/react-query@^5` (5.102.4 verified reachable; React 18.3.1 satisfies
its peer range). Added to `frontend/package.json` only — **not** to
`packages/ui`. The kit is platform-owned and must not gain a data-fetching
dependency; primitives stay presentational.

### 4.2 The client

`frontend/src/lib/queryClient.ts`. Defaults chosen against this app, not the
library's:

- `staleTime: 30_000` — the app polls on a 5-60s cadence today; an aggressive
  default would refetch more than the code it replaces, not less.
- `refetchOnWindowFocus: true` — this is what `usePoll` already does
  (`packages/ui/src/usePoll.ts` runs on `visibilitychange` and `focus`), so it
  preserves behaviour rather than introducing it.
- `refetchOnReconnect: true`.
- `retry`: reuse the existing transient-failure judgement from
  `packages/ui/src/httpError.ts` rather than a new count, so "what counts as
  retryable" has one definition. Do not restate the rule.
- `gcTime: 5 * 60_000`.

Provider mounted in `frontend/src/App.tsx`, outside the router, inside
`AuthProvider`.

### 4.3 What migrates

Each of these is today a `useState` + `useEffect` (or `usePoll`) pair. Each is
independently revertable.

| Read | Current site | Key |
|---|---|---|
| `GET /chat/threads/{id}/context` | `ContextMeter.tsx:67` | `["chat","context",threadId,nonce]` |
| `GET /chat/threads/{id}/work` | `useChatWork.ts:66` (poll 5s/20s) | `["chat","work",threadId]` |
| `GET /chat/threads/{id}/mentionable` | `Chat.tsx:776` | `["chat","mentionable",threadId]` |
| `GET /chat/prefs` | `useChatThread.ts:201` | `["chat","prefs"]` |
| `GET /chat/providers` | `useChatScope.ts:101` | `["chat","providers",scope]` |
| `GET /chat/threads/{id}/branches` | `BranchPicker.tsx:56` | `["chat","branches",threadId]` |

`useChatWork`'s adaptive interval becomes `refetchInterval: live ? 5000 : 20000`,
which is the same rule expressed as data.

### 4.4 What explicitly does NOT migrate

- `chatApi.threadEvents` and everything in `useThreadLog` — Stage 2b.
- Both `EventSource` streams. A stream is not a query.
- `lib/draftSync.ts` — fire-and-forget `PUT` with `keepalive`, deliberately not
  a managed mutation.
- `chatApi.listThreads` — its retry/backoff is entangled with auto-naming
  (`useChatThread.ts:165-176`). Migrating it is a Stage 3 ticket.

### 4.5 Rule for this stage

**No behaviour changes.** Same endpoints, same triggers, same rendered output.
If a migration seems to want a behaviour change, stop and leave that read alone.

---

## 5. Verification protocol

Nothing in this branch is "done" on a self-report.

In the worktree, before review:
1. `npm run typecheck` from the worktree root — must be clean.
2. `cd frontend && npm test` — must pass, count must not fall.
3. `npm test --workspace @workshop/ui` — kit tests, must pass.
4. `bash scripts/check_design.sh` — no new design violations.

After merge to `main` and `bash scripts/apply.sh` (the post-commit hook refuses
to build from a linked worktree, so the built origin can only be produced from
the canonical tree):
5. `scratch/mutation_probe.mjs` × 6 — **zero** post-send blanks, zero
   user-missing frames, zero "Still running" flashes. This is the regression
   gate for `2277cc60`.
6. `scratch/net_probe.mjs` — record the tail-read count. Stage 2a must not
   increase it. (Reducing it is Stage 2b's job.)

**Rollback:** the merge is a single commit on `main`. `git revert -m 1 <sha>`
followed by `bash scripts/apply.sh` restores the operator's chat in about a
minute. This is stated because the branch changes the operator's primary
interface while he is using it.

---

## 6. Worktree conventions used

Per `shared/skills/git-worktree-workflow/SKILL.md` and
`docs/platform-redesign/branches-worktrees-and-environments-2026-08-20.md`,
adapted for the platform repo:

- Branch `wt/development/client-state-v1`, worktree at
  `branches/development/client-state-v1` (gitignored).
- Branched off **local `main`**, not `origin/main`: the skill's fetch/rebase/PR
  ceremony is scoped to *project* repos bound to GitHub, and pushing the
  platform repo is blocked by design. Merge is local.
- `node_modules` provisioned by hardlink clone (`cp -al`, 0.11s for 317MB) per
  §13.1 of the branches doc, which measured that npm does not hard-link from
  cache the way pnpm and bun do. Shared build caches (`node_modules/.vite`,
  hardlinked `*.tsbuildinfo`) were unlinked afterwards, because a hardlinked
  file written inside the worktree writes THROUGH to the canonical tree.
- The worktree is left UNLOCKED. `allocate_task_worktree` locks the trees it
  makes, and `git worktree prune` cannot reclaim a locked tree whose directory
  was removed (HANDOFF-branch-isolation-2026-08-22, correction 3).
