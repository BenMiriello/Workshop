---
name: importing-backend-main-writes-live-instance-state
description: importing backend/main.py runs ensure_identity_manifests(), which writes .runtime/installed-apps.json and can silently reorder the operator's launcher cards
metadata:
  type: reference
---

`import main` is not read-only. Module import constructs the FastAPI app, which
calls `app_registry.ensure_identity_manifests()` (main.py, in the router-mount
block). That WRITES instance state, including `.runtime/installed-apps.json`,
which is what orders the operator's launcher cards.

Measured 2026-08-17: a tooling probe that merely imported `main` to read the
OpenAPI schema rewrote that file and reordered the launcher. It is gitignored,
so `git status` shows nothing and `git checkout` cannot undo it. There is no
backup.

Any script that imports the backend app for inspection must first redirect the
instance-state env vars to a temp directory. `scripts/gen_openapi.py` does this
and is the worked example: it redirects `WORKSHOP_RUNTIME`, the config dir, the
installed-apps path and the apps manifest before importing.

Two related traps in the same area:

- **The app catalog is per-box.** Routers mount only for INSTALLED apps, so the
  schema a naive dump produces differs between machines. `gen_openapi.py` forces
  the full built-in catalog so the artifact is deterministic.
- **`import fastapi` is not a safe venv probe on this box.** A partial fastapi
  in `~/.local` imports fine and then fails inside route construction on a
  missing `python-multipart`. And `backend/.venv/bin/python` symlinks onto the
  same interpreter `python3` resolves to, so comparing `sys.executable` reads
  equal from outside the venv. Compare `sys.prefix`.
