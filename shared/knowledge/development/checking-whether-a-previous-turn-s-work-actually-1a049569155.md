<!-- author: self; chat: f1b0f8cddafd4a438bc0a7931a023f57 -->
A turn that ends on narration and a turn that crashed look identical from the next turn's seat, and both look identical to work that never happened. The check is three commands and it takes a minute:

1. `git log --oneline --all | grep -i '<distinctive commit subject>'` finds the commits. `--since` alone is unreliable: another agent's 50 commits can push yours past `head -20`.
2. `git merge-base --is-ancestor <sha> HEAD` for each one. Existing in the object store is not the same as being in the branch you are on.
3. `bash scripts/apply.sh --check`. Committed is not the same as served; the operator only ever sees the built origin.

Measured 2026-08-28: a restart message reported a day's Memory work as "completely undone", and all four commits were ancestors of HEAD with the bundle in sync. The work had landed; what was missing were items the original pass never reached. Re-doing it would have thrown away a day and still not fixed what he was looking at.

So: establish ground truth before believing a report of lost work, including your own report. Then find what is ACTUALLY undone by re-reading the original request item by item against the tree.