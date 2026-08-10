"""The report a finished run posts back into the chat that asked for it.

The point of the report is that it is SHORT and HONEST. Short, because the whole
value of delegating work out of a conversation is that the delegated context
does not come back. Honest, because a run that did not complete cleanly must not
read like one that did — that is the failure mode the completion gate exists to
prevent, and a cheerful summary would reintroduce it one layer up.
"""
from services import run_report


def _job(**over):
    j = {"id": "job_123"}
    j.update(over)
    return j


def _ticket(text, status="done"):
    return {"id": "t1", "text": text, "status": status}


def test_names_the_run_and_what_it_did():
    out = run_report._summarize(_job(), [_ticket("Fix the picker")], "done")
    assert "job_123" in out
    assert "finished" in out
    assert "Fix the picker" in out


def test_only_the_first_line_of_a_ticket_body():
    """A ticket body carries objective/output/boundaries. The report is an index,
    not a copy — pulling the whole spec back in defeats the point."""
    out = run_report._summarize(
        _job(), [_ticket("Short title\nObjective: a very long spec that should not appear")], "done")
    assert "Short title" in out
    assert "Objective:" not in out


def test_a_failed_run_says_nothing_is_verified():
    out = run_report._summarize(_job(), [_ticket("Something", status="blocked")], "failed")
    assert "failed" in out
    assert "Nothing here is verified" in out


def test_a_clean_run_makes_no_such_disclaimer():
    out = run_report._summarize(_job(), [_ticket("Something")], "done")
    assert "nothing here is verified" not in out


def test_long_ticket_lists_are_capped_with_a_count():
    tickets = [_ticket(f"Ticket {i}") for i in range(12)]
    out = run_report._summarize(_job(), tickets, "done")
    assert "+7 more" in out
    assert out.count("- done:") == 5


def test_reviewer_gate_is_surfaced_when_present():
    out = run_report._summarize(_job(evidence={"reviewer": "approved"}), [_ticket("x")], "done")
    assert "Reviewer gate: approved" in out


def test_no_evidence_means_no_gate_line():
    out = run_report._summarize(_job(), [_ticket("x")], "done")
    assert "Reviewer gate" not in out


def test_cancelled_reads_as_cancelled():
    out = run_report._summarize(_job(), [_ticket("x", status="todo")], "cancelled")
    assert "was cancelled" in out


def test_owner_lookup_returns_none_for_an_unknown_thread():
    assert run_report.owner_of_thread("no-such-thread-id") is None


def test_owner_lookup_tolerates_an_empty_id():
    assert run_report.owner_of_thread("") is None


# --- Pre-claim failures must still reach the chat (W1 item 3) ---------------
#
# `job_ids` on a ticket is written by the CLAIM. A run that stalls BEFORE
# claiming (budget refused, tree busy, ticket not claimable) therefore matched
# no ticket at all, `post_for_job` returned 0, and the chat's last word stayed
# "run started" forever. The parent Job the chat card holds carries the ticket in
# its own `task_id`; that is the fallback.

def _post_env(monkeypatch, tmp_path, job, tickets):
    """Wire post_for_job to an in-memory job + ticket store and capture posts."""
    from services import jobs, tasks, task_scope, chat_store, ownership

    monkeypatch.setattr(jobs, "get", lambda jid: job if jid == job["id"] else None)
    monkeypatch.setattr(tasks, "_load", lambda root: list(tickets))
    monkeypatch.setattr(task_scope, "root_for_job", lambda j: tmp_path)
    monkeypatch.setattr(run_report, "owner_of_thread", lambda tid: 6)
    monkeypatch.setattr(ownership, "owner_or_none", lambda: 6)
    monkeypatch.setattr(ownership, "set_current_owner", lambda o: None)
    monkeypatch.setattr(ownership, "clear_current_owner", lambda: None)
    posted: list[dict] = []
    monkeypatch.setattr(
        chat_store, "append_message",
        lambda tid, role, text, meta=None: posted.append(
            {"thread": tid, "text": text, "meta": meta}))
    return posted


def _chat_ticket(tid="t1", thread="thread-9", **over):
    t = {"id": tid, "text": "Fix the thing", "status": "todo",
         "source": {"kind": "chat", "ref": thread}}
    t.update(over)
    return t


def test_a_run_that_never_claimed_still_reports_to_its_chat(monkeypatch, tmp_path):
    job = {"id": "job_parent", "task_id": "t1",
           "evidence": {"halted": "autonomous budget spent"}}
    ticket = _chat_ticket()          # no job_ids: the claim never happened
    posted = _post_env(monkeypatch, tmp_path, job, [ticket])

    assert run_report.post_for_job("job_parent", "cancelled") == 1
    assert posted[0]["thread"] == "thread-9"
    assert "autonomous budget spent" in posted[0]["text"]
    assert "Nothing here is verified" in posted[0]["text"]


def test_the_fallback_only_takes_the_jobs_own_ticket(monkeypatch, tmp_path):
    """The fallback must not sweep in unrelated tickets from the same tree."""
    job = {"id": "job_parent", "task_id": "t1"}
    posted = _post_env(monkeypatch, tmp_path, job,
                       [_chat_ticket("t1"), _chat_ticket("t2", thread="other")])
    assert run_report.post_for_job("job_parent", "cancelled") == 1
    assert posted[0]["thread"] == "thread-9"


def test_job_ids_still_wins_when_present(monkeypatch, tmp_path):
    """The normal path is unchanged: a claimed ticket matches by job_ids and the
    Job's own task_id is not consulted."""
    job = {"id": "job_child", "task_id": "t1"}
    worked = _chat_ticket("t2", thread="claimed-thread", job_ids=["job_child"])
    posted = _post_env(monkeypatch, tmp_path, job, [_chat_ticket("t1"), worked])
    assert run_report.post_for_job("job_child", "done") == 1
    assert posted[0]["thread"] == "claimed-thread"


def test_a_job_with_no_task_id_reports_nothing(monkeypatch, tmp_path):
    job = {"id": "job_parent"}
    _post_env(monkeypatch, tmp_path, job, [_chat_ticket()])
    assert run_report.post_for_job("job_parent", "cancelled") == 0


def test_a_halt_reason_is_stated_plainly():
    out = run_report._summarize(
        _job(evidence={"halted": "no queue runner is polling"}), [_ticket("x", "todo")],
        "cancelled")
    assert "Stopped before working: no queue runner is polling." in out
