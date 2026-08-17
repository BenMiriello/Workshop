"""One read behind the badge, the Work panel and every ticket card.

The point of `GET /chat/threads/{id}/work` is not fewer requests. It is that
three surfaces showing the same ticket cannot disagree about it, because there
is one copy of it in one payload. These tests pin the parts of that which are
easy to break later: the chat edge is `source.ref`, a chat with no task scope
gets an empty scope list rather than the workshop backlog, and a run summary
carries only fields a job record actually has.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import chat as chat_routes, tasks_all  # noqa: E402
from services import chat_store, chat_work, jobs, ownership, tasks  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private runtime, one project task store, and a job log nothing writes.

    `jobs.read` is stubbed rather than pointed at a temp log because the runs
    this asserts on are shapes (no run, a receipt, a string) rather than
    behaviour of the job store, and the sweeps that run during a reconcile read
    the same function.
    """
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: [("project:demo", root)])
    monkeypatch.setattr(tasks_all, "_scope_roots", lambda: [("project:demo", root)])

    runs: dict[str, list[dict]] = {}

    def fake_read(*, task_id=None, limit=50, **kw):
        return list(runs.get(task_id or "", []))[:limit]

    monkeypatch.setattr(jobs, "read", fake_read)

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    try:
        yield {"client": TestClient(app), "root": root, "runs": runs}
    finally:
        ownership.clear_current_owner()


def _file_ticket(root, thread_id, title="Ship the thing", **kw):
    return tasks.add(root, title=title, body="",
                     source={"kind": "chat", "ref": thread_id}, **kw)


class TestScopeKey:
    def test_a_general_chat_has_no_task_scope(self, env):
        """A general chat and an app dock file nowhere, so they get an empty
        scope list. Handing them the workshop backlog would make every chat in
        the system look like it owned the same work."""
        t = chat_store.create_thread(context_key="general")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert body["scope_key"] is None
        assert body["scope"] == []
        assert body["counts"]["all"] == 0

    def test_an_app_chat_has_no_task_scope(self, env):
        assert chat_work.task_scope_for("budget") is None

    def test_a_work_chat_maps_to_its_own_key(self, env):
        """Mirrors `taskScopeFor` in frontend/src/components/chat/chatScope.ts.
        `development` is in the list; it was once the only work scope whose chat
        could not see its own tickets."""
        for key in ("development", "project:demo", "agent:y"):
            assert chat_work.task_scope_for(key) == key

    def test_a_build_key_comes_out_as_a_real_scope(self, env):
        """`build:` is a context_key, never a scope: `scope_tree.parse` rejects
        it and `reach()` can never yield it. This used to return it verbatim, so
        the work panel compared a string against a scope-root list that could
        not contain it."""
        assert chat_work.task_scope_for("build:x") == "agent:x"

    def test_an_unknown_key_does_not_become_its_own_queue(self, env):
        """It used to be returned verbatim for anything prefixed. Unknown
        resolves to `workshop`, which is queueless for a chat, so: None."""
        assert chat_work.task_scope_for("zzz-not-a-scope") is None
        assert chat_work.task_scope_for("role:maker") is None

    def test_a_chat_with_no_task_scope_still_reports_what_it_filed(self, env):
        """A general chat cannot pick a scope, but a ticket filed from it before
        a rescope still names it as its source, and the card in that answer has
        to keep tracking it."""
        t = chat_store.create_thread(context_key="general")
        _file_ticket(env["root"], t["id"])
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [r["title"] for r in body["filed"]] == ["Ship the thing"]
        assert body["counts"]["this_chat"] == 1


