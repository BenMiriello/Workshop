"""An interactive chat can hold a worktree lease, and only when it asked to.

Connection C-3 of
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`: B1, B2,
B3, B4 and B8 were all built and none of them had ever run, because nothing gave
a chat a lease. `services/chat_worktree.py` is that caller, and what is pinned
here is the pair of promises it has to keep at once:

  * A CHAT THAT NEVER ASKED FOR A BRANCH IS UNCHANGED. Same cwd, no lease, no
    presence, no reads of a lease store that may not exist. That is section
    10.2, and it is the reason arming this cannot surprise anybody.
  * A CHAT THAT IS PINNED RUNS IN THAT TREE AND HOLDS IT, under its own thread
    id, which is the same id `scoped_exec` passes to `tree_access.decide` and
    the same id `services/control.py` stops when a preempt lands.

The lease table's own semantics (atomicity, fencing, the presence rule) belong
to `test_leases.py` and are not re-tested here. What is tested here is the
wiring, with a fake store, so these run without a database -- the same reason
`test_tree_access_mount.py` patches the record store rather than skipping.

ORDER IS ONE OF THE ASSERTIONS. Presence has to be written BEFORE the lease is
acquired: section 6's passive rule is applied on READ, so an interactive holder
with no live presence reads as `expired` however fresh its `expires_at` is.
Acquiring first produces a lease that `tree_access.decide` calls expired three
lines before the spawn, which is a read-only mount for the session that just
took the tree. That was measured, not imagined.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import chat_worktree  # noqa: E402

THREAD = "thread-aaa"
OTHER = "thread-bbb"
SUBJECT = "01a04600-0000-7000-8000-000000000001"


class FakeProvider:
    """A project chat, as `chat_worktree` reads one: a key, a slug and a dir."""

    def __init__(self, root: Path, slug: str = "demo"):
        self.key = f"project:{slug}"
        self.slug = slug
        self.dir = root


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A project with one branch, a fake lease table and a fake presence channel.

    Every call the module makes outward is recorded, so the tests can assert on
    what was asked of the store as well as on what came back.
    """
    from services import chat_context, tree_access, worktrees

    project = tmp_path / "projects" / "demo"
    branch = tmp_path / "branches" / "demo" / "fix-42"
    branch.mkdir(parents=True)
    project.mkdir(parents=True)

    monkeypatch.setattr(chat_context, "is_build_provider", lambda p: True)
    monkeypatch.setattr(worktrees, "branch_dir",
                        lambda p, b: tmp_path / "branches" / p / b)
    monkeypatch.setattr(worktrees, "default_ref", lambda p: "master")
    monkeypatch.setattr(worktrees, "worktree_record",
                        lambda p, b: {"id": SUBJECT, "branch": f"wt/{p}/{b}"})
    monkeypatch.setattr(worktrees, "list_branches",
                        lambda p: [{"branch_slug": "fix-42",
                                    "branch": "wt/demo/fix-42",
                                    "dir": str(branch)}])
    # ONE IDENTITY FOR THE TREE. The module must ask `tree_access` rather than
    # deriving a second answer, so the fake is installed there and the tests
    # assert the lease was taken on what it returned.
    monkeypatch.setattr(tree_access, "subject_id_for",
                        lambda root: SUBJECT if "branches" in str(root) else str(root))

    calls: list[tuple] = []
    table: dict[str, dict] = {}
    beats: dict[str, dict] = {}

    class FakeLeases:
        LeasesUnavailable = chat_worktree.leases.LeasesUnavailable
        LEASE_TTL_SEC = 90

        @staticmethod
        def get(kind, subject_id):
            calls.append(("get", kind, subject_id))
            row = table.get(subject_id)
            # A COPY, like the real module's `_shape`. Handing out the stored
            # row would let a caller holding a lease dict see a later
            # generation appear inside it, which is exactly the fencing case
            # these tests exist to catch.
            return dict(row) if row else None

        @staticmethod
        def acquire(kind, subject_id, *, holder_kind, holder_id, **kw):
            calls.append(("acquire", kind, subject_id, holder_kind, holder_id))
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
        def release_for_holder(holder_id, **kw):
            calls.append(("release_for_holder", holder_id))
            n = 0
            for sid, row in list(table.items()):
                if row["holder_id"] == holder_id:
                    row["state"] = "expired"
                    n += 1
            return n

    class FakePresence:
        @staticmethod
        def beat(session_id, **kw):
            calls.append(("beat", session_id, kw.get("subject_id")))
            beats[session_id] = dict(kw)
            return beats[session_id]

        @staticmethod
        def release(session_id):
            calls.append(("presence.release", session_id))
            beats.pop(session_id, None)
            # THE WHOLE OF THE RELEASE PATH. `presence.release` is what calls
            # `leases.release_for_holder` (section 6's join rule), which is why
            # `chat_worktree.leave` calls one function and not two.
            FakeLeases.release_for_holder(session_id)
            return True

    monkeypatch.setattr(chat_worktree, "leases", FakeLeases)
    monkeypatch.setattr(chat_worktree, "presence", FakePresence)
    return {"provider": FakeProvider(project), "project": project,
            "branch": branch, "calls": calls, "table": table, "beats": beats}


