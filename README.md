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
```

## Bring-up

```bash
git clone <repo> workshop && cd workshop
bash scripts/bootstrap.sh      # first run creates config templates, then stops
# fill in ~/.config/workshop/auth.env (CLAUDE_CODE_OAUTH_TOKEN) and instance.env
bash scripts/bootstrap.sh      # second run installs, builds and starts
```

`bash scripts/preflight.sh` checks whether a machine is ready and lists
everything missing at once. It changes nothing.

Full deployment notes, including what each service is, how to update an existing
install, and why this is systemd rather than a container:
**[docs/deploying.md](docs/deploying.md)**.

Two things that catch everyone:

- `npm install` at the **repo root**, never inside `frontend/`. This is an npm
  workspace, and installing in `frontend/` leaves `@workshop/ui` unresolvable.
- `npm run build:ui` before any typecheck. vite reads the kit's sources, tsc
  reads its built `dist`, so a missing `dist` builds fine and fails typecheck.

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
