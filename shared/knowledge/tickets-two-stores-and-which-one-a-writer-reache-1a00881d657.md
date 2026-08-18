<!-- author: self -->
For `development` and `workshop`, tickets READ from Postgres (`tasks._from_relational`) while `_persist` still writes `.tasks.json` authoritatively and mirrors to Postgres best-effort. Project scopes are file-backed and unaffected.

Consequence: any writer that cannot reach Postgres silently writes a store nothing reads. Two things must be true for a writer to reach the read store:

1. an interpreter with sqlalchemy (backend/.venv, not system python3)
2. a pinned data owner (`ownership.set_current_owner`), resolved via `auth_store.operator_id()`, never a hardcoded id

`scripts/project_tasks.py` had neither until 2026-08-15 (e7b758a); it now re-execs into the venv and pins the owner itself, so the documented `python3 scripts/project_tasks.py ...` invocation is correct again. A mirror that skips on a relational root is now logged at ERROR in `tasks._persist`.

This ends when `.tasks.json` is retired for relational roots (ADR 0019), which requires `db/ticket_sync.mirror` to stop swallowing failures and become the authoritative write.