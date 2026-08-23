"""One read behind the badge, the Work panel and every ticket card.

The point of `GET /chat/threads/{id}/work` is not fewer requests. It is that
three surfaces showing the same ticket cannot disagree about it, because there
is one copy of it in one payload. These tests pin the parts of that which are
easy to break later: the chat edge is `source.ref`, a chat with no task scope
gets an empty scope list rather than the workshop backlog, and a run summary
carries only fields a job record actually has.
"""
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
    this asserts on are shapes (no run, a receipt, a non-receipt dict) rather
    than behaviour of the job store, and the sweeps that run during a reconcile
    read the same function.
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

    def test_an_app_chat_has_no_task_scope_when_unlinked(self, env, monkeypatch):
        """An installed-only app (no working tree) still gets no queue --
        app-is-the-entity-2026-08-20.md sec 5.1: `_QUEUELESS_PREFIXES` used to
        blanket every `app:` scope; now it asks `task_scope.root_for_context`,
        and an unlinked app's root is None exactly like before."""
        from services import task_scope
        monkeypatch.setattr(task_scope, "root_for_context", lambda scope: None)
        assert chat_work.task_scope_for("app:zzz-app") is None

    def test_an_app_chat_gets_a_task_scope_when_linked_to_a_project(
            self, env, monkeypatch, tmp_path):
        """The reconsidered half of the same change: an app linked to a
        project (services.app_links) now owns a queue, because it owns a
        working tree to file tickets into."""
        from services import task_scope
        monkeypatch.setattr(task_scope, "root_for_context", lambda scope: tmp_path)
        assert chat_work.task_scope_for("app:zzz-app") == "app:zzz-app"

    def test_a_work_chat_maps_to_its_own_key(self, env):
        """Mirrors `taskScopeFor` in frontend/src/components/chat/chatScope.ts.
        `development` is in the list; it was once the only work scope whose chat
        could not see its own tickets."""
        for key in ("development", "project:demo", "agent:y"):
            assert chat_work.task_scope_for(key) == key

    def test_the_deleted_build_kind_opens_no_queue(self, env):
        """`build:<slug>` was a second spelling of `agent:<slug>`; both the
        spelling and the translation are deleted, so it is now junk and resolves
        to the queueless root rather than to a scope of its own."""
        assert chat_work.task_scope_for("build:x") is None

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

    def test_a_non_receipt_dict_yields_null_counts(self, env):
        """Counts come off the receipt shape and nothing else. A halted sweep
        wrote real evidence, but it is not a receipt and did not count files.

        There is deliberately no case here for evidence arriving as a STRING:
        `services.jobs` folds the two retired string producers to
        `{"summary": ...}` at the store's read boundary, so no string reaches
        this module. That coercion is pinned in test_jobs_cache.py."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{
            "id": "j2", "status": "failed", "trigger": "cron",
            "evidence": {"halted": "tree busy"},
        }]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["files"] is None and run["commits"] is None

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
                            "files", "commits", "halted", "refusal"}

    def test_a_refused_run_carries_what_refused_it(self, env):
        """The chat's run card had no way to say why a run did nothing.

        A run a guard turned away closes `cancelled` with the reason in
        `evidence`, and this summary dropped everything that was not a receipt.
        So the card read `an agent  job_17  cancelled  0s`, which is what a crash
        looks like (2026-08-23), and the reason existed only in
        `.runtime/queue-runner.log` on the host."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        reason = ("refused: the development scope has used 1.97M of its 1.50M "
                  "token allowance for the last 5 hours")
        env["runs"][tk["id"]] = [{
            "id": "j7", "status": "cancelled",
            "evidence": {"halted": reason, "refusal": {
                "by": "usage-budget", "kind": "scope_ceiling", "reason": reason,
                "scope": "development", "used": 1972531, "limit": 1500000,
                "window_s": 18000,
                "next_admissible_at": "2026-08-23T23:11:19+00:00"}},
        }]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["halted"] == reason
        assert run["refusal"]["scope"] == "development"
        assert run["refusal"]["used"] == 1972531
        assert run["refusal"]["limit"] == 1500000
        assert run["refusal"]["next_admissible_at"].startswith("2026-08-23T23:11")

    def test_a_run_that_worked_carries_no_refusal(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"])
        env["runs"][tk["id"]] = [{"id": "j8", "status": "done",
                                  "evidence": {"kind": "receipt", "files": ["a"],
                                               "commits": []}}]
        run = env["client"].get(
            f"/chat/threads/{t['id']}/work").json()["filed"][0]["run"]
        assert run["halted"] is None and run["refusal"] is None

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

    def test_a_blocked_ticket_is_read_as_a_question_off_the_ticket(self, env):
        """No producer, no copy, no reconcile pass: the `awaiting` record IS the
        question, so it renders the moment the ticket carries it."""
        from services import inbox
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Needs a key")
        tasks.update(env["root"], tk["id"], status="blocked",
                     awaiting={"kind": "input", "ask": "Which API key?"})

        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()

        row = next(i for i in body["inbox"] if i["ticket_id"] == tk["id"])
        assert row["body"] == "Which API key?"
        assert body["counts"]["needs_you"] == 1
        # Nothing was written to the inbox store to say any of that.
        assert inbox.list_items(state="open") == []

    def test_a_finished_ticket_needs_you_with_no_inbox_row_at_all(self, env):
        """THE POINT OF THE WHOLE CHANGE. `in_review` is the fact; the row is
        derived from it, and the store behind the panel stays empty."""
        from services import chat_work as cw, inbox
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Move the lock off /tmp")
        tasks.update(env["root"], tk["id"], status="in_review")

        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()

        assert inbox._load() == []
        row = next(i for i in body["inbox"] if i["ticket_id"] == tk["id"])
        assert row["id"] == cw.needs_you_id("project:demo", tk["id"])
        assert row["options"] == ["Accept", "Send back"]
        assert body["counts"]["needs_you"] == 1
        # And it says NOTHING beyond its state: "the work is finished and
        # waiting on you" is the definition of in_review, so every row would
        # carry the same sentence. The row shows the title instead (§3.3).
        assert row["body"] == ""
        assert row["title"] == "Move the lock off /tmp"

    def test_a_finished_run_leaves_no_inbox_item(self, env):
        """A run that ended with nobody watching used to raise a `notify`, and
        744 of those were open on the live record with none ever actioned. The
        run is on the run and on its ticket; see one-fact-one-place §2."""
        from services import inbox
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Swept the queue")
        env["runs"][tk["id"]] = [{"id": "j9", "status": "done", "trigger": "cron",
                                  "ended_at": "2026-08-22T12:00:00+00:00",
                                  "tasks_done": 2, "agent_id": "ralph"}]

        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()

        assert inbox._load() == []
        assert body["inbox"] == []
        assert body["counts"]["needs_you"] == 0
        # And the run is still reachable: it is on the ticket.
        assert body["filed"][0]["run"]["id"] == "j9"

    def test_the_row_names_who_did_the_work_not_the_operator(self, env):
        """`run_inbox` set the row's agent to `assignee or owner`, and `tasks`
        defaults `owner` to `operator`, so an operator-filed ticket read
        "operator asks" behind an `O`: the surface telling him he is asking
        himself. The run knows who actually did it."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Move the lock off /tmp")
        tasks.update(env["root"], tk["id"], status="in_review")
        assert tasks.get_task(env["root"], tk["id"])["owner"] == "operator"
        env["runs"][tk["id"]] = [{"id": "j9", "status": "done", "agent_id": "ralph"}]

        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()

        row = next(i for i in body["inbox"] if i["ticket_id"] == tk["id"])
        assert row["agent"] == "ralph"
        assert row["run_id"] == "j9"

    def test_a_dismissed_block_stops_asking_without_changing_status(self, env):
        """Dismiss is the one thing that takes a waiting ticket out of this
        column without moving it: the block stays a block, it just stops
        asking."""
        t = chat_store.create_thread(context_key="project:demo")
        tk = _file_ticket(env["root"], t["id"], title="Needs a key")
        tasks.update(env["root"], tk["id"], status="blocked",
                     awaiting={"kind": "input", "ask": "Which API key?",
                               "dismissed": True})

        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()

        assert body["inbox"] == []
        assert body["counts"]["needs_you"] == 0


class TestRoute:
    def test_an_unknown_thread_is_404(self, env):
        assert env["client"].get("/chat/threads/nope/work").status_code == 404

    def test_the_payload_has_exactly_the_declared_keys(self, env):
        t = chat_store.create_thread(context_key="project:demo")
        body = env["client"].get(f"/chat/threads/{t['id']}/work").json()
        assert set(body) == {"inbox", "filed", "scope", "counts", "scope_key"}
        assert set(body["counts"]) == {"needs_you", "running", "this_chat", "all"}


# --- the row has to say something DIFFERENT from the row above it -----------
#
# Operator, 2026-08-22, over a screenshot of twenty rows: "no useful info at a
# glance". Every one read "operator asks" above one identical sentence. Both
# halves are fixed here; the third half (the title leading the card) is in
# frontend/src/components/chat/work/TicketRow.tsx.

def test_the_finished_boilerplate_is_not_treated_as_an_ask():
    """The sweep stamped one sentence onto every in_review ticket. It is no
    longer written, and it is dropped on read for the ones that already carry
    it, so no migration is needed to make a stored row read correctly."""
    task = {
        "id": "t1", "status": "in_review",
        "awaiting": {"question": "The work on this ticket is finished and "
                                 "waiting on you. Accept it to mark it done."},
    }
    assert chat_work.needs_you_ask(task) == ""


def test_a_real_question_still_reaches_the_row():
    task = {"id": "t1", "status": "blocked",
            "awaiting": {"question": "Which store should win?"}}
    assert chat_work.needs_you_ask(task) == "Which store should win?"


def test_a_blocked_ticket_still_quotes_its_reason():
    task = {"id": "t1", "status": "blocked", "awaiting": {},
            "blocked_reason": "the migration needs a decision"}
    assert chat_work.needs_you_ask(task) == "the migration needs a decision"


def test_an_unknown_worker_is_nobody_rather_than_the_scope_name():
    """`development asks` is the same non-fact as `operator asks`, one word
    further out: it names the queue, not anyone who did anything. Empty, and
    the row leads with its title instead."""
    who = chat_work._who_did_the_work({"id": "t1"}, None, "development", {})
    assert who == ""


def test_a_sweep_worked_ticket_is_attributed_to_the_run_that_did_it():
    """A queue-picked ticket has a run but no agent identity. Its trigger is
    the true answer, and it is the one the run feed already shows."""
    who = chat_work._who_did_the_work(
        {"id": "t1"}, {"agent_name": None, "trigger": "loop"}, "development", {})
    assert who == "Autonomous run"


def test_a_named_agent_still_wins():
    who = chat_work._who_did_the_work(
        {"id": "t1"}, {"agent_name": "reviewer", "trigger": "loop"},
        "development", {})
    assert who == "reviewer"


def test_a_stored_row_naming_a_ticket_does_not_double_up_with_its_derived_row():
    """Deleting `run_inbox`'s producer left every row it had already written
    open in the store, so the panel showed 20 rows for 11 waiting tickets: each
    one once derived and once legacy. The ticket is the fact; the stored copy is
    the second answer to one question that deletion was meant to end."""
    stored = [
        {"id": "legacy-uuid", "kind": "question", "ticket_id": "t1",
         "title": "same ticket, stored", "body": "", "agent": "x",
         "scope": "development", "options": [], "chat_id": None,
         "run_id": None, "snoozed_until": None},
        {"id": "ambient", "kind": "notify", "ticket_id": None,
         "title": "an agent with something to say", "body": "", "agent": "keeper",
         "scope": "development", "options": [], "chat_id": None,
         "run_id": None, "snoozed_until": None},
    ]
    kept = [r for r in stored if not r.get("ticket_id")]
    assert [r["id"] for r in kept] == ["ambient"], \
        "a stored row with a ticket behind it must not surface beside the derived one"
