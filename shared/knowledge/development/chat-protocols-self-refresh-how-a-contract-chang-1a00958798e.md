<!-- author: self -->
A claude session is given its instructions ONCE, at spawn. `routes.chat._assemble_full_prompt` sends the role preamble and the persona on the first turn; every later turn resumes that session and sends only the volatile part (`_resumed_prompt`). Re-sending a 12KB persona every turn would waste the window and fight the session's own memory, so that shape is deliberate and correct.

The trap: those two blocks carry CONTRACTS, not just character. The ui-card format (`UI_CARD_PROTOCOL`, in the role preamble) and the task-proposal field list (`TASK_PROPOSAL_CARD`, in the persona) are wire formats between the prompt and the client. They change when the code changes, and before 2026-08-16 nothing re-stated them, so a session spawned before a change obeyed the retired contract for its entire life.

FIXED 2026-08-16 (commit 7039013). `chat_context.instruction_contract(provider, enable_chat_tools=...)` builds the role preamble plus the persona as ONE function, so the fresh path and the resumed path cannot compute it differently. Its digest is kept on the thread as `turn_marks['contract_hash']`. `_resumed_prompt` re-states the contract under a `=== PROTOCOL UPDATED ===` heading only when the digest moves; unchanged costs one string compare and emits nothing. The fresh path records what it sent, which is what stops a newly spawned session re-stating a contract it received one turn ago. The build-agent resume branch has the same guard.

Consequences worth knowing:
- A thread with NO recorded digest is refreshed once, on purpose: it predates the mechanism and is stale by definition.
- The block is worded as a REPLACEMENT, not an addition. The retired copy is still in the session's history and cannot be removed, so the new one must say which counts. Same reasoning as the LIVE CONTEXT supersession block.
- If you add a protocol block to a persona or the role preamble, it is covered automatically. If you put one somewhere else that is only sent on the first turn, it is NOT.
- Build-agent sessions are keyed by agent slug and shared across that agent's chats, while marks live on the thread, so two threads on one agent can each refresh once. Harmless.

When debugging 'the model followed an outdated instruction', check `turn_marks['contract_hash']` against `contract_digest(instruction_contract(provider))` before assuming the model improvised or the backend is stale.