"""The inbox as a stream: approve actually writes, snooze actually hides, and an
autonomous run that ended with nobody watching actually shows up.

Two defects, one surface (a third, the memory-promotion approve write path,
was pinned here until the `memory_promotion` module it served was deleted —
`PROMOTE_AT` could never be satisfied, see
docs/platform-redesign/memory-consolidation-2026-08-16.md D9):

  * **Snooze had to filter in the store.** `counts()` is built by calling
    `list_items` and the chat badge reads `counts()`, so a filter anywhere above
    the store leaves a badge counting rows the panel refuses to show.
  * **The surface was empty.** Only blocked tickets raised anything, so an
    autonomous run could fail at 3am and say so nowhere.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import inbox as inbox_routes  # noqa: E402
from services import inbox, jobs, ownership  # noqa: E402


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
def client(store, monkeypatch):
    # The listing reconciles on read. Those producers have their own tests; here
    # they are noise that would raise unrelated rows into the assertions. The
    # sequence moved into `inbox.reconcile` when the chat's aggregated work read
    # started needing the same pass.
    monkeypatch.setattr(inbox, "reconcile", lambda: None)
    app = FastAPI()
    app.include_router(inbox_routes.router, prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------------------
# 2. Snooze filters in the store, so the badge cannot disagree with the panel
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


def test_include_snoozed_is_how_a_reconciler_sees_everything(store):
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
# 3. Auto-unsnooze on activity
# ---------------------------------------------------------------------------

def _reraise(**over):
    kw = dict(kind="question", agent="agent:worker", scope="workshop",
              title="Bind a scope to a tracker", body="Which store holds the token?",
              dedupe_key="task:workshop:t1")
    kw.update(over)
    return inbox.raise_item(**kw)


def test_the_reconcile_pass_does_not_wake_a_snoozed_item(store):
    """`run_inbox.sync` re-raises every blocked task on EVERY inbox read, about
    every twenty seconds. If a bare re-raise woke the item, snooze would be a
    no-op for the only live producer and the row the operator just deferred
    would be back before he looked away."""
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


# ---------------------------------------------------------------------------
# 4. The producer: an autonomous run that ended with nobody watching
# ---------------------------------------------------------------------------

@pytest.fixture
def job_log(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    monkeypatch.setattr(jobs, "_collapsed_cache", None)
    return log


def _write_jobs(log: Path, *records: dict) -> None:
    log.write_text("".join(json.dumps(r) + "\n" for r in records))


def _job(job_id="job_1", *, trigger="cron", status="done", ended="2026-08-12T12:00:00+00:00",
         **over):
    rec = {"id": job_id, "trigger": trigger, "status": status,
           "started_at": "2026-08-12T11:00:00+00:00", "ended_at": ended}
    rec.update(over)
    return rec


def _sweep_from(when: datetime, log: Path, *records: dict):
    """Seed the watermark, then sweep. The first pass on any box only records
    where it started, so a test that wants a result has to run a second one."""
    _write_jobs(log, *records)
    inbox._set_run_watermark(when)
    return inbox.sweep_finished_runs(now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc))


def test_the_first_pass_records_where_it_started_and_reports_nothing(store, job_log):
    """This log holds months of history. Replaying it would bury the surface at
    the exact moment it starts working."""
    _write_jobs(job_log, _job("job_old", ended="2026-07-14T09:00:00+00:00", tasks_done=3))

    out = inbox.sweep_finished_runs()

    assert out == {"raised": 0, "watermark": "initialized"}
    assert inbox.list_items() == []
    # And the pass after it does not go back for them either.
    assert inbox.sweep_finished_runs()["raised"] == 0
    assert inbox.list_items() == []


def test_a_failed_autonomous_run_raises_a_notify(store, job_log):
    out = _sweep_from(
        datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), job_log,
        _job("job_9", trigger="loop", status="failed", task_id="t7",
             project_slug="birds", evidence={"halted": "tree busy"}))

    assert out["raised"] == 1
    items = inbox.list_items()
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "notify"
    assert it["run_id"] == "job_9"
    assert it["ticket_id"] == "t7"
    assert it["dedupe_key"] == "run:job_9"
    assert it["scope"] == "project:birds"
    assert "failed" in it["title"]
    assert "tree busy" in it["body"]


def test_a_run_the_operator_started_by_hand_raises_nothing(store, job_log):
    """`manual` means he pressed the button and is watching it. Telling him it
    finished is noise, and the autonomous tier is the loudest claim there is."""
    out = _sweep_from(datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), job_log,
                      _job("job_m", trigger="manual", status="failed"))
    assert out["raised"] == 0
    assert inbox.list_items() == []


def test_an_idle_sweep_tick_is_not_an_event(store, job_log):
    """1880 of this box's 2336 terminal autonomous runs finished having touched
    nothing. A notification for each would bury the one blocked agent the whole
    surface exists to surface."""
    out = _sweep_from(datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), job_log,
                      _job("job_idle", tasks_done=0, tasks_failed=0))
    assert out["raised"] == 0
    assert inbox.list_items() == []


def test_a_run_that_moved_a_ticket_reports(store, job_log):
    _sweep_from(datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), job_log,
                _job("job_w", trigger="loop", tasks_done=2))
    items = inbox.list_items()
    assert len(items) == 1
    assert "2 ticket(s) done" in items[0]["body"]


def test_a_run_still_in_flight_is_not_reported(store, job_log):
    out = _sweep_from(datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), job_log,
                      {"id": "job_live", "trigger": "cron", "status": "running",
                       "started_at": "2026-08-12T11:00:00+00:00"})
    assert out["raised"] == 0


def test_the_watermark_stops_the_same_run_reporting_twice(store, job_log):
    when = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    _sweep_from(when, job_log, _job("job_x", trigger="loop", status="failed"))
    assert len(inbox.list_items()) == 1

    # Every later pass reads the same log line again.
    for _ in range(3):
        assert inbox.sweep_finished_runs(
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc))["raised"] == 0
    assert len(inbox.list_items()) == 1


def test_runs_that_ended_before_the_watermark_are_left_alone(store, job_log):
    _sweep_from(datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc), job_log,
                _job("job_before", status="failed", ended="2026-08-12T12:00:00+00:00"))
    assert inbox.list_items() == []


def test_an_unreadable_job_log_does_not_empty_the_inbox(store, job_log, monkeypatch):
    inbox.raise_item(kind="question", agent="a", scope="workshop", title="Stuck")
    inbox._set_run_watermark(datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(jobs, "read", lambda **k: (_ for _ in ()).throw(OSError("gone")))

    assert inbox.sweep_finished_runs()["raised"] == 0
    assert len(inbox.list_items()) == 1
