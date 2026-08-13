"""What a run cost, on the run page.

Phase 2 of the automation plan judges K watched runs on wall time and token
spend. The ledger has carried `job_id` on every row since it was written and
nothing ever read them back that way, so answering "what did this run cost"
meant grepping usage.jsonl by hand. A number nobody can see does not get used.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import usage_ledger  # noqa: E402


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", tmp_path / "usage.jsonl")
    return tmp_path


def test_sums_the_rows_for_one_job(ledger) -> None:
    usage_ledger.record("development", 100, job_id="j1")
    usage_ledger.record("development", 250, job_id="j1")
    usage_ledger.record("development", 999, job_id="other")
    assert usage_ledger.usage_for_jobs(["j1"]) == {"j1": 350}


def test_a_job_with_no_rows_reports_zero_not_missing(ledger) -> None:
    """The caller renders "no cost recorded" from a zero. A missing key would
    make it an undefined lookup at the call site instead."""
    usage_ledger.record("development", 100, job_id="j1")
    assert usage_ledger.usage_for_jobs(["j1", "j2"]) == {"j1": 100, "j2": 0}


def test_batched_so_a_run_page_costs_one_read(ledger) -> None:
    for i in range(3):
        usage_ledger.record("development", 10, job_id=f"kid{i}")
    usage_ledger.record("development", 5, job_id="parent")
    got = usage_ledger.usage_for_jobs(["parent", "kid0", "kid1", "kid2"])
    assert sum(got.values()) == 35
    assert got["parent"] == 5


def test_no_ids_is_not_a_whole_ledger_scan(ledger) -> None:
    usage_ledger.record("development", 100, job_id="j1")
    assert usage_ledger.usage_for_jobs([]) == {}
    assert usage_ledger.usage_for_jobs([None, ""]) == {}


def test_a_row_with_a_junk_token_count_does_not_break_the_total(ledger) -> None:
    usage_ledger.record("development", 100, job_id="j1")
    with usage_ledger.LEDGER_PATH.open("a") as f:
        f.write('{"job_id": "j1", "tokens": "lots"}\n')
        f.write('not json at all\n')
    assert usage_ledger.usage_for_jobs(["j1"]) == {"j1": 100}
