"""The built-in spoken commands and their default phrases
(docs/dictation-and-clipboard.md, "Spoken commands"). What each one does is
the client's (frontend/src/components/shell/speech/voiceActions.ts); the order
here is the order the commands manager lists them.

INLINE commands are replaced by what they stand for wherever they are said.
ACTIONS count only when said on their own. One that takes an argument ("open
settings") is its phrase followed by the argument.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["inline", "action"]


@dataclass(frozen=True)
class Builtin:
    id: str
    label: str
    kind: Kind
    phrases: tuple[str, ...]
    takes_arg: bool = False


def _spoken(*phrases: str) -> tuple[str, ...]:
    """Read aloud's phrases with "speak" for "read", which means the same."""
    return phrases + tuple(re.sub(r"\bread(ing)?\b", r"speak\1", p) for p in phrases)


BUILTINS: tuple[Builtin, ...] = (
    Builtin("new_line", "New line", "inline", ("new line",)),
    Builtin("new_paragraph", "New paragraph", "inline", ("new paragraph",)),
    Builtin("scratch", "Scratch that", "action", ("scratch that", "delete that")),
    Builtin("send", "Send message", "action", ("send message", "submit message")),
    Builtin("cancel", "Cancel", "action", ("cancel", "cancel that")),
    Builtin("new_chat", "New chat", "action", ("new chat", "start a new chat")),
    Builtin("chat_model", "Chat model", "action",
            ("chat model", "change model", "switch model to", "use model"), takes_arg=True),
    Builtin("open_chat", "Open chat", "action", ("open chat", "show chat beside")),
    Builtin("stop_reply", "Stop chat", "action", ("stop chat", "stop the chat")),
    Builtin("open", "Open", "action", ("open", "go to"), takes_arg=True),
    Builtin("go_back", "Go back", "action", ("go back", "previous page")),
    Builtin("go_forward", "Go forward", "action", ("go forward", "next page")),
    Builtin("scroll_down", "Scroll down", "action", ("scroll down", "page down")),
    Builtin("scroll_up", "Scroll up", "action", ("scroll up", "page up")),
    Builtin("scroll_top", "Scroll to the top", "action", ("scroll to the top", "scroll to top", "go to the top")),
    Builtin("scroll_bottom", "Scroll to the bottom", "action",
            ("scroll to the bottom", "scroll to bottom", "go to the bottom")),
    Builtin("click", "Click", "action", ("click", "press", "tap"), takes_arg=True),
    Builtin("write_in", "Write in a field", "action", ("write in", "write to", "type in", "write into"), takes_arg=True),
    Builtin("close", "Close", "action", ("close that", "close this", "close dialog", "close menu")),
    Builtin("next_tab", "Next tab", "action", ("next tab",)),
    Builtin("previous_tab", "Previous tab", "action", ("previous tab",)),
    Builtin("read_page", "Read this page", "action", _spoken("read this page", "read the page", "read page")),
    Builtin("read_reply", "Read the last reply", "action", _spoken("read the last reply", "read last reply")),
    Builtin("stop_reading", "Stop reading", "action", _spoken("stop reading")),
    Builtin("read_faster", "Read faster", "action", _spoken("read faster")),
    Builtin("read_slower", "Read slower", "action", _spoken("read slower")),
    Builtin("pick", "Pick a number", "action", ("number", "pick", "choose"), takes_arg=True),
    Builtin("show_commands", "Show commands", "action",
            ("show commands", "what can i say", "list commands")),
    Builtin("show_labels", "Show labels", "action", ("show labels", "show numbers")),
    Builtin("hide_labels", "Hide labels", "action", ("hide labels", "hide numbers")),
    Builtin("dictation_only", "Dictation only", "action", ("dictation only", "dictation mode")),
    Builtin("mixed_mode", "Mixed", "action", ("mixed mode",)),
    Builtin("commands_only", "Commands only", "action",
            ("commands only", "command mode", "stop writing", "disable transcription")),
    Builtin("start_writing", "Start writing", "action", ("start writing", "enable transcription")),
    Builtin("pause", "Pause dictation", "action", ("pause dictation", "pause listening")),
    Builtin("resume", "Resume dictation", "action",
            ("resume dictation", "continue dictation", "start dictation", "resume listening")),
    Builtin("stop", "Stop dictation", "action", ("stop dictation", "stop listening")),
)
BY_ID = {b.id: b for b in BUILTINS}
INSERTS = {"new_line": "\n", "new_paragraph": "\n\n"}
# Heard only while paused, and the only ones heard then.
WHILE_PAUSED = frozenset({"resume", "stop"})
# Each switches the mode where it is heard (services/speech/live.py);
# "writing" is the mode words were last written in.
SWITCHES = {"dictation_only": "dictation", "mixed_mode": "mixed",
            "commands_only": "commands", "start_writing": "writing"}

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}


# The most labels a page can show (labels/pageTargets.ts TARGETS_MAX); the
# operator picks how many up to this (command_store.LABELS_MAX).
NUMBER_MAX = 250


def _below_hundred(t: list[str]) -> int | None:
    if len(t) == 1 and t[0] in _ONES[1:]:
        return _ONES.index(t[0])
    if len(t) == 1 and t[0] in _TENS:
        return _TENS[t[0]]
    if len(t) == 2 and t[0] in _TENS and t[1] in _ONES[1:10]:
        return _TENS[t[0]] + _ONES.index(t[1])
    return None


def spoken_number(toks: tuple[str, ...]) -> int | None:
    """The number 1 to NUMBER_MAX these words say whole ("4", "four", "twenty
    one", "number twelve", "a hundred and five", "one twenty three"), or None."""
    t = [w for w in (toks[1:] if toks[:1] == ("number",) else toks) if w != "and"]
    hundreds = 1 if t[:1] == ["a"] else _ONES.index(t[0]) if t and t[0] in _ONES[1:10] else 0
    if len(t) == 1 and re.fullmatch(r"\d{1,3}", t[0]):
        n: int | None = int(t[0])
    elif len(t) >= 2 and t[1] == "hundred" and hundreds:
        rest = _below_hundred(t[2:]) if len(t) > 2 else 0
        n = None if rest is None else hundreds * 100 + rest
    elif len(t) >= 2 and hundreds in (1, 2) and (rest := _below_hundred(t[1:])) is not None and rest >= 10:
        n = hundreds * 100 + rest
    else:
        n = _below_hundred(t)
    return n if n is not None and 1 <= n <= NUMBER_MAX else None
