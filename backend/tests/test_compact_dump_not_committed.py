"""The provider's context-usage dump must never be committed as a chat message.

REPORTED THREE TIMES. 2026-08-11 and 2026-08-12 (fixed by `5bcc9070`, which
replaced the reply text of the `/compact` turn and dropped that turn's text
segments), and again on 2026-09-01: "we are still printing out the context usage
in the chat when compacting. We fixed that a while ago, now it's back."

WHY IT CAME BACK. The 2026-08-12 fix hinged on the PROMPT: it fires only when a
turn's whole prompt is the literal string `/compact`. Between 2026-08-13 and
2026-08-29 `/compact` was a Workshop-side fold with no provider turn behind it
(see `routes/chat.py:compact_thread`), so no dump could reach the transcript at
all and nothing exercised that hinge. `5be52599` (2026-08-29, "use the provider's
own /compact, delete Workshop's fold") put the provider turn back. The guard came
back with it, but a compaction is not only that one prompt:

  * the CLI compacts mid-turn on its own when the window fills (29 `trigger:auto`
    boundaries on record) -- an ordinary work turn, ordinary prompt;
  * a `/compact` can pick up an attachment-carry note on the way to the CLI
    (`build_streaming` builds `_prompt` from `meta["prompt"]` plus carry text);
  * a local command can answer on a turn whose boundary event never arrived.

On every one of those the prompt test fails, nothing is dropped, and the dump
lands in `elif turn.final_text` and is committed verbatim -- as the message text
AND as the text segment the transcript actually draws.

So the second guard hinges on the TEXT. These tests pin the behaviour of
`_commit_turn`, not of the helper, because a helper-level test is exactly what
the last two rounds of this bug got past: `TestCompactTurnSaysOneLine` in
`test_synthetic_placeholder.py` modelled the reply-text decision correctly while
the table went on being drawn from a segment it never looked at.

The sample is the real thing, captured 2026-09-01 from the operator's own
session (CLI 2.1.239, `system`/`local_command` record `348e05fa`), trimmed in the
middle only.
"""
from __future__ import annotations

import asyncio

import pytest

from services import build_streaming as bs


# The CLI's own output, verbatim in shape. `<local-command-stdout>` is how the
# CLI wraps a local command's answer on the way past; it arrives both wrapped
# and bare depending on which event carries it, so both are exercised below.
DUMP = """## Context Usage

**Model:** claude-opus-5
**Tokens:** 597.9k / 1m (60%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 3.5k | 0.4% |
| System tools | 14.5k | 1.5% |
| MCP tools (deferred) | 872 | 0.1% |
| Custom agents | 241 | 0.1% |
| Memory files | 5k | 0.5% |
| Messages | 573.8k | 57.4% |
| Free space | 369.1k | 36.9% |
| Autocompact buffer | 33k | 3.3% |
"""

WRAPPED_DUMP = f"<local-command-stdout>{DUMP}</local-command-stdout>"

# The older form, from before the CLI printed a table: one 117-character
# sentence that ends by giving advice nobody asked for.
SENTENCE = ("Conversation compacted — earlier messages were summarized to free "
            "up the context window. Continue where you left off.")

COMPACT_LINE = "Compacted. Earlier messages are now a summary."


@pytest.fixture
def committed(monkeypatch):
    """Capture what `_commit_turn` hands `chat_store.commit_turn`.

    The transcript renders `meta.segments` and falls back to `content` only when
    there are none, so BOTH arguments are the answer to "what does the operator
    see" and a test that checked one of them would repeat the 2026-08-12 miss."""
    seen: dict = {}

    def _commit(thread_id, turn_id, segments, text, **kw):
        seen["segments"] = segments
        seen["text"] = text
        return {"id": "m1", "content": text}

    monkeypatch.setattr(bs.chat_store, "commit_turn", _commit)
    monkeypatch.setattr(bs.chat_store, "note_auto_compact", lambda *a, **k: None)
    monkeypatch.setattr(bs.chat_store, "clear_thread_context_tokens", lambda *a, **k: None)
    monkeypatch.setattr(bs.chat_store, "get_thread", lambda *a, **k: None)
    return seen


