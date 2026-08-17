<!-- author: derived:letta-export; type: reference -->
**Provenance.** Exported from Letta (`http://127.0.0.1:8283`) before its decommission (memory-consolidation-2026-08-16.md, decision D8), 2026-08-16. Letta itself does not track a block-level creation timestamp; the agent's own creation date is recorded below as the nearest available date.

- Block id: `block-050bfcdb-f2f1-4db8-a57e-9b1994823d59`
- Block label: `operator-context`
- Block description: Workshop global block: operator profile + active services + plan source. Mounted on every Workshop-created agent.
- Held by Letta agent(s):
- `masini-ch-1507197290358636574` (agent id `agent-a3b99f4b-ca39-45c8-ae28-28bb3fb3cbb1`, created 2026-06-04T02:13:49.250708Z)
- `masini-ch-1507029806909362237` (agent id `agent-600790d0-0865-4a55-800f-fc51de6b2095`, created 2026-05-25T13:27:28.680091Z)
- `masini-commute` (agent id `agent-7bb197f4-85b0-4cbd-acdf-b0f848ef81cc`, created 2026-05-21T16:24:07.229233Z)
- `email-triager` (agent id `agent-f2cd9727-362f-4758-ac9d-90f841785b11`, created 2026-05-21T04:54:30.944676Z)
- `plaid-watcher` (agent id `agent-4a35a3ce-13e9-4407-8a79-b28c99e52f33`, created 2026-05-20T21:54:22.533840Z)

---

# operator-context (global memory block)

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
