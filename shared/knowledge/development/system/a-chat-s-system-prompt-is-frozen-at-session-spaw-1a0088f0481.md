<!-- author: self -->
Measured 2026-08-16. The task-proposal card contract in `backend/services/chat_context.py` (TASK_PROPOSAL_CARD) was cut from eight fields to three by commit 209a8c3 at 01:45Z. The backend restarted onto the new code at 02:58Z. A chat session spawned at 22:03:28Z kept emitting the OLD eight-field card for hours afterward.

Mechanism: the CLI session's system prompt is assembled once at spawn and every later turn resumes that session, so the prompt is frozen for the life of the chat. Disk had exactly one copy of the contract (the correct one) the whole time, and sessions spawned after the restart got it. Verified by comparing session first-entry timestamps in `~/.claude/projects/-home-<service-user>-workshop/*.jsonl` against the commit and restart times.

Why it matters beyond staleness: the card contract is a WIRE FORMAT between the prompt and the client. An old-contract card names fields the current store no longer accepts, so it files wrong or renders dead. Any change to a card contract, tool contract, or emitted-block format has this exposure, not just cosmetic prompt text.

When debugging 'the model used an outdated instruction', check the session's spawn time before assuming the model improvised or that the backend is stale. `apply.sh --check` reporting the backend as current does NOT mean open chats are current.