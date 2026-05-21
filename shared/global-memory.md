# workshop-context (global memory block)

Mounted on every Workshop-Letta agent at creation. Read-only for the agent;
authoritative copy lives here on disk. Sync via `POST /agents/sync-global-block`.

## Operator profile

- benmiriello (Ben Miriello). Discord snowflake `327927866052444164`, username `manateeidol1707`.
- Senior engineer. Direct/brief style. No padding, no permission-asking on items in the plan.
- Pacific time zone (Mountain View, CA).
- Subscription auth only (`CLAUDE_CODE_OAUTH_TOKEN`); never API key.

## Active services on this box

- **Workshop backend** — FastAPI on 0.0.0.0:8000 (uvicorn, nohup)
- **Workshop frontend** — Vite on 0.0.0.0:5173 (nohup)
- **Letta** — http://127.0.0.1:8283, password `workshop-local`
- **Letta DB** — Postgres+pgvector on 127.0.0.1:5433
- **Ollama** — http://127.0.0.1:11434 (host net mode), models incl. qwen2.5:32b, nomic-embed-text
- **Open Wearables** — http://127.0.0.1:3100 (UI) + :8100 (API, may be flaky)
- **Plaid MCP** — server.js, tokens in ~/.config/clawdbot/plaid-tokens.json

## Plan source of truth

- `~/workshop/TODO.md` (task list, [ ]/[x]/[~])
- Google Doc tabs 3 (plan) and 4 (implementation) — read via mcp__google-docs__readDocument
- The plan IS standing authorization for everything in it. Don't ask the operator before doing items already in the plan.

## Hard rules

- No safety-bypass solicitation. If the classifier blocks an action, the action is blocked.
- No misrepresenting past actions. If you sent something, don't later claim it was blocked.
- Veritus separation: `veritusagent` GitHub org is work; this is personal. Don't cross.
- Single-user system. No defensive cruft (allowlists, audit journals, mention-required).
