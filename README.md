# Workshop

Personal-AI control panel: Agents (Letta-backed), Projects (git scan), Apps (Plaid + Open Wearables). Web + mobile, sharing types via `shared/`.

## Layout

```
workshop/
├── TODO.md                # build state for the cron agent
├── cron_agent.sh          # one-shot claude -p loop (installed by supervisor session)
├── infra/
│   ├── letta/             # docker compose: pgvector + Letta server (127.0.0.1:8283)
│   ├── open-wearables/    # docker compose (Phase 2)
│   └── systemd/           # workshop-backend.service template + secrets.env.example
│                          #   (installed by scripts/install_service.sh)
├── backend/               # FastAPI: /agents, /projects, /finance, /plaid/webhook
├── frontend/              # Vite + React + TS: Agents, Agent config, Projects, Apps
├── mobile/                # Expo + React Native scaffold
├── shared/types.ts        # Agent + Project types shared with frontend & mobile
├── scripts/
│   └── migrate_memory.py  # CLAUDE.md/MEMORY.md → Letta core + archival
└── vault/                 # Obsidian-style markdown notes (Phase 1+)
```

## Bring-up (operator runs these — classifier blocks the bot from doing them itself)

```bash
# 1. Deploy Letta
cd ~/workshop/infra/letta && docker compose up -d
docker ps --format '{{.Names}}\t{{.Status}}' | grep letta

# 2. Seed masini agent
cd ~/workshop/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
LETTA_URL=http://127.0.0.1:8283 LETTA_SERVER_PASS=workshop-local \
  .venv/bin/python ../scripts/migrate_memory.py

# 3. Install + start backend as a systemd --user service.
# Renders infra/systemd/workshop-backend.service for THIS checkout, enables
# linger (so it auto-starts on boot without a login session), and starts it.
# Idempotent — safe to re-run after a pull. Restart with: scripts/restart_backend.sh
bash scripts/install_service.sh
journalctl --user -u workshop-backend -f

# 4. Start frontend (dev)
cd ~/workshop/frontend && npm install && npm run dev
# → http://127.0.0.1:5173

# 5. (Optional) Mobile dev
cd ~/workshop/mobile && npm install && npm run start
```

## Endpoints

- `GET  /agents/`                  — list Letta agents
- `GET  /agents/{id}`              — agent detail (memory blocks)
- `POST /agents/`                  — create agent
- `PATCH /agents/{id}/memory/{label}` — update persona/human/etc.
- `DELETE /agents/{id}`
- `GET  /projects/`                — git repos under `$WORKSHOP_PROJECTS_ROOT`
- `GET  /finance/balances`         — Plaid balances (via plaid-mcp Node bridge)
- `GET  /finance/transactions?days=30` — Plaid transactions
- `POST /plaid/webhook`            — Plaid webhook sink (logs to `.runtime/plaid-webhooks.jsonl`)
- `GET  /healthz`

## Status (Phase 1)

- ✅ Monorepo scaffold
- ✅ FastAPI backend
- ✅ React frontend (Agents, Projects, Apps, Agent config)
- ✅ Systemd unit
- ✅ Memory migration script
- ⏸ Letta deploy (operator)
- ⏸ Memory migration run (depends on Letta)
- ⏸ End-to-end verify

## Status (Phase 2)

- ✅ Plaid webhook receiver route
- ✅ `/finance/balances` + `/finance/transactions` via plaid-mcp Node bridge
- ✅ Apps page rendering finance data
- ⏸ Open Wearables deploy (operator)
- ⏸ iOS Health MCP Sync configuration (operator-side, requires phone)
- ⏸ Cloudflare Tunnel (operator)
- ⏸ BlueBubbles connector (operator-side, requires MacBook Air)

## Status (Phase 3)

- ✅ Agent config UI (create/edit/delete from React)
- ⏸ Privacy relay service (Qwen-local boilerplate / Claude architecture)
- ⏸ Service registry

## Status (Phase 4)

- ✅ Mobile scaffold (Expo + Router + shared types)
- ⏸ Full mobile screens
- ⏸ On-device testing (operator)
