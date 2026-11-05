"""Actions a page or app declares by voice (services/speech/app_actions.py):
what the client's list is taken as, how they are heard in each mode, how they
are learned for loose wordings, and a declared phrase said on the live socket
coming back as a command named by its id."""
from __future__ import annotations

import json

import numpy as np

from routes import speech_live
from services.speech import app_actions, command_store, meaning
from services.speech.commands import Table
from services.speech.heard import Heard, Word
from services.speech.meaning import Understander, examples_for
from services.voice import protocol
from tests.test_speech_live import client, speech  # noqa: F401
from tests.test_speech_meaning import Words, learned, store  # noqa: F401
from tests.test_speech_spoken import Run, silence

RENDER = {"id": "app:1", "title": "Render", "description": "Start rendering the current recipe",
          "phrases": ["render", "render it"], "examples": ["make the picture"]}
GROUP = {"id": "app:2", "title": "Add to group", "description": "Put the selected item in a group",
         "phrases": ["add to group"], "takesArg": True}


def table(mode: str, *app: dict) -> Table:
    return Table.from_commands(command_store.current(), mode).with_actions(app_actions.clean(list(app)))


def test_the_client_list_keeps_only_well_formed_actions():
    got = app_actions.clean([RENDER, {"id": "send", "phrases": ["send"]}, {"id": "app:3"},
                             {"id": "app:4", "phrases": "go"}, "x", {**RENDER, "examples": "x"}])
    assert [a["id"] for a in got] == ["app:1"]
    assert got[0]["examples"] == ["Render", "Start rendering the current recipe"]
    (a,) = app_actions.clean([RENDER])
    assert a["examples"][-1] == "make the picture" and a["takes_arg"] is False
    assert app_actions.clean(None) == () and app_actions.clean({"id": "app:1"}) == ()


def test_heard_like_the_operators_own_commands_in_each_mode():
    mixed = table("mixed", RENDER, GROUP)
    assert mixed.match(["Render."]).name == "app:1"
    assert mixed.match(["Ok", "workshop,", "render", "it."]).name == "app:1"
    assert mixed.match(["Add", "to", "group", "Sunsets."]).arg == "Sunsets"
    assert mixed.could_be(["Add", "to"]) and not mixed.could_be(["Hello"])
    assert table("commands", RENDER).match(["render"]).name == "app:1"
    assert table("dictation", RENDER).match(["render"]) is None


def test_a_built_in_said_the_same_way_wins_and_the_mode_keeps_them():
    t = table("mixed", {"id": "app:1", "title": "Send", "description": "", "phrases": ["send message"]})
    assert t.match(["send", "message"]).name == "send"
    back = table("mixed", RENDER).with_mode("commands").with_mode("writing")
    assert back.mode == "mixed" and back.match(["render"]).name == "app:1"
    assert table("mixed", RENDER).with_actions(()).match(["render"]) is None


def test_learned_from_phrases_title_description_and_examples():
    every = examples_for(table("commands", RENDER))
    assert every["app:1"] == ("render", "render it", "Render", "Start rendering the current recipe",
                              "make the picture")
    assert "app:1" not in examples_for(table("dictation", RENDER))
    t = table("commands", RENDER, GROUP)
    g = learned(Understander(Words()), t).guess("rendering the current recipe", t)  # type: ignore[arg-type]
    assert g["guesses"][0]["name"] == "app:1"


def test_each_list_trains_once_while_it_is_kept(monkeypatch):
    trained = []
    real = meaning.train
    monkeypatch.setattr(meaning, "train", lambda *a, **k: trained.append(1) or real(*a, **k))
    u = Understander(Words())  # type: ignore[arg-type]
    one, two = table("commands", RENDER), table("commands", GROUP)
    learned(learned(u, one), two)
    for t in (one, two, one, two):
        u.guess("render it", t)
    assert len(trained) == 2


def test_live_dictation_hears_the_actions_sent_and_lets_them_go(monkeypatch):
    monkeypatch.setattr(meaning.shared, "warm", lambda *a: None)
    r = Run()
    r.s.set_actions([RENDER])
    r.say("Render it.", 0.8, then=1.0)
    (cmd,) = r.of("command")
    assert cmd["name"] == "app:1" and cmd["words"] == "Render it."
    r.s.set_actions([])
    r.feed(silence(0.6))
    r.say("Render it.", 0.8, then=1.0)
    assert len(r.of("command")) == 1 and "Render it." in r.shown()


def test_socket_sends_back_a_declared_phrase_as_its_command(client, monkeypatch):  # noqa: F811
    heard = Heard("hello there", (Word("hello", 0.05, 0.4), Word("there", 0.4, 0.75)))
    monkeypatch.setattr(speech_live.engine, "transcribe_timed", lambda pcm: heard)
    with client.websocket_connect("/speech/live") as ws:
        ws.send_text(json.dumps({"t": "hello", "from": 0}))
        assert json.loads(ws.receive_text())["t"] == "ready"
        ws.send_text(json.dumps({"t": "actions", "list": [
            {"id": "app:7", "title": "Greet", "description": "", "phrases": ["hello there"]}]}))
        pcm = np.concatenate([speech(0.8), np.zeros(16000, np.int16)])
        for i in range(0, pcm.shape[0], 320):
            ws.send_bytes(protocol.encode_audio(protocol.KIND_MIC, i, pcm[i:i + 320]))
        ws.send_text(json.dumps({"t": "stop"}))
        got = []
        while not got or got[-1]["t"] != "done":
            got.append(json.loads(ws.receive_text()))
    (cmd,) = [m for m in got if m["t"] == "command"]
    assert cmd["name"] == "app:7" and cmd["words"] == "hello there"


def test_a_choice_said_in_place_is_its_argument():
    put = {"id": "app:3", "title": "Put first", "description": "", "phrases": [],
           "fixed": [["put sunsets first", "Sunsets"], ["put old portraits first", "Old portraits"]]}
    m = table("mixed", put).match(["Put", "old", "portraits", "first."])
    assert (m.name, m.arg) == ("app:3", "Old portraits")
    assert table("mixed", put).match(["put", "cats", "first"]) is None


def test_what_does_not_apply_is_heard_as_words_and_never_guessed():
    on = table("mixed", RENDER)
    off = on.with_actions(on.cmds["app"], frozenset({"send", "app:1"}))
    assert on.match(["send", "message"]).name == "send"
    assert off.match(["send", "message"]) is None and off.match(["render"]) is None
    assert not off.could_be(["send"]) and off.with_mode("commands").off == off.off
    u = Understander(Words())  # type: ignore[arg-type]
    t = Table.from_commands(command_store.current(), "commands").with_actions(app_actions.clean([RENDER]))
    masked = t.with_actions(t.cmds["app"], frozenset({"app:1"}))
    learned(u, t)
    assert u.guess("render it", t)["guesses"][0]["name"] == "app:1"
    g = u.guess("render it", masked)
    assert all(x["name"] != "app:1" for x in g["guesses"])
    assert sum(x["p"] for x in g["guesses"]) + g["none"] > 0.5


def test_live_dictation_takes_what_applies_from_the_client(monkeypatch):
    monkeypatch.setattr(meaning.shared, "warm", lambda *a: None)
    r = Run()
    r.s.set_actions([RENDER], ["app:1", 7])
    assert r.s.commands.off == {"app:1"}
    r.say("Render it.", 0.8, then=1.0)
    assert not r.of("command") and "Render it." in r.shown()
