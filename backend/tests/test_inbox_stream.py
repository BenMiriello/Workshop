"""The inbox as a stream: snooze actually hides, and a re-raise does not wake it.

  * **Snooze had to filter in the store.** `counts()` is built by calling
    `list_items` and the chat badge reads `counts()`, so a filter anywhere above
    the store leaves a badge counting rows the panel refuses to show.

The other half of this file was the finished-run producer, and it is gone with
the producer (2026-08-22). It raised a `notify` per autonomous run that ended
with nobody watching, which on the live record was 744 of 756 open items and
nobody had ever actioned one. A finished run is on the run and on its ticket;
see docs/platform-redesign/one-fact-one-place-2026-08-22.md §2.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import inbox as inbox_routes  # noqa: E402
from services import inbox, ownership  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A private inbox store. Tests that auth as an owner must redirect
    `ownership._RUNTIME`, or they write into the operator's real chats."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(1)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(inbox_routes.router, prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Snooze filters in the store, so the badge cannot disagree with the panel
# ---------------------------------------------------------------------------

def _in_an_hour():
    return datetime.now(timezone.utc) + timedelta(hours=1)


def test_a_snoozed_item_leaves_the_listing(store):
    item = inbox.raise_item(kind="question", agent="a", scope="workshop", title="Which store?")
    assert [i["id"] for i in inbox.list_items()] == [item["id"]]

    inbox.snooze_item(item["id"], _in_an_hour())

    assert inbox.list_items() == []
    # Deferred, not closed: the block is still a block.
    assert inbox.get_item(item["id"])["state"] == "open"


def test_the_badge_agrees_with_the_panel(store):
    """THE POINT OF FILTERING IN `list_items`. `counts()` feeds the chat badge
    and `list_items` feeds the panel. A filter above the store would leave the
    badge on a row nothing will ever show, and no click could clear it."""
    a = inbox.raise_item(kind="question", agent="a", scope="workshop", title="One")
    inbox.raise_item(kind="notify", agent="a", scope="workshop", title="Two")

    inbox.snooze_item(a["id"], _in_an_hour())

    counts = inbox.counts()
    assert counts == {"notify": 1, "question": 0, "review": 0}
    assert sum(counts.values()) == len(inbox.list_items())


def test_a_snooze_that_has_expired_is_no_snooze(store):
    item = inbox.raise_item(kind="notify", agent="a", scope="workshop", title="Later")
    inbox.snooze_item(item["id"], datetime.now(timezone.utc) - timedelta(minutes=1))
    assert [i["id"] for i in inbox.list_items()] == [item["id"]]


def test_snoozing_with_none_wakes_it_now(store):
    item = inbox.raise_item(kind="notify", agent="a", scope="workshop", title="Later")
    inbox.snooze_item(item["id"], _in_an_hour())
    assert inbox.list_items() == []
    inbox.snooze_item(item["id"], None)
    assert [i["id"] for i in inbox.list_items()] == [item["id"]]


def test_a_corrupt_stamp_cannot_hide_a_blocked_agent(store):
    """An unreadable field must fail toward showing the row. The alternative is
    an item that is invisible forever and nothing says why."""
    item = inbox.raise_item(kind="question", agent="a", scope="workshop", title="Stuck")
    items = inbox._load()
    items[0]["snoozed_until"] = "not a timestamp"
    inbox._save(items)
    assert [i["id"] for i in inbox.list_items()] == [item["id"]]


def test_include_snoozed_is_how_a_sweep_sees_everything(store):
    item = inbox.raise_item(kind="question", agent="a", scope="workshop", title="One")
    inbox.snooze_item(item["id"], _in_an_hour())
    assert inbox.list_items() == []
    assert [i["id"] for i in inbox.list_items(include_snoozed=True)] == [item["id"]]


def test_presets_resolve_to_an_hour_and_to_tomorrow_morning(store):
    now = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
    assert inbox.snooze_until("hour", now) == now + timedelta(hours=1)
    tomorrow = inbox.snooze_until("tomorrow", now)
    # Tomorrow MORNING, local, not this time tomorrow.
    assert tomorrow > now
    assert tomorrow.astimezone().hour == 8
    assert (tomorrow - now) < timedelta(days=2)
    with pytest.raises(ValueError):
        inbox.snooze_until("next-year", now)


def test_the_endpoint_snoozes_and_the_listing_stops_showing_it(client, store):
    # GET /inbox had zero callers (the chat Work panel reads through
    # useChatWork's poller against the per-item endpoints, not this whole-list
    # read) and was deleted (coherence-overhaul-2026-08-16 T5), so this checks
    # the snooze endpoint's effect the way the surviving callers observe it:
    # through `inbox.list_items`/`counts`, not a second HTTP round trip.
    item = inbox.raise_item(kind="question", agent="a", scope="workshop", title="Which store?")
    assert len(inbox.list_items()) == 1

    r = client.post(f"/api/inbox/{item['id']}/snooze", json={"preset": "hour"})
    assert r.status_code == 200, r.text
    assert r.json()["snoozed_until"]

    assert inbox.list_items() == []
    assert inbox.counts() == {"notify": 0, "question": 0, "review": 0}


def test_snoozing_a_missing_item_is_a_404(client, store):
    assert client.post("/api/inbox/nope/snooze", json={"preset": "hour"}).status_code == 404


def test_a_bad_preset_is_refused(client, store):
    item = inbox.raise_item(kind="notify", agent="a", scope="workshop", title="One")
    r = client.post(f"/api/inbox/{item['id']}/snooze", json={"preset": "eventually"})
    assert r.status_code == 400
    assert inbox.get_item(item["id"])["snoozed_until"] is None


# ---------------------------------------------------------------------------
# Auto-unsnooze on activity
# ---------------------------------------------------------------------------

def _reraise(**over):
    kw = dict(kind="question", agent="agent:worker", scope="workshop",
              title="Bind a scope to a tracker", body="Which store holds the token?",
              dedupe_key="task:workshop:t1")
    kw.update(over)
    return inbox.raise_item(**kw)


def test_a_bare_re_raise_does_not_wake_a_snoozed_item(store):
    """An ambient agent re-raises what it is holding on every pass. If a bare
    re-raise woke the item, snooze would be a no-op and the row the operator
    just deferred would be back before he looked away."""
    item = _reraise()
    inbox.snooze_item(item["id"], _in_an_hour())

    for _ in range(3):
        _reraise()

    assert inbox.list_items() == []
    assert inbox.get_item(item["id"])["snoozed_until"] is not None


def test_the_entity_changing_wakes_it_early(store):
    item = _reraise()
    inbox.snooze_item(item["id"], _in_an_hour())

    _reraise(body="Actually, which tracker owns the binding?")

    assert [i["id"] for i in inbox.list_items()] == [item["id"]]
    after = inbox.get_item(item["id"])
    assert after["snoozed_until"] is None
    # And the row states the CURRENT question, not the one first asked.
    assert after["body"] == "Actually, which tracker owns the binding?"


def test_a_new_option_wakes_it_too(store):
    """A block that grew a structured proposal is now one tap to approve. That
    is a change in what is being asked, so it is worth waking for."""
    item = _reraise()
    inbox.snooze_item(item["id"], _in_an_hour())
    _reraise(options=["Approve", "Decline"])
    assert inbox.get_item(item["id"])["snoozed_until"] is None


def test_a_woken_item_keeps_its_identity_rather_than_duplicating(store):
    item = _reraise()
    inbox.snooze_item(item["id"], _in_an_hour())
    again = _reraise(title="Bind a scope to a tracker (retry 2)")
    assert again["id"] == item["id"]
    assert len(inbox._load()) == 1
