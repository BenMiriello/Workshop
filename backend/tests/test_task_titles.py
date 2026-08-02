"""The first line of a task's text is its title, and the store enforces it.

Producers are prompts. A model that ignores "a short imperative line, under 80
characters" is not a bug that gets fixed once, so the invariant belongs where
every write passes: `_normalize`. These tests pin the two properties that make
that safe to do — the split is lossless, and it only ever fires on a first line
that is plainly a spec rather than a title.
"""
import pytest

from services import tasks


def _title(text: str) -> str:
    return tasks._normalize_text(text).split("\n")[0]


def _body(text: str) -> str:
    out = tasks._normalize_text(text)
    return out.split("\n", 1)[1] if "\n" in out else ""


def _balanced_brackets(s: str) -> bool:
    return s.count("(") == s.count(")") and s.count("[") == s.count("]")


def test_short_title_is_left_alone():
    text = "Fix the commute alerter\nObjective: it stopped firing on Fridays."
    assert tasks._normalize_text(text) == text


def test_title_at_the_limit_is_left_alone():
    head = "x" * tasks.TITLE_MAX
    assert _title(head) == head


def test_overlong_first_line_splits_on_the_last_clause_that_fits():
    # The real shape this was built for: a paragraph filed as a title, with the
    # actual title sitting in front of a colon.
    text = (
        "Generalize the workshop Agent concept: an Agent = slug + persona + "
        "memory scope + triggers, and the pipeline supplies everything else."
    )
    assert _title(text) == "Generalize the workshop Agent concept"
    assert _body(text).startswith("an Agent = slug")


def test_comma_tier_when_there_is_no_sentence_boundary():
    """A closed parenthetical is a real title; the sentence tier never sees it."""
    text = ("[SEC-A5] Personal-data partition is app-layer only (contextvar), so a "
            "same-uid agent reads every owner's files regardless of the API gate")
    assert _title(text) == "[SEC-A5] Personal-data partition is app-layer only (contextvar)"
    assert _body(text).startswith("so a same-uid agent")


def test_a_cut_never_leaves_a_bracket_open():
    """The guard that makes the comma tier safe.

    Without it this cuts at the comma inside the parenthetical and yields
    "DEFERRED DECISION (operator must weigh in" — a fragment presented as a
    whole title, which reads as damaged. Rejecting that cut falls through to an
    elision, whose ellipsis says "there is more" honestly.
    """
    text = ("DEFERRED DECISION (operator must weigh in, and it is gated on the "
            "system being proven first) about the service naming")
    title = _title(text)
    assert title != "DEFERRED DECISION (operator must weigh in"
    assert title.endswith("…")
    # Falling through to an elision loses nothing: title + body is the line.
    assert (title.rstrip("…").rstrip() + " " + _body(text)).split() == text.split()


def test_split_keeps_the_later_lines_as_the_body():
    text = "A. " + "word " * 40 + "\nObjective: the rest of the spec.\nOutput: a thing."
    body = _body(text)
    assert body.endswith("Objective: the rest of the spec.\nOutput: a thing.")


def test_an_elided_title_is_not_repeated_by_the_body():
    """The bug the operator kept seeing: open a ticket, read the title twice.

    The elided preview used to be PREPENDED with the whole original line left
    below it, so the title's words appeared again immediately underneath.
    """
    text = "Rewire " + "everything everywhere all at once " * 8
    text = text.strip()
    title, body = _title(text), _body(text)
    assert title.endswith("…")
    assert not body.startswith(title.rstrip("…")[:28])
    # Still lossless: the body picks up exactly where the title stopped.
    assert (title.rstrip("…").rstrip() + " " + body).split() == text.split()


def test_normalizing_is_idempotent():
    """Running it twice must not stack a second preview on the first.

    Lowering TITLE_MAX from 120 to 80 did exactly that in the live store: one
    ticket ended up as three lines, two of them elided previews of the third.
    """
    for text in [
        "Rewire " + "everything everywhere all at once " * 8,
        "[SEC-M2] open-wearables: get vendor secrets out of container env into a "
        "non-committed secret store, and rotate every one of them",
        "A short one\nwith a body",
    ]:
        once = tasks._normalize_text(text.strip())
        assert tasks._normalize_text(once) == once


def test_a_stacked_preview_from_an_older_threshold_is_repaired():
    stacked = (
        "[SEC-M2] open-wearables: get vendor secrets out of…\n"
        "[SEC-M2] open-wearables: get vendor secrets out of container env into a…\n"
        "[SEC-M2] open-wearables: get vendor secrets out of container env into a "
        "non-committed secret store"
    )
    out = tasks._normalize_text(stacked)
    assert out.count("[SEC-M2]") == 1


def test_split_is_lossless_when_there_is_nothing_to_cut_on():
    # No sentence break anywhere: the title becomes an elided preview and the
    # body continues from exactly where it stopped. Lossless by CONTINUATION,
    # not by repeating the line — see the no-repeat test above.
    text = "Execute a deep review pass over " + "the entity model and ticket set " * 6
    text = text.strip()
    assert _title(text).endswith("…")
    assert (_title(text).rstrip("…").rstrip() + " " + _body(text)).split() == text.split()


def test_elided_preview_breaks_on_a_word():
    text = "Rewire " + "everything everywhere all at once " * 8
    assert "  " not in _title(text)
    assert not _title(text).rstrip("…").endswith(" ")


@pytest.mark.parametrize("status", ["todo", "blocked"])
def test_add_and_update_normalize(tmp_path, status):
    long_text = (
        "Audit every surface the redesign touches and report what is stale: "
        "the toolbar, the runs page, the jobs page and the agents page."
    )
    t = tasks.add(tmp_path, text=long_text, status=status)
    assert len(t["text"].split("\n")[0]) <= tasks.TITLE_MAX

    updated = tasks.update(tmp_path, t["id"], text=long_text)
    assert len(updated["text"].split("\n")[0]) <= tasks.TITLE_MAX


def test_stored_records_heal_on_read(tmp_path):
    """A ticket written before the invariant existed normalizes when loaded."""
    long_text = "Do the thing, and also this other thing: with a lot of detail " * 3
    t = tasks.add(tmp_path, text="placeholder")
    tasks.update(tmp_path, t["id"], text="placeholder two")
    # Reach past the write path the way a hand edit or an older writer would.
    raw = tasks._load(tmp_path)
    raw[0]["text"] = long_text
    tasks._persist(tmp_path, raw)

    reloaded = tasks._load(tmp_path)[0]
    assert len(reloaded["text"].split("\n")[0]) <= tasks.TITLE_MAX
