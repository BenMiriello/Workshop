"""A spoken turn spends real money, so it goes on the ledger.

`build_streaming` has written every typed turn to the usage ledger for months.
`voice_worker` had no reference to it at all — a separate resident `claude -p`
with its own session — so talking instead of typing made the cost vanish from
/usage entirely. The same account, the same model, and no record.

These pin the metering only. Voice turns still lack a run object and the
liveness that goes with it; that is a larger piece of work and is not this.
"""
import json

import pytest

from services import usage_ledger as ul
from services import voice_worker


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "LEDGER_PATH", tmp_path / "usage.jsonl")
    return ul


@pytest.fixture
def turn(monkeypatch):
    # The scope lookup reads the thread; the point here is the ledger write, so
    # the scope is pinned rather than staged through a whole chat store.
    monkeypatch.setattr(voice_worker, "usage_scope_for_thread", lambda _tid: "workshop")
    return voice_worker.VoiceTurn(id="vt-1", session_key="thread-1", thread_id="thread-1",
                                  user_text="what did the sweep find")


def _entries(ledger):
    if not ledger.LEDGER_PATH.exists():
        return []
    return [json.loads(x) for x in ledger.LEDGER_PATH.read_text().splitlines() if x.strip()]


def test_a_spoken_turn_lands_on_the_ledger(ledger, turn):
    voice_worker._record_spend(turn, {
        "input_tokens": 1200, "output_tokens": 300,
        "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10,
    })
    got = _entries(ledger)
    assert len(got) == 1
    # Everything the turn cost, output included — the same sum the text path
    # bills (_spend_tokens_from_usage), not the context-occupancy number.
    assert got[0]["tokens"] == 1550
    assert got[0]["project"] == "workshop"


def test_it_is_attributed_the_way_a_typed_turn_is(ledger, turn):
    voice_worker._record_spend(turn, {"input_tokens": 10, "output_tokens": 5})
    e = _entries(ledger)[0]
    # role="chat" is what keeps chat spend separable from autonomous worker
    # spend downstream; the ids are what make one entry traceable to one turn.
    assert e["role"] == "chat"
    assert e["run"] == "vt-1"
    assert e["chat"] == "thread-1"


def test_a_turn_that_reported_no_usage_writes_nothing(ledger, turn):
    voice_worker._record_spend(turn, None)
    voice_worker._record_spend(turn, {})
    assert _entries(ledger) == []


def test_a_ledger_failure_never_costs_the_operator_a_reply(ledger, turn, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ul, "record", boom)
    voice_worker._record_spend(turn, {"input_tokens": 10})  # must not raise


def test_the_pump_meters_the_turn_it_just_finished(ledger, turn):
    """The wiring, not just the helper: the `result` line is the only place the
    worker is ever told what the turn cost."""
    import asyncio
    from types import SimpleNamespace

    lines = [
        json.dumps({"type": "result", "usage": {"input_tokens": 900, "output_tokens": 100}}),
    ]

    async def stdout():
        for line in lines:
            yield (line + "\n").encode()

    worker = voice_worker.VoiceWorker(
        session_key="thread-1", thread_id="thread-1", session_id="s",
        model="m", context_key="general",
    )
    worker.proc = SimpleNamespace(stdout=stdout())
    worker.current = turn

    asyncio.run(voice_worker._pump(worker))

    assert turn.complete
    assert [e["tokens"] for e in _entries(ledger)] == [1000]
