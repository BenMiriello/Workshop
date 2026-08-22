"""A pinned ratchet baseline may not be loosened by the diff under review.

The hole this closes, found by watching the loop take it on 2026-08-22: every
ratchet reads its baseline out of the WORKING TREE, so a worker that rewrites
the baseline rather than fixing the regression looks, to the checker, exactly
like a clean tree. Commit 971f7f3 is the real instance.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "check_ratchets_not_loosened.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _guard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_the_real_historical_loosening_is_caught():
    """971f7f3 re-pinned five metrics upward to make a failing gate pass. The
    guard must flag exactly those, against the commit before it."""
    out = _guard("--baseline", "971f7f3^", "--json")
    if "unknown revision" in out.stderr or out.returncode not in (0, 1):
        pytest.skip("971f7f3 not present in this checkout")
    flagged = {(f["file"].split("/")[-1], f["metric"]): (f["was"], f["now"])
               for f in json.loads(out.stdout)["loosened"]}
    assert flagged[("module_layout_baseline.json", "function_local_imports")] == (452, 552)
    assert flagged[("module_layout_baseline.json", "largest_scc_size")] == (58, 110)
    assert flagged[("module_layout_baseline.json", "upward_violations")] == (42, 48)
    assert flagged[("module_layout_baseline.json", "big_file_count")] == (21, 22)
    assert flagged[("no_process_globals_baseline.json", "mutable_global_count")] == (60, 63)


def test_a_metric_that_went_DOWN_is_not_flagged():
    """scc_count went 3 -> 1 in that same commit. Tightening is the point of a
    ratchet and must never be reported as a loosening."""
    out = _guard("--baseline", "971f7f3^", "--json")
    if out.returncode not in (0, 1):
        pytest.skip("971f7f3 not present in this checkout")
    metrics = {f["metric"] for f in json.loads(out.stdout)["loosened"]}
    assert "scc_count" not in metrics


def test_provenance_keys_are_not_treated_as_measurements():
    """`_measured_against` went 223 -> 244 in the same commit. Underscore-
    prefixed keys are the files' own comments and provenance, not metrics."""
    out = _guard("--baseline", "971f7f3^", "--json")
    if out.returncode not in (0, 1):
        pytest.skip("971f7f3 not present in this checkout")
    metrics = {f["metric"] for f in json.loads(out.stdout)["loosened"]}
    assert not any(m.startswith("_") for m in metrics), metrics


def test_the_tree_as_committed_is_clean():
    """Guards against pinning a gate that fires on its own repo."""
    out = _guard("--baseline", "HEAD")
    assert out.returncode == 0, out.stdout


def test_direction_is_imported_not_restated():
    """check_module_layout owns the per-metric direction map and its header
    warns a future metric could be higher-is-better. A second copy here would be
    the exact drift this repo is trying to remove."""
    src = GUARD.read_text()
    assert "check_module_layout._HIGHER_IS_WORSE" in src
    assert "_HIGHER_IS_WORSE = {" not in src


def test_an_allowlist_that_grows_counts_as_loosening(tmp_path, monkeypatch):
    """Several baselines are allowlists of tolerated violations rather than
    counts, so 'more entries' is the same loosening as 'a bigger number'."""
    import check_ratchets_not_loosened as guard
    assert guard._size([1, 2, 3]) == 3
    assert guard._size({"a": 1}) == 1
    assert guard._size(7) == 7
    assert guard._size(True) is None, "a bool is a flag, not a count"
    assert guard._size(None) is None
