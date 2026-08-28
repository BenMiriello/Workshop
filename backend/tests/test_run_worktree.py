"""A non-interactive run holds the tree it was handed, and gives it back.

The other half of `test_chat_worktree.py`. That one pinned connection C-3 (an
interactive chat takes a lease); this one pins the connection that was still
missing after it: `worktrees.provision_worktree` hands a sweep a directory and
records it, and nothing ever acquired a lease on it. With
`WORKSHOP_TREE_LEASE_MOUNT` on, `tree_access.decide` gives a READ-ONLY mount to
any holder without a lease -- so every unattended run would have been read-only
inside its own worktree, which is why the flag was not merely unarmed but
unarmable.

What must be true:

  * the holder is the RUN, under the id the rest of the system already uses for
    it, so Queue, Steer and Preempt find the holder they were written to find;
  * the tree is named by `tree_access.subject_id_for` and by nothing else, or a
    run and a chat contending for one directory would be locking two doors;
  * a tree somebody ELSE holds comes back as `blocked_by` with no lease, and
    never as a second writer;
  * the renewal is a WORK event, or a long iteration would read as an idle
    holder ten minutes in;
  * release is fenced on the generation and touches ONLY the worktree subject,
    because this run's TICKET lease has a different owner and its own finalize;
  * the beater watches its parent and, when the parent is gone, EXITS WITHOUT
    RELEASING -- a crashed run falls to the existing expiry-and-fence path.

The lease table's own semantics belong to `test_leases.py`; the fake store here
exists so these run without a database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import run_worktree  # noqa: E402

RUN = "job_1787720648892_2a2c"
OTHER_RUN = "job_1787714610096_5e91"
SUBJECT = "01a04600-0000-7000-8000-0000000000aa"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A tree, a fake lease table, and a record of everything asked of it."""
    from services import tree_access

    tree = tmp_path / "branches" / "demo" / "t-42"
    tree.mkdir(parents=True)

    monkeypatch.setattr(tree_access, "subject_id_for",
                        lambda root: SUBJECT if "branches" in str(root) else str(root))

    calls: list[tuple] = []
    table: dict[str, dict] = {}

    class FakeLeases:
        LeasesUnavailable = run_worktree.leases.LeasesUnavailable
        LEASE_TTL_SEC = 90
        HEARTBEAT_SEC = 30

        @staticmethod
        def get(kind, subject_id):
            calls.append(("get", kind, subject_id))
            row = table.get(subject_id)
            return dict(row) if row else None

        @staticmethod
        def acquire(kind, subject_id, *, holder_kind, holder_id, **kw):
            calls.append(("acquire", kind, subject_id, holder_kind, holder_id,
                          kw.get("ttl_sec")))
            held = table.get(subject_id)
            if held and held["state"] == "held" and held["holder_id"] != holder_id:
                return None
            row = {"subject_kind": kind, "subject_id": subject_id,
                   "holder_kind": holder_kind, "holder_id": holder_id,
                   "generation": (held or {}).get("generation", 0) + 1,
                   "state": "held", "idle_since": None}
            table[subject_id] = row
            return dict(row)

        @staticmethod
        def renew(kind, subject_id, generation, **kw):
            calls.append(("renew", kind, subject_id, generation, kw.get("work")))
            row = table.get(subject_id)
            if not row or row["generation"] != generation:
                return None
            return dict(row)

        @staticmethod
        def release(kind, subject_id, generation):
            calls.append(("release", kind, subject_id, generation))
            row = table.get(subject_id)
            if not row or row["generation"] != generation:
                return False
            row["state"] = "expired"
            return True

        @staticmethod
        def release_for_holder(holder_id, **kw):  # pragma: no cover - must not be used
            calls.append(("release_for_holder", holder_id))
            raise AssertionError("a run releases its tree BY SUBJECT")

    monkeypatch.setattr(run_worktree, "leases", FakeLeases)
    return {"tree": tree, "calls": calls, "table": table}


# --- the holder is the run ----------------------------------------------------

class TestTheRunIsTheHolder:

    def test_it_takes_the_lease_under_the_run_id(self, wired):
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert got.held
        assert got.lease["holder_kind"] == "run"
        assert got.lease["holder_id"] == RUN

    def test_it_names_the_tree_the_way_tree_access_does(self, wired):
        """ONE IDENTITY. A subject derived here instead of asked for is a second
        lock on the same door, which is no lock at all."""
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert got.subject_id == SUBJECT
        assert ("acquire", run_worktree.SUBJECT_KIND, SUBJECT, "run", RUN, 90) \
            in wired["calls"]

    def test_an_unnameable_tree_takes_nothing(self, wired, monkeypatch):
        from services import tree_access

        def boom(root):
            raise RuntimeError("the record store did not answer")

        monkeypatch.setattr(tree_access, "subject_id_for", boom)
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert got.subject_id is None and got.lease is None
        # It must not have guessed an identity and taken a lease on it.
        assert wired["calls"] == []


# --- a tree somebody else holds ----------------------------------------------

