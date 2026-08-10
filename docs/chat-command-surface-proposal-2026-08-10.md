# Slash commands, and what should replace them (2026-08-10)

A proposal, following the operator's read that "most slash commands don't make
sense anymore actually. At least not in our managed ui."

He is right, and the measurement is worse than he guessed: **most of them were
not working at all.**

## 1. What was measured

Every passthrough command in the catalogue, run against the real CLI in the mode
this chat actually uses (`claude -p --output-format stream-json`), 2026-08-10.
Not inferred from documentation.

| command | what the CLI actually returned | verdict |
|---|---|---|
| `/context` | a full markdown breakdown: tokens by category, MCP tools per server, custom agents, memory files, skills | **works, and is rich** |
| `/init` | runs; generates a CLAUDE.md | works (it is a task, not a TUI command) |
| `/review` | runs; reviews a PR | works, same reason |
| `/cost` | "You are currently using your subscription to power your Claude Code usage" | runs, says nothing |
| `/usage` | same sentence | runs, says nothing |
| `/model` | `/model isn't available in this environment.` | **dead** |
| `/mcp` | same | **dead** |
| `/agents` | same | **dead** |
| `/memory` | same | **dead** |
| `/status` | same | **dead** |
| `/doctor`, `/export`, `/resume` | same | **dead** (were never listed, but passed through) |

Five of the nine commands the app was *offering in its own autocomplete* could
not run, and each cost a whole turn — a spawned process and a full prompt — to
print one sentence saying so.

**This included a regression I shipped an hour earlier.** `dc603a8` moved
`/model` out of the build-only gate, reasoning that every chat has a session now.
The premise was correct and the conclusion was wrong: `/model` does not work in
*any* chat. Measuring before changing would have caught it. Already corrected in
`878edde`, along with removing the other four and answering a known-dead command
locally instead of spending a turn on it.

## 2. The decisive measurement: `/context` is free

| | |
|---|---|
| cost | **$0.00** |
| API calls | **0** (`num_turns: 0`, `duration_api_ms: 0`) |
| wall time | ~2.8s |
| per-thread | `--resume <session>` reports THAT thread's real usage |

Verified against a real thread: the CLI reported `43k` and the thread's stored
`context_tokens` is `43004`.

It is computed locally by the CLI. **It involves no model call whatsoever.** That
removes the only real objection to putting it behind a button: a panel can
refresh it as often as the operator likes.

### One correction it needs

