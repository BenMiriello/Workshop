"""The spoken commands table, matching, the per-user store and its routes."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech_commands
from services.speech import command_store
from services.speech.commands import BUILTINS, Table, tokens


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(command_store, "store_path", lambda: tmp_path / "speech" / "commands.json")
    return tmp_path / "speech" / "commands.json"


def table() -> Table:
    return command_store.load_table()


def test_defaults_are_the_documented_table():
    cmds = {b["id"]: b for b in command_store.current()["builtins"]}
    assert [b.id for b in BUILTINS] == list(cmds)
    assert cmds["send"]["phrases"] == ["send message", "submit message"]
    assert cmds["chat_model"]["takes_arg"] and cmds["new_line"]["kind"] == "inline"
    assert all(c["when"] == "alone" and c["phrases"] == c["defaults"] for c in cmds.values())


def test_matching_ignores_case_punctuation_and_fillers():
    assert tokens("Um, Scratch-that!") == ("scratch", "that")
    assert table().match(["Uh,", "Scratch", "that."]).name == "scratch"
    assert table().match(["Send", "message", "now."]) is None
    assert table().match(["Resume", "dictation."]) is None
    assert table().match(["Resume", "dictation."], paused=True).name == "resume"
    assert table().match(["Send", "message."], paused=True) is None


def test_a_command_with_a_name_takes_the_rest():
    m = table().match(["Switch", "model", "to", "Opus", "5.5."])
    assert (m.name, m.arg) == ("chat_model", "Opus 5.5")
    assert table().match(["Chat", "model."]) is None
    assert table().could_be(["Chat", "model"]) and table().could_be(["Use"])
    assert not table().could_be(["Use", "the", "force"])


def test_a_last_word_half_said_still_holds_the_phrase_back():
    assert table().could_be(["Pause", "dictate."])
    assert table().could_be(["Send", "mes"])
    assert not table().could_be(["Pause", "depending", "on"])
    assert not table().could_be(["Send", "money."])
    assert table().could_be(["Can't"])


def test_inline_commands_become_breaks():
    t = table()
    assert t.apply_inline("One, new line, two new paragraph three.") == "One\nTwo\n\nThree."
    assert t.apply_inline("still going", capital=True) == "Still going"
    assert t.apply_inline("a new line") == "a\n"


def test_edits_are_kept_and_reset():
    command_store.save([{"id": "send", "phrases": ["Ship it", "ship  it", " "], "enabled": True},
                        {"id": "cancel", "phrases": ["cancel"], "enabled": False},
                        {"id": "nope", "phrases": ["x"]}],
                       [{"id": "", "phrases": ["sign off"], "text": "Best, Sam"}])
    t = table()
    assert t.match(["Ship", "it."]).name == "send" and t.match(["Send", "message."]) is None
    assert t.match(["Cancel."]) is None
    typed = t.match(["Sign", "off."])
    assert (typed.name, typed.arg) == ("type", "Best, Sam")
    cmds = command_store.current()
    assert {b["id"]: b["phrases"] for b in cmds["builtins"]}["send"] == ["Ship it"]
    assert cmds["custom"][0]["id"] and cmds["custom"][0]["phrases"] == ["sign off"]
    command_store.reset()
    assert table().match(["Send", "message."]).name == "send"


def test_a_broken_store_reads_as_defaults(store):
    store.parent.mkdir(parents=True)
    store.write_text("{not json")
    assert table().match(["Cancel."]).name == "cancel"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(speech_commands.router)
    return TestClient(app)


def test_routes_read_write_and_reset(client):
    got = client.get("/speech/commands").json()
    assert got["custom"] == [] and got["builtins"][0]["id"] == "new_line"
    put = client.put("/speech/commands", json={
        "builtins": [{"id": "stop", "phrases": ["that's all"], "enabled": True}],
        "custom": [{"phrases": ["my address"], "text": "1 Main St"}]}).json()
    assert {b["id"]: b["phrases"] for b in put["builtins"]}["stop"] == ["that's all"]
    assert put["custom"][0]["text"] == "1 Main St"
    assert client.get("/speech/commands").json() == put
    assert client.post("/speech/commands/reset").json()["custom"] == []
    assert client.put("/speech/commands", json={"custom": [{"phrases": ["x"], "text": ""}]}).status_code == 422


def test_the_label_limit_is_kept_within_range_and_survives_a_reset(client):
    assert client.get("/speech/commands").json()["labels"] == {"max": command_store.LABELS_MAX}
    assert client.put("/speech/commands/labels", json={"max": 250}).json()["labels"] == {"max": 250}
    assert client.put("/speech/commands/labels", json={"max": 251}).status_code == 422
    assert client.put("/speech/commands/labels", json={"max": 5}).status_code == 422
    assert client.post("/speech/commands/reset").json()["labels"] == {"max": 250}


def test_speak_says_what_read_says():
    for said, cid in [("Speak this page.", "read_page"), ("Speak the last reply.", "read_reply"),
                      ("Speak faster.", "read_faster"), ("Stop speaking.", "stop_reading"),
                      ("Stop chat.", "stop_reply"), ("Go forward.", "go_forward"), ("Next page.", "go_forward")]:
        m = table().match(said.split())
        assert m is not None and m.name == cid, said


def test_numbers_past_a_hundred_are_heard_up_to_the_label_limit():
    from services.speech.command_list import spoken_number
    say = lambda s: spoken_number(tuple(s.split()))  # noqa: E731
    assert say("123") == 123
    assert say("a hundred and five") == 105
    assert say("two hundred fifty") == 250
    assert say("number one twenty three") == 123
    assert say("one two") is None
    assert say("two hundred fifty one") is None
    assert say("ninety nine") == 99
