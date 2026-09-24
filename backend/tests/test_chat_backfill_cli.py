"""`db.chat_backfill` prints a plan unless told `--apply`, like its siblings.

`db.backfill` and `db.app_backfill` print a plan by default and write with
`--apply`; this one wrote by default and dry-ran only with `--plan`, so the same
habit did opposite things across the three. `--plan` is still accepted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("sqlalchemy")

from db import chat_backfill  # noqa: E402


@pytest.fixture()
def run(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_backfill, "plan", lambda: calls.append("plan") or {})
    monkeypatch.setattr(chat_backfill, "apply", lambda: calls.append("apply") or {})

    def go(*argv):
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["db.chat_backfill", *argv])
        assert chat_backfill.main() == 0
        return list(calls)
    return go


def test_no_flag_only_plans(run):
    assert run() == ["plan"]


def test_plan_is_still_accepted_and_only_plans(run):
    assert run("--plan") == ["plan"]


def test_apply_writes(run):
    assert run("--apply") == ["apply"]
