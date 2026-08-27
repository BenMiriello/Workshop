"""Reclaim must advance the fencing generation, not merely release the lease.

THE DEFECT, reproduced against the real module before the fix:

    claimed: token=1 status=doing
    reclaim: [{'id': '4d11fa4c...', 'action': 'released'}]
    after reclaim: status=todo claim_token=1
    ZOMBIE LATE WRITE: ('done', 1)

A crashed sweep is reclaimed, the ticket goes back to `todo`, and the crashed
worker comes back and writes `done` with its old token. The token matched
because reclaim deliberately left it alone, on the reasoning that "the next
acquire bumps it". That is true and it does not cover the window BEFORE the next
acquire: `update`'s fence compares against the RECORD's `claim_token`, not the
lease row, so expiring the lease fenced nobody. The ticket then read `done` with
its work on an abandoned branch.

The rule being restored: any act that ends a worker's right to a ticket must
ADVANCE the generation. An operator edit already did (`update`'s Fix 1); reclaim
is the same kind of act by a higher authority.

These run file-backed, with no lease store, which is deliberate: that is the
`_fence_ticket_lease` fallback leg (`current + 1` on the record), and it is the
one that runs everywhere rather than only where a database is configured.
`test_leases.py` covers the relational half.
"""
from __future__ import annotations

import datetime

import pytest


@pytest.fixture(autouse=True)
def _owner():
    from services import ownership

    ownership.pin_operator()


def _past_the_lease(offset_multiple: int = 10) -> datetime.datetime:
    from services import tasks

    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=tasks.LEASE_TTL_SEC * offset_multiple)


def _claim_then_crash(root, **add_kw):
    """Claim a ticket and let its lease go stale, as a killed sweep does."""
    from services import tasks

    task = tasks.add(root, text="a ticket a worker will crash on", **add_kw)
    claimed = tasks.claim(root, task["id"], "job-1", pid=999999)
    assert claimed is not None and claimed["status"] == "doing"
    return task["id"], claimed["claim_token"]


def test_a_zombie_cannot_finalize_a_released_ticket(tmp_path):
    from services import tasks

    root = tmp_path / "scope"
    root.mkdir()
    task_id, zombie_token = _claim_then_crash(root)

    assert tasks.reclaim(root, now=_past_the_lease())[0]["action"] == "released"
    after = tasks.get_task(root, task_id)
    assert after["status"] == "todo"
    assert after["claim_token"] > zombie_token, "the fence did not advance"

    assert tasks.update(root, task_id, status="done",
                        expect_claim_token=zombie_token) is None
    assert tasks.get_task(root, task_id)["status"] == "todo"


def test_a_zombie_cannot_finalize_a_parked_ticket(tmp_path):
    """The parked leg is reachable by the same zombie: a ticket parked at its
    attempt cap is still a ticket the crashed worker holds a token for."""
    from services import tasks

    root = tmp_path / "scope"
    root.mkdir()
    task_id, zombie_token = _claim_then_crash(root, max_attempts=1)

    assert tasks.reclaim(root, now=_past_the_lease())[0]["action"] == "parked"
    after = tasks.get_task(root, task_id)
    assert after["status"] == "parked"
    assert after["claim_token"] > zombie_token, "the fence did not advance"

    assert tasks.update(root, task_id, status="done",
                        expect_claim_token=zombie_token) is None
    assert tasks.get_task(root, task_id)["status"] == "parked"


def test_a_zombie_heartbeat_is_refused_BY_THE_GENERATION(tmp_path):
    """The heartbeat is how a displaced worker finds out, so it must be the
    GENERATION that refuses it, not the status.

    Stated that precisely because the naive version of this test passes without
    the fix: after a reclaim the ticket is `todo`, and `heartbeat` already
    refuses anything that is not `doing`, so a status check alone would satisfy
    it while the fence stayed broken. Re-claiming puts the ticket back into
    `doing` under a NEW generation, which removes the status from the argument
    and leaves only the number.
    """
    from services import tasks

    root = tmp_path / "scope"
    root.mkdir()
    task_id, zombie_token = _claim_then_crash(root)
    tasks.reclaim(root, now=_past_the_lease())

    # Someone else now holds it, and it is `doing` again.
    fresh = tasks.claim(root, task_id, "job-2", pid=1)
    assert fresh is not None and fresh["status"] == "doing"

    assert tasks.heartbeat(root, task_id, zombie_token) is None, (
        "the old generation was accepted while the ticket was doing")
    assert tasks.heartbeat(root, task_id, fresh["claim_token"]) is not None, (
        "the current holder's heartbeat must still work")


def test_the_next_claimant_is_not_harmed(tmp_path):
    """Reclaim must still FREE the ticket. `lease_repo.fence` bumps and expires
    in one statement, so fencing subsumes the release rather than skipping it."""
    from services import tasks

    root = tmp_path / "scope"
    root.mkdir()
    task_id, first_token = _claim_then_crash(root)
    tasks.reclaim(root, now=_past_the_lease())

    second = tasks.claim(root, task_id, "job-2", pid=1)
    assert second is not None, "reclaim did not actually free the ticket"
    assert second["claim_token"] > first_token, "the fence went backwards"

    finished = tasks.update(root, task_id, status="done",
                            expect_claim_token=second["claim_token"])
    assert finished is not None and finished["status"] == "done"


def test_the_generation_only_ever_goes_up(tmp_path):
    """Monotonicity across repeated crash-and-reclaim cycles. A fence that can
    go backwards is a fence a zombie holding the higher number can replay."""
    from services import tasks

    root = tmp_path / "scope"
    root.mkdir()
    task_id, _ = _claim_then_crash(root, max_attempts=99)

    seen = [tasks.get_task(root, task_id)["claim_token"]]
    for i in range(3):
        tasks.reclaim(root, now=_past_the_lease())
        seen.append(tasks.get_task(root, task_id)["claim_token"])
        claimed = tasks.claim(root, task_id, f"job-{i + 2}", pid=1)
        assert claimed is not None
        seen.append(claimed["claim_token"])

    assert seen == sorted(seen) and len(set(seen)) == len(seen), seen
