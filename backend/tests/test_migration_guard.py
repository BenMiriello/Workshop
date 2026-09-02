"""The deploy path's schema guard: `scripts/migration_state.py` and the refusal
`scripts/apply.sh` builds on top of it.

WHY THIS IS PINNED. On 2026-09-01 a commit added two columns to `chat_thread`
and the migration that creates them, and nothing in this repo's deploy path runs
alembic. Live stayed at `b9f3e2d07a16`, a read through the committed code
answered `UndefinedColumn: column chat_thread.armed does not exist`, and a
deferred restart was already armed. `restart_backend.sh`'s import preflight is
structurally blind to it: a missing column fails at QUERY time and `import main`
imports perfectly.

What must be true:

  * the tree has exactly ONE alembic head, or `upgrade head` is ambiguous and
    the comparison below has nothing to compare against;
  * a live database BEHIND the tree is `tree_ahead`, and the guard names both
    revisions and the exact command that fixes it -- it never runs that command;
  * a live database AHEAD of the tree (2026-09-01's revert) is reported as drift
    but does NOT hold a restart back;
  * a database that was never migrated is drift, not a pass;
  * "could not measure" is never reported as "ok" -- not when the database is
    unreachable, not when nothing is configured;
  * `apply.sh` declines the backend restart on `tree_ahead`, in the same shape
    as its uncommitted-work refusal, and says what to run.

The sqlite files here are throwaways in tmp_path. NOTHING in this module touches
the real database; the guard itself only ever issues one SELECT.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "migration_state.py"
APPLY = REPO / "scripts" / "apply.sh"
ALEMBIC_INI = REPO / "backend" / "alembic.ini"

INITIAL_REVISION = "966ddcaa35ae"          # the first migration in the tree
REVERTED_REVISION = "c5e1a8f24b03"          # 2026-09-01's; deliberately not in the tree


def _stamped(tmp_path: Path, name: str, revision: str | None) -> str:
    """A throwaway sqlite database holding just an `alembic_version` row."""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    if revision is not None:
        conn.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    else:
        conn.execute("CREATE TABLE unrelated (x int)")   # reachable, never migrated
    conn.commit()
    conn.close()
    return f"sqlite:///{path}"


def _run(url: str | None, **extra: str) -> tuple[str, str, str, int]:
    """Run the guard and split its `state<TAB>headline<TAB>remedy` line."""
    env = dict(os.environ, **extra)
    env.pop("WORKSHOP_DATABASE_URL", None)
    env.pop("WORKSHOP_DATABASE_ADMIN_URL", None)
    if url is not None:
        env["WORKSHOP_DATABASE_URL"] = url
    proc = subprocess.run([sys.executable, str(GUARD)], capture_output=True,
                          text=True, env=env, timeout=60)
    state, headline, remedy = proc.stdout.strip("\n").split("\t")
    return state, headline, remedy, proc.returncode


def test_the_tree_has_exactly_one_head() -> None:
    """Two heads and `upgrade head` has no single answer, which would make every
    comparison below meaningless rather than wrong."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    assert len(heads) == 1, f"expected one alembic head, got {heads}"


def test_live_behind_the_tree_is_tree_ahead_and_names_the_fix(tmp_path: Path) -> None:
    state, headline, remedy, code = _run(_stamped(tmp_path, "old.db", INITIAL_REVISION))
    assert state == "tree_ahead"
    assert INITIAL_REVISION in headline          # where live is
    assert "AHEAD" in headline
    assert "alembic upgrade head" in remedy      # printed, never run
    assert code == 1


def test_live_ahead_of_the_tree_is_drift_but_not_a_block(tmp_path: Path) -> None:
    """2026-09-01's revert put the database ahead of the tree. That is real drift
    and gets said, but the restart it would block is the very thing that puts the
    app back on code the database can serve."""
    state, headline, _, code = _run(_stamped(tmp_path, "ahead.db", REVERTED_REVISION))
    assert state == "live_ahead"
    assert REVERTED_REVISION in headline
    assert code == 1


