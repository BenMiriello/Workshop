<!-- author: self; chat: 67e5b629ff8844febe27d6b94f91f4d5 -->
`GET /chat/threads/{id}/events` is THE sync endpoint: the transcript load, the live poll, the active-turn snapshot, the context-cut divider, the branch identity and the send queue all ride on that one response (routes/chat.py:568-700, deliberately, so those facts cannot disagree with each other).

The consequence when diagnosing: a single 500 there presents as four unrelated-looking bugs at once, and none of them names the endpoint.

- chats that will not load (the `after=0&tail=30` mount call fails)
- "This chat stopped updating." with a broken connector (the poll fails; frontend/src/components/chat/staleNotice.ts)
- a `/compact` that completed server-side but never appears (the compact boundary is a log event delivered only by this endpoint, so the client never learns the fold happened)
- a queue or cut that looks stale

So when several chat symptoms appear together, check this one route before investigating them separately. Confirmed on 2026-08-17, when a wrong `response_model` field type 500d it for ~7 minutes and produced exactly those four reports.

Diagnostic that found it in one step:

    journalctl --user -u workshop-backend.service --since '1 hour ago' \
      | grep -E '\" (4[0-9][0-9]|5[0-9][0-9])'

Note that a thread with no relational branch row was unaffected, which is why only SOME chats broke and it read as data corruption rather than as a code change.