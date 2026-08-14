"""An interrupted reply is SEVERAL rows in the tree, threaded with the messages
that interrupted it.

This is step 1 of moving chat storage onto Postgres
(docs/platform-redesign/transcript-read-from-rows-2026-08-14.md). Before it, the
live mirror wrote one flat row per log event and never wrote a `chat_turn` at
all, so the rows were strictly less expressive than the log they mirrored: a
reply that had been interrupted twice was a single row with the two messages
sitting BEFORE it as siblings, in arrival order, with the positional meta
stripped. Nothing could have rendered the true order from that.

The obstacle looked fatal. `parent_id` is immutable by trigger (that is what
makes a cycle unbuildable), so the rows must be created with the right parent
first time, and the interrupting message needs a parent at a moment when the
stretch it follows has not finished streaming.

It has, though, as a decision: `build_streaming.cut_here` calls `seal_open_text`
the instant the message arrives, closing the open text run so nothing written
afterwards can merge backwards into it. That call IS the server saying "this
stretch is finished, a message goes next", and it is where the row is opened.
Content is filled when the turn commits. Structure when known, text when final,
`parent_id` never touched.

Pure-logic tests first (the split rule), then the whole path against a real
database.
"""
import asyncio
import os
import uuid

import pytest

from services import build_streaming, chat_branches, chat_log, chat_store, ownership


# --- the split rule ----------------------------------------------------------
# No database: this is arithmetic over a segment list, and it is the contract
# that keeps the number of stretches equal to the number of rows opened.

def _text(t, cut=None):
    s = {"t": "text", "text": t}
    if cut:
        s["cut_after"] = cut
    return s


class TestTheSplitRule:
    def test_an_uninterrupted_reply_is_one_stretch(self):
        assert chat_branches.stretches([_text("hello")]) == [[_text("hello")]]

    def test_no_segments_is_still_one_stretch(self):
        """A turn that emitted nothing is one (empty) reply, not zero."""
        assert chat_branches.stretches([]) == [[]]

    def test_one_cut_makes_two(self):
        got = chat_branches.stretches([_text("a", ["m1"]), _text("b")])
        assert [[s["text"] for s in p] for p in got] == [["a"], ["b"]]

    def test_two_cuts_make_three(self):
        got = chat_branches.stretches(
            [_text("a", ["m1"]), _text("b", ["m2"]), _text("c")])
        assert [[s["text"] for s in p] for p in got] == [["a"], ["b"], ["c"]]

    def test_two_messages_with_no_output_between_leave_an_empty_stretch(self):
        """Correct, and why the flush is per REF rather than per segment: two
        messages sent back to back really do have nothing between them."""
        got = chat_branches.stretches([_text("a", ["m1", "m2"]), _text("b")])
        assert [[s["text"] for s in p] for p in got] == [["a"], [], ["b"]]

    def test_a_trailing_cut_leaves_an_empty_final_stretch(self):
        """The message landed after the last thing the turn said."""
        got = chat_branches.stretches([_text("a", ["m1"])])
        assert [[s["text"] for s in p] for p in got] == [["a"], []]

    def test_the_count_is_one_more_than_the_refs(self):
        """THE CONTRACT. One `cut_here` with text already emitted produces one
        ref and one opened row, so stretches must always be refs + 1 or the
        rows and the pieces cannot line up at commit."""
        for segs in ([_text("a")],
                     [_text("a", ["m1"]), _text("b")],
                     [_text("a", ["m1", "m2"]), _text("b", ["m3"]), _text("c")]):
            refs = sum(len(s.get("cut_after") or ()) for s in segs)
            assert len(chat_branches.stretches(segs)) == refs + 1


# --- the whole path ----------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)


