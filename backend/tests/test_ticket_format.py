"""The ticket body format, and the checker that reads it, agree.

They did not. The chat dialog composed acceptance as "Done when: ...", while
`ticket_readiness` looked for "Done:" or "Output:" — so a ticket that plainly
stated how it would be judged failed the acceptance check, and the operator would
have seen a checklist complaining about something the ticket said. The labels now
live in one table (`services/ticket_format`) that producers compose with and the
checker matches against.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ticket_drafts, ticket_format as tf, ticket_readiness as tr  # noqa: E402


def test_compose_writes_the_canonical_labels():
    body = tf.compose(objective="One binding per scope.", output="A test covers it.",
                      entry_points="backend/services/tracker_bindings.py",
                      boundaries="Do not touch the sync engine.")
    assert body.splitlines() == [
        "Objective: One binding per scope.",
        "Output: A test covers it.",
        "Start from: backend/services/tracker_bindings.py",
        "Boundaries: Do not touch the sync engine.",
    ]


def test_a_composed_body_passes_every_readiness_check():
    """The loop that was open: the checker existed and nothing produced a body
    that satisfied it."""
    body = tf.compose(objective="One binding per scope.", output="A test covers it.",
                      entry_points="backend/services/tracker_bindings.py",
                      boundaries="Do not touch the sync engine.")
    assert tr.check({"title": "Bind a scope to a tracker", "body": body})["ok"] is True


def test_empty_fields_are_omitted_not_left_as_headings():
    body = tf.compose(objective="Just this.")
    assert body == "Objective: Just this."


def test_prose_the_model_wrote_anyway_is_kept():
    body = tf.compose(objective="Do the thing.", extra="Some extra context.")
    assert "Objective: Do the thing." in body
    assert "Some extra context." in body


@pytest.mark.parametrize("line", [
    "Output: a test covers it",
    "Done when: a test covers it",
    "Done: a test covers it",
    "Acceptance: a test covers it",
    "**Output:** a test covers it",
    "## Done looks like: a test covers it",
    "Verify - a test covers it",
])
def test_the_ways_a_model_actually_writes_acceptance_all_count(line):
    """Producers are prompts. Rejecting a ticket over the wording of a heading it
    got essentially right is pedantry that costs a run."""
    assert tf.states(line, "output") is True


def test_done_when_was_the_real_bug():
    """The exact string the chat dialog emitted, against the check it failed."""
    body = "Objective: fix it.\nDone when: the test passes.\nStart from: `pytest`"
    missing = {m["check"] for m in tr.check({"title": "Fix it", "body": body})["missing"]}
    assert "acceptance" not in missing


def test_a_label_is_not_matched_mid_sentence():
    """"...and the output: whatever" is prose, not a field."""
    assert tf.states("we discussed the output: whatever", "output") is False


def test_boundaries_stated_in_prose_satisfy_the_check_without_a_touches_lane():
    body = tf.compose(objective="Do it.", output="It is done.",
                      entry_points="a/b.py", boundaries="Nothing outside a/.")
    assert tr.check({"title": "Do it", "body": body})["ok"] is True


def test_a_proposed_draft_composes_into_a_ready_body():
    """End to end from what the model returns to what the checker says."""
    raw = ('{"tickets":[{"title":"Bind a scope to a tracker",'
           '"objective":"One binding per scope, chosen in Settings.",'
           '"output":"GET /api/trackers returns it and a test covers it.",'
           '"entry_points":"backend/services/tracker_bindings.py",'
           '"boundaries":"Do not touch the sync engine.",'
           '"confidence":"decided"}]}')
    draft = ticket_drafts._parse(raw)[0]
    assert draft["title"] == "Bind a scope to a tracker"
    assert draft["body"].startswith("Objective: One binding per scope")
    assert tr.check({"title": draft["title"], "body": draft["body"]})["ok"] is True


def test_a_model_that_ignored_the_fields_still_yields_a_draft():
    """Tolerance is the point: a reply with only prose must not be discarded."""
    raw = '{"tickets":[{"title":"Fix the alerter","body":"It stopped on Fridays."}]}'
    draft = ticket_drafts._parse(raw)[0]
    assert draft["title"] == "Fix the alerter"
    assert "It stopped on Fridays." in draft["body"]


# ---- effort: one vocabulary ---------------------------------------------------
# There were three. The store documented "s"|"m"|"l", the task-proposal card and
# the drafting prompt say quick|standard|deep, and the live stores held both. That
# was not cosmetic: scripts/project_ralph.sh maps effort to an inner-loop cap and
# a model tier with a case-SENSITIVE bash `case` on `quick|q|s` / `deep|d|l`, so
# 24 of the 38 tickets that set an effort stored it as uppercase "S" or "L", fell
# through to the default branch, and had no effect at all.


@pytest.mark.parametrize("stored,canonical", [
    ("S", "quick"), ("s", "quick"), ("quick", "quick"), ("Q", "quick"),
    ("M", "standard"), ("standard", "standard"), ("Medium", "standard"),
    ("L", "deep"), ("deep", "deep"), ("XL", "deep"),
])
def test_every_effort_spelling_canonicalizes(stored, canonical):
    from services import tasks
    assert tasks._normalize_effort(stored) == canonical


@pytest.mark.parametrize("junk", ["bogus", "", None, "  ", "urgent"])
def test_an_unrecognized_effort_is_none_not_standard(junk):
    """A typo must not read as a deliberate hint."""
    from services import tasks
    assert tasks._normalize_effort(junk) is None


def test_the_canonical_words_are_what_the_sweep_matches():
    """The sweep's case arms are `quick|q|s` and `deep|d|l`, lowercase. The store
    must emit words those arms actually match."""
    from services import tasks
    assert set(tasks.EFFORTS) == {"quick", "standard", "deep"}
    assert all(e == e.lower() for e in tasks.EFFORTS)


def test_a_stored_uppercase_effort_heals_on_read(tmp_path):
    from services import tasks
    t = tasks.add(tmp_path, title="A ticket", effort="S")
    assert tasks.get_task(tmp_path, t["id"])["effort"] == "quick"


def test_a_quick_ticket_still_waives_entry_points_through_the_alias(tmp_path):
    from services import tasks
    t = tasks.add(tmp_path, title="Bump the interval",
                  body="Objective: stop hammering.\nOutput: it is 60s.\nBoundaries: none.",
                  effort="S")
    got = tasks.get_task(tmp_path, t["id"])
    assert "entry_points" not in {m["check"] for m in tr.check(got)["missing"]}
