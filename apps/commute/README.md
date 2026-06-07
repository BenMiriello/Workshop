# Commute app

Day-of commute display. Sibling to `~/workshop/frontend/` — own Vite project,
own dev server (port **5174**), PWA-installable so it lives on phone.

## What goes where

- **This app (`apps/commute/`)** — live status: legs, projected arrival,
  late-risk banner, service alerts, recent alert log. The thing the operator
  opens during commute.
- **Workshop "Commute" tab (`frontend/src/pages/Commute.tsx`)** — config:
  schedule.json editor, targets.json editor, source-registry read-only view.
  The "set and managed in one place" surface.
- **Workshop backend (`backend/routes/commute.py`, `backend/services/commute/`)**
  — shared data + API. Both consumers above hit `/api/commute/*` on port 8000.

## Run it

```
cd ~/workshop/apps/commute
npm install      # first time only
npm run dev      # binds 0.0.0.0:5174, proxies /api → backend:8000
```

Open `http://<host>:5174` from a phone on the same network. Browser will offer
"Add to Home Screen" — that installs the PWA shell. Backend (port 8000) must be
reachable for `/api/*` calls to succeed.

## Backend dependency

This app depends on the Workshop backend running and serving `/api/commute/*`.
If the backend is down the page loads (cached shell) but status shows an error.