@pytest.fixture
def tree_thread(tmp_path, monkeypatch):
    """Drives one real turn, interrupted N times, and reads the tree back.

    A throwaway org and throwaway file storage: a test that authed as a real
    owner would write into real chats. Tenancy is monkeypatched rather than
    resolved, because what is under test is the tree write, not the lookup.
    """
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Membership, Organization, UserAccount
    from db import chat_repo
    from services import tenancy
    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}tt-{tag}", name="Turn tree")
            s.add(o); s.flush(); oid = o.id
            u = UserAccount(id=uuid7(), email=f"tt-{tag}@example.test")
            s.add(u); s.flush(); uid = u.id
        with tenant_session(oid) as s:
            s.add(Membership(org_id=oid, user_id=uid, role="owner"))

        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
        monkeypatch.setattr(tenancy, "available", lambda: True)
        monkeypatch.setattr(tenancy, "org_and_user", lambda: (oid, uid))
        ownership.set_current_owner(1)

        state: dict = {}

        def drive(interrupts: int, drop_stretch: int | None = None,
                  move_cut_to_head: bool = False):
            from routes import chat as chat_routes

            async def go():
                tid = chat_store.create_thread("general")["id"]
                state["tid"] = tid
                chat_store.append_message(tid, "user", "start")

                run_id = f"run-{tag}"
                state["run"] = run_id
                turn = build_streaming.Turn(id=run_id, thread_id=tid, agent_slug="a")
                build_streaming.TURNS[run_id] = turn
                await build_streaming.claim_run(tid, run_id, model="m",
                                                accepts_input=True)

                # Dropping a stretch write has to be decided when the write
                # RUNS, not when it is queued: it goes on the mirror chain and
                # lands well after `_file_during_run` returns. Counting calls
                # inside the wrapper is the only window that exists.
                real_open = chat_branches.open_stretch
                calls = {"n": 0}

                async def counted(thread_ref, rid):
                    i = calls["n"]
                    calls["n"] += 1
                    if i == drop_stretch:
                        return None
                    return await real_open(thread_ref, rid)

                monkeypatch.setattr(chat_branches, "open_stretch", counted)

                says = ["one", "two", "three"]
                sends = ["wait", "also"]
                build_streaming._seg_text(turn, says[0])
                for i in range(interrupts):
                    # The real path: seals the open text, records the cut, opens
                    # the stretch row, then persists the message.
                    chat_routes._file_during_run(tid, run_id, sends[i])
                    build_streaming._seg_text(turn, says[i + 1])

                segments = turn.segments
                cut_head = list(turn.cut_head)
                if move_cut_to_head:
                    # What `carry_cuts` does when the segment carrying a cut is
                    # dropped and nothing survives before it.
                    for seg in segments:
                        cut_head.extend(seg.pop("cut_after", []) or [])

                chat_store.commit_run(
                    tid, run_id, segments, "".join(says[:interrupts + 1]),
                    cut_head=cut_head)
                await chat_log.drain_mirror_writes()

                with tenant_session(oid) as s:
                    t = chat_repo.thread_by_ref(s, tid)
                    return [(n["role"], n["content"])
                            for n in chat_repo.transcript(s, t["id"])]

            return asyncio.run(go())

        def turn_status():
            from sqlalchemy import text as sql
            with tenant_session(oid) as s:
                return s.scalar(sql("SELECT status FROM chat_turn WHERE run_id = :r"),
                                {"r": state["run"]})

        def stretch_count():
            from sqlalchemy import text as sql
            with tenant_session(oid) as s:
                return s.scalar(sql(
                    "SELECT count(*) FROM chat_message m JOIN chat_turn t "
                    "ON t.id = m.turn_id WHERE t.run_id = :r"), {"r": state["run"]})

        drive.turn_status = turn_status
        drive.stretch_count = stretch_count
        yield drive
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()
        _delete_test_orgs([oid])


@pytestmark_db
class TestAgainstARealDatabase:
    """Drives the real seal point and the real commit, then reads the tree back
    in render order and checks it says what the screen says."""

    def test_an_interrupted_reply_threads_correctly(self, tree_thread):
        rendered = tree_thread(interrupts=2)
        assert rendered == [
            ("user", "start"),
            ("assistant", "one"),
            ("user", "wait"),
            ("assistant", "two"),
            ("user", "also"),
            ("assistant", "three"),
        ]

    def test_an_uninterrupted_reply_is_still_one_row(self, tree_thread):
        """The common case must not grow rows it does not need."""
        rendered = tree_thread(interrupts=0)
        assert rendered == [("user", "start"), ("assistant", "one")]

    def test_the_turn_row_is_written(self, tree_thread):
        """Nothing outside the backfill had ever written a `chat_turn`."""
        tree_thread(interrupts=1)
        assert tree_thread.turn_status() == "finished"

    def test_every_stretch_names_its_turn(self, tree_thread):
        """So the UI can group them, and so the turn's OUTCOME can be put on the
        last one only (`chat_repo._row`)."""
        tree_thread(interrupts=2)
        assert tree_thread.stretch_count() == 3

    def test_a_lost_stretch_row_costs_the_split_and_not_the_text(self, tree_thread):
        """The database blinked for one interruption. The reply comes out cut in
        fewer places than it should be, and every word is still there: losing
        the text would be the only genuinely wrong answer."""
        rendered = tree_thread(interrupts=2, drop_stretch=1)
        assert [r for r, _ in rendered] == [
            "user", "assistant", "user", "user", "assistant"]
        said = "".join(c for r, c in rendered if r == "assistant")
        assert said == "onetwothree"

    def test_a_cut_that_moved_to_the_head_leaves_an_empty_stretch(self, tree_thread):
        """`carry_cuts` can push a ref off a dropped segment and onto `cut_head`,
        which is the reply saying the message beat its first word. The row opened
        for that stretch is then genuinely empty, and empty is correct: it renders
        as nothing, so the message reads as arriving before the reply."""
        rendered = tree_thread(interrupts=1, move_cut_to_head=True)
        assert [(r, c) for r, c in rendered] == [
            ("user", "start"), ("assistant", ""), ("user", "wait"),
            ("assistant", "onetwo"),
        ]
