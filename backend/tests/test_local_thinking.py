"""A local model's effort is its thinking level: set on each request by the hop."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import local_llm
from services import local_model_provider as lmp
from services import local_thinking as lt


def _body(**extra):
    return json.dumps({"model": "m", "messages": [
        {"role": "user", "content": "hi"}], **extra}).encode()


def _rewritten(body, memory=None, **headers):
    return json.loads(lt.rewrite(body, headers, lt.new_memory() if memory is None else memory))


def test_a_message_gets_the_budget_of_its_effort():
    got = _rewritten(_body(output_config={"effort": "low"}),
                     **{lt.SESSION_HEADER: "a"})
    assert got["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert got["chat_template_kwargs"] == {"reasoning_effort": "low"}


def test_the_chats_effort_holds_for_requests_that_do_not_carry_it():
    memory = lt.new_memory()
    lt.rewrite(_body(output_config={"effort": "high"}), {lt.SESSION_HEADER: "b"},
               memory)
    side = _rewritten(_body(output_config={"effort": "medium"}), memory,
                      **{lt.SESSION_HEADER: "b", lt.CLASS_HEADER: "auxiliary"})
    assert side["thinking"]["budget_tokens"] == 0
    assert side["chat_template_kwargs"] == {"reasoning_effort": "xhigh"}


def test_a_tool_result_continuation_thinks_little():
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}],
        "output_config": {"effort": "high"}}).encode()
    got = _rewritten(body, **{lt.SESSION_HEADER: "c"})
    assert got["thinking"]["budget_tokens"] == lt.CONTINUATION


def test_a_request_that_turned_thinking_off_is_left_alone():
    body = _body(thinking={"type": "disabled"})
    assert lt.rewrite(body, {}, lt.new_memory()) == body


def test_not_json_passes_unchanged():
    assert lt.rewrite(b"not json", {}, lt.new_memory()) == b"not json"


class _Upstream:
    def __init__(self):
        self.seen: list[dict] = []
        seen = self.seen

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                n = int(self.headers["content-length"])
                seen.append({"path": self.path,
                             "body": json.loads(self.rfile.read(n))})
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
    app = FastAPI()
    app.include_router(local_llm.router)
    yield TestClient(app), up
    up.httpd.shutdown()


def test_the_hop_sends_the_server_a_request_with_thinking_set(hop):
    client, up = hop
    r = client.post("/local-llm/v1/messages?beta=true",
                    content=_body(output_config={"effort": "medium"}),
                    headers={lt.SESSION_HEADER: "d"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert up.seen[0]["path"] == "/v1/messages?beta=true"
    assert up.seen[0]["body"]["thinking"]["budget_tokens"] == 2048


def test_the_hop_answers_503_when_the_server_is_down(monkeypatch):
    monkeypatch.setattr(lmp, "base_url", lambda: "http://127.0.0.1:9")
    app = FastAPI()
    app.include_router(local_llm.router)
    r = TestClient(app).post("/local-llm/v1/messages", content=_body())
    assert r.status_code == 503


def test_a_local_turn_goes_through_the_hop_when_the_backend_knows_its_port(monkeypatch):
    provider = lmp.LocalHttpProvider("http://127.0.0.1:9")
    monkeypatch.setenv("WORKSHOP_BACKEND_PORT", "8999")
    env = provider.cli_env("m", {})
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8999/local-llm"
    assert env["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] == "1"
    monkeypatch.delenv("WORKSHOP_BACKEND_PORT")
    assert lmp.hop_url() is None
    assert provider.cli_env("m", {})["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
