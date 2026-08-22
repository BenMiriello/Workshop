"""Per-scope execution mode + its gate on the unattended sweep."""
import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    # Point the JSON store at a throwaway tree.
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    from services import scope_execution
    import importlib
    importlib.reload(scope_execution)
    return scope_execution


def test_default_is_reactive_and_fail_closed(store):
    scope = "project:some-scope"
    assert store.get_mode(scope) == "reactive"
    assert store.is_armed(scope) is False
    assert store.stage_ceiling(scope) is None


def test_set_and_read_modes(store):
    scope = "project:some-scope"
    assert store.set_mode(scope, "supervised") == "supervised"
    assert store.get_mode(scope) == "supervised"
    assert store.is_armed(scope) is True
    assert store.stage_ceiling(scope) == "in_review"

    store.set_mode(scope, "autonomous")
    assert store.stage_ceiling(scope) == "done"


def test_reactive_is_stored_as_absence(store):
    scope = "project:some-scope"
    store.set_mode(scope, "supervised")
    store.set_mode(scope, "reactive")  # back to default
    # The default is represented by absence, so the file does not accumulate keys
    # for scopes the operator has not deliberately armed.
    assert store._read() == {}
    assert store.is_armed(scope) is False


def test_unknown_mode_rejected(store):
    with pytest.raises(ValueError):
        store.set_mode("project:s", "yolo")


def test_unattended_is_gated_by_scope_mode(monkeypatch, tmp_path):
    """A reactive scope yields no unattended work, and the gate short-circuits
    BEFORE loading tasks. An armed scope proceeds."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    from services import tasks, scope_execution

    reached = {"reconcile": False}
    monkeypatch.setattr(tasks, "reconcile", lambda root: reached.__setitem__("reconcile", True))
    monkeypatch.setattr(tasks, "reclaim", lambda root: None)
    monkeypatch.setattr(tasks, "_load", lambda root: [])

    # reactive (default): returns None and never reaches reconcile/_load.
    monkeypatch.setattr(scope_execution, "is_armed_root", lambda root: False)
    assert tasks.next_ready(tmp_path, unattended=True) is None
    assert reached["reconcile"] is False

    # armed: proceeds past the gate (reconcile runs); None only because no tasks.
    monkeypatch.setattr(scope_execution, "is_armed_root", lambda root: True)
    assert tasks.next_ready(tmp_path, unattended=True) is None
    assert reached["reconcile"] is True


def test_attended_path_is_never_gated(monkeypatch, tmp_path):
    """readiness and arming must never refuse a human: unattended=False proceeds
    even for a reactive scope."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    from services import tasks, scope_execution

    reached = {"reconcile": False}
    monkeypatch.setattr(tasks, "reconcile", lambda root: reached.__setitem__("reconcile", True))
    monkeypatch.setattr(tasks, "reclaim", lambda root: None)
    monkeypatch.setattr(tasks, "_load", lambda root: [])
    monkeypatch.setattr(scope_execution, "is_armed_root", lambda root: False)  # reactive

    assert tasks.next_ready(tmp_path, unattended=False) is None
    assert reached["reconcile"] is True  # the human path was not short-circuited
