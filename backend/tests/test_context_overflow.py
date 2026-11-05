"""A local chat that outgrows its model's window compacts and goes on.

On 2026-10-14 an app chat on an 81,920-token model sent a request of 84,627
and stuck: the CLI had been told no window (the server listed none for a model
it had not loaded since reading its config), so it never compacted, and the
server's refusal was in words the CLI does not compact on. Pinned here: the
hop puts that refusal in the Anthropic API's words, and an app's gateway gives
the window the CLI is told.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import local_llm
from services import app_chat_gateway, context_overflow
from services import local_model_provider as lmp

TOO_LONG = {"error": {
    "code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 84627,
    "n_ctx": 81920, "message": "request (84627 tokens) exceeds the available "
    "context size (81920 tokens), try increasing it"}}


class _Server:
    """Answers every request with `self.status` and `self.answer`."""

    def __init__(self, status: int, answer: dict):
        self.status, self.answer = status, answer
        self.seen: list[tuple[str, str, dict]] = []
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _reply(self):
                n = int(self.headers.get("content-length") or 0)
                self.rfile.read(n)
                server.seen.append((self.command, self.path, dict(self.headers)))
                out = json.dumps(server.answer).encode()
                self.send_response(server.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _reply

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    made: list[_Server] = []

    def make(status: int, answer: dict) -> _Server:
        made.append(_Server(status, answer))
        return made[-1]
    yield make
    for s in made:
        s.httpd.shutdown()


def _hop(monkeypatch, up: _Server) -> TestClient:
    monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
    app = FastAPI()
    app.include_router(local_llm.router)
    return TestClient(app)


def _ask(client: TestClient):
    return client.post("/local-llm/v1/messages", content=json.dumps(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]}))


def test_the_refusal_reads_from_its_fields_or_its_words():
    raw = json.dumps(TOO_LONG).encode()
    assert context_overflow.parse(400, raw) == (84627, 81920)
    words = json.dumps({"error": {"message": TOO_LONG["error"]["message"]}})
    assert context_overflow.parse(400, words.encode()) == (84627, 81920)
    assert context_overflow.parse(500, raw) is None
    assert context_overflow.anthropic_error(400, b'{"error": {"message": "x"}}') is None
    assert context_overflow.anthropic_error(400, b"not json") is None


def test_the_hop_refuses_an_over_long_prompt_in_the_apis_words(server, monkeypatch):
    r = _ask(_hop(monkeypatch, server(400, TOO_LONG)))
    assert r.status_code == 400
    assert r.json() == {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "prompt is too long: 84627 tokens > 81920 maximum"}}


def test_any_other_400_passes_as_it_came(server, monkeypatch):
    r = _ask(_hop(monkeypatch, server(400, {"error": {"message": "bad tool"}})))
    assert r.status_code == 400 and r.json() == {"error": {"message": "bad tool"}}


def test_window_env_sets_the_window_and_bounds_the_reply():
    assert lmp.window_env(81920) == {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "81920",
                                     "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"}
    assert lmp.window_env(16384)["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    assert lmp.window_env(0) == {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"}


def test_the_gateways_window_is_the_one_the_cli_is_told(server, monkeypatch):
    gw = server(200, {"data": [{"id": "m", "context_length": 81920},
                               {"id": "other", "context_length": 98304}]})
    monkeypatch.setattr(app_chat_gateway, "gateway_route", lambda thread, turn="": (
        f"{gw.origin}/llm", {"X-Workshop-Thread": "t-1"}))
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_MODEL": "m",
           **lmp.window_env(98304)}
    out = app_chat_gateway.gateway_env({"id": "t-1"}, env)
    assert out["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "81920"
    method, path, headers = gw.seen[0]
    assert (method, path) == ("GET", "/llm/v1/models")
    assert headers.get("X-Workshop-Thread") == "t-1"


def test_a_gateway_that_gives_no_window_leaves_the_servers(server, monkeypatch):
    gw = server(200, {"data": [{"id": "m"}]})
    monkeypatch.setattr(app_chat_gateway, "gateway_route", lambda thread, turn="": (
        f"{gw.origin}/llm", {}))
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_MODEL": "m",
           **lmp.window_env(98304)}
    assert app_chat_gateway.gateway_env({"id": "t-1"}, env)[
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "98304"
    monkeypatch.setattr(app_chat_gateway, "gateway_route", lambda thread, turn="": (
        "http://127.0.0.1:9/llm", {}))
    assert app_chat_gateway.gateway_env({"id": "t-1"}, env)[
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "98304"
