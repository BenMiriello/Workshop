# workshop-backend

FastAPI service exposing `/agents` (Letta-backed) and `/projects` (git-backed) for the Workshop UI.

## Local setup

```
cd ~/workshop/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## systemd

```
systemctl --user daemon-reload
systemctl --user enable --now workshop-backend
journalctl --user -u workshop-backend -f
```

## Env

| Var | Default | Purpose |
|---|---|---|
| `LETTA_URL` | `http://127.0.0.1:8283` | Letta server |
| `LETTA_SERVER_PASS` | `workshop-local` | Bearer for Letta |
| `WORKSHOP_PROJECTS_ROOT` | `/home/clawd-a` | Where /projects scans for `.git` dirs |