That probe reported `43k / 200k (22%)`. Our threads run a 1M window (the
operator's own thread has measured 440k, which a 200k window cannot hold), so the
CLI's *percentages* are computed against a different denominator than ours.

**Use the CLI's per-category token counts, which are real. Compute every
percentage ourselves, against our own window.** Rendering its "Free space" and
"Autocompact buffer" percentages directly would put a second, disagreeing context
figure on screen a few pixels from the meter — exactly the class of bug this
month has been spent removing.

## 3. Proposal: the command surface

### Keep as slash commands (3)

| command | why it earns its place |
|---|---|
| `/compact` | the operator's own instinct, and correct: it is a verb, it is used mid-conversation, and typing it is faster than reaching for a control. Already local and already correct (it folds both halves of the context). |
| `/clear` | same shape as compact, and the pair should not be split up |
| `/new` | a verb, used constantly, and the fastest possible path to a blank chat |

The rule that picks these: **a slash command should be a VERB that acts on this
conversation, right now.** Everything else is a noun — a view, a setting, a list
— and a noun belongs to a control that is visible whether or not you know its
name.

That rule also explains why the operator's list feels wrong to him. `/model` is a
noun (a setting, already a picker). `/mcp`, `/agents`, `/memory`, `/status` are
nouns (lists). `/delete` is a verb but a *destructive* one, and a destructive verb
reachable by typing four characters and pressing enter is the one place a
confirm-by-typing is worse than a button.

### Remove (already done, `878edde`)

`/model /mcp /agents /memory /status /doctor /export /resume` — measured dead.
Typing one now gets an instant local line pointing at the control that does the
job ("Use the model picker in the toolbar"), costing nothing.

### Remove (proposed, needs your nod)

| command | why | where it goes |
|---|---|---|
| `/delete` | destructive, and duplicated by a button that already confirms | the existing delete control |
| `/expand` | a noun, and there is already a pop-out button next to it | the existing button |
| `/tasks`, `/inbox` | toggles for drawers that have visible buttons | the existing buttons |
| `/deploy` | a navigation shortcut, not a chat action | the Deploys page |
| `/help` | its whole job was listing the commands; with three left, the picker IS the help | the `/` picker |
| `/cost` | runs, but returns a sentence with no numbers in it, which is worse than nothing because it looks like an answer | the Usage page, which has the real figures |

### Keep, build-only, unchanged

`/init` and `/review`. Both genuinely run, both are tasks rather than TUI
commands, and neither has a UI worth building yet.

### What that leaves

`/compact`, `/clear`, `/new`, plus `/context` if you want to keep a typed route
to the panel below, plus `/init` and `/review` in a build chat.

**I do not recommend dropping the slash surface entirely.** Three verbs and a
picker is a small, discoverable, honest surface, and `/` is a real accelerator
for the two commands used mid-conversation. What was wrong was never that slash
commands exist; it was that the list was a menu of things that mostly did not
work.

## 4. Proposal: the context panel

The operator's idea, and it is the right one: **a drop-up from the context
percentage ring.**

The ring is already the only thing on screen that talks about context, it already
carries the number, and "click the number to see what makes it up" needs no
explanation. It replaces `/context` with something better than the command was:
the command dumps ~60 lines of markdown table into the transcript, permanently,
where it scrolls away and is never read again.

### What it shows

```
  ┌─ Context ────────────────────────────────┐
  │  440k of 1M used            44%          │   our window, our number
  │  ▇▇▇▇▇▇▇▇▇▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁          │   one stacked bar
  │                                          │
  │  Conversation      2.2k                  │   ← the part you control
  │  System prompt     8.7k                  │
  │  Tools            13.1k                  │
  │  MCP tools         6.6k    4 servers  ›  │   ← expands
  │  Memory files      5.0k    3 files    ›  │
  │  Skills            1.3k   14 skills   ›  │
  │  Subagents         241                   │
  │                                          │
  │  Compact now          Refreshed 2m ago ⟳ │
  └──────────────────────────────────────────┘
```

Design notes, against `frontend/CLAUDE.md`:

- Built from `src/ui` primitives; the stacked bar is a `ChartFrame`-free plain
  element (a bar of divs with `--cat-*` hues, since these are CATEGORIES, not
  statuses — rule 4). No status colors: a full context is not an error.
- The rows are a `Table`, not a bespoke grid.
- Four states (rule 9): loading skeleton, the normal case, an error `StateBlock`
  naming the endpoint, and the "this thread has no session yet" empty case.
- `Compact now` is the one action, and it is the existing `/compact` — the panel
  is where you would look when the number worries you, so the fix belongs there.

### Where the data comes from

A new endpoint that runs the free local command and parses it:

```
GET /api/chat/threads/{id}/context/detail
  → { model, window_tokens, used_tokens, categories[], mcp[], memory[], skills[], agents[] }
```

It runs `claude -p --resume <session> "/context"` out of band. Three properties
make this safe, and all three were verified above:

1. **It costs nothing** and makes no API call.
2. **It does not touch the transcript.** This is a side query, NOT a turn. It
   must not claim the thread, must not write a run event, and must not append a
   message. (`accepts_input`, added today, is the machinery that keeps a real
   turn and a side query from colliding.)
3. **It is per-thread**, via `--resume`.

Cache the parsed result per thread with the timestamp shown in the panel, and
refresh on open plus on explicit `⟳`. The panel should never block the meter:
the ring keeps rendering its own number whether or not the detail loads.

## 5. Proposal: MCP

The operator asked about showing MCP usage in the panel and managing it there.
Two halves, and they are very different in difficulty:

**Showing it: easy, and already justified.** `/context` returns every MCP tool,
its server, and its token cost. On the measured session that is **6.6k tokens
across 4 servers** — a real, ongoing tax on every single turn, currently
invisible. Expanding the "MCP tools" row to a per-server breakdown is the single
most useful thing in the panel, because it is the one line the operator can
actually act on.

**Managing it: real work, and it should NOT be a slash command.** The CLI's
`/mcp` is dead here, so there is nothing to pass through even if we wanted to.
But we already hold the truth server-side: `app_registry.installed_mcp_servers()`
merges the platform servers with each installed app's, and that is what is
actually launched. So management is ours to build, not the CLI's to lend:

- a Settings → MCP section listing installed servers, where each comes from
  (platform vs which app), its tools, and its token cost per turn
- enable/disable per server, honouring the Edit-mode rule (rule 7) since this is
  config
- the context panel links INTO that section rather than duplicating it

I would do the read-only breakdown in the panel first, see whether the number
actually bothers you once it is visible, and only then build the management
screen. A 6.6k tax may or may not be worth a settings page; you cannot tell until
you can see it.

## 6. Accepting commands you have removed

Worth keeping, and cheap: a removed command should answer, not fail. `878edde`
already does this for the dead ones. The same treatment suits the ones removed by
choice — typing `/model` says "Use the model picker in the toolbar", typing
`/mcp` will say "Settings → MCP".

This is the discoverability argument in reverse: the slash surface stops being a
menu of features and becomes a **map to the controls**, which is exactly what
someone typing a command they remember from the CLI needs.

## 7. What needs your decision

1. **The removals in §3** ("Remove, proposed"): `/delete`, `/expand`, `/tasks`,
   `/inbox`, `/deploy`, `/help`, `/cost`. All are duplicated by visible controls.
2. **Whether to build the context panel** as specced in §4. Free to run, and it
   retires the `/context` transcript dump.
3. **How far to take MCP** (§5): breakdown only, or breakdown plus a management
   screen.
4. **The autonomy brake** — see below; separate question, one-line answer.
