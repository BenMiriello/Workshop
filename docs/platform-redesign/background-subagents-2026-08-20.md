# Background subagents in chat: what they are, and why the solution is shared across `claude -p` and the API

Status: design. Author turn: development chat, 2026-08-20.
Supersedes nothing. Extends the Run/inbox model, does not fork it.

## The question this answers

The operator asked: we know production will drive the model through the Anthropic
API (raw Messages) for regular users, while the developer keeps using `claude -p`
(the Claude Code CLI, headless) in the development environment. So if we make
background agents work for `claude -p`, is that a throwaway that has nothing to
do with the API chat, or is the solution shared?

Answer, up front: **the solution is almost entirely shared, and it is not a
throwaway.** The one part that is `claude -p`-specific is a part we should NOT
use. Everything of value lives at our own orchestration layer, which does not
care how the model is driven.

## What "foreground" and "background" actually mean

The Agent/Task tool launches a subagent: a second model context with its own
window that runs some bounded work and returns a summary. Foreground vs
background is one distinction only: does the parent turn WAIT for it.

- Foreground: the parent turn blocks on the call. The subagent runs, finishes,
  and its summary comes back as the tool result inside the same turn. One reply,
  paused while the child works, then continued.
- Background: the call returns immediately with a task id. The child keeps
  running in its own process. The parent is free to do other things, and when
  the child finishes the result is delivered back and the model is re-invoked to
  fold it in. The result arrives in a LATER turn.

In the three registers the operator asked about:
- The agent: foreground = parent idle-blocked; background = parent and child run
  concurrently and the parent is pinged later.
- The chat/turn: foreground = all inside one turn; background = the result lands
  in a new turn triggered by completion.
- The visual: in an interactive CLI, a background task shows as a live "running"
  chip you can watch, and its result pops in later while you keep typing.

Note one thing that is easy to get wrong: foreground does not cost parallelism.
Several Agent calls in one message run concurrently and the parent blocks until
all return. What background uniquely buys is the parent doing UNRELATED work
(including talking to the operator) while children run, and results crossing
turn boundaries.

## Why the naive `claude -p` version broke (and why we disabled it)

A chat turn here is a one-shot `claude -p` process (`build_streaming.start_turn`
-> `_spawn_and_drive` at build_streaming.py:1764/2215). It exits the moment it
stops emitting tool calls. The CLI's OWN background-subagent feature (the
`run_in_background` flag on the Agent tool) needs the CLI session to stay
resident to receive its `task_notification`; our turn is gone by then, so the
result is dropped. On top of that the backend restarts often (every concurrent
turn runs apply.sh), which would kill an in-flight child regardless.

`services/subagent_guard.py` now forces chat-turn subagents to the foreground
(fail closed unless `run_in_background: false`). That is correct as the floor:
the CLI-native background feature cannot work in a one-shot turn.

The important point: **that CLI-native feature is the throwaway.** It only
exists in `claude -p`, it does not exist on the API at all (on the API we own
the loop and there is no built-in subagent), and it depends on a resident
session we deliberately do not run. We should build background somewhere else.

## The two model drivers, and what is shared

`claude -p` (developer / development env): the CLI drives the whole agent loop,
tools, subagents, hooks, context. Model work inside a Run is already `claude -p`
today (`scripts/ralph.sh` wraps it).

Claude API (production / regular users): we own the agent loop. A "subagent" is
a nested series of API calls with a fresh context returning a summary.
"Background" is us running that nested loop as an async task and posting the
result back. Nothing is built in; it is all ours.

Now the layers, and which are shared:

1. A durable, out-of-turn worker that survives a backend restart. SHARED. This
   is exactly the Run/Job system (`services/jobs.py`): an append-only
   `.runtime/jobs.jsonl`, a detached process (`start_new_session=True`), pid
   liveness reconcile running inside the backend (`jobs.reconcile_loop`,
   main.py:59). Model-driver-agnostic: a Job wraps whatever worker you give it.
