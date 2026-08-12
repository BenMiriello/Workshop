<!-- author: self -->
`chat_log.read_thread` reads thread `meta` BEFORE `read_events` and computes `active_run` from that already-stale meta. `commit_run` writes in the opposite order: the reply event first, then `active_run_id = None` and `clear_live`.

A read landing between those two writes returns a page carrying **both** the committed reply **and** `active_run` still naming the run that produced it, with its now-stale live draft attached.

This caused the 2026-08-12 'previous response renders under my message, then gets replaced' bug. Fixed client-side in `transcriptBlocks.ts` (`liveRunId`: a run whose reply the log already carries is finished, whatever the snapshot says), with tests.

**The server-side window is still open.** Swapping the read order in `read_thread` so `active_run` is computed from a meta read AFTER `read_events` would narrow it from the whole read span to the inter-write gap in `commit_run`. Not done; any future 'stale live block' symptom should start here rather than in the client.