"""A thread carries `armed` and `role`, and they are independent.

THE CLAIM UNDER TEST, from `threads-branches-and-pages-2026-08-29` section 1:
there is one entity, the Thread, and two fields on it decide different things.
`armed` is the only field that says whether the thread works unasked. `role`
names a definition and says nothing about when it runs.

The two assertions the ticket names, and they are the ones that would catch the
mistake this design exists to prevent -- collapsing the pair into one "is this
an agent" checkbox:

  * an ARMED thread with NO role advances. It continues on its own context,
    which is what "nudge it along" means, and a design that required a role
    first would have made every armed thread need a definition it does not have.
  * a thread with a ROLE but `armed = off` does NOT advance. A job description
    is not a shift.

Plus the wire and the store: both fields round-trip through the PATCH, `role`
clears on an explicit null, an unknown `armed` clamps to `off` rather than
riding through, and every thread answers `armed` even if its file predates the
field.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes  # noqa: E402
from services import chat_store, ownership, thread_arming  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        yield TestClient(app)
    finally:
        ownership.clear_current_owner()


# --- the two assertions the ticket names -----------------------------------


def test_an_armed_thread_with_no_role_advances():
    thread = {"armed": "on-ready-ticket", "role": None}
    assert thread_arming.is_armed(thread)
    assert thread_arming.should_advance(thread, "ready-ticket")


def test_a_thread_with_a_role_but_not_armed_does_not_advance():
    thread = {"armed": "off", "role": "Builder"}
    assert not thread_arming.is_armed(thread)
    for reason in thread_arming.WAKE_REASONS:
        assert not thread_arming.should_advance(thread, reason), reason


def test_each_armed_setting_admits_exactly_its_own_wake_reason():
    """A schedule does not wake an on-message thread, and vice versa. Without
    this, `armed` would be a boolean wearing four names."""
    pairs = {"on-message": "message", "schedule": "schedule",
             "on-ready-ticket": "ready-ticket"}
    for armed, own in pairs.items():
        thread = {"armed": armed}
        for reason in thread_arming.WAKE_REASONS:
            assert thread_arming.should_advance(thread, reason) is (reason == own)


def test_an_unrecognised_armed_value_reads_as_off():
    """The failure direction is a thread that does nothing, never one that runs
    on a value nobody meant."""
    assert thread_arming.armed_of({"armed": "continuous"}) == "off"
    assert thread_arming.armed_of({}) == "off"
    assert thread_arming.armed_of(None) == "off"
    assert not thread_arming.should_advance({"armed": "continuous"}, "message")


def test_advancing_filters_a_list_and_keeps_its_order():
    threads = [
        {"id": "a", "armed": "off", "role": "Planner"},
        {"id": "b", "armed": "on-message"},
        {"id": "c", "armed": "schedule", "role": "Planner"},
        {"id": "d", "armed": "on-message", "role": "Builder"},
    ]
    assert [t["id"] for t in thread_arming.advancing(threads, "message")] == ["b", "d"]


# --- the store and the wire -------------------------------------------------


def test_a_new_thread_is_off_with_no_role(client):
    t = chat_store.create_thread(context_key="general", title="t")
    assert t["armed"] == "off"
    assert t["role"] is None


def test_a_thread_whose_file_predates_the_fields_still_answers(client):
    """`armed` is NOT NULL on the wire, so a thread created before the field
    existed has to read as `off` rather than as a missing key."""
    t = chat_store.create_thread(context_key="general", title="t")
    from services import chat_log
    chat_log.patch_meta_sync(t["id"], armed=None, role=None)
    fresh = chat_store.get_thread(t["id"])
    assert fresh["armed"] == "off"
    assert fresh["role"] is None


def test_patch_sets_both_fields_and_reads_them_back(client):
    t = chat_store.create_thread(context_key="general", title="t")
    r = client.patch(f"/chat/threads/{t['id']}",
                     json={"armed": "on-ready-ticket", "role": "Builder"})
    assert r.status_code == 200, r.text
    assert r.json()["armed"] == "on-ready-ticket"
    assert r.json()["role"] == "Builder"

    got = client.get(f"/chat/threads/{t['id']}").json()
    assert (got["armed"], got["role"]) == ("on-ready-ticket", "Builder")


def test_patching_one_field_leaves_the_other_alone(client):
    """The independence claim, at the wire. Arming a thread must not invent a
    role for it and naming a role must not arm it."""
    t = chat_store.create_thread(context_key="general", title="t")
    client.patch(f"/chat/threads/{t['id']}", json={"role": "Researcher"})
    assert chat_store.get_thread(t["id"])["armed"] == "off"

    client.patch(f"/chat/threads/{t['id']}", json={"armed": "schedule"})
    got = chat_store.get_thread(t["id"])
    assert (got["armed"], got["role"]) == ("schedule", "Researcher")


def test_an_explicit_null_role_clears_it(client):
    t = chat_store.create_thread(context_key="general", title="t")
    client.patch(f"/chat/threads/{t['id']}", json={"role": "Planner"})
    client.patch(f"/chat/threads/{t['id']}", json={"role": None})
    assert chat_store.get_thread(t["id"])["role"] is None


def test_the_rename_body_still_works_and_touches_nothing_else(client):
    """PATCH grew from a rename into a settings write; the call every client
    already makes has to keep meaning exactly what it meant."""
    t = chat_store.create_thread(context_key="general", title="t")
    client.patch(f"/chat/threads/{t['id']}", json={"armed": "on-message"})
    r = client.patch(f"/chat/threads/{t['id']}", json={"title": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "renamed"
    assert r.json()["armed"] == "on-message"


def test_an_unknown_armed_value_clamps_rather_than_erroring(client):
    t = chat_store.create_thread(context_key="general", title="t")
    r = client.patch(f"/chat/threads/{t['id']}", json={"armed": "continuous"})
    assert r.status_code == 200, r.text
    assert r.json()["armed"] == "off"


def test_the_list_row_carries_both_fields(client):
    """The chats list filters on `armed`, which is what replaces the roster as
    a page. It cannot do that with one call per thread."""
    t = chat_store.create_thread(context_key="general", title="t")
    chat_store.set_thread_armed(t["id"], "schedule")
    chat_store.set_thread_role(t["id"], "Keeper")
    row = next(r for r in chat_store.list_threads() if r["id"] == t["id"])
    assert (row["armed"], row["role"]) == ("schedule", "Keeper")
