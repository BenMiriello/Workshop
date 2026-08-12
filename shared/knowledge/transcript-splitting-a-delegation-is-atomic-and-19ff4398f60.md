<!-- author: self -->
Two rules for `frontend/src/components/chat/transcriptUnits.ts` + `transcriptBlocks.ts`, both learned by getting them wrong.

**1. Never divide a subagent chip across a mid-turn message.** A cut landing inside an `agent` segment snaps to the chip's END (`snapCut`), so the delegation stays whole in the slice where it was LAUNCHED and the message renders below it.

The old rule divided the chip and forced the earlier piece to `done:false`. Result: two rows for one delegation, both pulsing (`.chat-agent.is-running` keys on `!done`), the lower counting up and the upper frozen. Because the forcing was unconditional it also reached settled history, so an old interrupted reply carried a permanently pulsing chip for a finished agent.

Snap to the END, not the head: the chip is one collapsed fixed-height row while running, so the message below it does not drift as steps arrive. Snapping to the head would place the message above a delegation that was already running when it was sent, which is the placement error the whole file exists to prevent. Two messages sent during ONE delegation therefore land together after it, which is correct: there is no work between them to separate them by.

**2. How a turn ended goes on the last block that EXISTS, not the last slice.** An empty slice emits no block, so keying `tail` to `parts.length - 1` drops `error`/`stopped`/`timeout`/`receipts`/`status`/`ended_at` entirely. That is the common shape (message then Stop; message landing after the last output; and now any message landing inside the FINAL delegation, which snaps to the very end). Compute `lastPart` as the last non-empty slice.

Corollary: a split reply shows NO turn total. Every block carries the same `started_at` and only one carries `ended_at`, so a total paired with a per-block step count read '2 steps · 3h 41m'. Drop `started_at` on the split path.

**Also:** `is_error` must be forwarded by the SSE `tool_result` listener in `useTurnStream.ts` or every live step reads as succeeded; and nested subagent steps must be registered with an id + start in BOTH ingest paths (`build_streaming._segment_ingest` and `segments.ts applyStreamEvent`) or they can never close, and an absent outcome reads as succeeded.