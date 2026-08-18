# A finished turn must commit even when its process outlives the result

2026-08-17. Status: implemented in this pass.

## The symptom

A chat thread shows "Working" with an ever-climbing timer and no content, for
over an hour, across at least one backend restart. The operator comes back to a
turn that says it is still working but shows nothing, and a restart does not
clear it.

Observed live on thread `70a3bfc156e341b6949bf6f944ac6888` ("Platform Ontology
Review"), turn `f171a47b`: the turn's reply was fully written (11.4 KB, 198
segments) and sitting in the draft, but `active_turn_id` was never cleared, so
the chat rendered a permanent in-flight state over a reply that was done.

## The mechanism

Chat turns run as a detached `claude -p --input-format stream-json` process that
streams to a disk journal (`.runtime/turns/<id>/stream.jsonl`). The process does
NOT exit when it finishes a prompt: stdin stays open, so it idles waiting for the
next message. The driver retires it explicitly once it sees the turn's `result`
line.

On a backend restart, `reattach_running_turns()` adopts any journal still marked
`running` and re-enters `_drive_turn`, which RESUMES the journal at the byte
offset the draft was last persisted at (`_resume_from`) rather than replaying it
from zero. Resuming is correct: replaying rebuilds the reply from scratch and
was itself a source of "the conversation rewound mid-reply" bugs.

But resume has a hole. If the turn had already written its terminal `result`
before the restart, the resume offset is AT or PAST that result line. The tail
loop then reads no new bytes (the process is idle), so `saw_result` never
becomes true. And because the process is still alive (open stdin) and a chat
turn has no wall clock by design, none of the loop's exit conditions ever fire:

- `turn_is_over(...)` is gated on `state["saw_result"]`, which is false.
- the "process died" branch never runs, because the process is alive.
- there is no deadline (`timeout_s == 0`).

So the loop spins at 0.15s forever, re-persisting an unchanging draft, never
committing. The reply exists; nothing ever delivers it. A restart does not help
because the restart's own reattach re-enters the same hole.

This is strictly a reattach phenomenon. A freshly spawned turn (`proc != None`)
always ingests its `result` live, so `saw_result` is set the normal way and the
existing logic retires it correctly. The hole is only reachable by resuming a
journal that already contains the result.

## The fix

The journal already records the result. Recovery must not depend on the live
process emitting a NEW result it will never send; it must consult what is on
disk.

In `_drive_turn`, on the reattach path only (`proc is None`), after computing the
resume offset, check whether the journal's last event is a terminal `result` and
we have resumed to (or past) the end of the file. If so, the turn finished before
the restart: seed `state` from the recorded result envelope (subtype, spend,
context tokens) and let the loop's first pass retire the idle process and finalize
from the restored draft. `commit_turn` then appends the reply and clears
`active_turn_id`, exactly as a live finish would have.

Guard conditions, so this cannot end a turn that is genuinely working:

- Scoped to `proc is None`. A live spawn is untouched.
- Requires the resume offset to be at/beyond end-of-file. If unread bytes remain,
  the ordinary drain reads them (including a result written after reattach) and
  sets `saw_result` the normal way; no seeding happens.
- Requires the LAST journal event to be a `result`. A turn mid-answer on an
  injected second prompt has non-result output after its first result, so its
  tail is not a result and the check does not fire. On the reattach path there
  are no in-memory injections anyway (`delivered == 0`, `unread == []`).

## Why no separate reconcile loop

The spin state can only be entered at reattach (resume-past-result). Once the
new check is in place, reattach finalizes such a turn on its first loop pass, so
the state cannot persist past a restart. In steady state (`proc != None`) the
result is always ingested live, so a running backend cannot newly wedge a turn
this way. A periodic sweep would be guarding a state that can no longer arise,
which is the kind of defensive machinery this system deliberately does not add.
Startup already has `reconcile_threads()` for threads whose turn no process
drives.

## How the operator learns of the recovery

The events endpoint (`GET /threads/{id}/events`) is the single sync path and the
per-turn SSE stream tails the same journal. The instant the backend commits and
clears `active_turn_id`, the SSE closes and the next poll returns the committed
message with no active turn. Nothing on the client needs to change: it already
learns of the new status immediately once the server produces it. The defect was
that the server never produced it.

## Recovering the turn that is already stuck

Applying this change restarts the backend. On that restart, reattach re-enters
`_drive_turn` for `f171a47b` with the new check, detects the terminal journal,
and commits the reply. So shipping the fix also clears the stuck thread.
