"""Zero apps mounted must not be able to look like a fresh clone.

MEASURED. On 2026-09-04 every app route on this installation answered 404 for
two days. `/healthz` said `ok`, `apply.sh --check` said `in sync`, the launcher
showed an empty shelf -- which is also exactly what a correct fresh clone shows
-- and the logs said nothing at all. The source was fine: the same tree,
imported, registered every app route.

What differed was WHICH FILE the running process read as its installed-apps
roster. That path is anchored to the module's own location, so two checkouts of
one source on one host resolve two different `.runtime/` directories, and
nothing the process served ever printed the one it chose. It could not be
settled from outside either: an agent in a sandbox cannot read `/proc/<pid>/cwd`
or `/proc/<pid>/environ`, so every check measured the checker's own filesystem.

The rule these tests pin, in one sentence: THE PROCESS SAYS WHAT IT MOUNTED AND
FROM WHERE, an empty instance says it quietly, and a roster that names apps the
process did not mount says it loudly.

It names no app: every roster and catalog here is written by the test.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# MODULE SCOPE, deliberately. A `from services import ...` inside each test is
# the habit in this suite, but `scripts/check_module_layout.py` counts
# function-local imports across `backend/` including `tests/`, and a new file
# full of them is a ratchet regression for no benefit: monkeypatching an
# attribute of an already-imported module works exactly the same either way.
from main import app
from services import app_mount_record, installed_apps
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


# --- what the process records at mount time -----------------------------------

def test_the_record_names_the_resolved_roster_path(monkeypatch, tmp_path):
    """The one fact nobody could get from outside the process."""
    roster = tmp_path / "installed-apps.json"
    roster.write_text(json.dumps({"installed": []}), encoding="utf-8")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", roster)

    rec = app_mount_record.build([], 0)
    assert rec["registry"] == str(roster)
    assert rec["registry_present"] is True


def test_an_absent_roster_is_reported_as_absent(monkeypatch, tmp_path):
    """`registry_present: false` with a path is what tells "this process never
    saw the file you are looking at" from "the file says nothing is installed"."""
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "nope.json")
    rec = app_mount_record.build([], 0)
    assert rec["registry_present"] is False
    assert rec["installed"] == []


def test_a_blank_instance_records_and_logs_quietly(monkeypatch, tmp_path, caplog):
    """The fresh-clone case. It must boot, must record, and must not shout --
    `scripts/check_fresh_clone.sh` measures a stranger's clone booting clean."""
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", tmp_path / "nope.json")
    with caplog.at_level("INFO"):
        rec = app_mount_record.build([], 0)
    assert rec["installed"] == [] and rec["mounted"] == []
    assert rec["unresolved"] == []
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]


def test_a_roster_that_mounted_nothing_is_an_error_naming_the_path_and_the_ids(
        monkeypatch, caplog):
    """The outage, and the only state here that cannot be an intentional one."""
    monkeypatch.setattr(installed_apps, "installed_app_ids",
                        lambda: ["ghost-a", "ghost-b"])
    with caplog.at_level("INFO"):
        rec = app_mount_record.build([], 0)

    assert rec["unresolved"] == ["ghost-a", "ghost-b"]
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    msg = errors[0]
    assert "ghost-a" in msg and "ghost-b" in msg
    assert str(installed_apps.REGISTRY_PATH) in msg


def test_a_partial_mount_is_an_error_too(monkeypatch, caplog):
    """Seven of eight is an outage for the eighth, and used to log nothing."""
    monkeypatch.setattr(installed_apps, "installed_app_ids",
                        lambda: ["ghost-a", "ghost-b"])
    with caplog.at_level("INFO"):
        rec = app_mount_record.build(["ghost-a"], 4)

    assert rec["unresolved"] == ["ghost-b"]
    assert any(r.levelname == "ERROR" and "ghost-b" in r.getMessage()
               for r in caplog.records)


def test_an_unreadable_roster_is_recorded_rather_than_raised(monkeypatch, tmp_path):
    """`/healthz` must answer even when the roster is corrupt: an endpoint that
    500s tells the caller less than one that names the fault."""
    roster = tmp_path / "installed-apps.json"
    roster.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", roster)

    rec = app_mount_record.build([], 0)
    assert rec["error"] and str(roster) in rec["error"]


def test_mounted_is_null_before_the_mounting_pass():
    """Null is a real answer: "this app has not mounted yet". Nothing may read
    it as zero apps, which is why it is not `[]`."""
    rec = app_mount_record.unmounted()
    assert rec["mounted"] is None
    assert rec["unresolved"] is None
    assert rec["registry"]


def test_the_record_is_a_value_and_not_module_state():
    """No process global. The record describes ONE app object's mounting pass,
    so it is returned and held by the caller -- under more than one uvicorn
    worker a module global would let one worker serve another's answer."""
    assert not [n for n, v in vars(app_mount_record).items()
                if n.isupper() and isinstance(v, (dict, list, set))]


