<!-- author: self; chat: 50cf71c1e6564676b908b6f33ddf9fe1 -->
Researched 2026-08-27 in response to the operator's observation that the chat/agent line breaks down the further we go.

**The model.** One entity: a Thread (id, scope, transcript, mailbox). Sender is a field on the message, not a property of the thread. What varies is three independent facts, none of which is type:

1. **What advances it** — a person sending, or something armed. `chat_drain.request_continuation` already implements this and is in production use by run_report and background_runs. A thread holding a continuation seed IS a self-advancing thread. Because advancing is armed rather than switched, a chat opened by typing cannot work ahead, with no ceremony needed to prevent it.
2. **Whether a person is attending** — a live observed fact, not a setting. Decides only where a question goes: inline vs inbox. Already in the code unnamed: `frontend/src/lib/chat.ts:250` justifies the `meta.autonomous` render tier with the words "with nobody watching". Industry terms are handoff/handback (Zendesk).
3. **What it may do without asking** — existing autonomy model, unchanged.

**An agent** = a thread with a declared definition (name, role, triggers, limits) AND something armed to advance it. **A chat** = a thread with neither. Chatting with an agent converts nothing; it changes attendance only.

**Two record failures found while measuring:**
- D4 ("agent-initiated messages do not land in the transcript") was decided once, restated as law twice, and is contradicted by three shipped paths (run_report, question_report, background_runs). No doc reconciles it. The rule was drawn on the wrong axis: the requirement is to keep IRRELEVANT senders out, not all senders.
- `triager` appears in 1 of 11,957 job records, and that job is the one that wrote the doc defining the role. The only declared triager (agents/masini.md) is marked INERT. The routing role the operator says the system is missing has never executed, and cannot, because there is no thread-to-thread send.

**External check:** Temporal, Restate, Step Functions, Inngest, Trigger.dev, LangGraph, OpenAI Agents SDK and A2A all model blocked-on-a-human as a STATE of one entity, via the same primitive used to wait on any external party. None ships a distinct human-process type. A2A puts `input-required` in the same TaskState enum as `working`. The lone dissenter is BPMN (User Task vs Service Task as different elements), whose documented cost is exactly the ambiguity being described: escalating automated work to a human becomes a model change rather than a runtime decision.

**Vocabulary to use, not invent:** steer vs queue (already in control.py), handoff/handback, input-required, ambient (= event-triggered, narrower than autonomous). Avoid "unattended" (RPA vocabulary) and avoid manual/automated mode (collapses the three axes).

**First build:** one primitive, a thread can send to a thread, with `sender` on the message, through the existing chat_queue door. Successor links, the triager and cross-thread nudges all fall out of it.

Full writeup: docs/platform-redesign/one-thread-many-senders-2026-08-27.md (committed 24cbeeae).