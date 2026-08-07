"""A ticket has a `title` and a `body`, and the store keeps them well-formed.

Producers are prompts. A model that ignores "a short imperative line, under 80
characters" is not a bug that gets fixed once, so the invariant belongs where
every write passes: `_normalize`. Callers may hand the store one blob (`text`)
or the pair; either way `split_text` decides, and these tests pin the properties
that make that safe — the split is lossless apart from the separator it cuts on,
it is idempotent, it only fires on a first line that is plainly a spec, and it
never elides.
"""
import json

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
    whole title, which reads as damaged. Rejecting that cut falls through to a
    word-boundary break, which keeps the remainder in the body.
    """
    text = ("DEFERRED DECISION (operator must weigh in, and it is gated on the "
            "system being proven first) about the service naming")
    title = _title(text)
    assert title != "DEFERRED DECISION (operator must weigh in"
    # Lossless: title + body is the original line, word for word.
    assert (title + " " + _body(text)).split() == text.split()


def test_split_keeps_the_later_lines_as_the_body():
    text = "A. " + "word " * 40 + "\nObjective: the rest of the spec.\nOutput: a thing."
    body = _body(text)
    assert body.endswith("Objective: the rest of the spec.\nOutput: a thing.")


def test_a_long_title_is_never_elided():
    """No title is ever truncated with an ellipsis.

    An ellipsis claimed information had been dropped when it had not, and
    because the preview was written back to the store, a later change to
    TITLE_MAX stacked a second preview on the first. 32 of 179 live tickets were
    left in that state; the break is now a plain word boundary.
    """
    text = "Rewire " + "everything everywhere all at once " * 8
    text = text.strip()
    title, body = _title(text), _body(text)
    assert not title.endswith("…")
    assert not body.startswith(title[:28])
    # Lossless: the body picks up exactly where the title stopped.
    assert (title + " " + body).split() == text.split()


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
    # No sentence break anywhere: the title breaks at the last word that fits and
    # the body continues from exactly where it stopped. Lossless by CONTINUATION,
    # not by repeating the line — see the no-repeat test above.
    text = "Execute a deep review pass over " + "the entity model and ticket set " * 6
    text = text.strip()
    assert len(_title(text)) <= tasks.TITLE_MAX
    assert (_title(text) + " " + _body(text)).split() == text.split()


def test_the_break_lands_on_a_word():
    text = "Rewire " + "everything everywhere all at once " * 8
    assert "  " not in _title(text)
    assert not _title(text).endswith(" ")


def test_an_old_elided_title_is_rejoined(tmp_path):
    """The 32 live tickets whose title the retired clamp truncated.

    The clamp wrote `head[:cut] + "…"` as line one and the remainder as line two,
    so the original line is recoverable by concatenating them.
    """
    title, body = tasks.split_text(
        "Move the ambient cross-chat digest out of the cache-stable prompt…\n"
        "prefix so the cache holds across turns")
    assert title == ("Move the ambient cross-chat digest out of the cache-stable "
                     "prompt prefix so the cache holds across turns")
    assert body == ""


def test_a_human_ellipsis_title_is_left_alone():
    """The guard on the repair: a real body under a short elided title is a body,
    not a spill, so rejoining it would corrupt the ticket."""
    title, body = tasks.split_text("And then what…\nObjective: decide the thing.")
    assert title == "And then what…"
    assert body == "Objective: decide the thing."


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
    """A ticket written before title/body existed normalizes when loaded."""
    long_text = "Do the thing, and also this other thing: with a lot of detail " * 3
    t = tasks.add(tmp_path, text="placeholder")
    tasks.update(tmp_path, t["id"], text="placeholder two")
    # Reach past the write path the way a hand edit or an older writer would: a
    # legacy record carries `text` and no title/body pair at all. Written
    # straight to the file because _persist strips the `text` projection.
    raw = tasks._load(tmp_path)
    legacy = tasks._for_storage(raw[0])
    legacy.pop("title", None)
    legacy.pop("body", None)
    legacy["text"] = long_text
    (tmp_path / tasks.STORE_NAME).write_text(
        json.dumps({"version": tasks.STORE_VERSION, "tasks": [legacy]}))

    reloaded = tasks._load(tmp_path)[0]
    assert len(reloaded["title"]) <= tasks.TITLE_MAX
    assert reloaded["text"] == tasks.join_text(reloaded["title"], reloaded["body"])
    # Lossless apart from the separator itself: a clause cut drops the trailing
    # ":" or "," it cut on, because that punctuation belonged to the join and a
    # title should not end in it. Every word survives on one side or the other.
    assert long_text.strip().startswith(reloaded["title"])
    assert long_text.strip().endswith(reloaded["body"])