class TestATreeSomebodyElseHolds:

    def test_it_is_refused_and_says_who_has_it(self, wired):
        first = run_worktree.enter(OTHER_RUN, str(wired["tree"]))
        assert first.held
        second = run_worktree.enter(RUN, str(wired["tree"]))
        assert second.lease is None
        assert second.blocked_by["holder_id"] == OTHER_RUN
        # And it did not overwrite the incumbent on the way past.
        assert wired["table"][SUBJECT]["holder_id"] == OTHER_RUN

    def test_the_same_run_coming_back_is_not_blocked(self, wired):
        run_worktree.enter(RUN, str(wired["tree"]))
        again = run_worktree.enter(RUN, str(wired["tree"]))
        assert again.held and again.blocked_by is None


# --- renewal ------------------------------------------------------------------

class TestRenewal:

    def test_a_renewal_is_a_work_event(self, wired):
        """`IDLE_AFTER_SEC` measures `last_work_at`. A heartbeat that did not
        move it would paint every long iteration as an idle holder."""
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert run_worktree.beat(got.subject_id, got.generation) is True
        assert ("renew", run_worktree.SUBJECT_KIND, SUBJECT, 1, True) \
            in wired["calls"]

    def test_a_stale_generation_is_a_fence(self, wired):
        got = run_worktree.enter(RUN, str(wired["tree"]))
        run_worktree.enter(OTHER_RUN, str(wired["tree"]))  # not possible while held
        wired["table"][SUBJECT]["generation"] = 7          # ... so fence it directly
        assert run_worktree.beat(got.subject_id, got.generation) is False

    def test_a_store_that_did_not_answer_is_not_a_fence(self, wired, monkeypatch):
        """The distinction `ralph.sh`'s own heartbeat draws: only a store that
        ANSWERS and says the claim is gone is a displacement."""
        got = run_worktree.enter(RUN, str(wired["tree"]))

        def boom(*a, **k):
            raise RuntimeError("the database blipped")

        monkeypatch.setattr(run_worktree.leases, "renew", boom)
        assert run_worktree.beat(got.subject_id, got.generation) is True


# --- release ------------------------------------------------------------------

class TestRelease:

    def test_it_is_fenced_on_the_generation(self, wired):
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert run_worktree.leave(got.subject_id, got.generation) is True
        assert wired["table"][SUBJECT]["state"] == "expired"

    def test_a_stale_generation_cannot_release_somebody_elses_tree(self, wired):
        got = run_worktree.enter(RUN, str(wired["tree"]))
        wired["table"][SUBJECT]["generation"] = 9
        assert run_worktree.leave(got.subject_id, got.generation) is False
        assert wired["table"][SUBJECT]["state"] == "held"

    def test_it_releases_by_subject_and_not_by_holder(self, wired):
        """`release_for_holder` would also drop this run's TICKET lease, which
        has a different owner, a different TTL and its own finalize path. The
        fake raises if it is ever called."""
        got = run_worktree.enter(RUN, str(wired["tree"]))
        run_worktree.leave(got.subject_id, got.generation)
        assert not any(c[0] == "release_for_holder" for c in wired["calls"])


# --- the beater ---------------------------------------------------------------

class TestTheBeaterWatchesItsParent:

    def test_a_dead_parent_stops_the_renewal_without_releasing(self, wired,
                                                               monkeypatch):
        """SECTION 3'S RULE: a crashed run falls to the EXISTING expiry-and-fence
        path. A beater that released here would be a second reclaim mechanism
        racing the one that already works."""
        got = run_worktree.enter(RUN, str(wired["tree"]))
        monkeypatch.setattr(run_worktree.port_registry, "process_alive",
                            lambda pid: False)
        monkeypatch.setattr(run_worktree.time, "sleep", lambda s: None)
        rc = run_worktree.beat_forever(got.subject_id, got.generation,
                                       parent_pid=424242, interval=1)
        assert rc == 0
        # STILL HELD. Nothing released it; the TTL will.
        assert wired["table"][SUBJECT]["state"] == "held"

    def test_a_fence_stops_it_with_the_displaced_code(self, wired, monkeypatch):
        got = run_worktree.enter(RUN, str(wired["tree"]))
        monkeypatch.setattr(run_worktree.port_registry, "process_alive",
                            lambda pid: True)
        monkeypatch.setattr(run_worktree.time, "sleep", lambda s: None)
        wired["table"][SUBJECT]["generation"] = 12
        rc = run_worktree.beat_forever(got.subject_id, got.generation,
                                       parent_pid=424242, interval=1)
        assert rc == 9


# --- no lease store at all -----------------------------------------------------

class TestNoLeaseStore:
    """A file-backed project tree with no Postgres near it must still sweep,
    exactly as it did before this table existed."""

    def test_enter_comes_back_with_a_cwd_and_no_lease(self, wired, monkeypatch):
        def unavailable(*a, **k):
            raise run_worktree.leases.LeasesUnavailable("no store")

        monkeypatch.setattr(run_worktree.leases, "get", unavailable)
        monkeypatch.setattr(run_worktree.leases, "acquire", unavailable)
        got = run_worktree.enter(RUN, str(wired["tree"]))
        assert got.cwd == str(wired["tree"])
        assert got.lease is None and got.blocked_by is None
        assert "no lease store" in got.detail.lower()
