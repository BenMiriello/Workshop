# The full fix set: what the system claims vs what it does (2026-08-10)

Four parallel audits: agent/subagent orchestration, the ticket-to-run loop, chat failure
modes, and the orchestration UI. This is the merged plan.

## Why the first review missed all of this

The 2026-08-09 audit asked "is this architecture sound?" and got a good answer. It never
asked "does this control do what it claims?" Structural soundness and honesty are
orthogonal: the Stop button was structurally clean and completely fake. Every review of
this system from now on runs four axes, not one:

1. **Structure** — ownership, races, state, ordering.
2. **Claims vs reality** — trace every control the operator touches to its actual effect.
3. **Failure modes** — what happens when it breaks, and does the operator learn?
4. **Guarantees** — is a stated safety property enforced, or is it persuasion?

## W1 — the automated loop cannot complete anything

This is the operator's oldest complaint and it has a single dominant cause.

1. **Chat spends the workers' budget.** `usage_ledger.py:149-158` sums the admission
   window with no role filter, and `role:"chat"` rows land in the same ledger. Live: 19.7M
   tokens/5h against a 3.25M autonomous ceiling. `admit` refuses every sweep. Chat turns
   are already declared unmetered and never gated, so they must be excluded from the
   window.
2. **A refused sweep reports success.** `project_ralph.sh:740-746` breaks after claiming,
   then `:1116` closes the Job `done` with 0 tasks. A sweep that never ran is not done.
   It must close as blocked, carrying the reason.
3. **Nothing reaches the chat when a sweep stalls before claiming.**
   `run_report.post_for_job` matches `job_id in task["job_ids"]`, but only the CHILD job id
   is ever written there. The parent Job the chat card holds is never matched, so every
   pre-claim failure posts zero messages. Fall back to the Job's own `task_id`.
4. **`launch_task_run` records no pid** (`tasks_all.py:851`), so `reconcile_stuck` degrades
   to a 2-hour ceiling; and `liveness_watchdog.sh` is not in crontab, so it never runs at
   all. Live evidence: 5 jobs `running` with `pid=None` since 2026-08-05. Pass the pid,
   and give reconcile an in-process caller.
5. **The chat path skips the readiness gate** that `run_task` applies, so a BROKEN ticket
   launches from a card.
6. **The refusal string is wrong**: "autonomous budget 65% used" prints the reserved
   fraction, not the usage.
7. **The autonomy brake has been engaged since 2026-07-18** on a stale reason ("in-flight
   coherence migration"). Releasing it is the operator's call, not a fix to apply
   silently, but the UI must stop hiding it.
8. **A ticket filed to `project:<slug>` is picked up by nothing.** One runner covers the
   workshop root; zero projects carry a `loop` trigger. Say so in the UI rather than
   letting it queue forever.

## W2 — chat fails silently

9. **A crash in the drive loop wedges the thread forever.** `_spawn_and_drive` is a bare
   `create_task` with no exception handler and `_drive_turn` has no `try/finally`, so any
   raise skips finalize: `active_run_id` never clears, no wall clock exists
   (`CHAT_TIMEOUT_S = 0`), and the chat shows a ticking clock indefinitely.
10. **A prompt that fails to deliver is a log line.** `build_streaming.py:888` warns and
    proceeds; the CLI idles with no prompt and never ends. Same symptom as 9.
11. **Two tabs on one thread destroy each other's queue.** `set()` writes this tab's whole
    in-memory list over storage with no merge.
12. **A failed send is unrecoverable after a reload.** `pending` lives only in the reducer
    and the draft was already cleared. Put failed text back on the durable queue.
13. **A failed thread delete says nothing.**
14. **Re-attaching renders the turn twice.** A fresh `EventSource` sends no `Last-Event-ID`,
    so the server replays from zero and `applyStreamEvent` is not idempotent. Clear the
    live segments before attaching.
15. **Unbounded growth**: `TURNS` never shrinks; `log.jsonl` is parsed twice per poll and
    never truncated.
16. **Error surfacing is one slot** above the composer, overwritten by the next error,
    cleared on send, often a raw exception, and never placed under the message that caused
    it, which contradicts the log-placement rule.

## W3 — guarantees that are only persuasion

The operator has said explicitly he does not want scope/touches/latitude represented as a
security boundary if they are not one. Each item below is either wired for real or the
claim is removed. No middle.

17. **The irreversible-action denylist never applies to unattended runs.**
    `disallowed_for()` is called only from the @mention path, where `is_unattended()` is
    False and the list is empty. The genuinely unattended sweep invokes `claude -p` with no
    tool restrictions at all.
18. **Role tool narrowing never reaches sweeps** either, so a cron keeper or triager runs
    with the full toolset while the UI says it cannot write.
19. **Declared-and-ignored agent fields**: `tools`, `model`, `capabilities`, `limits`,
    `depth`, `fanout`. `capabilities` is the worst: it is rendered to the agent under a
    header reading "this is the enforced truth" while `mcp_servers=[]` is hardcoded.
20. **`touches` is detective, not preventive** (checked after the commit) and `latitude` is
    prompt text. Both are worth keeping; neither may be described as a boundary.
21. **A stopped turn leaves its subagent chip reading "working" forever.**

## W4 — UI that reports the wrong thing

22. **`AutonomyControls` says "all autonomy: running" from the override flag alone**, while
    the row directly beneath it correctly says nothing is running. Same card, same render,
    contradicting. This is the exact bug `QueueRunner` was written to kill.
23. **`RunDetail` never polls**: a running run reads "running" forever.
24. **`Work.tsx`'s `EditTicket` drawer is unreachable** (~280 lines), taking with it the
    only 409-aware run handler and the only `status === "todo"` run gate.
25. **`TicketDetail`'s header Deploy button is inert** unless a target was already chosen
    elsewhere on the page; Run is offered on `done`/`cancelled` tickets; a 409 renders as
    raw JSON; the status pill does not move after a successful launch.
26. **`?tab=` is written by five navigations and read by nobody**, so "Back to runs" does
    not go to runs. `?scope=` applies only on mount.
27. **Buttons labelled "Open ticket" and "task" navigate to filtered lists** with the
    `task_id` available and unused.
28. **`addTasksAndRun` skips the `scope_is_runnable` check** that the drafts dialog
    performs, producing exactly the after-the-fact failure footnote that predicate exists
    to prevent.

## Order

W1 first: it is the difference between a system that can work and one that cannot. W2
next, because silent failure is the operator's stated core complaint. W3 and W4 after,
and they are largely deletions of claims rather than new machinery.

Not in scope, flagged for a decision: releasing the autonomy brake, and giving voice turns
a real run object.
