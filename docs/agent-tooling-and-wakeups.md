# Coming back later: what an agent has for waiting, watching and being woken

- Date: 2026-08-22
- For: every chat and every agent in this system. Read the table, not the whole file.
- Why it exists: agents here repeatedly either block a turn on a slow thing, or
  promise to "report back when it's done" and then end the turn, which kills the
  work and delivers nothing. Both are avoidable with tools that already exist.

---

## 1. The rule that makes the rest make sense

**A turn ends the moment you emit text with no tool call.** Nothing resumes on
its own. So there are exactly two ways for work to reach the operator after that:

1. Something the HARNESS tracks finishes, and the harness re-invokes you. You do
   not poll and you must not sleep waiting for it.
2. Something OUTSIDE the turn owns the work: a Ticket, a Workshop agent/run, or
   a background agent that posts back into the chat.

Everything below is one of those two.

## 2. Pick by what you are waiting for

| You want | Use | Notifications |
| --- | --- | --- |
| One "tell me when this finishes" | **Bash `run_in_background: true`** | one, on exit |
| One "tell me when a condition becomes true" | **Bash `run_in_background`** with an `until` loop that exits | one, on exit |
| One per occurrence, indefinitely | **Monitor** (`tail -f`, `inotifywait -m`, poll loop) | one per stdout line |
| One per occurrence, until a known end | **Monitor** with a command that exits | one per line, then done |
| A prompt at a wall-clock time, once | **CronCreate** `recurring: false` | fires once, auto-deletes |
| A prompt on a schedule | **CronCreate** `recurring: true` | until deleted; auto-expires in 7 days |
| Work that should run while you keep talking | **`mcp__workshop__spawn_background`** | result posts back into THIS chat |
| Work that must survive the session | **a Ticket** (or a Workshop agent) | the ticket is the record |
| Pull the operator's attention to the session | **PushNotification** | one desktop/phone push |
| Read or kill something you started | **TaskOutput**, **TaskStop** | n/a |
| Ask another live session/agent something | **ListAgents** then **SendMessage** | replies arrive automatically |
| Be told when another local session goes idle | **SendMessage** `notify_when_idle: true` | one, one-shot |

`ScheduleWakeup` is NOT general-purpose: it only paces `/loop` dynamic mode.

## 3. The traps, all of which have bitten something here

- **Do not poll for harness-tracked work.** A background bash task re-invokes you
  when it exits. A `sleep` loop or a short-interval wakeup to "check on it" is
  pure waste, and foreground `sleep` is blocked outright.
- **An unbounded Monitor is wrong for a single notification.** `tail -f`,
  `inotifywait -m` and `while true` never exit, so the watch stays armed long
  after the event. Use background bash with `until` for one-shot.
- **Silence is not success.** A Monitor filter that matches only the happy path
  stays quiet through a crash, and quiet looks exactly like "still running".
  Match the failure signatures too (`Traceback|FAILED|Killed|OOM`).
- **Every pipe stage must flush per line** or matches sit in a buffer unseen:
  `grep --line-buffered`, `awk` with `fflush()`. `head` cannot flush at all.
- **CronCreate is SESSION-ONLY.** It is in-memory, it dies with the session, and
  recurring jobs expire after 7 days. For anything that must genuinely outlive
  the session, the durable scheduler is the Workshop's own: the resident queue
  runner (`scripts/queue_runner.sh`, reconciled by `services/work_driver.py`),
  `scripts/dispatch_triggers.sh`, and the crontab. A chat turn cannot set a
  durable timer, and pretending otherwise is how a promise gets dropped.
- **Do not `nohup ... &` INSIDE a `run_in_background` call.** The harness tracks
  the command it launched; detaching the real work makes that command exit
  immediately, so you get a completion notification within a second, the log
  file stays empty, and the actual job is an orphan nobody is watching. Hit
  while writing this file. Pass the loop itself as the command and let
  `run_in_background` do the backgrounding.
- **In THIS chat surface a foreground subagent dies at turn end.** A hook forces
  `run_in_background: false` on Agent/Task for exactly that reason, so a
  subagent is context isolation for the CURRENT reply and nothing more. For
  out-of-band work use `spawn_background`; for durable work use a Ticket.

## 4. How to know these exist without carrying them in context

**Deferred tools plus `ToolSearch`.** Most of the tools above are not in the
prompt as schemas: only their NAMES appear, in a system reminder. Until fetched
there is no parameter schema, so they cannot be called. `ToolSearch` fetches the
full definition on demand and it is then callable exactly like a built-in:

    ToolSearch({query: "select:Monitor,CronCreate,PushNotification", max_results: 3})
    ToolSearch({query: "notify wake up timer", max_results: 5})

`select:` takes exact names. A bare keyword query ranks matches. Fetch the two or
three you actually need at the moment you need them; do not fetch the set "just
in case", which is the context cost this design exists to avoid.

So the discipline is: **know the table in section 2, look up the schema when you
reach for one.** That is the whole mechanism. This file is the table.

## 5. Worked example, from the session that wrote this

Building the branch-isolation work needed a bundle build (slow, one outcome) and
an autonomous loop that might pick up a ticket at any time (unknown count, no
known end). Both, correctly:

    # one outcome, one notification, no polling. No `nohup` and no trailing
    # `&`: run_in_background is already the backgrounding, and adding a second
    # layer makes the tracked command exit at once.
    Bash({command: "bash scripts/apply.sh > /tmp/apply.log 2>&1",
          run_in_background: true})

    # wait for a condition, then act, then exit: still ONE notification
    Bash({command: "for i in $(seq 1 100); do " +
                   "  [ -z \"$(git status --porcelain -- frontend)\" ] && " +
                   "    { bash scripts/apply.sh; exit 0; }; sleep 15; done; exit 1",
          run_in_background: true})

    # per-occurrence, quiet until something real happens, failure included
    Monitor({command: "tail -n0 -F .runtime/queue-runner.log | grep -E --line-buffered " +
                      "'provisioned worktree|change=[0-9a-f]+ opened|" +
                      "task=[0-9a-f]+ (IN_REVIEW|DONE|PARKED|BLOCKED)|Traceback'",
             description: "queue runner: a ticket worked, landed, parked or failing",
             persistent: true, timeout_ms: 3600000})

The build notification arrived on its own and the turn continued. No sleep, no
poll, and no "I'll report back when it's done" written into a reply that was
about to end.
