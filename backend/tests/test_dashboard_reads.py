"""The three reads the dashboard needs, and the rule each one must not break.

The dashboard shows many chats at once and one instance-wide "needs you"
column. Neither is a new fact: the open chat already renders whether a turn is
in flight, and the chat's work panel already builds the waiting column. What
was missing was a way to ask for either WITHOUT one request per chat, and the
hazard in adding that is a second derivation which can disagree with the first.

So these tests pin the agreement, not the plumbing:
  * `running` is stamped over whichever list source answered, so the file fold
    and the Postgres path cannot differ on it.
  * the context denominator comes from the ONE table, including its
    self-correction, so a list row and the composer meter cannot render two
    different percentages for one chat.
  * the global column applies the same waiting predicate and the same
    ticket-shadow rule as the per-chat one, so a ticket is never shown twice.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import (chat_list_pg, chat_store, chat_work, context_window,  # noqa: E402
                      inbox, jobs, ownership, tasks)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private runtime and one task store, the same shape
    `test_chat_work_read` uses."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: [("project:demo", root)])
    monkeypatch.setattr(tasks_all, "_scope_roots", lambda: [("project:demo", root)])
    monkeypatch.setattr(jobs, "read", lambda **kw: [])
    try:
        yield {"root": root}
    finally:
        ownership.clear_current_owner()


class TestARowSaysWhetherItIsRunning:
    def test_a_thread_with_a_turn_in_flight_is_running(self, env):
        t = chat_store.create_thread(context_key="general")
        rows = chat_store.stamp_running(chat_store.list_threads())
        assert [r["running"] for r in rows if r["id"] == t["id"]] == [False]

        chat_store.begin_turn(t["id"], "turn-1")
        rows = chat_store.stamp_running(chat_store.list_threads())
        assert [r["running"] for r in rows if r["id"] == t["id"]] == [True]

        chat_store.release_turn(t["id"], "turn-1")
        rows = chat_store.stamp_running(chat_store.list_threads())
        assert [r["running"] for r in rows if r["id"] == t["id"]] == [False]

    def test_it_is_stamped_over_rows_from_any_source(self, env):
        """The point of stamping over finished rows rather than inside the two
        row builders: a Postgres row gets the same answer as a folded one,
        because it is the same code reading the same file."""
        t = chat_store.create_thread(context_key="general")
        chat_store.begin_turn(t["id"], "turn-2")
        pg_shaped = [{"id": t["id"], "title": "from postgres"}]
        assert chat_store.stamp_running(pg_shaped)[0]["running"] is True

    def test_a_row_with_no_thread_behind_it_is_not_running(self, env):
        """Never a crash and never a true: a row whose thread is gone reports
        the only thing that is safe to report."""
        assert chat_store.stamp_running([{"id": "gone"}])[0]["running"] is False


class TestARowSaysHowFullItsWindowIs:
    def test_no_completed_turn_reports_nothing_rather_than_zero(self, env):
        """A list row does not estimate. The estimate needs the draft and only
        the composer has that, so null is the honest answer and 0% would be a
        confident lie -- the exact failure `contextReading` was written for."""
        t = chat_store.create_thread(context_key="general")
        row = next(r for r in chat_store.list_threads() if r["id"] == t["id"])
        assert row["context_tokens"] is None
        assert row["context_window"] is None

    def test_the_denominator_comes_from_the_one_table(self, env):
        t = chat_store.create_thread(context_key="general", model="claude-opus-5")
        chat_store.set_thread_context_tokens(t["id"], 250_000)
        row = next(r for r in chat_store.list_threads() if r["id"] == t["id"])
        assert row["context_tokens"] == 250_000
        assert row["context_window"] == context_window.window_for("claude-opus-5")

    def test_a_measurement_wider_than_the_table_corrects_the_table(self, env):
        """The self-correction has to survive the move out of routes/chat.py.
        Without it a stale table pins the ring at 100% and nothing on screen
        can distinguish that from a genuinely full chat."""
        t = chat_store.create_thread(context_key="general",
                                     model="claude-haiku-4-5")
        chat_store.set_thread_context_tokens(t["id"], 250_000)
        row = next(r for r in chat_store.list_threads() if r["id"] == t["id"])
        assert row["context_window"] == 1_000_000

    def test_the_postgres_row_resolves_it_the_same_way(self, env):
        """Both list sources answer field for field; the denominator is part of
        that. Shaped straight from a mirror row, which is what that path reads."""
        row = chat_list_pg._shape_row({
            "external_id": "x", "context_key": "general", "title": "t",
            "message_count": 0, "last_message": "",
            "model": "claude-haiku-4-5", "context_tokens": 250_000,
        })
        assert row["context_window"] == 1_000_000
        assert "running" not in row  # stamped later, over whichever source won


class TestWhatWantsTheOperator:
    def test_a_waiting_ticket_is_one_row(self, env):
        t = tasks.add(env["root"], title="Approve the migration", body="")
        tasks.update(env["root"], t["id"], status="in_review")
        out = chat_work.needs_you()
        assert [i["ticket_id"] for i in out["items"]] == [t["id"]]
        # `in_review` is finished work waiting to be accepted, so it counts as a
        # review rather than a question. See `_needs_you_row`.
        assert out["counts"]["review"] == 1
        assert out["total"] == 1

    def test_a_dismissed_block_stops_asking(self, env):
        """The one thing that takes a waiting ticket out of the column without
        changing its status, and it must mean the same thing here as in a
        chat's own panel."""
        t = tasks.add(env["root"], title="Blocked on a decision", body="")
        tasks.update(env["root"], t["id"], status="blocked",
                     awaiting={"ask": "Which database?"})
        assert chat_work.needs_you()["total"] == 1
        tasks.update(env["root"], t["id"],
                     awaiting={"ask": "Which database?", "dismissed": True})
        assert chat_work.needs_you()["total"] == 0

    def test_a_stored_row_naming_a_ticket_does_not_double_it(self, env):
        """`run_inbox` was deleted for being the second answer to one question,
        and nine tickets still appeared twice in the panel afterwards because
        the rows it had already written stayed open. The global column must
        apply the same shadow rule."""
        t = tasks.add(env["root"], title="Approve the migration", body="")
        tasks.update(env["root"], t["id"], status="in_review")
        inbox.raise_item(kind="review", agent="reviewer", scope="project:demo",
                         title="Approve the migration", body="legacy row",
                         ticket_id=t["id"])
        out = chat_work.needs_you()
        assert len(out["items"]) == 1
        assert out["items"][0]["ticket_id"] == t["id"]

    def test_a_notification_is_not_something_that_needs_you(self, env):
        """It is an agent saying what it DID. There is nothing to answer,
        approve or unblock, so it does not belong in a count of what is waiting
        on someone -- "Run finished" and "Consolidated 4 notes this pass" were
        both sitting in the operator's Needs-you card."""
        inbox.raise_item(kind="notify", agent="nightly-sync",
                         scope="project:demo", title="Run finished", body="")
        out = chat_work.needs_you()
        assert out["items"] == []
        assert out["total"] == 0

    def test_an_ambient_question_with_no_ticket_still_surfaces(self, env):
        inbox.raise_item(kind="question", agent="memory-keeper",
                         scope="project:demo", title="Which port is current?",
                         body="One note says 8012, another 8013.")
        out = chat_work.needs_you()
        assert [i["kind"] for i in out["items"]] == ["question"]
        assert out["counts"]["question"] == 1

    def test_a_rows_kind_follows_the_ticket_status(self, env):
        """`in_review` is finished work waiting to be accepted; `blocked` is
        stopped until you answer. Every ticket-derived row used to claim to be a
        question, so the card led with "who is asking" over work that was not
        asking, and the sort could not rank a real question above an approval."""
        a = tasks.add(env["root"], title="finished work", body="")
        tasks.update(env["root"], a["id"], status="in_review")
        b = tasks.add(env["root"], title="stopped work", body="")
        tasks.update(env["root"], b["id"], status="blocked")
        by_title = {i["title"]: i["kind"] for i in chat_work.needs_you()["items"]}
        assert by_title == {"finished work": "review", "stopped work": "question"}

    def test_the_total_counts_the_whole_set_not_the_page(self, env):
        """The alarm must not silently count only what fitted. A badge that caps
        itself at the page size is one the operator learns to distrust."""
        for i in range(5):
            t = tasks.add(env["root"], title=f"blocked {i}", body="")
            tasks.update(env["root"], t["id"], status="blocked")
        out = chat_work.needs_you(limit=2)
        assert len(out["items"]) == 2
        assert out["total"] == 5
        assert out["counts"]["question"] == 5

    def test_a_question_outranks_an_approval(self, env):
        """One sort, shared with the chat's read. A blocked agent must not sit
        under finished work in one surface and above it in the other -- and on a
        card that shows three of nine rows, the order IS the surface."""
        a = tasks.add(env["root"], title="finished work", body="")
        tasks.update(env["root"], a["id"], status="in_review")
        b = tasks.add(env["root"], title="stopped work", body="")
        tasks.update(env["root"], b["id"], status="blocked")
        assert [i["kind"] for i in chat_work.needs_you()["items"]] == \
            ["question", "review"]
