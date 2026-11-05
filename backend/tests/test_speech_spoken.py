"""Spoken commands in live dictation: an action counts only said on its own,
its words never show as text while it could still be one, inline commands are
written as line breaks, and pause drops what is said until resume."""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pytest

from services.speech import command_store
from services.speech.heard import Heard, Word
from services.speech.live import LiveDictation
from services.voice.detectors import SpeechFrame

RATE = 16000


class LoudIsSpeech:
    tier = "loud"

    def __call__(self, frame: np.ndarray) -> SpeechFrame:
        p = float(np.abs(frame).mean() > 0.05)
        return SpeechFrame(p >= 0.5, p)


class Speaker:
    """Each line is said at its own loudness, so a read of any part of the
    stream knows which line it hears and how much of it has been said."""

    def __init__(self) -> None:
        self.lines: dict[int, tuple[list[str], int]] = {}

    def say(self, text: str, seconds: float) -> np.ndarray:
        amp = 3000 + 400 * len(self.lines)
        n = int(seconds * RATE)
        self.lines[amp] = (text.split(), n)
        return np.full(n, amp, dtype=np.int16)

    def __call__(self, pcm: np.ndarray) -> Heard:
        words: list[Word] = []
        at = 0
        while at < pcm.shape[0]:
            amp = int(pcm[at])
            end = at
            while end < pcm.shape[0] and int(pcm[end]) == amp:
                end += 1
            if amp in self.lines:
                said, n = self.lines[amp]
                per = n / len(said)
                for j in range(min(len(said), math.ceil((end - at) / per))):
                    last = min(at + (j + 1) * per, end) / RATE - 0.05
                    words.append(Word(said[j], (at + j * per) / RATE, last))
            at = end
        return Heard(" ".join(w.text for w in words), tuple(words))


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.int16)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(command_store, "store_path", lambda: tmp_path / "commands.json")


class Run:
    def __init__(self, paused: bool = False, voice: Speaker | None = None) -> None:
        self.voice = voice or Speaker()
        self.out: list[dict] = []
        self.s = LiveDictation(self.voice, detector=LoudIsSpeech(),
                               commands=command_store.load_table(), paused=paused)
        self.at = 0

    async def emit(self, msg: dict) -> None:
        self.out.append(msg)

    def feed(self, pcm: np.ndarray) -> None:
        for i in range(0, pcm.shape[0], 320):
            chunk = pcm[i:i + 320]
            self.s.write(self.at, chunk)
            self.at += chunk.shape[0]

    def say(self, text: str, seconds: float, then: float = 0.0) -> None:
        """Say a line, a tick every 0.25 s, then `then` seconds of silence."""
        pcm = np.concatenate([self.voice.say(text, seconds), silence(then)])
        step = RATE // 4
        for i in range(0, pcm.shape[0], step):
            self.feed(pcm[i:i + step])
            self.tick()

    def tick(self) -> None:
        self.s._read_at = 0.0
        asyncio.run(self.s.tick(self.emit))

    def of(self, t: str) -> list[dict]:
        return [m for m in self.out if m["t"] == t]

    def shown(self) -> str:
        """What a client tracking `from` the way the shell does ends up with."""
        done: list[str] = []
        open_from, open_text = 0, ""
        for m in self.out:
            if m["t"] == "text" and m["from"] == open_from:
                open_text = m["text"]
            elif m["t"] == "final" and m["from"] == open_from:
                done.append(m["text"])
                open_from, open_text = m["to"], m["rest"]
        return " ".join(t for t in [*done, open_text] if t)


def test_an_action_said_alone_settles_before_it_and_is_cut_from_the_stream():
    r = Run()
    r.say("Hello there.", 1.0, then=0.6)
    r.say("Send message.", 1.0, then=1.0)
    (cmd,) = r.of("command")
    final = [m for m in r.of("final") if m["text"]][-1]
    assert cmd["name"] == "send" and cmd["arg"] is None and cmd["words"] == "Send message."
    assert final["text"] == "Hello there." and final["to"] == cmd["to"]
    assert final["from"] < cmd["from"] < cmd["to"] <= r.at
    assert r.s.open_from == cmd["to"]
    r.say("More.", 0.5, then=0.3)
    assert r.of("text")[-1]["from"] == cmd["to"] and r.of("text")[-1]["text"] == "More."


class LateSpeaker(Speaker):
    """Hears each line's last word ending 0.2 s past its speech, as the
    browser's noise suppression makes the speech detector's runs end early."""

    def __call__(self, pcm: np.ndarray) -> Heard:
        heard = super().__call__(pcm)
        ends = {said[-1] for said, _ in self.lines.values()}
        words = tuple(Word(w.text, w.start, w.last + 0.25) if w.text in ends else w for w in heard.words)
        return Heard(heard.text, words)


def test_a_last_word_heard_late_stays_with_its_sentence():
    r = Run(voice=LateSpeaker())
    r.say("Hello there.", 1.0, then=0.6)
    r.say("Send message.", 1.0, then=1.0)
    (cmd,) = r.of("command")
    assert cmd["name"] == "send"
    assert [m for m in r.of("final") if m["text"]][-1]["text"] == "Hello there."


def test_command_words_never_show_as_text():
    r = Run()
    r.say("Hello there.", 1.0, then=0.6)
    r.say("Send message.", 1.0, then=1.0)
    assert all("Send" not in m["text"] for m in r.of("text"))
    cmd = r.of("command")[0]
    assert r.of("text")[-1]["text"] == "Hello there." and r.of("text")[-1]["to"] == cmd["from"]
    assert all(m["to"] <= cmd["from"] for m in r.of("text"))


