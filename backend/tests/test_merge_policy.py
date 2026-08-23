"""The per-scope merge policy is off, and stays off unless it is turned on.

Step C7 of `docs/platform-redesign/changes-and-review-2026-08-22.md` sec 6.

The claim under test is not "the setting round-trips" -- that is the easy half.
It is that EVERY way of not knowing the answer produces the same answer:
`manual`. A merge policy read from a file that does not exist, will not parse,
is not an object, or holds a word this build has never heard of must all mean
"a person merges this", because the alternative is code landing unreviewed
because a settings file got truncated.
"""
from __future__ import annotations

import json

import pytest

from services import merge_policy


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Point the store at a throwaway .runtime, the way test_work_driver does
    for scope_execution. Never the instance's own file: this suite writes."""
    path = tmp_path / ".runtime" / "merge-policy.json"
    monkeypatch.setattr(merge_policy, "_store_path", lambda: path)
    return path


# --- the default ------------------------------------------------------------

def test_a_fresh_instance_merges_nothing_by_itself(store):
    """No file at all is the state of every deployment until somebody acts."""
    assert not store.exists()
    assert merge_policy.get_policy("development") == "manual"
    assert merge_policy.allows_auto_merge("development") is False
    assert merge_policy.requires_low_risk("development") is False
    assert merge_policy.configured_scopes() == []


def test_the_shipped_tree_has_no_scope_configured():
    """The real store, not a fixture: the boundary on this work was to build the
    policy and enable it NOWHERE. If this ever fails, something turned it on."""
    assert merge_policy.configured_scopes() == [], (
        "a scope has a non-default merge policy in this tree; C7 was built "
        "fail-closed and enabling one is the operator's deliberate act")


def test_an_unnamed_scope_is_manual(store):
    assert merge_policy.get_policy("") == "manual"


# --- every unreadable state is manual ---------------------------------------

def test_a_policy_file_that_will_not_parse_reads_as_manual(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ this is not json")
    assert merge_policy.get_policy("development") == "manual"
    assert merge_policy.allows_auto_merge("development") is False


def test_a_policy_file_that_is_not_an_object_reads_as_manual(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(["development", "auto_when_green"]))
    assert merge_policy.get_policy("development") == "manual"


def test_a_policy_file_that_cannot_be_OPENED_reads_as_manual(store):
    """A directory where the file should be. Stands in for every OSError the
    real world produces -- a permission change, a bad mount, a half-written
    atomic replace -- and the point is that the read answers rather than
    raising: a caller deciding whether to merge must never have to hold an
    `except` around the question."""
    store.parent.mkdir(parents=True, exist_ok=True)
    store.mkdir()
    assert merge_policy.get_policy("development") == "manual"
    assert merge_policy.allows_auto_merge("development") is False


def test_an_unknown_policy_word_reads_as_manual(store):
    """The forward-compatibility case: a file written by a newer build, or by
    hand. Guessing which way "auto_when_probably_fine" leans is exactly the
    thing this module refuses to do."""
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"development": "auto_when_probably_fine"}))
    assert merge_policy.get_policy("development") == "manual"
    assert merge_policy.allows_auto_merge("development") is False


def test_a_non_string_policy_reads_as_manual(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"development": True}))
    assert merge_policy.get_policy("development") == "manual"


# --- setting it -------------------------------------------------------------

def test_setting_a_policy_round_trips_and_is_scoped(store):
    merge_policy.set_policy("project:alpha", "auto_when_green")
    assert merge_policy.get_policy("project:alpha") == "auto_when_green"
    assert merge_policy.allows_auto_merge("project:alpha") is True
    assert merge_policy.requires_low_risk("project:alpha") is False
    # and it says nothing about anybody else
    assert merge_policy.get_policy("project:beta") == "manual"


def test_the_low_risk_policy_says_so(store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    assert merge_policy.requires_low_risk("project:alpha") is True


def test_setting_it_back_to_manual_removes_the_key(store):
    """Absence already means manual, so the file stays a list of exactly the
    scopes the operator opened up -- which is the list he audits."""
    merge_policy.set_policy("project:alpha", "auto_when_green")
    merge_policy.set_policy("project:alpha", "manual")
    assert json.loads(store.read_text()) == {}
    assert merge_policy.configured_scopes() == []


def test_an_unknown_policy_cannot_be_SET(store):
    with pytest.raises(ValueError):
        merge_policy.set_policy("project:alpha", "auto_when_probably_fine")
    assert not store.exists()


def test_a_policy_needs_a_scope(store):
    with pytest.raises(ValueError):
        merge_policy.set_policy("", "auto_when_green")


def test_configured_scopes_lists_only_deliberate_choices(store):
    merge_policy.set_policy("project:alpha", "auto_when_green")
    merge_policy.set_policy("project:beta", "auto_when_green_and_low_risk")
    assert merge_policy.configured_scopes() == [
        ("project:alpha", "auto_when_green"),
        ("project:beta", "auto_when_green_and_low_risk"),
    ]
