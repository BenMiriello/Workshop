# Continuation brief — 2026-07-31

> **Read this first if you are picking up this work.** It records what is done,
> what is left, and one unresolved design problem (background autonomy) that
> must NOT be switched on until it is worked through.
>
> Companion docs, in reading order:
> 1. [`entity-model-2026-07-18.md`](entity-model-2026-07-18.md) — authoritative vocabulary and schemas
> 2. [`agent-interaction-model-2026-07-31.md`](agent-interaction-model-2026-07-31.md) — the decisions, research and phase plan this session executed against
> 3. this file — current state, and the open problem

---

## 1. Where the work stands

The plan is §8 of `agent-interaction-model-2026-07-31.md`. Status:

| Phase | State | Commits |
|---|---|---|
| **P1** Dead code + vocabulary | done | `505f590` |
| **P2** Agent model (role, not runtime) | done | `d54c092` |
| **P3** @mention routing | done | `33016f6`, `1b26d12` |
| **P4** Inbox | done | `8146c93`, `babe12f` |
| **P5** Run → Session | **partial** (asking half done) | `8f37faf`, `2720a98` |
| **P6** Memory layers | **done** | `9002956`, `ca6f973` |
| **P7** Detail pages + cross-linking | **done** | `8f37faf`, `a6f9a54` |

Also landed outside the phase plan, in response to operator review:

- **Ticket filing reworked** (`fd5ed77`, `10fcf38`). Two blind icons became one
  review step; then the underlying model was replaced — a conversation is not
  one ticket, so `services/ticket_drafts.py` proposes 0..N self-contained
  drafts with confidence levels, stored on the thread so reopening never
  re-derives or loses edits.
- **Modals are centered by default** (`10fcf38`). `components.css` had an
  unconditional `@media (max-width: 560px)` turning EVERY modal into a bottom
  sheet. Now opt-in via `<Modal sheet>`.
- **`restart_backend.sh --deferred` fixed** (`7db6c83`). See §4.
- **Turn contract + draft-clear** (`a7faffa`). See §2 and §3.
- **Chat coherence + split** (`380190a` … `4a1c65b`). Compaction no longer
  renders as ticket filing, one drawer at a time, errors no longer swallowed on
  empty chats, and `Chat.tsx` broken up: 2,560 → 1,823 lines, `useState` 41 →
  31, `useRef` 22 → 6, `useEffect` 23 → 14. See
  [`../chat/chat-state-audit-2026-07-31.md`](../chat/chat-state-audit-2026-07-31.md).
- **No wall clock on a turn** (`2720a98`).

### What P5/P6/P7 still need

**P5 (Run → Session).** The ASKING half is done: `services/run_inbox.py`
reconciles every task carrying an `awaiting` record into an inbox `question`,
and closes it when the task unblocks. That was the part that mattered — the
escalation primitive existed and nothing surfaced it.

Still undone is the state machine itself: the entity model's point (§4 D11) is
that a Run is really a *session* with state (`waiting/working/blocked/done`)
emitting structured activities (thoughts, tool calls, elicitations). Today a run
is still a fire-and-forget process that happens to be able to ask.

**P6 (Memory).** Built. `role:<role>` is a scope kind
(`shared/knowledge/roles/<role>/`); `knowledge.read_layers` is the union
`workshop → role → place → private`, injected into the chat prompt LABELLED per
layer so a conflict between "how this work is done" and "how this project does
it" is resolvable; `services/memory_promotion.py` proposes a note held by three
or more narrow scopes for promotion, as a `review` item that only writes when
approved.

**P7 (Detail pages).** Done. Ticket → chat was already there; Run → chat and
Agent → chats/tickets/runs already existed on the design system. The actual gap
was the INBOX: items carried `ticket_id` and `run_id` and the UI never used
them, so a row told the operator something was blocked and made them go find it.
Expanded rows now link to both.

---

## 2. The turn-continuation problem (fixed this session — verify it worked)

