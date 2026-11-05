"""Words and their times from the speech model's subword pieces."""
from services.speech.heard import Heard, Word, from_spans, from_tokens, without_fillers


def test_pieces_join_into_words_with_their_first_and_last_times():
    h = from_tokens([" This", " sent", "ence", ".", " N", "ow"], [0.6, 1.7, 2.0, 2.4, 6.1, 6.3])
    assert h.text == "This sentence. Now"
    assert [(w.text, w.start, w.last) for w in h.words] == [
        ("This", 0.6, 0.6), ("sentence.", 1.7, 2.4), ("Now", 6.1, 6.3)]


def test_nothing_heard_is_no_words():
    assert from_tokens([], []).words == ()


def test_spans_from_a_server_that_reports_words():
    h = from_spans(" Hi there. ", [{"word": "Hi", "start": 0.1, "end": 0.3}, {"word": " ", "start": 0.3}])
    assert h.text == "Hi there." and len(h.words) == 1 and h.words[0].last == 0.3


def _words(*texts):
    return Heard(" ".join(texts), tuple(Word(t, i, i + 0.5) for i, t in enumerate(texts)))


def test_fillers_are_taken_out():
    assert without_fillers(_words("So", "um,", "I", "went", "uh", "home.")).text == "So I went home."


def test_a_filler_starting_a_sentence_passes_on_its_capital():
    assert without_fillers(_words("Done.", "Um,", "then", "we", "left.")).text == "Done. Then we left."


def test_a_filler_ending_a_sentence_passes_on_its_end():
    assert without_fillers(_words("We", "left,", "um.", "Then")).text == "We left. Then"


def test_words_that_only_contain_a_filler_stay():
    assert without_fillers(_words("Umbrella", "under", "her", "arm.")).text == "Umbrella under her arm."
