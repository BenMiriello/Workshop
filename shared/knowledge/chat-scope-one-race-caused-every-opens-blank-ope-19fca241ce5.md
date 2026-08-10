Fixed 2026-08-03 in `464cb81`.

**Root cause.** `Chat.tsx` derived `scope` from `activeProvider`, which initialized to the URL contextKey and then flipped asynchronously after `listProviders` resolved. The thread-loader effect keys on `scope`, so it ran twice. The URL intents (`?new=1`, `?thread=<id>`) are one-shot refs consumed on use, so the FIRST pass burned them under the pre-resolution scope and the SECOND pass fell through to the persisted selection for the resolved scope. Symptoms that all trace to this: New chat reopening an old thread, an existing thread opening blank, intermittency (it depended on fetch ordering).

**Compounding factor.** `readProvider`/`writeProvider` are keyed by contextKey, and nearly every chat lives at `/chat/general` — so it was effectively ONE global 'last scope picked' inherited by every chat. Selecting a scoped assistant (e.g. budget) once put every subsequently opened chat in that scope, which then made linked threads unfindable via the race above.

**Invariant to preserve.** A chat's scope is the SAME axis as the thread's server-side `context_key`: `chat_store.list_threads` filters on an exact match (`backend/services/chat_store.py:316`). A thread therefore always knows where it lives; opening one must never be a question about picker state.

**The rule now** lives as a pure function `resolveScope()` in `frontend/src/lib/chatSelection.ts`, ranked intent-first: linked thread's context_key > New chat's launching surface (DEFAULT_SCOPE `general` unless scoped) > picker memory. The loader is gated on `providersReady` so intents are consumed exactly once.

**If this regresses**, check two things before anything else: (1) is the loader effect still gated so it cannot run on an unresolved scope, and (2) is the picker's remembered provider still ranked LAST. Those two are the failure mode.

No frontend test runner exists and npm installs are blocked, so `resolveScope` is covered by an 8-case decision table in `scratch/scope_check.mjs` (plain node, no deps).