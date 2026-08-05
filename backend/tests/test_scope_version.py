"""The scope change counter, and the two things that read it.

What matters here is the ECONOMICS, not the mechanism. Learning "nothing has
changed" has to be free, because that is the answer almost every time; only a
real change is allowed to cost anything. So these tests pin that silence is the
default, that a change is announced exactly once, and that the reverse mapping
from a task store back to its scope is exact — a wrong mapping would announce
one scope's work in another's chat, which is worse than announcing nothing.
"""
import json

import pytest

from services import scope_version, task_scope


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "scope-versions.json"
    monkeypatch.setattr(scope_version, "STORE", p)
    return p


def test_unrecorded_scope_is_version_zero(store):
    assert scope_version.current("project:nothing-here") == 0


def test_bump_increments_and_records_what_moved(store):
    assert scope_version.bump("project:birds", "ticket", "3 tickets") == 1
    assert scope_version.bump("project:birds", "note", "a finding") == 2
    d = scope_version.describe("project:birds")
    assert d["version"] == 2
    assert d["kind"] == "note"
    assert d["detail"] == "a finding"
    assert d["at"]


def test_scopes_do_not_bleed_into_each_other(store):
    scope_version.bump("project:birds", "ticket")
    assert scope_version.current("project:birds") == 1
    assert scope_version.current("project:budget") == 0


def test_a_reader_that_is_current_sees_no_change(store):
    scope_version.bump("workshop", "ticket")
    seen = scope_version.current("workshop")
    assert scope_version.changed_since("workshop", seen) is False


def test_a_reader_that_is_behind_sees_the_change(store):
    scope_version.bump("workshop", "ticket")
    assert scope_version.changed_since("workshop", 0) is True


def test_a_reader_with_no_memory_looks_once(store):
    """An unset or corrupt mark must read as behind, not as current: a chat that
    has never checked should look, rather than silently assume it is up to date."""
    scope_version.bump("workshop", "ticket")
    assert scope_version.changed_since("workshop", None) is True
    assert scope_version.changed_since("workshop", "not-a-number") is True


def test_no_change_recorded_means_nothing_to_report(store):
    """The counter being absent must NOT read as 'behind' — otherwise every chat
    in a quiet scope reports an update forever."""
    assert scope_version.changed_since("workshop", None) is False


def test_an_unwritable_store_never_raises(tmp_path, monkeypatch):
    """A counter is an optimisation. If it cannot be written, the caller that
    just wrote a note to disk must still succeed."""
    monkeypatch.setattr(scope_version, "STORE", tmp_path / "nodir" / "x" / "v.json")
    monkeypatch.setattr(scope_version.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert scope_version.bump("workshop", "note") == 1  # returns, does not raise


def test_corrupt_store_reads_as_empty(store):
    store.write_text("{not json")
    assert scope_version.current("workshop") == 0


class TestScopeRoundTrip:
    """key_for_root is the inverse of root_for_context. They are written apart
    and must agree, because the bump side uses one and the read side the other."""

    def test_workshop_root(self):
        r = task_scope.root_for_context("workshop")
        assert task_scope.key_for_root(r) == "workshop"

    def test_general_shares_the_workshop_queue(self):
        assert task_scope.root_for_context("general") == task_scope.root_for_context("workshop")
        assert task_scope.key_for_root(task_scope.root_for_context("general")) == "workshop"

    def test_project(self, tmp_path):
        assert task_scope.key_for_root(tmp_path / "projects" / "birds") == "project:birds"

    def test_agent_scope(self, tmp_path):
        assert task_scope.key_for_root(tmp_path / "agent-scopes" / "memory-keeper") == "agent:memory-keeper"

    def test_an_unrelated_path_maps_to_nothing(self, tmp_path):
        assert task_scope.key_for_root(tmp_path / "somewhere" / "else") is None

    def test_none_maps_to_nothing(self):
        assert task_scope.key_for_root(None) is None
