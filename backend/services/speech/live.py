"""Live dictation: the words of what is being said, revised while it is said.

Everything said since the last settled point is the OPEN STRETCH. Whenever
more speech has come in, the whole open stretch is read again, its pauses
shortened (services/speech/stretch.py), and sent as the stretch's text, with the
words earlier reads agreed on held as shown (services/speech/steady.py). A pause
never ends a sentence: the model reads and punctuates the words around it whole.

  text    {"text", "from", "to"}   the open stretch as heard now, from stream
          sample `from`; `to` is just past its last word shown.
  final   {"text", "from", "to", "rest", "restTo"}   [from, to) is settled as
          `text`; `rest` is the open stretch after it, as heard through `restTo`.
  command {"name", "arg", "words", "from", "to"}   a spoken action said on its
          own (services/speech/spoken.py), its words [from, to) cut from the
          stream: the final just before it settled everything through `to`.
          Words said to the app that are none of its phrases come as the name
          "guess" with {"guesses": [{"name", "p", "text"?}], "none"}, what
          they most likely mean (services/speech/meaning.py), or with
          "loading" while that model is not ready.
  paused  {"paused"}                whether words are written; while paused only
          the resume and stop phrases are listened for, and the rest is dropped.
  mode    {"mode", "said"}          the command mode (services/speech/modes.py)
          changed; `said`: by a spoken switch, which the client saves. In
          "commands" nothing is written and every command is listened for.
Inline commands ("new line") arrive inside text and final as line breaks.

What settles: the earlier sentences of a long stretch (cuts.sentence_cut); a
stretch spanning OPEN_SPAN_MS of stream, so its audio is still held;
everything, at Stop or pause; what the client shows, when it says so (`commit`).
Silence with nothing said is let go, so the client stops keeping its audio.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

import numpy as np

from services.speech import lag, spoken
from services.speech.command_list import SWITCHES
from services.speech.commands import Table
from services.speech.cuts import LONGEST_MS, Reading, between, heard_through, sentence_cut
from services.speech.heard import Heard, without_fillers
from services.speech.listening import Listening
from services.speech.live_controls import Controls, Emit
from services.speech.meaning import shared as meaning
from services.speech.steady import Steady
from services.speech.stretch import GAP_KEEP_MS, Shortened, samples
from services.voice.detectors import SpeechDetector

READ_EVERY_MS = 300
OPEN_SPAN_MS = 150_000
# Speech shorter than this is a click or a breath, not a word.
REAL_SPEECH_MS = 200
# Silence kept in front of the next stretch, so its first sound is not clipped.
PREROLL_MS = 300
# Silence longer than this, with nothing said, is let go.
IDLE_DROP_MS = 1500
# Read past an utterance's deciding silence by this much when catching up.
CATCH_UP_SLACK_MS = 200



class LiveDictation(Controls, Listening):
    def __init__(self, transcribe: Callable[[np.ndarray], Heard], start: int = 0,
                 detector: SpeechDetector | None = None, commands: Table | None = None,
                 paused: bool = False) -> None:
        super().__init__(start, detector or SpeechDetector())
        self._transcribe = transcribe
        self.commands = commands or Table([], [])
        self.paused = paused
        self.numbers = False                # the client shows numbered things
        self._read_to = start
        self._read_at = 0.0
        self._agreed: tuple[str, ...] = ()
        self._open_text = ""
        self._open_to = start
        self.steady = Steady()
        self._busy = asyncio.Lock()
        self._wait: spoken.Candidate | None = None   # held back: a command?
        self._full = ("", start)            # the last read's text, nothing held back
        self._capital = False               # the next word starts a line
        self._cut_out: list[tuple[int, int]] = []   # commands, and words said paused
        self.lag = lag.Lag()

    @property
    def dropping(self) -> bool:
        """Words said now are not written: paused, or listening for commands only."""
        return self.paused or not self.commands.writing

    @property
    def heard(self) -> bool:
        """Something worth reading was said in the open stretch."""
        return self.speech.total(self.open_from) >= samples(REAL_SPEECH_MS)

    async def _hear(self, audio: Shortened) -> Heard:
        if audio.pcm.shape[0] == 0:
            return Heard("")
        asked = time.monotonic()
        fresh, self.fresh_at = self.fresh_at, None
        async with self._busy:
            started = time.monotonic()
            heard, read_ms, device, queued_ms = await asyncio.to_thread(
                lag.timed, self._transcribe, audio.pcm)
        wait_ms = (started - (fresh or asked)) * 1000 + queued_ms
        self.lag.read(audio.ms / 1000, wait_ms, read_ms, device)
        return without_fillers(heard)

    async def tick(self, emit: Emit) -> None:
        """Decide what to say now. Called on a clock, not per frame."""
        end = self.timeline.end
        if self._wait and self._read_to >= self.speech.last_end \
                and spoken.quiet(self.speech, self._wait.said.end, end):
            await self._decide(self._wait, emit)
            return
        if not self.heard:
            quiet_from = max(self.open_from, self.speech.last_end)
            if end - quiet_from >= samples(IDLE_DROP_MS):
                await self._drop(end - samples(PREROLL_MS), emit, least=samples(IDLE_DROP_MS))
            return
        if end - self.open_from >= samples(OPEN_SPAN_MS):
            await self.finish(emit)
            return
        new_speech = min(end, self.speech.last_end + samples(GAP_KEEP_MS) // 2) > self._read_to
        soon = (time.monotonic() - self._read_at) * 1000 < READ_EVERY_MS
        if not new_speech or soon or self._busy.locked():
            return
        self._read_at = time.monotonic()
        end = self._read_end(end)
        self.asked_to = end
        start, paused, table = self.open_from, self.paused, self.commands
        audio = self._shortened(start, end)
        heard = await self._hear(audio)
        if self.open_from != start or self.paused != paused or self.commands is not table:
            return
        self._read_to = end
        r = Reading(start, end, heard, audio)
        shown = self.steady.show(heard, until=audio.ms / 1000)
        self._full = (shown, heard_through(self.speech, r))
        c = spoken.look(self.commands, self.speech, r, self.paused, self.numbers)
        self._wait = c
        if c and spoken.quiet(self.speech, c.said.end, end):
            await self._decide(c, emit)
        elif self.dropping:   # keep what is still being said: it may be a command
            await self._drop(self._keep_from(c, r, end), emit)
        elif c:
            await self._show(self.steady.text_before(len(heard.words) - c.said.first), c.said.cut, emit)
        else:
            await self._show(*self._full, emit)
            await self._settle_sentences(r, emit)

    def _keep_from(self, c: spoken.Candidate | None, r: Reading, end: int) -> int:
        """While nothing is written, how far to let go of the stretch: never
        into an utterance still being said (even one whose words this read has
        not made out), or a command said then would lose its start."""
        u = c.said if c else spoken.last_said(self.speech, r)
        if u and not spoken.quiet(self.speech, u.end, end):
            return u.cut
        if not spoken.quiet(self.speech, self.speech.last_end, end):
            said = spoken.utterances(self.speech, end)
            return said[-1][0] - samples(PREROLL_MS) if said else self.open_from
        return self._full[1]

    def _read_end(self, end: int) -> int:
        """Where a read stops. Live, that is the end of the stream. When audio
        arrived in a burst (a suspended page catching up, a reconnect resending
        what was not settled), it is the end of the next unread utterance plus
        the silence that decides it, so each utterance is the last one of some
        read, as it was when it was said, and a command inside the backlog is
        heard as a command rather than typed."""
        for _, e in spoken.utterances(self.speech, end):
            if e > self._read_to:
                return min(end, e + samples(spoken.SILENCE_AFTER_MS + CATCH_UP_SLACK_MS))
        return end

    async def _show(self, text: str, to: int, emit: Emit) -> None:
        self._open_text = self.commands.apply_inline(text, self._capital)
        self._open_to = to
        await emit({"t": "text", "text": self._open_text, "from": self.open_from, "to": to})

    async def _decide(self, c: spoken.Candidate, emit: Emit, ended: bool = False) -> None:
        """The held-back utterance had its silence after: a command, or text."""
        self._wait = None
        if c.match is None and c.loose is None:
            if self.dropping:
                if not self.paused and self._full[0].strip():   # said in Commands only: kept, never lost
                    await emit({"t": "unwritten", "text": self._full[0]})
                await self._drop(self._full[1], emit)
            else:
                await self._show(*self._full, emit)
            return
        end = self.timeline.end
        to = end if ended else spoken.span_end(c, end)
        before = "" if self.dropping or c.said.first == 0 else self.steady.split(c.said.first - 1)[0]
        self._cut_out.append((c.said.cut, to))
        self._agreed = ()
        self.steady.reset()
        await self._settle(to, before, "", to, emit)
        words = " ".join(c.said.words)
        m = c.match
        if m is None:
            table = self.commands
            meant = await asyncio.to_thread(meaning.guess, " ".join(c.loose or ()), table)
            if self.commands.mode != table.mode:   # said in a mode that has since been left
                return
            await emit({"t": "command", "name": "guess", "arg": None, "words": words,
                        "from": c.said.cut, "to": to, **(meant or {"loading": True})})
            return
        if m.name in ("pause", "resume"):
            self.paused = m.name == "pause"
            await emit({"t": "paused", "paused": self.paused})
            return
        if m.name in SWITCHES:
            self.commands = self.commands.with_mode(SWITCHES[m.name])
            if self.commands.mode != "dictation":
                meaning.warm(self.commands)
            await emit({"t": "mode", "mode": self.commands.mode, "said": True})
            return
        await emit({"t": "command", "name": m.name, "arg": m.arg, "words": words,
                    "from": c.said.cut, "to": to})

    async def _settle_sentences(self, r: Reading, emit: Emit) -> None:
        words = r.heard.words
        texts = tuple(w.text for w in words)
        agreed = next((i for i, (a, b) in enumerate(zip(texts, self._agreed)) if a != b),
                      min(len(texts), len(self._agreed)))
        self._agreed = texts
        i = sentence_cut(r, agreed)
        if i is None:
            if r.audio.ms >= LONGEST_MS and not words:
                self.steady.reset()
                await self._settle(r.to, r.heard.text, "", r.to, emit)
            return
        cut = between(self.speech, r, i)
        self._agreed = texts[i + 1:]
        text, rest = self.steady.split(i)
        await self._settle(cut, text, rest, heard_through(self.speech, r), emit)

    async def _settle(self, cut: int, text: str, rest: str, rest_to: int, emit: Emit) -> None:
        start = self.open_from
        self.open_from = cut
        self.speech.drop_before(self.timeline.start)
        self._read_to = max(self._read_to, cut)
        self._wait = None
        if self.dropping:
            text = rest = ""
            self._cut_out.append((start, cut))
        self._cut_out = [s for s in self._cut_out if s[1] > self.timeline.start][-32:]
        text = self.commands.apply_inline(text, self._capital)
        self._capital = text.endswith("\n") if text else self._capital
        self._open_text = self.commands.apply_inline(rest, self._capital)
        self._open_to = rest_to
        await emit({"t": "final", "text": text, "from": start, "to": cut,
                    "rest": self._open_text, "restTo": rest_to})

    async def _drop(self, upto: int, emit: Emit, least: int = 1) -> None:
        """Let go of the open stretch through `upto`, writing nothing."""
        if upto - self.open_from < least:
            return
        self._agreed = ()
        self.steady.reset()
        await self._settle(upto, "", "", upto, emit)
