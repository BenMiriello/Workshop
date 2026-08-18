"""The one-shot that renames the chat store's on-disk `run` vocabulary.

What is pinned here is what makes the rename safe to run against the live
store: the event COUNT is invariant, a line nothing matched is left byte for
byte alone (including a torn one), and a second pass changes nothing. The last
one is what lets the startup hook be unconditional rather than sequenced.
"""
import json

import pytest

from services import chat_log_turn_rename as rename
from services import ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    d = tmp_path / "users" / "6" / "chat" / "t1"
    d.mkdir(parents=True)
    return d


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_a_log_is_renamed_and_nothing_is_lost(store):
    log = store / "log.jsonl"
    log.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in [
        {"seq": 1, "kind": "message", "role": "user", "content": "go"},
        {"seq": 2, "kind": "run", "run_id": "R1", "state": "started"},
        {"seq": 3, "kind": "message", "role": "user", "content": "wait",
         "meta": {"during_run": "R1", "at_unit": 1}},
        {"seq": 4, "kind": "message", "role": "assistant", "meta": {"turn_id": "R1"}},
        {"seq": 5, "kind": "run", "run_id": "R1", "state": "finished"},
    ]))

    stats = rename.run(root=store.parents[2])

    assert stats["log_events"] == 3          # two markers plus the held message
    evs = _lines(log)
    assert len(evs) == 5
    assert [e["kind"] for e in evs] == [
        "message", "turn", "message", "message", "turn"]
    assert [e.get("turn_id") for e in evs] == [None, "R1", None, None, "R1"]
    assert "run_id" not in evs[1]
    assert evs[2]["meta"] == {"during_turn": "R1", "at_unit": 1}
    assert evs[3]["meta"] == {"turn_id": "R1"}          # already right, untouched
    assert evs[0]["content"] == "go"


def test_a_torn_line_survives_verbatim(store):
    log = store / "log.jsonl"
    log.write_text('{"seq":1,"kind":"run","run_id":"R1","state":"started"}\n'
                   '{"seq":2,"kind":"messa\n'
                   '{"seq":3,"kind":"message","role":"user"}\n')

    rename.run(root=store.parents[2])

    raw = log.read_text().splitlines()
    assert len(raw) == 3
    assert raw[1] == '{"seq":2,"kind":"messa'
    assert raw[2] == '{"seq":3,"kind":"message","role":"user"}'


def test_meta_and_live_move_their_claim(store):
    (store / "meta.json").write_text(json.dumps({
        "id": "t1", "last_seq": 5, "active_run_id": "R1", "active_run_seq": 2,
        "live_message": {"run_id": "R1", "content": "half",
                         "meta": {"turn_id": "R1", "segments": []}},
    }, indent=2))
    (store / "live.json").write_text(json.dumps(
        {"run_id": "R1", "content": "half", "meta": {"segments": []}}))

    rename.run(root=store.parents[2])

    meta = json.loads((store / "meta.json").read_text())
    assert meta["active_turn_id"] == "R1"
    assert meta["active_turn_seq"] == 2
    assert "active_run_id" not in meta and "active_run_seq" not in meta
    assert meta["live_message"]["turn_id"] == "R1"
    assert "run_id" not in meta["live_message"]
    assert meta["last_seq"] == 5
    assert json.loads((store / "live.json").read_text())["turn_id"] == "R1"


def test_a_second_pass_changes_nothing(store):
    log = store / "log.jsonl"
    log.write_text('{"seq":1,"kind":"run","run_id":"R1","state":"started"}\n')
    (store / "meta.json").write_text(json.dumps({"active_run_id": "R1"}, indent=2))

    rename.run(root=store.parents[2])
    after = (log.read_bytes(), (store / "meta.json").read_bytes())

    again = rename.run(root=store.parents[2])
    assert again == {"threads": 1, "log_events": 0, "meta_files": 0, "live_files": 0}
    assert (log.read_bytes(), (store / "meta.json").read_bytes()) == after


def test_dry_run_writes_nothing(store):
    log = store / "log.jsonl"
    log.write_text('{"seq":1,"kind":"run","run_id":"R1","state":"started"}\n')
    before = log.read_bytes()

    assert rename.run(root=store.parents[2], dry=True)["log_events"] == 1
    assert log.read_bytes() == before


def test_run_once_is_guarded_by_its_marker(store, tmp_path):
    log = store / "log.jsonl"
    log.write_text('{"seq":1,"kind":"run","run_id":"R1","state":"started"}\n')

    first = rename.run_once()
    assert first is not None and first["log_events"] == 1
    assert (tmp_path / rename.MARKER).exists()

    log.write_text('{"seq":1,"kind":"run","run_id":"R2","state":"started"}\n')
    assert rename.run_once() is None
    assert _lines(log)[0]["kind"] == "run"       # untouched: the marker held
