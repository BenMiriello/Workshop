"""Spoken commands: phrases said to dictation that do something instead of
being written (docs/dictation-and-clipboard.md, "Spoken commands"). The
built-ins are listed in services/speech/command_list.py.

INLINE commands are replaced by what they stand for wherever they are said.
ACTIONS count only when said on their own (services/speech/spoken.py decides
that); the client carries them out. Every command's phrases are the operator's
to change (services/speech/command_store.py), and the operator's own commands
each type a fixed text (emitted as the `type` action with that text as its
argument). Actions a page or app declares are heard as its own commands are
(services/speech/app_actions.py).

Matching ignores case, punctuation and fillers: both the phrase and what was
heard become `tokens`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.speech.command_list import BUILTINS, BY_ID, INSERTS, WHILE_PAUSED, Builtin
from services.speech.heard import FILLERS
from services.speech.modes import DEFAULT_TRIGGER, WRITING, Mode, heard_in, mode_of, variants, when_of

__all__ = ["BUILTINS", "BY_ID", "Builtin", "Match", "Phrase", "Table", "TYPE", "tokens"]

TYPE = "type"
# The most words a command that takes a name ("chat model ...") takes after it.
MAX_ARG_WORDS = 8
# A word half said is taken for the start of a phrase's word when the two
# share their first this many letters ("can't" read for "cancel" half said).
STEM = 3


def tokens(text: str) -> tuple[str, ...]:
    out = (t.strip("'") for t in re.findall(r"[\w']+", text.lower()))
    return tuple(t for t in out if t and t not in FILLERS)


@dataclass(frozen=True)
class Phrase:
    name: str
    words: tuple[str, ...]
    takes_arg: bool = False
    text: str | None = None


@dataclass(frozen=True)
class Match:
    name: str
    arg: str | None


def _flat(words: list[str]) -> tuple[list[str], list[int]]:
    """The tokens of heard words, and which word each one came from."""
    toks: list[str] = []
    owner: list[int] = []
    for i, w in enumerate(words):
        for t in tokens(w):
            toks.append(t)
            owner.append(i)
    return toks, owner


def _begins(heard: str, word: str) -> bool:
    heard = heard.replace("'", "")
    if word.startswith(heard):
        return True
    same = next((i for i, (x, y) in enumerate(zip(heard, word)) if x != y), min(len(heard), len(word)))
    return same >= STEM


class Table:
    """The commands one dictation listens for, in one mode (services/speech/
    modes.py). `writing`: words said are written, rather than only listened
    to for commands. `trigger`: the trigger word's tokens, after which any
    wording is understood (services/speech/meaning.py). `off`: the commands
    the client says do not apply now, whose phrases are heard as words."""

    def __init__(self, inline: list[Phrase], actions: list[Phrase], mode: Mode = "mixed",
                 cmds: dict | None = None, trigger: tuple[str, ...] = ()) -> None:
        self.inline = sorted(inline, key=lambda p: -len(p.words))
        self.actions = sorted(actions, key=lambda p: -len(p.words))
        self.mode = mode
        self.cmds = cmds
        self.trigger = trigger
        self.writing = mode != "commands"
        self.off: frozenset[str] = frozenset()

    def loose(self, toks: tuple[str, ...]) -> tuple[str, ...] | None:
        """The words to understand by meaning, when these are said to the app
        without being one of its phrases: all of them in commands mode, the
        ones after the trigger word in mixed."""
        n = len(self.trigger)
        said = n and len(toks) > n and toks[:n] == self.trigger
        if self.mode == "commands":
            return toks[n:] if said else toks
        return toks[n:] if self.mode == "mixed" and said else None

    @classmethod
    def from_commands(cls, cmds: dict, mode: str | None = None) -> "Table":
        m = mode_of(mode if mode is not None else cmds.get("mode"))
        trigger = tokens(cmds.get("trigger") or DEFAULT_TRIGGER)
        inline: list[Phrase] = []
        actions: list[Phrase] = []
        for b in cmds["builtins"]:
            is_inline = b["kind"] == "inline"
            if not heard_in(m, b["id"], is_inline):
                continue
            into = inline if is_inline else actions
            for p in b["phrases"]:
                for words in variants(tokens(p), when_of(b), m, trigger) if tokens(p) else []:
                    into.append(Phrase(b["id"], words, b["takes_arg"]))
        for c in cmds["custom"] if heard_in(m, TYPE, False) else []:
            for p in c["phrases"]:
                for words in variants(tokens(p), when_of(c), m, trigger) if tokens(p) else []:
                    actions.append(Phrase(TYPE, words, text=c["text"]))
        for a in cmds.get("app") or () if heard_in(m, TYPE, False) else ():
            said = [(p, a["takes_arg"], None) for p in a["phrases"]] + [(w, False, arg) for w, arg in a["fixed"]]
            for p, takes, arg in said:
                for words in variants(tokens(p), "alone", m, trigger) if tokens(p) else []:
                    actions.append(Phrase(a["id"], words, takes, arg))
        table = cls(inline, actions, m, cmds, trigger)
        table.off = frozenset(cmds.get("off") or ())
        return table

    def with_actions(self, app: tuple[dict, ...], off: frozenset[str] = frozenset()) -> "Table":
        """The same commands and mode with the actions the open pages declare
        and what does not apply now (services/speech/app_actions.py) in
        place of the ones before."""
        cmds = self.cmds or {"builtins": [], "custom": [], "trigger": " ".join(self.trigger)}
        return Table.from_commands({**cmds, "app": app, "off": off}, self.mode)

    def with_mode(self, mode: str) -> "Table":
        """The same commands in another mode; "writing" is the mode words were
        last written in."""
        if mode == "writing":
            back = (self.cmds or {}).get("back")
            mode = back if back in WRITING else "mixed"
        if self.cmds is None:
            return self
        cmds = self.cmds
        if mode == "commands" and self.mode in WRITING:
            cmds = {**cmds, "back": self.mode}
        return Table.from_commands(cmds, mode)

    def _heard(self, paused: bool) -> list[Phrase]:
        if paused:
            return [p for p in self.actions if p.name in WHILE_PAUSED and p.name not in self.off]
        return [p for p in self.actions if p.name != "resume" and p.name not in self.off]

    def match(self, words: list[str], paused: bool = False) -> Match | None:
        """The action these heard words are, said whole, if any."""
        toks, owner = _flat(words)
        for p in self._heard(paused):
            n = len(p.words)
            if tuple(toks[:n]) != p.words:
                continue
            if not p.takes_arg:
                if len(toks) == n:
                    return Match(p.name, p.text)
                continue
            if n < len(toks) <= n + MAX_ARG_WORDS:
                arg = " ".join(words[owner[n]:]).strip().rstrip(".,!?;:")
                return Match(p.name, arg or None)
        return None

    def could_be(self, words: list[str], paused: bool = False) -> bool:
        """These words are an action, or the start of one. The last word may
        be one still being said, which a read often gets half right
        ("dictate" for "dictation"), so it need only begin the same way."""
        toks, _ = _flat(words)
        if not toks:
            return False
        k = len(toks)
        for p in self._heard(paused):
            n = len(p.words)
            if k <= n and p.words[:k - 1] == tuple(toks[:-1]) and _begins(toks[-1], p.words[k - 1]):
                return True
            if p.takes_arg and tuple(toks[:n]) == p.words and len(toks) <= n + MAX_ARG_WORDS:
                return True
        return False

    def _inline_at(self, toks: list[tuple[str, ...]], i: int) -> tuple[int, str] | None:
        for p in self.inline:
            got: list[str] = []
            j = i
            while j < len(toks) and len(got) < len(p.words):
                got += toks[j]
                j += 1
            if tuple(got) == p.words and j > i:
                return j - i, INSERTS[p.name]
        return None

    def apply_inline(self, text: str, capital: bool = False) -> str:
        """`text` with inline commands written as what they stand for: their
        words and the punctuation on them gone, a comma before them dropped,
        the next word capitalised (and the first, with `capital`)."""
        if not text:
            return text
        parts = text.split(" ")
        toks = [tokens(p) for p in parts]
        out: list[str] = []
        i = 0
        while i < len(parts):
            hit = self._inline_at(toks, i) if self.inline else None
            if hit:
                if out and not out[-1].endswith("\n"):
                    out[-1] = out[-1].rstrip(",;:")
                out.append(hit[1])
                capital = True
                i += hit[0]
                continue
            w = parts[i]
            if capital and w[:1].isalpha():
                w = w[0].upper() + w[1:]
                capital = False
            out.append(w)
            i += 1
        joined = ""
        for piece in out:
            gap = joined and not joined.endswith("\n") and not piece.startswith("\n")
            joined += (" " if gap else "") + piece
        return joined
