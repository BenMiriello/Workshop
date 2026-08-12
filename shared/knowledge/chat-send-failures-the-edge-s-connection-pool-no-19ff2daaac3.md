Root cause found 2026-08-11 for the recurring "Not sent: this message never reached the server" reports.

**Two windows, both in the deployment:**

1. `backend/workshop_public_app.py` proxies to the backend with a pooled `httpx.AsyncHTTPTransport`. httpx defaults `keepalive_expiry` to 5.0s; uvicorn defaults `timeout_keep_alive` to 5s. Both ends reap an idle socket at the same instant and httpx defaults to `retries=0`, so a request written to a just-closed socket raises `RemoteProtocolError` and **the backend logs nothing at all** (it never saw the request). Evidence: edge log 2026-08-02 (`GET /queue/runner`), 2026-08-09 (`PUT /chat/drafts`), neither near a restart.
2. Backend restarts (32 in the 7 days to 2026-08-11) produce `ConnectError` in the same place.

Neither was caught, so both surfaced as an **uncaught 500 + traceback**. The client maps 500 to "the server failed to start this turn", which points at the turn rather than at the transport.

**Fixed (commit a4f663f):** pool `keepalive_expiry=2.5` (half the backend's clock), `retries=2`, one explicit retry on `ConnectError`/`ConnectTimeout`/`RemoteProtocolError` only (NOT `ReadError`/`ReadTimeout` -- those can mean the backend read the request, and this proxy carries writes), and a 502 instead of an uncaught 500. Same treatment on `_proxy_stream`.

**Diagnosing this again:** the tell is a client-side failure with NO corresponding line in `journalctl --user -u workshop-backend`. Check `journalctl --user -u workshop-public` for `httpx.RemoteProtocolError` / `ConnectError` / `Exception in ASGI application`. The recurring cloudflared `stream N canceled by remote with error code 0` lines are all `dest=.../turns/<id>/stream` and are just the browser closing SSE sockets -- normal, not a fault.

**Do not raise the edge's read timeout to "fix" a chat send.** `POST /threads/{id}/turns` returns as soon as the run is claimed; the turn itself is watched over SSE. The 120s timeout only binds the non-UI blocking path `POST /threads/{id}/messages` (voice, scripts), which Cloudflare's own 100s origin timeout beats anyway.

Related: `frontend/src/components/chat/sendFailure.ts` decides which failures are retried silently. Anything the server ANSWERED with is about the message and must stay non-transient.