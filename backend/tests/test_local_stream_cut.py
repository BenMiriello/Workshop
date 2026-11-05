"""A local model server that cuts its answer off ends the stream, not the hop.

On 2026-10-22 a config edit made the model server reload and drop the answer
it was streaming. The hop must close the response where the bytes stopped,
without raising, so the CLI reads a stream with no `message_stop`, gives that
response up and retries it (verified against CLI 2.1.311 with a fake server).
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import local_llm
from services import local_model_provider as lmp


def _sse(kind: str, data: dict) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()


START = _sse("message_start", {"type": "message_start", "message": {
    "id": "msg_1", "type": "message", "role": "assistant", "content": []}})
DELTA = _sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "half"}})
STOP = _sse("message_stop", {"type": "message_stop"})


class _Upstream:
    """Streams `START` and `DELTA`, then ends as `how` says: "reset" drops
    the connection mid-body, "short" ends the body cleanly with no
    `message_stop`, "whole" sends the rest."""

    def __init__(self, how: str):
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()

                def chunk(b: bytes):
                    self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
                    self.wfile.flush()
                chunk(START + DELTA)
                if how == "reset":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if how == "whole":
                    chunk(STOP)
                chunk(b"")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def hop(monkeypatch):
    made = []

    def make(how: str) -> TestClient:
        up = _Upstream(how)
        made.append(up)
        monkeypatch.setattr(lmp, "base_url", lambda: up.origin)
        monkeypatch.setattr(local_llm, "_window", lambda m: 98304)
        app = FastAPI()
        app.include_router(local_llm.router)
        return TestClient(app)
    yield make
    for up in made:
        up.httpd.shutdown()


def _ask(client: TestClient):
    body = {"model": "w", "max_tokens": 10, "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}
    return client.post("/local-llm/v1/messages", json=body)


def test_a_dropped_connection_ends_the_stream_where_it_stopped(hop, caplog):
    r = _ask(hop("reset"))
    assert r.status_code == 200
    assert r.content == START + DELTA
    assert "cut its stream" in caplog.text


def test_a_stream_that_ends_short_is_passed_on_as_it_came(hop, caplog):
    r = _ask(hop("short"))
    assert r.content == START + DELTA
    assert "without message_stop" in caplog.text


def test_a_whole_stream_is_not_reported(hop, caplog):
    r = _ask(hop("whole"))
    assert r.content == START + DELTA + STOP
    assert "local-llm" not in caplog.text
