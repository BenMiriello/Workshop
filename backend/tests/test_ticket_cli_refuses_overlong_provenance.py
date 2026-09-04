"""`--provenance` is a key, and the CLI says so instead of crashing.

It lands in `ticket.spec_hash`, `character varying(64)`. A longer value used to
reach psycopg, which raised StringDataRightTruncation and printed the whole
INSERT with every parameter inlined -- including the ticket body it had just
been handed. The filing was lost and the reason was buried in thirty lines of
SQL. Hit for real on 2026-09-04 while filing a ticket from a chat.

A stack trace is not an error message, and this is the surface an agent types
at: the refusal names the limit and the actual length, so the next attempt is a
correction rather than a guess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "project_tasks.py"


def _add(provenance: str) -> tuple[int, dict]:
    r = subprocess.run(
        [sys.executable, str(CLI), "add", str(ROOT), "a probe that must not be filed",
         "--acceptance-cmd", "pytest -q", "--provenance", provenance],
        capture_output=True, text=True, cwd=ROOT,
    )
    try:
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return r.returncode, {"_stdout": r.stdout, "_stderr": r.stderr}


def test_an_overlong_provenance_is_refused_by_name():
    code, out = _add("x" * 80)
    assert code == 2, out
    assert "provenance" in out.get("error", ""), out
    # The two numbers a person needs in order to fix it, not a traceback.
    assert "80" in out["error"] and "64" in out["error"], out
    assert out.get("fix"), "a refusal without a fix is a wall"


def test_the_refusal_does_not_leak_the_sql_or_the_body():
    """The failure this replaces printed the INSERT and every bound parameter."""
    code, out = _add("x" * 80)
    blob = json.dumps(out)
    assert "INSERT INTO" not in blob
    assert "a probe that must not be filed" not in blob


def test_the_limit_is_read_from_one_place():
    """Spelled once, so the message cannot drift from the column."""
    src = (ROOT / "scripts" / "project_tasks.py").read_text()
    assert "_PROVENANCE_MAX = 64" in src
    assert src.count("_PROVENANCE_MAX") >= 3  # definition, guard, message
