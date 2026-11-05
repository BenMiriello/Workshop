"""Which phrases dictation listens for, by mode (docs/dictation-and-clipboard.md,
"Modes").

  dictation  everything said is written; the only commands heard are the
             inline ones ("new line") and the ones that leave the mode, pause
             or stop
  mixed      words are written and each command is heard as set: on its own,
             after the trigger word, or off. After the trigger word any
             wording is understood (services/speech/meaning.py)
  commands   nothing is written; every command is heard, trigger word or not,
             and any wording is understood

A command needing the trigger word is simply its phrase with the trigger's
words in front, so matching (services/speech/commands.py) is unchanged and an
inline one ("ok workshop new line") is found inside running speech like any other.
A command heard on its own may also be said after the trigger word.

Saved settings from before these three read as the nearest: "words" is
dictation, "custom" is mixed, and "trigger" is mixed with every command set to
need the trigger word (services/speech/command_store.py).
"""
from __future__ import annotations

from typing import Literal

Mode = Literal["dictation", "mixed", "commands"]
When = Literal["alone", "trigger", "off"]
MODES: tuple[str, ...] = ("dictation", "mixed", "commands")
WHENS: tuple[str, ...] = ("alone", "trigger", "off")
DEFAULT_MODE: Mode = "mixed"
DEFAULT_TRIGGER = "ok workshop"
# The modes words are written in, which "start writing" goes back to.
WRITING: tuple[str, ...] = ("dictation", "mixed")
LEGACY: dict[str, Mode] = {"words": "dictation", "custom": "mixed", "trigger": "mixed"}
# The actions heard in dictation mode.
IN_DICTATION = frozenset({"dictation_only", "mixed_mode", "commands_only", "start_writing",
                          "pause", "resume", "stop"})


def mode_of(value: object, default: Mode = DEFAULT_MODE) -> Mode:
    if value in MODES:
        return value  # type: ignore[return-value]
    return LEGACY.get(value, default) if isinstance(value, str) else default


def when_of(saved: dict) -> When:
    """A command's setting, reading the older on/off switch as on its own."""
    w = saved.get("when")
    if w in WHENS:
        return w  # type: ignore[return-value]
    return "off" if saved.get("enabled") is False else "alone"


def heard_in(mode: Mode, name: str, inline: bool) -> bool:
    """Whether a command is listened for at all in `mode`."""
    if inline:
        return mode != "commands"
    return mode != "dictation" or name in IN_DICTATION


def variants(words: tuple[str, ...], when: When, mode: Mode,
             trigger: tuple[str, ...]) -> list[tuple[str, ...]]:
    """The word sequences that say one phrase of a command in `mode`."""
    if when == "off":
        return []
    triggered = trigger + words if trigger else words
    if when == "trigger" and mode != "commands":
        return [triggered]
    return [words] if triggered == words else [words, triggered]
