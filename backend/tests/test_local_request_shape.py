"""A local chat's request reaches the server in the shape it reads right.

Two failures of one local chat's compactions on 2026-10-18, both measured:
the summary instruction, sent beside the last tool results, was rendered
BEFORE them by llama-server's Anthropic conversion, so the model went on with
the work and that became the "summary"; and a whole summary did not fit the
8,000-token reply cap (one stopped at exactly 8,000 mid-section).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import local_llm
from services import local_model_provider as lmp
from services import local_request_shape as shape

RESULT = {"type": "tool_result", "tool_use_id": "tu-1", "content": "saved"}
RESULT_2 = {"type": "tool_result", "tool_use_id": "tu-2", "content": "sheet"}
ASK = {"type": "text", "text": "Your task is to create a detailed summary"}
REMINDER = {"type": "text", "text": "<system-reminder>x</system-reminder>"}
CALL = {"role": "assistant", "content": [
    {"type": "tool_use", "id": "tu-1", "name": "f", "input": {}},
    {"type": "tool_use", "id": "tu-2", "name": "g", "input": {}}]}
OPENING = {"role": "user", "content": "do the work"}


def body(last: list, max_tokens: int = 8000) -> bytes:
    return json.dumps({"model": "w", "max_tokens": max_tokens, "system": "persona",
                       "messages": [OPENING, CALL,
                                    {"role": "user", "content": last}]}).encode()


def test_the_results_go_before_what_the_cli_wrote_after_them():
    got = json.loads(shape.shape(body([RESULT, RESULT_2, ASK]), None,
                                 lambda m: 0))["messages"]
    assert got == [OPENING, CALL, {"role": "user", "content": [RESULT, RESULT_2]},
                   {"role": "user", "content": [ASK]}]


def test_every_run_keeps_its_place():
    msgs = shape.split_results([{"role": "user", "content": [
        REMINDER, RESULT, RESULT_2, ASK, REMINDER]}])
    assert [m["content"] for m in msgs] == [[REMINDER], [RESULT, RESULT_2],
                                            [ASK, REMINDER]]


def test_a_request_with_nothing_to_split_is_sent_as_it_came():
    for last in ([RESULT, RESULT_2], [ASK], [REMINDER, ASK]):
        raw = body(last)
        assert shape.shape(raw, None, lambda m: 0) == raw
    assert shape.shape(b"not json", None, lambda m: 0) == b"not json"
    msgs = [{"role": "assistant", "content": [RESULT, ASK]}]
    assert shape.split_results(msgs) == msgs


def test_a_compaction_has_room_for_its_summary_within_the_window():
    room = json.loads(shape.shape(body([RESULT, ASK]), "compaction",
                                  lambda m: 98304))["max_tokens"]
    assert room == shape.COMPACTION_OUT
    big = json.loads(body([RESULT, {"type": "text", "text": "x" * 3 * 60000}]))
    left = 70000 - shape.request_tokens(big) - shape.MARGIN
    assert shape.compaction_room(big, 70000) == left
    assert 8000 < left < shape.COMPACTION_OUT
    assert shape.compaction_room(big, 65000) is None
    assert shape.compaction_room(big, 0) is None


def test_only_a_compaction_gets_more_room():
    for klass in (None, "main", "subagent", "auxiliary"):
        got = json.loads(shape.shape(body([RESULT, ASK]), klass, lambda m: 98304))
        assert got["max_tokens"] == 8000


def test_a_picture_counts_as_a_picture_not_as_its_base64():
    pic = {"type": "image", "source": {"type": "base64", "data": "A" * 400000}}
    def asking(content):
        return shape.request_tokens({"messages": [{"role": "user", "content": content}]})
    assert asking([pic]) - asking([]) == shape.IMAGE_TOKENS


class _Upstream:
    def __init__(self):
        self.seen: list[dict] = []
        seen = self.seen

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                n = int(self.headers["content-length"])
                seen.append(json.loads(self.rfile.read(n)))
                out = b'{"ok": true}'
                self.send_response(200)
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def hop(monkeypatch):
    up = _Upstream()
    monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
    monkeypatch.setattr(local_llm, "_window", lambda m: 98304)
    app = FastAPI()
    app.include_router(local_llm.router)
    yield TestClient(app), up
    up.httpd.shutdown()


def test_the_hop_sends_a_compaction_on_in_this_shape(hop):
    client, up = hop
    client.post("/local-llm/v1/messages", content=body([RESULT, RESULT_2, ASK]),
                headers={"x-claude-code-request-class": "compaction"})
    client.post("/local-llm/v1/messages", content=body([RESULT, REMINDER]))
    compaction, main = up.seen
    assert compaction["messages"][-1]["role"] == "user"
    assert compaction["messages"][-1]["content"][0] == ASK
    assert compaction["max_tokens"] == shape.COMPACTION_OUT
    assert compaction["thinking"]["budget_tokens"] == 0
    assert main["messages"][-2:] == [{"role": "user", "content": [RESULT]},
                                     {"role": "user", "content": [REMINDER]}]
    assert main["max_tokens"] == 8000
    # Still a continuation: the thinking is set before the split.
    assert main["thinking"]["budget_tokens"] == 512


def test_a_compaction_is_told_to_write_one_short_complete_summary():
    got = json.loads(shape.shape(body([RESULT, ASK]), "compaction", lambda m: 98304))
    last = got["messages"][-1]["content"]
    assert last[0] == ASK and "LENGTH LIMIT FOR THIS RUN" in last[-1]["text"]
    assert "1,500 words" in last[-1]["text"]


def test_the_length_limit_is_added_once_and_only_to_a_compaction():
    once = shape.shape(body([RESULT, ASK]), "compaction", lambda m: 98304)
    again = shape.shape(once, "compaction", lambda m: 98304)
    assert json.loads(again)["messages"] == json.loads(once)["messages"]
    plain = json.loads(shape.shape(body([RESULT, ASK]), "main", lambda m: 98304))
    assert "LENGTH LIMIT" not in json.dumps(plain)


def test_a_compaction_with_no_summary_instruction_is_left_alone():
    got = json.loads(shape.shape(body([REMINDER]), "compaction", lambda m: 98304))
    assert "LENGTH LIMIT" not in json.dumps(got)
