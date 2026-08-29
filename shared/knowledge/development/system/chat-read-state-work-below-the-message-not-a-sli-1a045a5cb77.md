<!-- author: self; chat: 95f7fd7fe8f6413dbcea1b7e80d47979 -->
The dim/solid state on a user message means "has the turn reached this". It is computed in `transcriptBlocks.ts`.

**The distinction that keeps getting lost** (three rounds now: 2026-08-16, 2026-08-18, 2026-08-27): where the working indicator RENDERS and what the turn has REACHED are different questions.

A turn that is still running must render its indicator somewhere, and that somewhere is always below the newest message. So there is ALWAYS a work slice below a message cut into a running turn. Using the indicator's slot as the read signal therefore marks every mid-turn message read on arrival.

- `workingPieceIndex` = where the indicator sits. Used for `working:`.
- `spokenThroughIndex` = last slice with actual content. Used for `inflight:`.

Do not merge them again.

**Second, separate rule:** `.chat-bubble--inflight` (0.55 opacity) absent means the confirm fade plays, which styles.css defines as "the server just confirmed this". So an optimistic send must be marked inflight while `status === "sending"` and only go solid on `sendAcked`. This regressed in 51aabbb6 (2026-08-25) when non-failed sends moved from `PendingBubble` (which set the class itself) to `MessageBubble` (which only dims what the block marks). Keep the block key unchanged when marking it, or the optimistic-to-committed handover remounts and jumps again.