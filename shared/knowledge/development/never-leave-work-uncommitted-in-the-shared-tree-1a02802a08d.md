<!-- author: self; chat: 2c44eaf71ef14a3f83c96d119787ba9f -->
Measured 2026-08-22. While the resident queue runner was sweeping, a worker reset the shared working tree and silently discarded uncommitted edits (a half-finished reviewer.sh wiring had to be redone from scratch). Untracked NEW files survived; modifications to tracked files did not.

This is the same hazard that caused the chat-storage stage-2 revert (cac00c7), now confirmed live rather than theoretically.

**How to work:** on the shared tree, make an edit and commit it in the same step. Do not accumulate a multi-file change in the working tree and commit at the end. Where the change is genuinely multi-step, use a branch.

**Second-order effect:** a worker's reviewer runs against ITS baseline, so any commit you land while it works enters its diff and it can be rejected for your changes. It self-corrects as the worker re-baselines on the next attempt, but a single attempt can be lost to it.