class TestFiled:
    def test_the_edge_is_source_ref(self, env):
        """`job.chat_id` is declared and never written (0 of 2761 records), so
        the ticket's own source is the only edge back to a chat that works."""
        mine = chat_store.create_thread(context_key="project:demo")
        theirs = chat_store.create_thread(context_key="project:demo")
        _file_ticket(env["root"], mine["id"], title="Mine")
        _file_ticket(env["root"], theirs["id"], title="Theirs")
        tasks.add(env["root"], title="Nobody's", body="")
        body = env["client"].get(f"/chat/threads/{mine['id']}/work").json()
        assert [r["title"] for r in body["filed"]] == ["Mine"]

    def test_a_filed_ticket_is_not_repeated_in_scope(self, env):
        """The panel renders both lists. A ticket in both would be two rows for
        one entity, which is the duplication this whole read exists to remove."""
        t = chat_store.create_thread(context_key="project:demo")
        _file_ticket(env["root"], t["id"])
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert body["scope"] == []
        # ...but it is still an active ticket in the scope, so the All chip counts it.
        assert body["counts"]["all"] == 1

    def test_filed_carries_the_fields_the_card_renders(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        _file_ticket(env["root"], t["id"], labels=["infra"], effort="quick")
        rec = env["client"].get(f"/chat/threads/{t['id']}/work").json()["filed"][0]
        assert rec["scope"] == "project:demo"
        assert rec["labels"] == ["infra"]
        assert rec["effort"] == "quick"
        assert rec["state_type"] == "unstarted"
        assert rec["blocked_by"] == []

    def test_a_terminal_ticket_stays_filed(self, env):
        """The card lives for the life of the thread. A ticket that finished is
        the case the card most needs to show, not one to drop from the payload."""
        t = chat_store.create_thread(context_key="project:demo")
        done = _file_ticket(env["root"], t["id"], title="Finished")
        tasks.update(env["root"], done["id"], status="done")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [r["title"] for r in body["filed"]] == ["Finished"]
        assert body["counts"]["all"] == 0


class TestScopeRows:
    def test_only_active_tickets(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        tasks.add(env["root"], title="Open", body="")
        tasks.add(env["root"], title="Parked", body="", status="parked")
        finished = tasks.add(env["root"], title="Closed", body="")
        tasks.update(env["root"], finished["id"], status="done")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [r["title"] for r in body["scope"]] == ["Open"]

    def test_capped_at_eight_while_all_counts_the_rest(self, env):
        """A popover is not a list page. The count is honest about what the cap
        hid, which is what lets the panel link to the board instead of paging."""
        t = chat_store.create_thread(context_key="project:demo")
        for i in range(12):
            tasks.add(env["root"], title=f"T{i}", body="")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert len(body["scope"]) == chat_work.SCOPE_CAP == 8
        assert body["counts"]["all"] == 12

    def test_another_scope_does_not_leak_in(self, env, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setattr(tasks_all, "scope_roots",
                            lambda: [("project:demo", env["root"]),
                                     ("project:other", other)])
        t = chat_store.create_thread(context_key="project:demo")
        tasks.add(other, title="Elsewhere", body="")
        tasks.add(env["root"], title="Here", body="")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [r["title"] for r in body["scope"]] == ["Here"]

    def test_a_ticket_filed_in_another_scope_is_still_filed(self, env, tmp_path,
                                                            monkeypatch):
        """`filed` is ANY scope: a chat can be rescoped after it filed, and the
        card in the old answer must keep tracking the ticket it made."""
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setattr(tasks_all, "scope_roots",
                            lambda: [("project:demo", env["root"]),
                                     ("project:other", other)])
        t = chat_store.create_thread(context_key="project:demo")
        _file_ticket(other, t["id"], title="Filed elsewhere")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [(r["title"], r["scope"]) for r in body["filed"]] == \
            [("Filed elsewhere", "project:other")]


class TestRunSummary:
    def test_a_ticket_that_never_ran_has_no_run(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        _file_ticket(env["root"], t["id"])
        assert env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"] is None

    def test_the_receipt_becomes_counts(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{
            "id": "j1", "status": "done", "started_at": "2026-08-15T10:00:00+00:00",
            "ended_at": "2026-08-15T10:04:00+00:00", "trigger": "manual",
            "exit_code": 0,
            "evidence": {"kind": "receipt", "files": ["a.py", "b.py"],
                         "commits": ["abc123"], "summary": "did it"},
        }]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["id"] == "j1" and run["status"] == "done"
        assert run["exit_code"] == 0 and run["trigger"] == "manual"
        assert run["files"] == 2 and run["commits"] == 1

    def test_a_receipt_written_as_a_json_string_is_parsed(self, env):
        """Runs are closed by shell workers through the stdlib-only jobs CLI
        shim, and some of them pass the receipt as a string. The frontend already
        has to cope with both."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{
            "id": "j2", "status": "done", "trigger": "cron",
            "evidence": json.dumps({"kind": "receipt", "files": ["one.py"],
                                    "commits": []}),
        }]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["files"] == 1 and run["commits"] == 0

    def test_freeform_string_evidence_yields_null_counts(self, env):
        """Null, not zero: a zero would claim the receipt said nothing changed,
        and there is no receipt here to have said it."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{"id": "j3", "status": "failed", "exit_code": 1,
                                  "evidence": "worker died on the gate"}]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["status"] == "failed" and run["exit_code"] == 1
        assert run["files"] is None and run["commits"] is None

    def test_no_invented_fields(self, env):
        """There is no latest tool call on a run. Tool events belong to chat
        turns; a job has no step data and no stream. The key set is fixed so a
        reader never has to test for two spellings of an absent field."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{"id": "j4", "status": "running",
                                  "agent_id": "ralph", "trigger": "loop"}]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert set(run) == {"id", "status", "started_at", "ended_at",
                            "agent_name", "trigger", "exit_code",
                            "files", "commits"}

    def test_a_scope_row_carries_its_run_too(self, env):
        """The panel renders filed and scope rows with one row component, and
        the poll's cadence is driven by whether anything is in flight. A scope
        row with no run field would make both of those lie."""
        t = chat_store.create_thread(context_key="project:demo")
        other = tasks.add(env["root"], title="Someone else's", body="")
        env["runs"][other["id"]] = [{"id": "j6", "status": "running"}]
        row = env["client"].get(f"/chat/threads/{t['id']}/work").json()["scope"][0]
        assert row["run"]["id"] == "j6"

    def test_running_counts_runs_in_flight(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        live = _file_ticket(env["root"], t["id"], title="Live")
        _file_ticket(env["root"], t["id"], title="Idle")
        env["runs"][live["id"]] = [{"id": "j5", "status": "running"}]
        counts = env["client"].get(f"/chat/threads/{t['id']}/work").json()["counts"]
        assert counts["running"] == 1
        assert counts["this_chat"] == 2


class TestInbox:
    def test_the_badge_is_the_inbox_total(self, env):
        from services import inbox
        t = chat_store.create_thread(context_key="project:demo")
        inbox.raise_item(kind="question", agent="ralph", scope="project:demo",
                         title="Which database?", chat_id=t["id"])
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert [i["title"] for i in body["inbox"]] == ["Which database?"]
        assert body["counts"]["needs_you"] == 1

    def test_an_item_pointed_at_another_chat_does_not_surface(self, env):
        from services import inbox
        mine = chat_store.create_thread(context_key="project:demo")
        theirs = chat_store.create_thread(context_key="project:demo")
        inbox.raise_item(kind="question", agent="ralph", scope="project:demo",
                         title="Not yours", chat_id=theirs["id"])
        body = env["client"].get(f"/chat/threads/{mine['id']}/work").json()
        assert body["inbox"] == []
        assert body["counts"]["needs_you"] == 0

    def test_the_reconcilers_run(self, env, monkeypatch):
        """The inbox route reconciles before an open read, and this read has to
        run the same pass or the panel shows a blocked agent one poll late and
        the badge and the inbox page disagree."""
        calls = []
        from services import inbox
        monkeypatch.setattr(inbox, "reconcile", lambda: calls.append(1))
        t = chat_store.create_thread(context_key="project:demo")
        env["client"].get(f"/chat/threads/{t['id']}/work")
        assert calls == [1]

    def test_a_blocked_ticket_becomes_a_question_here(self, env):
        """End to end through the real reconciler: run_inbox turns an `awaiting`
        record into the question the panel renders."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Needs a key")
        tasks.update(env["root"], tk["id"], status="blocked",
                     awaiting={"kind": "input", "ask": "Which API key?"})
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert any("Which API key?" in (i.get("body") or "") for i in body["inbox"])
        assert body["counts"]["needs_you"] >= 1


class TestRoute:
    def test_an_unknown_thread_is_404(self, env):
        assert env["client"].get("/chat/threads/nope/work").status_code == 404

    def test_the_payload_has_exactly_the_declared_keys(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert set(body) == {"inbox", "filed", "scope", "counts", "scope_key"}
        assert set(body["counts"]) == {"needs_you", "running", "this_chat", "all"}
