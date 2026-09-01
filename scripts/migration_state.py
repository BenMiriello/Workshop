#!/usr/bin/env python3
"""Compare the tree's alembic head against the live database's `alembic_version`.

WHY THIS EXISTS. On 2026-09-01 a commit added two columns to `chat_thread` plus
the migration that creates them, and nothing in this repo's deploy path applies
a migration -- not `scripts/apply.sh`, not `scripts/restart_backend.sh`. The
live database stayed at `b9f3e2d07a16`, the code that reads those columns was
one deferred restart away from serving, and a real ORM read against live already
answered `UndefinedColumn: column chat_thread.armed does not exist`. The commit
was reverted to save the app.

`restart_backend.sh`'s import preflight cannot catch that class of break: a
missing column fails at QUERY time, and `import main` imports perfectly. The
only thing that sees it before the outage is the comparison this file makes.

WHAT THIS IS NOT: it never runs alembic, never opens a write transaction and
never touches the schema. Applying a schema change to a live database stays an
explicit act a person performs, in a terminal, having read what it will do. The
job here is to make the drift IMPOSSIBLE TO MISS, not to resolve it.

Output is one TAB-separated line, `state<TAB>headline<TAB>remedy`, because the
caller is a shell script that formats its own report lines:

  ok              live is exactly at the tree's head
  tree_ahead      live is an ANCESTOR of the head: migrations are unapplied, so
                  code from this tree would query columns that do not exist
  live_ahead      live is at a revision this tree does not contain (a revert, or
                  a checkout older than the database)
  unknown         configured but not measurable -- unreachable, no driver, no
                  alembic_version table. NEVER report this as in sync
  not_configured  no database URL at all; there is no schema to be in sync with

Exit status: 0 for ok/not_configured, 1 for either drift, 2 for unknown.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
ALEMBIC_INI = BACKEND / "alembic.ini"

# The command that fixes tree_ahead. It is printed, never run. Two halves,
# because migrations run as the schema OWNER: the application role holds DML
# only and cannot execute DDL (see backend/db/migrations/env.py).
UPGRADE_CMD = (
    "set -a; . ~/.config/workshop/db-admin.env; set +a; "
    "cd backend && .venv/bin/alembic upgrade head"
)


def emit(state: str, headline: str, remedy: str = "") -> None:
    print("\t".join((state, headline, remedy)))
    raise SystemExit({"ok": 0, "not_configured": 0,
                      "tree_ahead": 1, "live_ahead": 1}.get(state, 2))


def live_url() -> str:
    """The DSN of the database the RUNNING BACKEND uses, which is the one that
    matters: drift against any other database is not what takes the app down.

    Falls back to the owner DSN only so that an installation which configures
    migrations but not the app still gets an answer.
    """
    sys.path.insert(0, str(BACKEND))
    try:
        from db.script_env import load_config_env  # the declared home of both
        load_config_env()                          # values; never overrides env
    except Exception:
        pass
    return (os.environ.get("WORKSHOP_DATABASE_URL", "").strip()
            or os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip())


def tree_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return list(ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads())


def main() -> None:
    if not ALEMBIC_INI.is_file():
        emit("not_configured",
             f"schema: no alembic config at {ALEMBIC_INI.relative_to(REPO)}, "
             f"so this tree declares no schema")

    url = live_url()
    if not url:
        emit("not_configured",
             "schema: no database configured (WORKSHOP_DATABASE_URL unset), so "
             "there is no schema to compare -- this check did not run")

    # The tree side first: it needs no network, and if it cannot be read there
    # is nothing to compare against.
    try:
        from alembic.script import ScriptDirectory  # noqa: F401  (import check)
        from sqlalchemy import create_engine, text
    except Exception as exc:
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- alembic/sqlalchemy do not import "
             f"here ({type(exc).__name__}), so tree and live were not compared")

    try:
        heads = tree_heads()
    except Exception as exc:
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- the tree's migrations do not read "
             f"({type(exc).__name__}: {exc})")
    if len(heads) != 1:
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- the tree has {len(heads)} heads "
             f"({', '.join(heads) or 'none'}); `upgrade head` is ambiguous")
    head = heads[0]

    # A SHORT TIMEOUT IS PART OF THE CONTRACT. apply.sh is documented as safe to
    # run at any time from any agent; a check that can hang on a wedged database
    # is not safe to run at any time.
    connect_args = {"connect_timeout": 3} if url.startswith("postgres") else {}
    try:
        engine = create_engine(url, connect_args=connect_args, future=True)
        with engine.connect() as conn:
            rows = [r[0] for r in conn.execute(
                text("SELECT version_num FROM alembic_version"))]
    except Exception as exc:
        name = type(exc).__name__
        if "UndefinedTable" in str(exc) or "no such table" in str(exc):
            emit("tree_ahead",
                 f"schema: the live database has NO alembic_version table, so it "
                 f"has never been migrated; this tree is at {head}",
                 UPGRADE_CMD)
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- the live database did not answer "
             f"({name}); tree head is {head}",
             "check the database is up, then: bash scripts/apply.sh --check")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

    if len(rows) != 1:
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- alembic_version holds {len(rows)} "
             f"rows ({', '.join(rows) or 'none'}); tree head is {head}",
             UPGRADE_CMD)
    live = rows[0]

    if live == head:
        emit("ok", f"schema ({head})")

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    try:
        script.get_revision(live)
    except Exception:
        # The live revision is not in this tree at all. That is a REVERT, or a
        # checkout older than the database. Real drift, and worth saying, but it
        # does not block a restart: the code on disk does not know about
        # whatever that revision added, so the extra schema is inert, and the
        # restart is usually the very thing that puts the app back on code the
        # database can serve.
        emit("live_ahead",
             f"schema: the live database is at {live}, which THIS TREE DOES NOT "
             f"CONTAIN; the tree's head is {head}",
             f"the tree is behind the database (a revert, or an old checkout); "
             f"restore the commit that adds {live}, or downgrade deliberately")

    try:
        pending = [r.revision for r in script.iterate_revisions(head, live)]
    except Exception:
        pending = []
    if not pending:
        # Known revision, not the head, and the head does not descend from it:
        # the history branched. Ambiguous, so do not guess.
        emit("unknown",
             f"schema: COULD NOT DETERMINE -- live is at {live} and the tree's "
             f"head is {head}, and neither is an ancestor of the other")

    n = len(pending)
    emit("tree_ahead",
         f"schema: the tree is {n} migration{'s' if n != 1 else ''} AHEAD of the "
         f"live database (live {live}, tree head {head}); code in this tree "
         f"queries a schema that is not there yet",
         UPGRADE_CMD)


if __name__ == "__main__":
    main()