def test_a_database_that_was_never_migrated_is_not_a_pass(tmp_path: Path) -> None:
    state, headline, remedy, _ = _run(_stamped(tmp_path, "empty.db", None))
    assert state == "tree_ahead"
    assert "alembic_version" in headline
    assert "alembic upgrade head" in remedy


def test_unreachable_is_unknown_and_never_ok() -> None:
    """A check that passes when it could not measure is worth less than no check."""
    state, headline, _, code = _run(
        "postgresql+psycopg://nobody:nobody@127.0.0.1:5999/nothing")
    assert state == "unknown"
    assert "COULD NOT DETERMINE" in headline
    assert code == 2


def test_no_database_configured_says_the_check_did_not_run(tmp_path: Path) -> None:
    """An installation with no database has no schema to be out of sync with --
    but it must not be told it is in sync either."""
    state, headline, _, _ = _run(None, WORKSHOP_CONFIG_DIR=str(tmp_path))
    assert state == "not_configured"
    assert "did not run" in headline
    assert "ok" not in headline.split(":")[0]


def test_the_state_line_is_exactly_three_fields(tmp_path: Path) -> None:
    """apply.sh parses this line positionally, so the shape is the contract."""
    proc = subprocess.run(
        [sys.executable, str(GUARD)], capture_output=True, text=True,
        env=dict(os.environ,
                 WORKSHOP_DATABASE_URL=_stamped(tmp_path, "x.db", INITIAL_REVISION)),
        timeout=60)
    assert len(proc.stdout.strip("\n").split("\t")) == 3
    assert proc.stdout.count("\n") == 1


@pytest.mark.skipif(not (REPO / "backend" / ".venv" / "bin" / "python").exists(),
                    reason="apply.sh reads the schema with the backend venv's python")
def test_apply_declines_the_backend_restart_when_the_tree_is_ahead(tmp_path: Path) -> None:
    """The whole point of the guard: not the report, the refusal.

    `--check` never restarts anything, so this asserts on the decision apply.sh
    prints rather than on a restart it did not perform -- and the decision line
    is the one a reader has to see.
    """
    if not shutil.which("git"):
        pytest.skip("apply.sh reads git for the uncommitted-work guard")
    proc = subprocess.run(
        ["bash", str(APPLY), "--check"], capture_output=True, text=True, cwd=REPO,
        env=dict(os.environ,
                 WORKSHOP_DATABASE_URL=_stamped(tmp_path, "old.db", INITIAL_REVISION)),
        timeout=180)
    out = proc.stdout
    assert "STALE   schema:" in out
    assert "AHEAD of the live database" in out
    # The wording changed on 2026-09-01 and the CHANGE IS THE POINT, so this
    # asserts the new frame rather than the old sentence. It used to read
    # "apply.sh does not run migrations and will not", which is true but reads
    # as a prohibition; every agent that met it handed the command back to the
    # operator instead of running it. This is a development installation and
    # applying a migration in it is ordinary work. What apply.sh actually
    # guarantees is ORDER: the migration lands before the restart, because a
    # missing column fails at query time and the import preflight cannot see it.
    assert "apply.sh does not run migrations" in out
    assert "ORDER, not permission" in out
    assert "alembic upgrade head" in out
    assert proc.returncode != 0


def test_nothing_in_the_deploy_path_runs_alembic() -> None:
    """A guard, not an auto-migrator. Applying DDL to a live database stays an
    act a person performs, having read what it will do; if this ever fails,
    something in the deploy path has learned to migrate on its own."""
    apply_sh = APPLY.read_text()
    assert "migration_state.py" in apply_sh
    assert "alembic upgrade" not in apply_sh   # the command is the guard's to PRINT

    guard = GUARD.read_text()
    assert "import subprocess" not in guard
    assert "from alembic import command" not in guard
    # The one place the upgrade command may appear is the string it hands back.
    assert guard.count("alembic upgrade head") == 1

    restart = (REPO / "scripts" / "restart_backend.sh").read_text()
    assert "alembic upgrade" not in restart