def _run(committed, *, prompt, segments, final_text, compacted=True):
    """Commit one turn and return what the transcript would show."""
    turn = bs._make_turn("thread-ctx", "general", turn_id="turn-ctx")
    bs.TURNS["turn-ctx"] = turn
    turn.segments = list(segments)
    turn.final_text = final_text
    try:
        asyncio.run(bs._commit_turn(
            turn, {"prompt": prompt, "cwd": "/tmp", "timeout_s": 0},
            rc=0, timed_out=False, compacted=compacted))
    finally:
        bs.TURNS.pop("turn-ctx", None)
    return committed


def _texts(segments):
    return [s.get("text", "") for s in segments or [] if s.get("t") == "text"]


class TestAnAutoCompactionDoesNotPrintTheDump:
    """THE REGRESSION, and the case the prompt-gated guard cannot see.

    The CLI compacted in the middle of an ordinary turn. The prompt is the
    operator's message, not `/compact`, so `compacted and prompt == "/compact"`
    is False and every branch that drops anything is skipped."""

    def test_the_dump_is_not_the_committed_text(self, committed):
        out = _run(committed,
                   prompt="carry on with the migration",
                   segments=[{"t": "text", "text": DUMP}],
                   final_text=DUMP)
        assert "## Context Usage" not in out["text"]
        assert "597.9k" not in out["text"]

    def test_the_dump_is_not_in_the_segments_either(self, committed):
        """Segments are what the transcript DRAWS. Rewriting the text alone is
        the exact half-fix that let this through in August."""
        out = _run(committed,
                   prompt="carry on with the migration",
                   segments=[{"t": "text", "text": DUMP}],
                   final_text=DUMP)
        assert not any("Context Usage" in t for t in _texts(out["segments"]))

    def test_it_says_the_compaction_happened(self, committed):
        """With the dump gone there is nothing else the turn produced, so the
        turn reports the one true thing instead of an empty bubble."""
        out = _run(committed,
                   prompt="carry on with the migration",
                   segments=[{"t": "text", "text": DUMP}],
                   final_text=DUMP)
        assert out["text"] == COMPACT_LINE

    def test_the_wrapped_form_is_recognised_too(self, committed):
        """The CLI hands local-command output back inside
        `<local-command-stdout>`, so the payload has to be unwrapped before it
        can be recognised at all."""
        out = _run(committed,
                   prompt="carry on with the migration",
                   segments=[{"t": "text", "text": WRAPPED_DUMP}],
                   final_text=WRAPPED_DUMP)
        assert "Context Usage" not in out["text"]
        assert not any("Context Usage" in t for t in _texts(out["segments"]))

    def test_the_older_one_sentence_form_too(self, committed):
        out = _run(committed,
                   prompt="carry on with the migration",
                   segments=[{"t": "text", "text": SENTENCE}],
                   final_text=SENTENCE)
        assert "Continue where you left off" not in out["text"]
        assert out["text"] == COMPACT_LINE


class TestTheTurnsOwnWorkSurvives:
    """Dropping work that DID happen would be a worse lie than the dump.

    An auto-compaction lands in the middle of real work, which is the whole
    difference between this and the explicit `/compact` turn: there the reply is
    replaced wholesale and every text segment can go, here there is a turn's
    output around the dump that the operator needs."""

    SEGMENTS = [
        {"t": "tool", "name": "Read", "input": "app.py"},
        {"t": "text", "text": "Found the bad import; fixing it now."},
        {"t": "text", "text": DUMP},
        {"t": "agent", "name": "Explore", "steps": []},
    ]

    def test_the_prose_and_the_tools_stay(self, committed):
        out = _run(committed, prompt="fix the import",
                   segments=self.SEGMENTS,
                   final_text="Found the bad import; fixing it now.")
        kinds = [s["t"] for s in out["segments"]]
        assert kinds == ["tool", "text", "agent"]
        assert _texts(out["segments"]) == ["Found the bad import; fixing it now."]

    def test_the_real_reply_is_still_the_message(self, committed):
        out = _run(committed, prompt="fix the import",
                   segments=self.SEGMENTS,
                   final_text="Found the bad import; fixing it now.")
        assert out["text"] == "Found the bad import; fixing it now."


