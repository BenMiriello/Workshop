"""What the client asks of a live dictation (services/speech/live.py) besides
its audio: the text it shows is final through a point, Pause, the mode changed,
the actions its page declares, numbered things are on its screen, and Stop.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from services.speech import app_actions, spoken
from services.speech.commands import Table
from services.speech.cuts import Reading
from services.speech.heard import Heard
from services.speech.meaning import shared as meaning
from services.speech.stretch import SpeechRuns
from services.speech.steady import Steady
from services.speech.stretch import Shortened
from services.voice.timeline import AudioTimeline

Emit = Callable[[dict], Awaitable[None]]


class _Session(Protocol):
    """What these controls touch of the live dictation they are mixed into."""
    timeline: AudioTimeline
    commands: Table
    steady: Steady
    speech: SpeechRuns
    paused: bool
    numbers: bool
    open_from: int
    _read_to: int
    _agreed: tuple[str, ...]
    _wait: spoken.Candidate | None
    _capital: bool
    _open_text: str
    _open_to: int
    _cut_out: list[tuple[int, int]]

    async def _hear(self, audio: Shortened) -> Heard: ...
    def _shortened(self, start: int, end: int) -> Shortened: ...
    @property
    def heard(self) -> bool: ...
    async def _drop(self, upto: int, emit: Emit, least: int = 1) -> None: ...
    async def _decide(self, c: spoken.Candidate, emit: Emit, ended: bool = False) -> None: ...
    async def _settle(self, cut: int, text: str, rest: str, rest_to: int, emit: Emit) -> None: ...
    async def finish(self, emit: Emit) -> None: ...


class Controls:
    async def commit(self: _Session, to: int, emit: Emit) -> None:
        """The client froze the text it shows, read through `to`."""
        to = min(to, self.timeline.end)
        if to > self.open_from:
            self._capital = self._open_text.endswith("\n") if self._open_text else self._capital
            self.open_from = to
            self._read_to = max(self._read_to, to)
            self._agreed = ()
            self._wait = None
            self.steady.reset()
            self._open_text, self._open_to = "", to
        elif to < self.open_from:
            # Settled past what the client had shown: the words in between
            # reached the client only inside a final it set aside, so say them
            # again from where it froze, without commands or words said paused.
            said = [await self._hear(self._shortened(a, b))
                    for a, b in spoken.outside(to, self.open_from, self._cut_out)]
            text = self.commands.apply_inline(" ".join(h.text for h in said if h.text))
            await emit({"t": "final", "text": text, "from": to, "to": self.open_from,
                        "rest": self._open_text, "restTo": self._open_to})

    async def set_mode(self: _Session, mode: str, emit: Emit) -> None:
        """The client changed the mode: words said before it are written as
        the mode they were said in says."""
        table = self.commands.with_mode(mode)
        if table.writing != self.commands.writing:
            if table.writing:
                await self._drop(self.timeline.end, emit)
            else:
                await self.finish(emit)
        self.commands = table
        if table.mode != "dictation":
            meaning.warm(self.commands)
        await emit({"t": "mode", "mode": table.mode, "said": False})

    async def set_paused(self: _Session, paused: bool, emit: Emit) -> None:
        """The client's Pause or Resume: words said before a pause are written,
        words said while paused are dropped."""
        if paused != self.paused:
            if paused:
                await self.finish(emit)
            else:
                await self._drop(self.timeline.end, emit)
            self.paused = paused
        await emit({"t": "paused", "paused": self.paused})

    def set_actions(self: _Session, raw: object, off: object = None) -> None:
        """The actions the open pages declare, and what does not apply now
        (services/speech/app_actions.py), from now on in place of the last."""
        app, out = app_actions.clean(raw), app_actions.clean_off(off)
        if app != (self.commands.cmds or {}).get("app", ()) or out != self.commands.off:
            self.commands = self.commands.with_actions(app, out)
            if app and self.commands.mode != "dictation":
                meaning.warm(self.commands)

    def set_numbers(self: _Session, on: bool) -> None:
        """The client shows numbered things (labels, a list): a number said on
        its own picks one."""
        self.numbers = on

    async def finish(self: _Session, emit: Emit) -> None:
        """Settle everything heard so far: Stop, or pause, was pressed."""
        end = self.timeline.end
        if end <= self.open_from:
            return
        text = ""
        if self.heard and not self.paused:
            # The last read with everything heard, keeping the words already held.
            audio = self._shortened(self.open_from, end)
            heard = await self._hear(audio)
            text = self.steady.show(heard, hold=False)
            r = Reading(self.open_from, end, heard, audio)
            c = spoken.look(self.commands, self.speech, r, False, self.numbers)
            if c and (c.match or c.loose):
                await self._decide(c, emit, ended=True)
                return
            if not self.commands.writing:
                text = ""
        self._agreed = ()
        self.steady.reset()
        await self._settle(end, text, "", end, emit)
