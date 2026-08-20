<!-- author: self; chat: 1a6cf9dda32f467295f46c97594f4e3c -->
Built 2026-08-20. A chat-launched run, on finishing, makes the chat take its own next turn seeded with the result. Mechanism, all reusing existing machinery:

- run_report.post_for_job (runs in the DETACHED worker at jobs.finish) posts the result as an autonomous message, then, ONLY when job.chat_id == thread_id, calls chat_drain.request_continuation(thread_id, seed, dedupe_id=job-thread).
- request_continuation persists an `auto` seed onto the thread's chat_queue (chat_queue.enqueue(..., auto=True), idempotent on client_id `auto-<dedupe>`). The worker cannot reach the backend event loop, so it only writes the file; drain_soon no-ops there.
- Backend loop chat_drain.continuation_loop (wired in main.py lifespan, 5s) drains any thread with an auto seed once idle. drain's existing active_turn/thread_ops guards prevent a rival turn; the seed on disk makes it restart-safe.
- routes/chat.py _start_queued_batch routes a lone auto entry to _turn_for_message(..., already_recorded=True, parts=None) so NO operator/user message is recorded; the seed is the prompt only. chat_queue.next_batch treats an auto seed as alone (never batched with operator messages), via _alone().

Scope: only runs the chat itself launched (job.chat_id gate) continue a chat; autonomous sweeps still post a report but do not. NOT yet built: a free-form no-ticket launcher + a model-facing tool to spawn a background investigation directly; the continuation half already supports it. See docs/platform-redesign/background-subagents-2026-08-20.md.