# --- section 10.2: the chat that never asked ---------------------------------

class TestAChatWithNoPinIsUnchanged:
    """The default must not surprise someone who never asked for a branch."""

    def test_it_runs_where_it_always_did(self, wired):
        tree = chat_worktree.plan({}, wired["provider"],
                                  fallback_cwd=str(wired["project"]))
        assert tree.cwd == str(wired["project"])
        assert tree.source == "app_default"
        assert not tree.pinned and not tree.held

    def test_it_takes_nothing_and_says_nothing_to_the_stores(self, wired):
        tree = chat_worktree.enter(THREAD, {}, wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert tree.lease is None and tree.blocked_by is None
        # NOT ONE CALL. An unpinned chat must not read a lease store that may
        # not exist, or write a presence row nothing will ever withdraw.
        assert wired["calls"] == []
        assert wired["beats"] == {}

    def test_a_chat_with_no_project_cannot_be_pinned_at_all(self, wired, monkeypatch):
        from services import chat_context
        monkeypatch.setattr(chat_context, "is_build_provider", lambda p: False)
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"], fallback_cwd="/srv/chat")
        assert tree.cwd == "/srv/chat"
        assert tree.project_slug is None and not tree.pinned
        assert wired["calls"] == []


# --- section 10.1: the chat that did ------------------------------------------

class TestAPinnedChatHoldsItsTree:
    def test_the_turn_runs_in_the_worktree(self, wired):
        tree = chat_worktree.plan({"branch_slug": "fix-42"}, wired["provider"],
                                  fallback_cwd=str(wired["project"]))
        assert tree.cwd == str(wired["branch"])
        assert tree.branch == "wt/demo/fix-42"
        assert tree.source == "branch"
        assert tree.subject_id == SUBJECT

    def test_the_lease_is_taken_under_the_thread_id(self, wired):
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert tree.held
        assert tree.lease["holder_kind"] == "chat"
        # The SAME id `scoped_exec.spawn_argv(..., session_id=thread_id)` asks
        # about and `services/control.py` stops on a preempt. A different id
        # here would be a lease nothing else in the system can match.
        assert tree.lease["holder_id"] == THREAD
        assert ("acquire", "worktree", SUBJECT, "chat", THREAD) in wired["calls"]

    def test_presence_is_written_before_the_lease_is_acquired(self, wired):
        chat_worktree.enter(THREAD, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        verbs = [c[0] for c in wired["calls"]]
        assert verbs.index("beat") < verbs.index("acquire"), wired["calls"]
        assert wired["beats"][THREAD]["subject_id"] == SUBJECT
        assert wired["beats"][THREAD]["session_kind"] == "chat"

    def test_a_pin_whose_tree_is_gone_falls_back_and_says_so(self, wired):
        tree = chat_worktree.enter(THREAD, {"branch_slug": "reaped"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert tree.cwd == str(wired["project"])
        assert not tree.held
        assert "reaped" in tree.detail
        # Silently running somewhere else is how work lands on the wrong branch.
        assert "master" in tree.detail


# --- section 8: a busy tree gets a verb, not a second writer -------------------

class TestASecondChatIsOfferedTheVerbs:
    def test_it_does_not_take_a_tree_somebody_holds(self, wired):
        chat_worktree.enter(THREAD, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        rival = chat_worktree.enter(OTHER, {"branch_slug": "fix-42"},
                                    wired["provider"],
                                    fallback_cwd=str(wired["project"]))
        assert not rival.held and rival.lease is None
        assert rival.blocked_by["holder_id"] == THREAD
        # It still RUNS, in the same tree, and reads it. Refusing the turn would
        # be a new failure mode; the mount is what stops the write.
        assert rival.cwd == str(wired["branch"])
        assert wired["table"][SUBJECT]["holder_id"] == THREAD

    def test_it_does_not_leave_a_phantom_presence_behind(self, wired):
        chat_worktree.enter(THREAD, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        chat_worktree.enter(OTHER, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        assert OTHER not in wired["beats"]


# --- sections 6 and 7: releasing ----------------------------------------------

class TestReleasing:
    def test_leaving_withdraws_presence_and_the_lease_goes_with_it(self, wired):
        chat_worktree.enter(THREAD, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        chat_worktree.leave(THREAD)
        assert THREAD not in wired["beats"]
        assert wired["table"][SUBJECT]["state"] == "expired"
        # ONE CALL, NOT TWO: `presence.release` already releases the leases the
        # holder had. A second `leases.release` here would be a second
        # implementation of section 6's join rule.
        assert [c for c in wired["calls"] if c[0] == "presence.release"] == [
            ("presence.release", THREAD)]

    def test_the_next_chat_can_then_take_it(self, wired):
        chat_worktree.enter(THREAD, {"branch_slug": "fix-42"}, wired["provider"],
                            fallback_cwd=str(wired["project"]))
        chat_worktree.leave(THREAD)
        took = chat_worktree.enter(OTHER, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert took.held and took.lease["holder_id"] == OTHER
        assert took.lease["generation"] == 2

    def test_the_heartbeat_renews_as_a_work_event(self, wired):
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert chat_worktree.beat(THREAD, tree) is True
        # `work=True`, because a turn that is still running is still working:
        # `leases.IDLE_AFTER_SEC` measures `last_work_at`, and a heartbeat that
        # did not move it would paint every long turn as idle ten minutes in.
        assert ("renew", "worktree", SUBJECT, 1, True) in wired["calls"]

    def test_a_fenced_holder_stops_claiming_the_tree(self, wired):
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        wired["table"][SUBJECT]["generation"] = 9  # somebody preempted us
        assert chat_worktree.beat(THREAD, tree) is False


# --- degrading -----------------------------------------------------------------

class TestNoLeaseStoreIsNotAFailedTurn:
    def test_the_turn_still_gets_its_worktree(self, wired, monkeypatch):
        def _down(*a, **kw):
            raise chat_worktree.leases.LeasesUnavailable("no store")
        monkeypatch.setattr(chat_worktree.leases, "acquire", _down)
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert tree.cwd == str(wired["branch"])
        assert not tree.held
        assert "no lease store" in tree.detail

    def test_a_tree_that_cannot_be_named_is_never_leased(self, wired, monkeypatch):
        from services import tree_access

        def _blind(root):
            raise RuntimeError("record store unreadable")
        monkeypatch.setattr(tree_access, "subject_id_for", _blind)
        tree = chat_worktree.enter(THREAD, {"branch_slug": "fix-42"},
                                   wired["provider"],
                                   fallback_cwd=str(wired["project"]))
        assert tree.subject_id is None and not tree.held
        # An unknown identity is not a second candidate to try: a lease taken on
        # a name a second participant would not agree with is no lease at all.
        assert [c for c in wired["calls"] if c[0] == "acquire"] == []


# --- section 10.3: choosing where to work --------------------------------------

class TestChoosingABranch:
    def test_the_default_is_always_a_new_branch(self, wired):
        offer = chat_worktree.pin_offer("demo", current_ref="master")
        assert offer["default"]["action"] == "new_branch"
        assert offer["joined_automatically"] is False
        # Joining is reachable, and only through the offer.
        assert [o["branch"] for o in offer["options"]] == ["fix-42"]

    def test_joining_a_branch_that_was_not_offered_is_refused(self, wired):
        offer = chat_worktree.pin_offer("demo", current_ref="master")
        with pytest.raises(ValueError):
            chat_worktree.apply_choice("demo", offer, "join", branch="somewhere-else")

    def test_joining_an_offered_branch_pins_it_and_creates_nothing(self, wired):
        offer = chat_worktree.pin_offer("demo", current_ref="master")
        out = chat_worktree.apply_choice("demo", offer, "join", branch="fix-42")
        assert out == {"branch_slug": "fix-42", "created": False}
