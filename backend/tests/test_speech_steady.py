"""The open stretch's shown text: words two reads agree on stop changing."""
from services.speech.heard import Heard, Word
from services.speech.steady import Steady


def read(*words: str) -> Heard:
    ws = tuple(Word(w, i * 0.5, i * 0.5 + 0.3) for i, w in enumerate(words))
    return Heard(" ".join(words), ws)


def test_words_two_reads_agree_on_are_held_and_not_rewritten():
    s = Steady()
    s.show(read("so", "I", "went", "to"))
    s.show(read("so", "I", "went", "to", "the"))
    assert [w.text for w in s.held] == ["so", "I", "went"]
    shown = s.show(read("So,", "I", "went", "to", "the", "store"))
    assert shown == "so I went to the store"


def test_only_the_words_after_the_held_ones_change():
    s = Steady()
    s.show(read("I", "think", "we"))
    s.show(read("I", "think", "we", "could"))
    assert s.show(read("I", "think", "we", "should")) == "I think we should"


def test_the_last_agreed_word_stays_open_for_its_punctuation():
    s = Steady()
    s.show(read("we", "went", "home"))
    s.show(read("we", "went", "home"))
    assert [w.text for w in s.held] == ["we", "went"]
    assert s.show(read("we", "went", "home.", "Then")) == "we went home. Then"


def test_a_read_that_splits_an_earlier_word_does_not_repeat_the_rest():
    s = Steady()
    s.show(read("a", "cannot", "be", "done"))
    s.show(read("a", "cannot", "be", "done", "now"))
    split = Heard("a can not be done now", (
        Word("a", 0.0, 0.3), Word("can", 0.5, 0.6), Word("not", 0.7, 0.8),
        Word("be", 1.0, 1.3), Word("done", 1.5, 1.8), Word("now", 2.0, 2.3)))
    assert s.show(split) == "a cannot be done now"


def test_settling_takes_the_shown_words():
    s = Steady()
    s.show(read("One.", "two", "three"))
    s.show(read("One.", "two", "three", "four"))
    s.show(read("one.", "Two", "three", "four", "five"))
    assert s.split(0) == ("One.", "two three four five")
    assert [w.text for w in s.held] == ["two", "three"]


def test_a_last_read_keeps_the_held_words():
    s = Steady()
    s.show(read("left", "the", "keys"))
    s.show(read("left", "the", "keys", "there"))
    assert s.show(read("Left", "the", "keys", "there."), hold=False) == "left the keys there."


def test_a_read_that_changes_a_held_word_does_not_repeat_the_next_ones():
    s = Steady()
    s.show(read("This", "first", "sentence", "goes", "to", "the"))
    s.show(read("This", "first", "sentence", "goes", "to", "the", "top"))
    shown = s.show(read("This", "first", "sentence", "goes", "into", "the", "top", "field."))
    assert shown == "This first sentence goes to the top field."


def test_a_word_still_being_said_is_not_held():
    s = Steady()
    s.show(read("sends", "the", "same"), until=1.4)
    s.show(read("sends", "the", "same", "now"), until=1.5)
    assert [w.text for w in s.held] == ["sends"]
    assert s.show(read("sends", "this", "one", "back"), until=2.2) == "sends this one back"


def test_a_held_word_the_read_changed_is_not_matched_further_on():
    s = Steady()
    s.show(read("away", "sends", "the", "same"))
    s.show(read("away", "sends", "the", "same", "thing"))
    assert [w.text for w in s.held] == ["away", "sends", "the"]
    shown = s.show(read("away", "sends", "this", "one", "back", "to", "the", "end"))
    assert shown == "away sends the one back to the end"


def test_the_next_word_takes_the_case_the_held_word_calls_for():
    s = Steady()
    s.show(read("the", "lower", "field,", "clicking"))
    s.show(read("the", "lower", "field,", "clicking", "away"))
    assert s.show(read("the", "lower", "field.", "Clicking", "away", "sends")) == "the lower field, clicking away sends"