class TestItDoesNotEatAReplyAboutTheReport:
    """The over-match is the worse failure, so the recogniser is tight.

    This project has whole replies that quote the table while discussing this
    very bug. A guard that dropped any text MENTIONING "## Context Usage" would
    silently delete them, and the operator would have no way to tell a deleted
    answer from one that was never written."""

    PROSE = (
        "The dump comes back because the guard is keyed on the prompt.\n\n"
        "The CLI answers with a `## Context Usage` table listing every token "
        "category, and `**Tokens:** 597.9k / 1m (60%)` at the top of it.\n\n"
        "I have moved the guard onto the text instead."
    )

    def test_prose_that_quotes_the_report_is_kept(self, committed):
        out = _run(committed, prompt="why is the table back?",
                   segments=[{"t": "text", "text": self.PROSE}],
                   final_text=self.PROSE)
        assert out["text"] == self.PROSE
        assert _texts(out["segments"]) == [self.PROSE]

    def test_a_heading_alone_is_not_a_reading(self):
        """Someone writing under the same heading is not the CLI reporting.
        The totals line or the category table is what makes it a reading."""
        assert not bs._is_context_report("## Context Usage\n\nis what it is called.")
        assert bs._is_context_report("## Context Usage\n\n**Tokens:** 30.6k / 200k (15%)")

    def test_an_ordinary_reply_is_untouched(self, committed):
        out = _run(committed, prompt="status?",
                   segments=[{"t": "text", "text": "All green."}],
                   final_text="All green.")
        assert out["text"] == "All green."


class TestTheExplicitCompactTurnStillSaysOneLine:
    """`5bcc9070`'s behaviour, unchanged. The new guard runs before it and must
    not have altered what the operator sees when they press Compact."""

    def test_one_line_and_no_segments(self, committed):
        out = _run(committed, prompt="/compact",
                   segments=[{"t": "text", "text": DUMP}],
                   final_text=DUMP)
        assert out["text"] == COMPACT_LINE
        assert out["segments"] == []

    def test_work_still_survives_that_turn_too(self, committed):
        out = _run(committed, prompt="/compact",
                   segments=[{"t": "tool", "name": "Read", "input": "x.py"},
                             {"t": "text", "text": DUMP}],
                   final_text=DUMP)
        assert [s["t"] for s in out["segments"]] == ["tool"]


class TestALocalCommandTurnThatNeverCompacted:
    """A slash command the CLI answered locally without a boundary event. There
    is no compaction to report, so the turn says what it always said -- but the
    dump still must not be the message."""

    def test_the_dump_is_dropped_with_no_boundary(self, committed):
        out = _run(committed, prompt="/context",
                   segments=[{"t": "text", "text": DUMP}],
                   final_text=DUMP,
                   compacted=False)
        assert "Context Usage" not in out["text"]
        assert not any("Context Usage" in t for t in _texts(out["segments"]))


class TestTheRecogniser:
    """`_is_context_report` on its own: total, and never raising on junk."""

    @pytest.mark.parametrize("text", [DUMP, WRAPPED_DUMP, SENTENCE,
                                      DUMP.upper(), f"\n\n{DUMP}\n"])
    def test_recognised(self, text):
        assert bs._is_context_report(text)

    @pytest.mark.parametrize("text", ["", None, "All green.",
                                      "See the ## Context Usage table for why.",
                                      "<local-command-stdout>Compacted </local-command-stdout>"])
    def test_not_recognised(self, text):
        assert not bs._is_context_report(text)

    def test_dropping_is_total_on_junk(self):
        assert bs._drop_context_report(None) == []
        assert bs._drop_context_report(["not a dict"]) == ["not a dict"]
