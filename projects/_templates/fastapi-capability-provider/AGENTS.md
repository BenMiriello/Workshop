# Agent rules: {{title}}

- Read `PROJECT.md` first every turn.
- Update `TODO.md` as work progresses; check items off when done.
- Edit only within this directory.
- Keep `PROVIDES` (in `app/router.py`) in sync with the routes you add; every
  declared capability must map to a working endpoint.
- Keep the service startable end to end: `pip install -r requirements.txt` then
  `uvicorn app.main:app`. Do not pick a port; the runner supplies `$PORT`.
- Do not hardcode secrets. Add an entry to `workshop.app.json` `env_requirements`
  and read the value from the environment.
- No stubs (TODO/FIXME/NotImplementedError/pass # stub/...). Mark `[~] BLOCKED`
  if a task is too big to finish in one tick.
