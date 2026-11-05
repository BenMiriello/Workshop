"""Modes (services/speech/modes.py): dictation, mixed and commands; switched
from the store, by the client, and by voice. Saved settings from the older
four modes read as the nearest of the three."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech_commands
from services.speech import command_store, live

from tests.test_speech_spoken import Run


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(command_store, "store_path", lambda: tmp_path / "commands.json")
    # Never the real model here: it is fetched and loaded on first use.
    monkeypatch.setattr(live.meaning, "guess", lambda words, table: None)
    monkeypatch.setattr(live.meaning, "warm", lambda *a: None)


@pytest.fixture
def meant(monkeypatch):
    """What the meaning model is asked, and a fixed answer from it."""
    asked: list[str] = []

    def guess(words, table):
        asked.append(words)
        return {"guesses": [{"name": "scroll_down", "p": 0.9}], "none": 0.05}

    monkeypatch.setattr(live.meaning, "guess", guess)
    return asked


def table(mode: str | None = None):
    if mode:
        command_store.set_mode(mode)
    return command_store.load_table()


def test_dictation_hears_only_the_ways_out_and_inline_commands():
    t = table("dictation")
    assert t.match(["Send", "message."]) is None
    assert t.match(["Command", "mode."]).name == "commands_only"
    assert t.match(["Pause", "dictation."]).name == "pause"
    assert t.apply_inline("one new line two") == "one\nTwo"
    assert t.loose(("ok", "workshop", "scroll", "down")) is None


def test_mixed_hears_each_command_as_set():
    command_store.save([{"id": "send", "phrases": ["send message"], "when": "trigger"},
                        {"id": "cancel", "phrases": ["cancel"], "when": "off"}], [])
    t = table("mixed")
    assert t.match(["Send", "message."]) is None
    assert t.match(["Ok", "workshop", "send", "message."]).name == "send"
    assert t.match(["Cancel."]) is None
    assert t.match(["New", "chat."]).name == "new_chat"
    assert t.loose(("ok", "workshop", "scroll", "a", "bit")) == ("scroll", "a", "bit")
    assert t.loose(("scroll", "a", "bit")) is None


def test_commands_writes_nothing_and_understands_any_wording():
    command_store.save([{"id": "send", "phrases": ["send message"], "when": "trigger"}], [])
    t = table("commands")
    assert not t.writing
    assert t.match(["Send", "message."]).name == "send"
    assert t.inline == []
    assert t.loose(("scroll", "a", "bit")) == ("scroll", "a", "bit")
    assert t.loose(("ok", "workshop", "scroll")) == ("scroll",)


def test_older_saved_modes_read_as_the_nearest():
    path = command_store.store_path()
    for old, new in (("words", "dictation"), ("custom", "mixed"), ("trigger", "mixed")):
        path.write_text(json.dumps({"mode": old}))
        assert command_store.current()["mode"] == new
    whens = {b["id"]: b["when"] for b in command_store.current()["builtins"]}
    assert whens["send"] == "trigger"
    assert table().match(["Send", "message."]) is None
    command_store.set_mode("commands")
    assert {b["id"]: b["when"] for b in command_store.current()["builtins"]}["send"] == "trigger"


def test_commands_remembers_the_mode_to_go_back_to():
    command_store.set_mode("dictation")
    assert command_store.set_mode("commands")["back"] == "dictation"
    assert command_store.set_mode("writing")["mode"] == "dictation"
    assert command_store.load_table().with_mode("commands").with_mode("writing").mode == "dictation"


def test_reset_keeps_the_mode_and_trigger_word():
    command_store.set_mode("commands", trigger="hey shop")
    command_store.save([{"id": "send", "phrases": ["ship it"]}], [])
    cmds = command_store.reset()
    assert (cmds["mode"], cmds["trigger"]) == ("commands", "hey shop")
    assert {b["id"]: b["phrases"] for b in cmds["builtins"]}["send"] == ["send message", "submit message"]


def test_live_switches_by_voice_between_all_three():
    r = Run()
    r.say("Hello there.", 1.0, then=0.6)
    r.say("Command mode.", 1.0, then=1.0)
    assert r.of("mode")[-1] == {"t": "mode", "mode": "commands", "said": True}
    r.say("New chat.", 1.0, then=1.0)
    assert [c["name"] for c in r.of("command")] == ["new_chat"]
    r.say("Dictation mode.", 1.0, then=1.0)
    assert r.of("mode")[-1]["mode"] == "dictation"
    r.say("New chat.", 1.0, then=1.0)
    assert [c["name"] for c in r.of("command")] == ["new_chat"]
    r.say("Mixed mode.", 1.0, then=1.0)
    assert r.of("mode")[-1]["mode"] == "mixed"
    assert "Command mode" not in r.shown() and "New chat" in r.shown()


def test_live_commands_understands_what_is_not_a_phrase(meant):
    r = Run()
    r.say("Command mode.", 1.0, then=1.0)
    r.say("Move down a little.", 1.0, then=1.0)
    g = r.of("command")[-1]
    assert (g["name"], g["guesses"][0]["name"], g["none"]) == ("guess", "scroll_down", 0.05)
    assert meant == ["move down a little"]
    assert r.shown() == ""


def test_live_mixed_understands_only_after_the_trigger_word(meant):
    r = Run()
    r.say("Move down a little.", 1.0, then=1.0)
    assert r.of("command") == [] and meant == []
    r.say("Ok workshop, move down a little.", 1.5, then=1.0)
    assert r.of("command")[-1]["name"] == "guess"
    assert meant == ["move down a little"]
    assert "Ok workshop" not in r.shown()


def test_live_says_when_the_meaning_model_is_loading(monkeypatch):
    monkeypatch.setattr(live.meaning, "guess", lambda words, table: None)
    monkeypatch.setattr(live.meaning, "warm", lambda *a: None)
    r = Run()
    r.say("Command mode.", 1.0, then=1.0)
    r.say("Move down a little.", 1.0, then=1.0)
    assert r.of("command")[-1]["loading"] is True


def test_a_guess_answered_after_the_mode_changed_is_dropped(monkeypatch):
    r = Run()

    def guess(words, table):
        r.s.commands = r.s.commands.with_mode("dictation")   # the bar's button, mid-guess
        return {"guesses": [{"name": "dictation_only", "p": 0.6}], "none": 0.05}

    monkeypatch.setattr(live.meaning, "guess", guess)
    r.say("Command mode.", 1.0, then=1.0)
    r.say("These words are said in commands mode.", 1.5, then=1.0)
    assert r.of("command") == []
    r.say("These are written again.", 1.0, then=1.0)
    assert "These are written again" in r.shown()


def test_a_number_alone_picks_only_while_numbers_are_shown():
    r = Run()
    r.say("Four.", 0.6, then=1.0)
    assert r.of("command") == []
    r.s.set_numbers(True)
    r.say("Number twelve.", 1.0, then=1.0)
    assert (r.of("command")[-1]["name"], r.of("command")[-1]["arg"]) == ("pick", "12")


def test_client_mode_change_reaches_the_live_session():
    import asyncio
    r = Run()
    asyncio.run(r.s.set_mode("commands", r.emit))
    assert r.of("mode")[-1] == {"t": "mode", "mode": "commands", "said": False}
    r.say("Not written.", 1.0, then=1.0)
    assert r.shown() == ""


def test_mode_route():
    app = FastAPI()
    app.include_router(speech_commands.router)
    c = TestClient(app)
    got = c.put("/speech/commands/mode", json={"mode": "commands", "trigger": "  hey   shop "}).json()
    assert (got["mode"], got["trigger"]) == ("commands", "hey shop")
    assert c.put("/speech/commands/mode", json={"mode": "trigger"}).status_code == 422
