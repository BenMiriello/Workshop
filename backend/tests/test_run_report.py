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