def test_a_start_that_never_becomes_a_command_is_released_as_text():
    r = Run()
    r.say("Hello.", 0.6, then=0.6)
    r.say("Send", 0.4, then=1.0)
    assert r.of("command") == []
    assert r.of("text")[-1]["text"] == "Hello. Send"


def test_inside_running_speech_a_command_is_text():
    r = Run()
    r.say("Please send message now.", 2.0, then=1.0)
    assert r.of("command") == []
    assert r.of("text")[-1]["text"] == "Please send message now."


def test_no_pause_before_means_text():
    r = Run()
    r.say("Hello there.", 1.0, then=0.2)
    r.say("Cancel.", 0.5, then=1.0)
    assert r.of("command") == []
    assert "Cancel." in r.of("text")[-1]["text"]


def test_speech_right_after_means_text():
    r = Run()
    r.say("Hello.", 0.6, then=0.6)
    r.say("Cancel that", 0.6, then=0.3)
    r.say("plan.", 0.4, then=1.0)
    assert r.of("command") == []
    assert r.of("text")[-1]["text"] == "Hello. Cancel that plan."


def test_the_start_of_dictation_counts_as_a_pause_before():
    r = Run()
    r.say("Cancel.", 0.5, then=1.0)
    (cmd,) = r.of("command")
    assert cmd["name"] == "cancel" and cmd["words"] == "Cancel."
    assert r.of("final")[-1]["text"] == "" and r.of("final")[-1]["to"] == cmd["to"]


def test_chat_model_takes_the_rest_as_its_name():
    r = Run()
    r.say("Chat model opus five.", 1.2, then=1.0)
    (cmd,) = r.of("command")
    assert cmd["name"] == "chat_model" and cmd["arg"] == "opus five"


def test_inline_commands_are_line_breaks_anywhere():
    r = Run()
    r.say("Dear Sam, new line, thanks new paragraph bye.", 2.4, then=0.3)
    assert r.of("text")[-1]["text"] == "Dear Sam\nThanks\n\nBye."
    asyncio.run(r.s.finish(r.emit))
    assert r.of("final")[-1]["text"] == "Dear Sam\nThanks\n\nBye."


def test_a_line_break_settled_capitalises_the_next_stretch():
    r = Run()
    r.say("Hello new line", 0.9, then=0.3)
    asyncio.run(r.s.finish(r.emit))
    r.say("again.", 0.5, then=0.3)
    assert r.of("final")[0]["text"] == "Hello\n"
    assert r.of("text")[-1]["text"] == "Again."


def test_spoken_pause_drops_words_until_resume():
    r = Run()
    r.say("First.", 0.5, then=0.6)
    r.say("Pause dictation.", 1.0, then=1.0)
    assert r.of("paused") == [{"t": "paused", "paused": True}] and r.of("command") == []
    r.say("Secret words here.", 1.2, then=1.0)
    r.say("Send message.", 1.0, then=1.0)
    assert r.of("command") == []
    r.say("Resume dictation.", 1.0, then=1.0)
    assert r.of("paused")[-1] == {"t": "paused", "paused": False}
    r.say("After.", 0.5, then=0.3)
    assert r.shown() == "First. After."
    assert r.of("text")[-1]["from"] == r.s.open_from


def test_client_pause_settles_then_drops_and_hears_stop():
    r = Run()
    r.say("Before.", 0.5, then=0.2)
    asyncio.run(r.s.set_paused(True, r.emit))
    assert r.of("final")[-1]["text"] == "Before." and r.out[-1] == {"t": "paused", "paused": True}
    r.say("Not this.", 0.8, then=0.6)
    r.say("Stop dictation.", 1.0, then=1.0)
    assert r.of("command")[-1]["name"] == "stop"
    assert r.shown() == "Before."


def test_paused_at_hello_writes_nothing():
    r = Run(paused=True)
    r.say("Quiet please.", 1.0, then=1.0)
    asyncio.run(r.s.finish(r.emit))
    assert r.shown() == "" and r.s.open_from == r.at


def test_a_command_said_just_before_stop_still_counts():
    r = Run()
    r.say("Done here.", 0.8, then=0.6)
    r.voice_line = r.voice.say("Send message.", 1.0)
    r.feed(r.voice_line)
    asyncio.run(r.s.finish(r.emit))
    assert r.of("command")[-1]["name"] == "send"
    assert [m["text"] for m in r.of("final") if m["text"]] == ["Done here."]


def test_commit_behind_does_not_say_a_command_again():
    r = Run()
    r.say("Hello.", 0.5, then=0.6)
    first = r.of("text")[-1]
    r.say("Open chat.", 0.8, then=1.0)
    assert r.of("command")[-1]["name"] == "open_chat"
    asyncio.run(r.s.commit(first["from"], r.emit))
    assert r.of("final")[-1]["text"] == "Hello."


def test_commands_in_audio_that_arrives_in_one_burst_are_each_heard():
    """A suspended page sends what it recorded all at once when it wakes. Every
    utterance in that backlog is decided as it was said: the cancel after the
    first send is a command, and the next send comes after the words before it."""
    r = Run()
    r.say("Hello there.", 1.0, then=0.6)
    r.say("Send message.", 1.0, then=1.0)
    r.feed(r.voice.say("Cancel.", 0.6))
    r.feed(silence(5.0))
    r.feed(r.voice.say("Another line.", 1.0))
    r.feed(silence(0.6))
    r.feed(r.voice.say("Send message.", 1.0))
    r.feed(silence(1.0))
    for _ in range(10):
        r.tick()
    assert [c["name"] for c in r.of("command")] == ["send", "cancel", "send"]
    finals = [m["text"] for m in r.of("final") if m["text"]]
    assert finals == ["Hello there.", "Another line."]
