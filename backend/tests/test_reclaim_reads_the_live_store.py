"""`reclaim()` must read the store every other caller reads.

WHAT BROKE (found 2026-08-23). `reclaim` opened its cheap lock-free prescan with
`_read_store(root)` — a direct read of the RETIRED `.tasks.json` file store —
while the pass it guards, two lines below and inside the lock, uses `_load()`,
which goes through the relational branch. On this installation the file held 126
rows with ZERO in `doing` and the relational store held 175 with two, so the
prescan saw nothing stale and returned `[]` on every poll. The self-healing this
function IS never ran once: two tickets sat `doing` with dead pids for 41 hours,
and since `next_ready` calls `reclaim` first, the scheduler could not see them
either. The loop's own bookkeeping was broken silently, which is the worst way
for a watchdog to fail.

A prescan reading a DIFFERENT store from the pass it guards is not an
optimization; it is a guaranteed wrong answer whenever the two disagree — and
the whole reason both exist is that they disagree. So the test that matters is
not "reclaim reclaims", it is "reclaim reclaims a row that exists ONLY in the
live store", which is exactly the shape the bug had.

The two stores are stubbed rather than stood up: a real Postgres is not needed
to state that reclaim must not consult the retired file.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks  # noqa: E402


def _stale_doing(task_id: str = "stale-1") -> dict:
    """A `doing` row whose claim is unambiguously dead: an hour-old heartbeat and
    a pid that is not this process and not recorded at all, so it falls through
    the heartbeat window rather than depending on what pids exist on the box."""
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return tasks._normalize({
        "id": task_id,
        "title": "a claim whose worker died",
        "status": "doing",
        "attempts": 1,
        "claim_token": 3,
        "claim_pid": None,
        "lease_until": old,
        "last_heartbeat": old,
        "updated": old,
    })


@pytest.fixture()
def stores(monkeypatch):
    """Point the two stores at DIFFERENT contents, the way the live tree was."""
    state: dict = {"relational": [], "file": [], "persisted": None}
    monkeypatch.setattr(tasks, "_from_relational", lambda root: state["relational"])
    monkeypatch.setattr(tasks, "_read_store", lambda root: state["file"])
    monkeypatch.setattr(tasks, "_persist",
                        lambda root, t: state.__setitem__("persisted", t))
    # The lease table is a separate store again; releasing is not what is under
    # test here and needs no database.
    monkeypatch.setattr(tasks, "_release_ticket_lease", lambda *a, **k: None)
    return state


def test_it_reclaims_a_row_that_only_the_live_store_has(tmp_path: Path, stores):
    """The exact live shape: the retired file has rows, none of them `doing`."""
    stores["relational"] = [_stale_doing()]
    stores["file"] = [tasks._normalize(
        {"id": "old-row", "title": "left behind in the retired file", "status": "done"}
    )]

    out = tasks.reclaim(tmp_path)

    assert out == [{"id": "stale-1", "action": "released"}], (
        "the prescan read the retired file store and found nothing to do"
    )
    assert stores["persisted"] is not None
    assert [t["status"] for t in stores["persisted"]] == ["todo"]


def test_the_retired_file_cannot_hide_a_stale_claim(tmp_path: Path, stores):
    """Even with the file store EMPTY — `_read_store` returning [] short-circuits
    the old code before it looked at anything real."""
    stores["relational"] = [_stale_doing()]
    stores["file"] = []

    assert tasks.reclaim(tmp_path) == [{"id": "stale-1", "action": "released"}]


def test_the_retired_file_cannot_invent_one_either(tmp_path: Path, stores):
    """The other direction: a `doing` row that exists only in the retired file is
    not this scope's work and must not be reclaimed (or persisted) from it."""
    stores["relational"] = []
    stores["file"] = [_stale_doing("ghost")]

    assert tasks.reclaim(tmp_path) == []
    assert stores["persisted"] is None


def test_a_live_claim_is_still_left_alone(tmp_path: Path, stores):
    """The prescan is still a real short-circuit: nothing stale, no lock, no
    write. Reclaiming a running worker's ticket is the failure on the other side
    of this function."""
    fresh = _stale_doing("live")
    now = datetime.now(timezone.utc)
    fresh["last_heartbeat"] = now.isoformat()
    fresh["lease_until"] = (now + timedelta(hours=1)).isoformat()
    stores["relational"] = [fresh]

    assert tasks.reclaim(tmp_path) == []
    assert stores["persisted"] is None


def test_it_parks_at_the_attempt_cap_rather_than_re_running(tmp_path: Path, stores):
    """Dead-lettering still works through the live store — the branch that turns
    a poison pill into `parked` instead of handing it back to a runner."""
    doomed = _stale_doing("doomed")
    doomed["attempts"] = tasks.RECLAIM_ATTEMPT_CAP
    stores["relational"] = [doomed]

    assert tasks.reclaim(tmp_path) == [{"id": "doomed", "action": "parked"}]
    assert [t["status"] for t in stores["persisted"]] == ["parked"]
