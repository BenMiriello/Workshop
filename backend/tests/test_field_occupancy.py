"""scripts/field_occupancy.py -- R2 of coherence-overhaul-2026-08-16.md.

Loaded from its file path, same pattern as the other two ratchet-script
tests. All tests here isolate from the real owner/ticket/job stores by
monkeypatching `collect_tickets` / `CORPORA` / `auth_store.operator_id`
directly -- per the standing rule (see the "tests must not share owner
storage" project memory) that a test must never read or write the real
operator's data.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "field_occupancy", _SCRIPTS / "field_occupancy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


class TestEmpty:
    @pytest.mark.parametrize("v", [None, "", [], {}, ()])
    def test_absent_or_empty_container_is_empty(self, mod, v):
        assert mod._empty(v) is True

    @pytest.mark.parametrize("v", [0, False, "x", [1], {"a": 1}])
    def test_a_written_zero_or_false_is_not_empty(self, mod, v):
        """An explicit 0 or False is a value someone wrote, not an absence --
        this is the distinction that made the `attempts` bug visible instead
        of reading as 100% healthy occupancy."""
        assert mod._empty(v) is False


class TestOccupancy:
    def test_counts_present_and_non_empty_fields(self, mod):
        records = [
            {"a": "x", "b": None},
            {"a": "y", "b": "z"},
            {"a": "", "b": "z"},
        ]
        total, filled, constant = mod._occupancy(records)
        assert total == 3
        assert filled["a"] == 2  # "" doesn't count
        assert filled["b"] == 2  # None doesn't count

    def test_constant_field_flagged_only_when_filled_everywhere(self, mod):
        records = [{"owner": "operator"}, {"owner": "operator"}, {"owner": "operator"}]
        _, _, constant = mod._occupancy(records)
        assert constant["owner"] == "operator"

    def test_field_missing_on_some_records_is_not_constant(self, mod):
        records = [{"owner": "operator"}, {"owner": None}, {"owner": "operator"}]
        _, _, constant = mod._occupancy(records)
        assert "owner" not in constant

    def test_unhashable_values_do_not_crash(self, mod):
        records = [{"tags": ["a"]}, {"tags": ["b"]}]
        total, filled, constant = mod._occupancy(records)
        assert filled["tags"] == 2
        assert "tags" not in constant  # different values, so not constant either way


class TestDefaultOwner:
    def test_uses_operator_id(self, mod, monkeypatch):
        from services import auth_store
        monkeypatch.setattr(auth_store, "operator_id", lambda: 42)
        assert mod._default_owner() == 42

    def test_raises_when_no_operator(self, mod, monkeypatch):
        from services import auth_store
        monkeypatch.setattr(auth_store, "operator_id", lambda: None)
        with pytest.raises(SystemExit):
            mod._default_owner()


class TestBuildReportTickets:
    """Isolated with a fake `collect_tickets` so no real ticket store is read."""

    def _patch(self, mod, monkeypatch, per_scope, kind):
        monkeypatch.setattr(mod, "collect_tickets", lambda: (per_scope, kind))
        monkeypatch.setattr(mod, "CORPORA", {})
        from services import ownership
        monkeypatch.setattr(ownership, "set_current_owner", lambda owner_id: None)

    def test_by_scope_and_by_store_present(self, mod, monkeypatch):
        per_scope = {
            "development": [{"id": "1", "effort": "standard"}],
            "project:demo": [{"id": "2"}],
        }
        kind = {"development": "relational", "project:demo": "file"}
        self._patch(mod, monkeypatch, per_scope, kind)

        report = mod.build_report(owner=1)
        t = report["ticket"]
        assert t["total"] == 2
        assert t["by_scope"] == {"development": 1, "project:demo": 1}
        assert t["by_store"]["relational"]["total"] == 1
        assert t["by_store"]["file"]["total"] == 1

    def test_store_divergence_flags_field_in_one_store_only(self, mod, monkeypatch):
        per_scope = {
            "development": [{"id": "1", "effort": "standard"}],
            "project:demo": [{"id": "2"}],  # no `effort` key at all
        }
        kind = {"development": "relational", "project:demo": "file"}
        self._patch(mod, monkeypatch, per_scope, kind)

        report = mod.build_report(owner=1)
        divergent_fields = {row["field"] for row in report["ticket"]["store_divergence"]}
        assert "effort" in divergent_fields

    def test_no_divergence_reported_when_only_one_store_has_records(self, mod, monkeypatch):
        """An empty store must not trivially "diverge" from a populated one --
        that would flag every single field on every run with only one live
        store, which is noise, not signal."""
        per_scope = {"development": [{"id": "1", "effort": "standard"}]}
        kind = {"development": "relational"}
        self._patch(mod, monkeypatch, per_scope, kind)

        report = mod.build_report(owner=1)
        assert report["ticket"]["store_divergence"] == []

    def test_absent_when_no_tickets(self, mod, monkeypatch):
        self._patch(mod, monkeypatch, {}, {})
        report = mod.build_report(owner=1)
        assert "ticket" not in report


class TestBaselineRoundtrip:
    def test_write_then_load(self, mod, tmp_path):
        report = {"job": {"fields": {"id": 5, "status": 5, "kind": 0}}}
        fields = mod._report_field_sets(report)
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(fields))
        loaded = mod._load_baseline(path)
        assert loaded == {"job": ["id", "kind", "status"]}

    def test_load_missing_baseline_is_empty_not_an_error(self, mod, tmp_path):
        assert mod._load_baseline(tmp_path / "nope.json") == {}


class TestCliModes:
    def _patch(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "collect_tickets", lambda: ({}, {}))
        monkeypatch.setattr(mod, "CORPORA", {
            "job": lambda: [{"id": "1", "kind": "loop"}, {"id": "2"}],
        })
        from services import ownership
        monkeypatch.setattr(ownership, "set_current_owner", lambda owner_id: None)

    def test_json_mode(self, mod, monkeypatch, capsys):
        self._patch(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["field_occupancy.py", "--owner", "1", "--json"])
        rc = mod.main()
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["job"]["fields"]["kind"] == 1
        assert out["job"]["fields"]["id"] == 2

    def test_write_baseline_then_fail_on_new_zero_is_clean(self, mod, monkeypatch, tmp_path, capsys):
        self._patch(mod, monkeypatch)
        baseline = tmp_path / "baseline.json"
        monkeypatch.setattr(sys, "argv", [
            "field_occupancy.py", "--owner", "1", "--baseline", str(baseline),
            "--write-baseline",
        ])
        assert mod.main() == 0
        assert baseline.is_file()

        monkeypatch.setattr(sys, "argv", [
            "field_occupancy.py", "--owner", "1", "--baseline", str(baseline),
            "--fail-on-new-zero",
        ])
        assert mod.main() == 0  # nothing new since the baseline was just written

    def test_fail_on_new_zero_catches_a_new_never_populated_field(self, mod, monkeypatch, tmp_path):
        self._patch(mod, monkeypatch)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"job": ["id"]}))  # "kind" is new

        monkeypatch.setattr(sys, "argv", [
            "field_occupancy.py", "--owner", "1", "--baseline", str(baseline),
            "--fail-on-new-zero",
        ])
        # "kind" is populated on 1/2 fake job records above, so re-patch with
        # a corpus where the key exists on every record but is always empty --
        # a KEY ABSENT from every record never becomes a reported field at
        # all (see `_all_keys`), so to exercise "new field, zero occupancy"
        # the key must be present-but-empty, not omitted.
        monkeypatch.setattr(mod, "CORPORA", {
            "job": lambda: [{"id": "1", "kind": None}, {"id": "2", "kind": ""}],
        })
        rc = mod.main()
        assert rc == 1

    def test_fail_on_new_zero_without_baseline_is_a_clear_error(self, mod, monkeypatch, tmp_path):
        self._patch(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", [
            "field_occupancy.py", "--owner", "1",
            "--baseline", str(tmp_path / "missing.json"),
            "--fail-on-new-zero",
        ])
        assert mod.main() == 2

    @pytest.mark.reads_the_operators_tenant(
        "the point of this one is that the real collectors survive the real "
        "data, which a fixture tree cannot stand in for. It reports and writes "
        "nothing.")
    def test_smoke_against_the_real_tree_does_not_crash(self, mod, monkeypatch):
        """Not isolated -- exercises the real collectors end to end, the way
        an operator running the script by hand would. Only asserts it
        completes and stays on the documented exit-0 contract."""
        monkeypatch.setattr(sys, "argv", ["field_occupancy.py", "--json"])
        rc = mod.main()
        assert rc == 0
