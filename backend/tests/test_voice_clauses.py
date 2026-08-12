"""Clause splitting: how soon a reply starts speaking, and whether it sounds it.

The bug this covers: the old splitter cut on SENTENCES and withheld the trailing
fragment until generation finished, then merged anything under 24 characters.
The voice prompt asks for one to three sentences, so the common case spoke
nothing at all until the model was completely done.
"""
from __future__ import annotations

from services.voice.clauses import ClauseStream, clean_for_speech, split_clauses


class TestTheShortReplyCase:
    def test_a_one_sentence_reply_still_gets_spoken(self):
        # THE REGRESSION. Under the old rules this produced nothing until done.
        cs = ClauseStream()
        assert cs.flush("Sure, that's done.") == ["Sure, that's done."]

    def test_a_very_short_reply_is_not_swallowed(self):
        cs = ClauseStream()
        assert cs.flush("Done.") == ["Done."]

    def test_a_reply_with_no_punctuation_at_all_still_speaks(self):
        cs = ClauseStream()
        assert cs.flush("okay working on that now") == ["okay working on that now"]


class TestFirstAudioIsEarly:
    def test_the_first_sentence_speaks_while_the_rest_is_still_generating(self):
        # The property that matters: audio starts before the reply is finished.
        cs = ClauseStream()
        assert cs.push("I checked the logs. There were") == ["I checked the logs."]

    def test_a_one_word_opener_is_not_spoken_on_its_own(self):
        # "Right," alone sounds clipped and gives Kokoro no prosody to work with,
        # so a comma that early is not a boundary.
        cs = ClauseStream()
        assert cs.push("Right, so the first thing") == []

    def test_a_long_opening_sentence_does_not_hold_up_first_audio(self):
        # The one case that could still stall the opening: a first sentence with
        # no early comma. Capped by FIRST_MAX_WORDS rather than left to run.
        cs = ClauseStream()
        out = cs.push(" ".join(["word"] * 20) + " and more")
        assert out, "a long comma-free opener must still start speaking"

    def test_later_clauses_need_more_words_so_it_does_not_sound_choppy(self):
        # A four-word bar everywhere would chop "yes, but" out of the middle of
        # a sentence. The first clause buys latency; the rest buy fluency.
        clauses, _rest = split_clauses("that is fine, but only if you ask", first=False)
        assert clauses == []

    def test_a_long_comma_free_run_still_gets_cut(self):
        text = " ".join(["word"] * 40)
        clauses, rest = split_clauses(text)
        assert clauses, "a run of prose with no punctuation must still stream"


class TestItDoesNotSplitInsideThings:
    def test_a_decimal_is_not_a_sentence_end(self):
        clauses, rest = split_clauses("the value is 3.14 and it matters")
        assert not any(c.endswith("3.") for c in clauses)

    def test_an_abbreviation_is_not_a_sentence_end(self):
        clauses, rest = split_clauses("ask Dr. Smith about it tomorrow morning")
        assert not any(c.strip().endswith("Dr.") for c in clauses)

    def test_an_initial_is_not_a_sentence_end(self):
        clauses, rest = split_clauses("it was J. Smith who wrote the whole thing")
        assert not any(c.strip().endswith("J.") for c in clauses)

    def test_a_numbered_item_is_not_a_sentence_end(self):
        clauses, rest = split_clauses("1. check the logs and then restart it")
        assert not any(c.strip() == "1." for c in clauses)

    def test_a_time_is_not_a_clause_boundary(self):
        clauses, rest = split_clauses("the meeting is at 10:30 in the morning room")
        assert not any(c.strip().endswith("10:") for c in clauses)


class TestStreaming:
    def test_a_clause_is_handed_out_exactly_once(self):
        cs = ClauseStream()
        a = cs.push("The first part is done, ")
        b = cs.push("The first part is done, and the second part is running now.")
        assert not (set(a) & set(b)), "a clause was spoken twice"

    def test_the_full_reply_is_covered_with_nothing_lost(self):
        cs = ClauseStream()
        full = "Okay, I checked the logs. There were three errors. I fixed them all."
        spoken = []
        for i in range(10, len(full), 7):
            spoken += cs.push(full[:i])
        spoken += cs.flush(full)
        joined = " ".join(spoken)
        for word in ("logs", "three", "errors", "fixed"):
            assert word in joined, f"{word} was never spoken"

    def test_a_shrinking_reply_does_not_re_emit(self):
        cs = ClauseStream()
        cs.push("A full first sentence here.")
        assert cs.push("A full") == []


class TestCleanForSpeech:
    def test_code_fences_become_a_word_not_punctuation(self):
        assert "(code)" in clean_for_speech("here:\n```py\nx=1\n```\n")

    def test_links_are_not_read_out_character_by_character(self):
        assert "link" in clean_for_speech("see https://example.com/a/b?c=d")
        assert "https" not in clean_for_speech("see https://example.com/a/b?c=d")

    def test_markdown_emphasis_is_stripped(self):
        assert clean_for_speech("that is **very** important") == "that is very important"