**Symptom.** Chat turns announce continuation ("continuing to P5") and then stop
dead. Repeatedly, across long pieces of work. The operator named it the main
failure mode of the Workshop chat.

**Cause, and it is not motivational.** A turn ends the instant the model emits a
message containing only text and no tool call. So writing a closing summary IS
the stop signal — identical, mechanically, to finishing. The model experiences
writing "next I'll do X" as making a plan; the harness experiences it as the end
of the session. Nothing resumes. The agent has no way to observe the stop
condition it is subject to, and was never told it.

Confirmed against the live turn journal: no step cap, and since `2720a98` no
wall clock either — `CHAT_TIMEOUT_S = 0` means no deadline, threaded through
build_streaming (`deadline = None`), llm_router (0 must become `None` before
`subprocess.run`, which treats `timeout=0` as already expired) and
voice_worker's build path. **Nothing forces the stop.** The model chooses it
every time, by writing a paragraph.

The wall clock was removed because it produced the same failure as the step cap
from the other direction: work guillotined mid-task and reported as "stopped at
the time limit". `turn_contract` no longer asserts a bound that does not exist —
telling an agent it has fifteen minutes when nothing enforces that is a false
constraint, and a false constraint on turn length is exactly what makes an agent
wrap up work it should be finishing.

**Fix.** `chat_context.turn_contract()` — a block re-sent on EVERY turn, first in
the prompt, stating: text-only ends the turn; narrate progress alongside tool
calls (the chat streams those segments live); never announce continuation and
then stop; write terminal text only when done or genuinely blocked; there is no
step cap and running long is expected. Wired into both `_resumed_prompt` (every
turn after the first) and `_assemble_full_prompt` (the first), so a fresh chat
and a continued one behave the same.

**Why re-sent every turn rather than once at session start:** by the time it
matters, a session-start instruction is thousands of tokens back, behind
everything the conversation has since accumulated. It sits with the other
volatile environment facts because it is one.