2. Completion posts the result back into the originating thread. SHARED, and
   ALREADY BUILT. `jobs.finish()` -> `run_report.post_for_job()` appends an
   assistant message into the thread (`chat_store.append_message`,
   run_report.py:297) keyed by the originating chat, and does not corrupt a live
   turn. Driver-agnostic.
3. An async surface that shows pending/done without the operator watching.
   SHARED, ALREADY BUILT. The inbox (`services/inbox.py`,
   `inbox.sweep_finished_runs()` already raises a notify for finished unwatched
   runs) rendered in the Work panel (`WorkPanel.tsx`). Driver-agnostic.
4. (Only for "fire and continue") a seam by which backend code starts a fresh
   assistant turn on a thread when a result lands. SHARED but NOT YET BUILT.
   Today backend code can inject into a RUNNING turn (`write_to_turn`) or drain
   already-queued operator messages (`chat_drain.drain`); nothing starts a new
   turn from nothing. Whatever we build here is driver-agnostic: it decides
   "produce a new turn seeded with this result," and the driver underneath is
   `claude -p --resume` or a fresh API call.

So: layers 1-3 exist and are shared; layer 4 is the one genuinely new seam and
is also shared. The `claude -p`-native subagent-background feature is the only
throwaway, and we are already not using it.

Conclusion for the operator's question: **build background once, at our
orchestration layer, and both the `claude -p` dev chat and the API production
chat use it identically.** Solving it is not `claude -p` work that gets thrown
away; it is platform work that both paths inherit.

## Two levels of "background," because they are different amounts of work

Level 1, fire-and-surface: launch a Run from a chat with a free-form prompt (no
ticket ceremony), let the operator keep chatting, and when it finishes its
summary appears in the thread as an autonomous-tier message plus an inbox notify.
The model reads it on the operator's next turn. This is ~90% existing wiring:
Job durability (1) + run_report post-back (2) + inbox (3). Missing pieces are
small: a free-form launcher alongside `launch_task_run` (today the launcher is
hardwired to `project_ralph.sh` on a ticket), and a `chat_id`-direct delivery
path in `run_report.post_for_job` for a Run that has no worked ticket
(today delivery is task-mediated; a ticketless job posts nothing).

Note Level 1 is close to what "File and run" already does. The real new
affordances it adds are: no ticket, and a prompt the chat model itself issues as
a tool call rather than the operator clicking a card.

Level 2, fire-and-continue: when the Run completes, automatically start a fresh
assistant turn on the thread seeded with the result, so the conversation picks
up where it left off without the operator prompting. This is the faithful
reproduction of the interactive-CLI experience (there, the `task_notification`
re-invokes the model unprompted). It needs layer 4, the new turn-start seam, and
that seam touches the restart-sensitive turn driver, so it is the careful part.

## Recommended build sequence

1. Level 1 first. Low risk, self-contained, reuses jobs.py + run_report + inbox.
   Deliverables: a free-form chat-initiated Run launcher; a `chat_id`-direct
   delivery branch in `run_report.post_for_job`; the model gets a tool (in the
   workshop MCP surface) to spawn one; a test that a ticketless chat-spawned Run
   posts its summary back into the right thread and raises a notify. No change to
   the turn driver.
2. Level 2 second, as its own increment, once Level 1 is proven. Add the
   backend-initiated turn-start seam (a sibling to `chat_drain.drain` that
   synthesizes a system-seeded prompt rather than carrying a queued operator
   send), guarded by the same idle/lock checks, and verified against a real
   restart. This is where the "keep going on its own" behavior lives.
3. The API path inherits both for free at the orchestration layer; only the
   worker inside the Job and the turn driver in layer 4 differ by driver, and
   both are already abstracted behind the Job record and the turn meta.

## What stays true regardless

Even in the interactive CLI, background ultimately means the result comes back
into the session and the model is re-invoked to handle it. We reproduce that with
post-back (built) plus, for Level 2, the turn-start seam. We are not missing a
capability by declining the CLI-native version; we are building the same
capability at a layer that works for every user and survives our restarts.
