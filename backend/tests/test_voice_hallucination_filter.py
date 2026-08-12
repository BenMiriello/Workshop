"""Whisper invents captions over silence; the filter must drop those and only those.

The operator was sent a message reading "Bye bye." for words he never spoke. A
phantom transcription is worse than a dropped one, because it becomes a message
in the log and a turn the model answers.
"""
from dataclasses import dataclass

from services.voice_engine import _is_speech


@dataclass
class Seg:
    text: str
    no_speech_prob: float = 0.05
    avg_logprob: float = -0.2


def test_confident_speech_is_kept():
    assert _is_speech(Seg("Move the slider to the left."))


def test_stock_phrase_over_silence_is_dropped():
    # The reported case: near-silence decoded as a stock farewell.
    assert not _is_speech(Seg("Bye bye.", no_speech_prob=0.85, avg_logprob=-0.9))


def test_stock_phrase_dropped_on_either_signal_alone():
    # High no-speech probability alone is enough for a stock phrase.
    assert not _is_speech(Seg("Thank you.", no_speech_prob=0.6, avg_logprob=-0.2))
    # So is a poor average log-probability alone.
    assert not _is_speech(Seg("Thanks for watching", no_speech_prob=0.05, avg_logprob=-1.1))


def test_confident_stock_phrase_is_kept():
    # "Bye" is a thing a person says. A confidently decoded goodbye is a goodbye,
    # which is why the phrase list alone is not the filter.
    assert _is_speech(Seg("Bye.", no_speech_prob=0.05, avg_logprob=-0.15))
    assert _is_speech(Seg("Okay", no_speech_prob=0.1, avg_logprob=-0.3))


def test_normalisation_ignores_case_and_punctuation():
    assert not _is_speech(Seg("  BYE-BYE!! ", no_speech_prob=0.9, avg_logprob=-1.0))


def test_real_words_survive_moderate_uncertainty():
    # A genuine utterance decoded with middling confidence must NOT be dropped:
    # both hard thresholds have to fire together, and this clears one of them.
    assert _is_speech(Seg("the analyser sidecar is on eight thousand thirteen",
                          no_speech_prob=0.9, avg_logprob=-0.3))
    assert _is_speech(Seg("rebuild the public bundle",
                          no_speech_prob=0.5, avg_logprob=-0.9))


def test_non_speech_noise_is_dropped():
    assert not _is_speech(Seg("[BLANK_AUDIO]", no_speech_prob=0.95, avg_logprob=-0.8))


def test_empty_segment_is_dropped():
    assert not _is_speech(Seg("   "))


def test_missing_attributes_default_to_keeping_speech():
    class Bare:
        text = "hello there"

    assert _is_speech(Bare())