**Not yet verified.** This is a behavioural change; it needs several long turns
to judge. If turns still stop early, the next thing to check is whether the
contract is actually reaching the prompt (log a turn's assembled prompt) before
assuming the instruction is insufficient.

---

## 3. Composer retains sent text (fixed this session — verify)

**Symptom.** Occasionally a message sends and the text stays in the input.

**Cause.** The composer is a *synced draft* (`lib/draftSync.ts`): localStorage +
BroadcastChannel + a server copy, with debounced writes (120ms broadcast, 600ms
server). `setInput("")` on send only SCHEDULES the clear. Until it propagates,
the just-sent text is still the newest copy somewhere else — another tab, another
device, or the server — and the three adopt paths (mount, focus/visibilitychange,
BroadcastChannel) will restore it once the 3s "actively editing" guard lapses.

**Fix.** `useDraft` gained `clearNow()`: writes through synchronously to every
surface, and sets a `justSent` guard that refuses ANY adopt for 8s. A send is
deliberate; nothing arriving later should undo it. `send()` uses it instead of
`setInput("")`.

**Residual risk:** if it still recurs, the remaining suspect is a mobile IME
composition event completing after the clear and re-inserting the old value.
That would need reproducing on-device.

---

## 4. Operational trap worth knowing

`restart_backend.sh --deferred` used to sleep a fixed 5s and then run the
restart, which hits the live-turn guard and is **refused** while a turn is
running — so any turn longer than five seconds never restarted, the refusal went
to `/tmp/workshop-backend.log` where nothing surfaces it, and the command printed
success. Three "scheduled" restarts left a process with 12h uptime while a
shipped endpoint 404'd in production.

Fixed to poll for the guard to clear. **But mid-conversation the guard never
clears**, because a turn is always live — use `systemctl --user restart
workshop-backend.service` on a detached delay.

**Always verify a restart against the running process, never the script output:**

```
PID=$(systemctl --user show workshop-backend.service -p MainPID --value)
ps -o etime= -p "$PID"                                    # seconds, not hours
curl -s -o /dev/null -w '%{http_code}' localhost:8000/<route>   # 401 = up, 404 = old code
```

Related: router prefixes here are BARE (`/inbox`, `/tasks`). The edge strips
`/api` before requests reach the app. A router declared as `prefix="/api/inbox"`
registers a route nothing will ever request, and the route list looks correct
while every call 404s.

---

## 5. THE OPEN PROBLEM: background autonomy must not be switched on yet

This is the thing to design next, and the reason nothing was enabled.

### 5.1 What is actually off

Two switches, both set 2026-07-18, both still set:

- `.runtime/AUTONOMY_DISABLED` — empty flag file
- `.runtime/autonomy-override.json` — `{"engaged": true, "reason": "in-flight coherence migration (Phase A)"}`

`scripts/queue_runner.sh` is alive and polling every 60 seconds, logging
`autonomy disabled (flag or override present); idling` continuously since. The
pause was meant to last a migration and has lasted thirteen days.

**The control was broken, which is how it survived.** Autonomy is gated by
`AUTONOMY_FLAG.exists() or override.engaged` — two switches read as one truth —
but `DELETE /agents/autonomy/override` cleared only the JSON overlay. Anyone
lifting it from Settings would have seen "cleared" and still had it off, with
nothing on screen naming what was still holding it. **Fixed** (`8f37faf`): the
endpoint now clears both and reports which.

### 5.2 Why it stays off

*(Triaged 2026-08-02 — see 5.4 step 1. The queue is now 36. What follows is the
state that motivated the pause and is kept as written.)*

The queue holds **46 `todo` tickets**, and they are mostly July-18 design-phase
items: "Design the orchestration surface", "Produce the ordered build-ticket
set", "B7: shared-scope context", "B8: resilience", several "deep design pass"
entries. A large share are **superseded by work completed since**, including this
session's P1-P4, and some were arguably completed by the very documents that
describe them.

Turning autonomy on today means a worker sweep picks these up and executes them.
Consequences: usage-cap burn, commits against obsolete plans, and — worst —
tickets whose acceptance is "produce a design doc" being satisfied by producing
*another* design doc that contradicts the one already accepted.

### 5.3 Failure modes to design against before re-enabling

1. **Stale-ticket execution.** No ticket carries a notion of freshness or of
   "the plan this belonged to was superseded". A queue is not a plan, and a
   13-day-old design ticket is not the same object it was when filed.
2. **No supersession relation.** The entity model has `depends_on`, `blocks` and
   `relates_to`, but nothing expresses *"this replaces that"*. Without it the
   queue cannot know that P1-P4 obsoleted several of its own entries.
3. **Design tickets are not executable work.** A ticket whose output is a
   judgement ("decide X", "design Y") should never be handed to an autonomous
   maker. There is no field distinguishing them. `role` types the AGENT, not the
   ticket.
4. **No dry run.** There is no way to ask "what would the sweep do next, and in
   what order?" before letting it act. That should exist before the switch does.
5. **Blast radius is undeclared.** `touches` (path globs) is in the schema and
   enforced nowhere (entity model defect D3). An autonomous maker is currently
   bounded only by the git checkpoint.
6. **No pause-on-divergence.** If a run's output contradicts an accepted design
   doc, nothing notices. The reviewer gate checks completion, not coherence.
7. **The operator's own standing rule** (`chat-distilled`): *"Until the automated
   ticket system completes several tickets end-to-end reliably, default to manual
   work."* It has not done that yet. Re-enabling should follow a demonstrated
   end-to-end success, not precede it.

### 5.4 Suggested sequence when this is picked up

1. ~~**Triage the 46.**~~ **Done 2026-08-02.** The root queue is 46 → 36 open;
   across every scope, 79 → 65. Six cancelled (four project-template "First
   task" stubs; one duplicate — the by-chat usage view was filed twice 90
   minutes apart from two chat scopes, which dedupe missed because `provenance`
   is per-source; one superseded), eight closed as done against the accepted
   doc or the commit that delivered them. Every one carries an evidence note
   `triage 2026-08-02 -> <status>: <why>`, which is the raw material asked for
   here, and the two supersession pairs are joined with `relates_to` — the
   closest existing link, since nothing yet means "this replaces that" (5.3
   failure mode 2).

   **Second pass, same day: 65 → 40.** The first pass closed what a design doc
   or a commit message proved. The second closed what the *running system*
   proved, which turned out to be much more — 24 more tickets, and the lesson
   is that a queue drifts out of date silently in a way that only checking can
   surface:

   - **Seven of budget's eight "BLOCKED, needs operator apply" tickets were
     already applied.** They named exact files; every one of those edits is in
     the tree (`_payoff_view`, the `cadence`/`strategy` schema, the PLAID env
     strip). Nobody closed them. The eighth is genuinely open and stays open —
     it writes money config, which is the operator's action, not a triage pass's.
   - **Three security findings are no longer true.** `ss -ltn` says nothing
     listens on 6379 at all, `:9000` is loopback-bound, and `tailscale serve
     status` has no 8443 and nothing pointed at `:5173`. Three others were
     re-verified as still exposed and now carry today's evidence, including
     Letta, which was confirmed by probe rather than inspection: `GET
     /v1/agents/` returns 200 with no credentials.
   - **notes-app's 2026-07-12 scaffold checklist was all satisfied**, and the
     app moved in-process (`app_registry` → `routes/notes.py`), so "first run
     via the App panel" describes a path that no longer exists.
   - **Commute's "location is the weak link"** closed on its own data:
     journey coverage is 1.0 at 500-785 points where the ticket was filed
     against 8-10 points at 0.07.

   One ticket was cancelled for arguing against itself — it proposed a sort and
   noted in the same sentence that it was "currently a no-op".

   **Third pass, operator-directed: 40 → 21, to start fresh.** The first two
   passes closed what was *provably done*. This one archives what is no longer
   *worth doing*, on four tests — superseded design phase, premise no longer
   true, gated on something that never happened, or self-described as optional.
   Nineteen cancelled: the whole 2026-07-18 design residue (the settings-UI
   concept, the note-capture model, the three-ticket preference thread, B8, the
   agent-generalize ticket), the C1/C2/C4 autonomous-queue mechanics (autonomy
   is off by design until step 5, so dispatch machinery is work against an
   unproven loop), and the "consider / optional / if it feels off" tail across
   commute, location and notes-app. Two of those closed on live data: both
   OwnTracks tickets asked "is the track dense enough" and journeys now record
   coverage 1.0 at 500-785 points.

   **The twelve `[SEC-*]` findings were deliberately NOT archived.** They are
   not stale; they are unfixed, and three were re-verified as live exposures
   this same day. Clearing a queue by dropping security findings would be
   tidying, not triage.

   What survives is 21: twelve security findings, P5, the toolbar Design-System
   pass, the by-chat usage view, the one budget row that needs the operator, the
   three commute journey-learning items, the notes CodeMirror editor and the
   notes backup decision. Nothing cancelled is lost — the ticket text and a
   reason are kept on every one, so re-filing against the current plan is a
   copy-paste.
2. **Add a dry-run**: `queue_runner.sh --plan` printing the next N picks with
   reasons, changing nothing.
3. **Type the tickets**: at minimum executable vs judgement, so a maker can never
   claim the latter.
4. **Enforce `touches`** (D3) so blast radius is declared before it is used.
5. **Run one ticket end to end, watched.** Then a few. Then enable.

Do not skip to 5.

---

## 6. Immediate next actions

1. Watch whether turns now continue (§2). It is a behavioural fix and needs
   real long turns to judge.
2. Finish **P7**: Runs and Agents detail pages on the design system, bidirectional
   links with chats. This is the operator's most-repeated outstanding complaint.
3. **P5 proper**: raise `awaiting` from a run into an inbox `question`. The inbox
   already exists to receive it.
4. **P6**: the `role:` memory scope and read-union.
5. Only then: §5, the autonomy design work.

Do not enable background autonomy as part of any of the above.
