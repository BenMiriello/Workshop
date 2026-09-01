<!-- author: self; chat: 50cf71c1e6564676b908b6f33ddf9fe1 -->
Operator, 2026-08-29: "nobody needs to decide what a compaction should keep. just use the claude compaction!"

**What runs now.** `POST /threads/{id}/compact` starts a turn whose prompt is the literal string `/compact`. `_is_raw_command` sends a single-line slash message to the CLI verbatim, which is what makes it register as a command; `build_streaming` already watched for the `compact_boundary` meta event, replaced the CLI's context-usage dump with one sentence, and wrote the boundary the transcript draws its divider from. That whole path existed and had no caller.

**Why it is right.** The CLI compacts here: measured 2026-08-27 on 2.1.143 with this backend's own spawn flags, `trigger:manual`, 27,088 -> 2,104 tokens in 17.4s. It is also already what happens unasked when a window fills, 29 `trigger:auto` boundaries on record. The button and the automatic path used to do two different things.

**Deleted:** `_fold_summary`, `_land_fold`, `_land_fold_when_quiet`, `_est_tokens`, `_COMPACT_KEEP_TAIL_TOKENS/MIN`, and `services/compaction_adapter.py` (a provider seam whose entire premise was that the CLI could not compact).

**Kept, and it is a separate feature:** the scope's working-state note. It rode on the fold's summary but it is the only thing that makes compacting help a chat OTHER than the one compacting. It now makes its own summarising call over the transcript, off the request path, and MERGES with what other chats in the scope left instead of overwriting.

**Also kept:** the READ path for a stored `summary` and a `compact` cut. Threads compacted before this date carry both and the prompt still honours them.

**Fell out of it:** `working_state_block`'s `exclude_chat` suppression is gone. Its reason was that the writing chat already carried the note verbatim as its own fold summary. It does not any more, so the suppression only took the handoff away from the session most likely to need it.