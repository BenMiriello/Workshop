# workshop-backend

FastAPI service exposing `/agents` (definition-file backed) and `/projects` (git-backed) for the Workshop UI.

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
| `WORKSHOP_PROJECTS_ROOT` | `$HOME` | Where /projects scans for `.git` dirs |