def test_the_record_carries_ids_and_a_path_and_nothing_else(monkeypatch, tmp_path):
    """`/healthz` is UNAUTHENTICATED. The record may disclose app ids and one
    filesystem path -- the same disclosure the launcher already makes -- and
    must not grow a route table, a configuration dump or any data."""
    roster = tmp_path / "installed-apps.json"
    roster.write_text(json.dumps({"installed": ["ghost-a"]}), encoding="utf-8")
    monkeypatch.setattr(installed_apps, "REGISTRY_PATH", roster)
    rec = app_mount_record.build(["ghost-a"], 3)

    assert set(rec) == {"registry", "registry_present", "installed", "mounted",
                        "unresolved", "catalog", "routes", "error"}
    assert set(rec) == set(app_mount_record.unmounted())
    for key in ("installed", "mounted", "unresolved", "catalog"):
        assert all(isinstance(i, str) for i in rec[key])


# --- what apply.sh reads off it -----------------------------------------------

@pytest.fixture()
def state_mod():
    """`scripts/backend_apps_state.py`, imported by path (scripts/ is not a
    package and must not become one just to be tested)."""
    path = SCRIPTS / "backend_apps_state.py"
    spec = importlib.util.spec_from_file_location("backend_apps_state", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend_apps_state"] = mod
    spec.loader.exec_module(mod)
    return mod


def _health(**apps):
    base = {"registry": "/somewhere/.runtime/installed-apps.json",
            "registry_present": True, "installed": [], "mounted": [],
            "unresolved": [], "catalog": [], "routes": 0, "error": None}
    base.update(apps)
    return {"ok": True, "source_id": "abc", "apps": base}


def test_state_a_blank_instance_agreeing_with_a_blank_tree_is_ok(state_mod):
    state, _, _ = state_mod.verdict(_health(), [], None)
    assert state == "ok"


def test_state_a_full_mount_agreeing_with_the_tree_is_ok(state_mod):
    h = _health(installed=["ghost-a"], mounted=["ghost-a"], catalog=["ghost-a"],
                routes=7)
    assert state_mod.verdict(h, ["ghost-a"], None)[0] == "ok"


def test_state_no_tree_roster_at_all_is_still_ok(state_mod):
    """A linked worktree and a fresh clone both legitimately have no roster;
    there is then nothing to compare, which is not a fault."""
    h = _health(installed=["ghost-a"], mounted=["ghost-a"], catalog=["ghost-a"])
    assert state_mod.verdict(h, None, None)[0] == "ok"


def test_state_a_roster_the_process_did_not_mount_is_unresolved(state_mod):
    h = _health(installed=["ghost-a", "ghost-b"], mounted=[],
                unresolved=["ghost-a", "ghost-b"], catalog=[])
    state, msg, fix = state_mod.verdict(h, ["ghost-a", "ghost-b"], None)
    assert state == "unresolved"
    assert "ghost-a" in msg and "404" in msg
    assert fix


def test_state_two_checkouts_disagreeing_is_the_outage_that_happened(state_mod):
    """The process is internally consistent -- it read an empty roster and
    mounted nothing, and by itself that is a legitimate blank instance. The
    fault is only visible against the roster this tree holds."""
    h = _health(registry="/elsewhere/.runtime/installed-apps.json",
                registry_present=False, installed=[], mounted=[], catalog=[])
    state, msg, fix = state_mod.verdict(h, ["ghost-a", "ghost-b"], None)
    assert state == "roster_differs"
    assert "/elsewhere/.runtime/installed-apps.json" in msg
    assert "ghost-a" in msg and "ghost-b" in msg
    assert fix


def test_state_a_backend_without_the_record_is_never_in_sync(state_mod):
    """The pre-fix backend. "Cannot tell" is its own exit code and its own
    word, because a check that silently passes when it could not measure is the
    failure this whole ticket is about."""
    state, _, fix = state_mod.verdict({"ok": True, "source_id": "abc"}, [], None)
    assert state == "unreported"
    assert state_mod.EXIT[state] == 2
    assert fix


def test_state_an_unreachable_backend_is_not_in_sync_either(state_mod):
    assert state_mod.verdict(None, [], None)[0] == "unreachable"
    assert state_mod.EXIT["unreachable"] == 2


def test_state_an_unreadable_roster_on_either_side_is_reported(state_mod):
    assert state_mod.verdict(_health(error="boom"), [], None)[0] == "unreadable"
    h = _health(installed=["ghost-a"], mounted=["ghost-a"], catalog=["ghost-a"])
    assert state_mod.verdict(h, None, "not json")[0] == "unreadable"


def test_state_only_ok_exits_zero(state_mod):
    assert state_mod.EXIT["ok"] == 0
    assert all(v != 0 for k, v in state_mod.EXIT.items() if k != "ok")


# --- and the endpoint anyone outside the namespace can actually reach ---------

def test_healthz_serves_the_record_without_authentication():
    """The endpoint is the deliverable. The record can be perfect and still
    settle nothing if the only way to read it is from inside the process, which
    is precisely the position nobody investigating the outage was in.

    Unauthenticated on purpose, and asserted here so nobody moves it behind a
    gate: the caller that needs it most is a script on a box that has no session.
    """
    body = TestClient(app).get("/healthz").json()
    rec = body["apps"]
    assert rec is app.state.apps or rec == app.state.apps, (
        "/healthz must serve the record made at mount time, not one recomputed "
        "per request: a re-read answers what the roster says NOW, which is a "
        "different question from what this process loaded at boot")
    assert rec["mounted"] is not None, "the mounting pass never recorded"
    assert rec["registry"], "the resolved roster path is the point"
    assert rec["unresolved"] == [], (
        "this instance's roster names apps it did not mount: "
        f"{rec['unresolved']} (roster {rec['registry']})")
