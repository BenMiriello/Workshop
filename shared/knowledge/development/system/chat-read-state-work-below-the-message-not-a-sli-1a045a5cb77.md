<!-- author: self; chat: 95f7fd7fe8f6413dbcea1b7e80d47979 -->
**CORRECTED 2026-08-29: the read-state rule this note defended is DELETED. Do not restore it.**

This note used to explain how to keep the dim/solid "has the turn reached this" state on a user message working, and ended "do not merge them again". The right conclusion from that history was the opposite one.

`spokenThroughIndex` and the `inflight` read-state it fed are gone from `transcriptBlocks.ts` (commit efb38e6a). A message the operator sends renders solid immediately and never dims.

**Why, and it is the part worth keeping.** The rule was added, removed, and re-added across 2026-08-16, 08-18 (twice) and 08-27, each time in response to a real complaint, each time correctly fixing the previous version's failure and introducing the next one. On 2026-08-29 the operator reported it a fourth time: "i just sent that last message and it still shows unread even though it shows ABOVE your working message."

Four rounds on one rule is evidence the rule is wrong, not that its tuning is. And the rule was unusual to begin with: no ordinary chat application models per-message read state against the party that is replying. Whether the assistant has "reached" a message is not a thing the reader can act on, and every attempt to display it was either wrong or, at best, a distinction the operator did not want drawn.

**What is still true, and is a SEPARATE rule.** `.chat-bubble--inflight` (0.55 opacity) still exists and still marks a genuine client-side state: an optimistic send this browser has made that the server has not acknowledged. That one is real, is not read-state, and stays. An optimistic send must carry it while `status === "sending"` and go solid on `sendAcked`; absent, the confirm fade plays, which `styles.css` defines as "the server just confirmed this". This regressed in 51aabbb6 (2026-08-25) when non-failed sends moved from `PendingBubble` (which set the class itself) to `MessageBubble` (which only dims what the block marks). Keep the block key unchanged when marking it, or the optimistic-to-committed handover remounts and jumps.

The general lesson, which outlives this particular control: when a rule has been reverted three or more times, stop tuning it and ask whether it should exist.
