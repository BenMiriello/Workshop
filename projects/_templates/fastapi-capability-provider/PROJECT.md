# {{title}}

> {{purpose}}

## Goal

A backend capability provider: a FastAPI service that declares the capabilities
it `provides` (see `PROVIDES` in `app/router.py`) and exposes them as real HTTP
routes. Run by the Workshop app runner from `workshop.app.json`.

## Constraints

- Keep the service self-contained and startable: `pip install -r requirements.txt`
  then `uvicorn app.main:app`. The runner allocates the port centrally (`$PORT`);
  do not hardcode one.
- `/health` must stay a cheap liveness probe (the runner polls it).
- Declared capabilities in `PROVIDES` must match real routes. When this project is
  registered as a Workshop app, mirror them into `backend/apps.json` `provides`.
- No secrets in code. Declare any needed keys in `workshop.app.json`
  `env_requirements` so the operator can fill them in the Environment panel.

## Open questions

- What is the real data source behind `/items` (database, upstream API, file)?
- Which capabilities should this provide, and who consumes them?
- Does it need auth beyond the platform boundary?

## Run it

- Start from the project's App panel, or `POST /base-apps/run/{{slug}}/start`.
- The runner builds (`pip install`) then starts uvicorn on an allocated port.
