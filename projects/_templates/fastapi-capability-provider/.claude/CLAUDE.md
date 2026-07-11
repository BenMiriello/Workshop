# {{title}}: house rules

A FastAPI capability-provider service inside the Workshop.

- Entrypoint is `app/main.py` (`app` ASGI object); routes live in `app/router.py`.
- The Workshop app runner owns the process and the port. Do not start/stop dev
  servers or pick ports yourself; use `$PORT`. Apply a finished change with
  `bash scripts/restart_app.sh {{slug}} api` from the workshop root.
- Keep `/health` cheap; the runner polls it for liveness.
- `PROVIDES` (in `app/router.py`) is the capability contract. Every entry must map
  to a real route; mirror it into `backend/apps.json` `provides` when registered.
- No secrets in code. Declare them in `workshop.app.json` `env_requirements`.
- No stubs. Finish the slice or mark it `[~] BLOCKED` in TODO.md.
