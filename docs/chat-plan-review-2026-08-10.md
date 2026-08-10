# Chat plan review and disposition (2026-08-10)

A sweep of every open plan doc in the chat track, verified against code rather
than against commit messages or the docs' own claims. This file records what was
found, what is being built off the back of it, and what was deliberately put
down. It exists so the next sweep does not re-derive the same list.

## Method

Four readers, each given a slice of the docs and told to check every concrete
item against the tree and report `DONE | PARTIAL | NOT DONE` with file:line
evidence. A doc's own status header was treated as a claim to test, not as fact.
That mattered: several docs marked their items done, and several items marked
"remaining" had in fact shipped.

## Plans that are fully closed

- `chat-remaining-plan-2026-08-10.md` — all items, including the autonomy brake.
- `chat-baseline-2026-08-09.md` — P1.1 through P2.F.
- `chat-rebuild-spec-2026-08-09.md` — all three passes.
- `system-honesty-plan-2026-08-10.md` — 26 of 28; the two partials are below.

One deliberate deviation, recorded so it stops being rediscovered: the rebuild
spec says "no 0.4s store writes during a turn", and `update_run`
(`backend/services/chat_store.py:840-857`) does write a throttled `meta.json`
snapshot. That is intentional — it lets the events poll carry live output
without SSE. The code is right and the sentence is stale.

## Built in this pass

Ordered as they were dispatched, each verified against typecheck, design check
and the test suites.

1. **The dead `depth` / `fanout` autonomy controls are gone.** They were rendered
   as project-scope config with no consumer anywhere. This is exactly the class
   of thing `system-honesty-plan` W3.19 exists to remove.
2. **The stale enforcement comment** at `AutonomyPanel.tsx:25` no longer claims
   `touches` is enforced. It is a post-hoc diff check; see §10 of
   `platform-redesign/dev-mode-and-development-scope-2026-08-09.md`.
3. **Tracker two-way sync actually writes back.** `push_status`, `push_text` and
   `comment` existed with zero production callers, so a binding in `sync` mode
   was publish-and-pull only.
4. **`log.jsonl` is bounded.** Per-thread chat logs were append-only forever.
5. **MCP health has a surface.** Nothing in the app called `claude mcp list`, so
   a broken server was invisible.
6. **Context detail is cached** with a visible freshness line, instead of
   re-spawning the CLI on every panel open.
7. **Operator-pref defaults** for effort and permission mode, closing the missing
   tier in the turn -> thread -> pref -> model-default chain.
8. **Per-agent `effort` / `permission_mode` frontmatter** is parsed and wins over
   the chat's on an @mention turn.
9. **Plan mode reverts to the previous mode** rather than landing on a fixed one.
10. **One error presentation in chat** instead of three competing idioms.
11. **Developer mode and the `development` scope** — the full sequence in §11 of
    the dev-mode doc.

## Deferred by the operator, for discussion first

These are designed and understood; they were not deprioritised for difficulty.

- **A live run card inline in the originating transcript.** `JobStatusCard`
  exists and polls; nothing emits it, because the server-side card was removed to
  stop double cards. The unblocking move is to have the task-proposal card
  subscribe rather than to re-add a second emitter.
- **A presence strip of agents running work from this chat.** The data exists
  (`job.chat_id`).
- **"Remember this" on a message, and the "N notes in context" line above the
  composer.** Both are small; both are part of the notes/memory question below,
  which the operator wants reviewed as a whole before more surface is added.
- **The clarifying-question card and float**
  (`platform-redesign/clarifying-question-flow-2026-07-18.md`). The mechanism
  shipped by another route: escalations reach the operator through the Inbox
  (`run_inbox.py` reconciles `awaiting` into a question, `routes/inbox.py`
  writes the answer back into the ticket). What is missing is only the in-chat
  delivery. That doc needs either a supersession note or a build, and which one
  it gets is a design call.

## Flagged as underscoped, not attempted

- **P5, Run-as-session.** The asking half shipped (`run_inbox.py`); the state
  machine (waiting/working/blocked/done plus structured activities) did not. This
  is a platform entity-model change, not a chat change, and it needs a design
  pass before code. Building it from the one-line description in
  `continuation-2026-07-31.md` would be guessing.
- **The scope-chip row** (`chat/chat-header-restructure-2026-07-30.md` §4.1b).
  Replacing the single scope picker with a composable chip set implies a
  multi-scope context-composition backend that does not exist. The UI is the
  small half.

## The open decision, restated

`/status`, `/agents`, `/mcp`, `/memory`, `/init`, `/review` were described as
build-only. The gate itself went away with the slash picker, so `/init` and
`/review` are already typeable in any chat and merely unlisted. The decision is
no longer "should they be gated" but "document the current behaviour or re-gate
them".

## The rule this sweep confirms

The 2026-08-10 plan closed with "ask does this control do what it claims, not is
this code sound". Every item in the "built in this pass" list above was found the
same way. Two of them — the dead autonomy controls and the unwired tracker
push — are controls the app renders or advertises with nothing behind them, and
neither would be caught by any test of code quality.
