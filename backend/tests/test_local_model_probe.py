"""The local model probe never holds a request path on a slow server, and never
sends a request that names a model (a swapping server loads whatever is named)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services import local_model_provider as lmp


class _Server:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.delay = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _answer(self):
                server.seen.append((self.command, self.path))
                time.sleep(server.delay)
                body = json.dumps({"data": [{"id": "big", "context_length": 65536},
                                            {"id": "small"}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture
def server(monkeypatch):
    s = _Server()
    monkeypatch.setattr(lmp, "base_url", lambda: s.origin)
    lmp.forget_probe()
    yield s
    s.close()
    lmp.forget_probe()


def test_a_reading_only_lists_models(server):
    probe = lmp.probe()
    assert [m.id for m in probe.models] == ["big", "small"]
    assert server.seen == [("GET", "/v1/models")]
    assert probe.models[0].capabilities.context_tokens == 65536


def test_a_stale_reading_is_answered_at_once_and_refreshed_behind(server, monkeypatch):
    first = lmp.probe()
    server.delay = 1.0
    monkeypatch.setattr(lmp, "PROBE_TTL_S", 0.0)
    start = time.monotonic()
    again = lmp.probe()
    assert time.monotonic() - start < 0.2
    assert again is first
    deadline = time.monotonic() + 5
    while lmp._PROBE.get(server.origin) is first and time.monotonic() < deadline:
        time.sleep(0.05)
    assert lmp._PROBE.get(server.origin) is not first


def test_one_refresh_at_a_time(server, monkeypatch):
    lmp.probe()
    server.delay = 0.5
    monkeypatch.setattr(lmp, "PROBE_TTL_S", 0.0)
    for _ in range(10):
        lmp.probe()
    time.sleep(1.0)
    assert server.seen.count(("GET", "/v1/models")) == 2


def test_the_context_window_is_what_the_server_reported(server):
    from services import context_window

    assert context_window.window_for("big") == 65536
    assert context_window.window_for("big", 70000) == 200_000
    assert context_window.window_for("small") == 32_768


def test_effort_is_the_thinking_level_where_a_hop_applies(server, monkeypatch):
    from services import model_caps

    monkeypatch.setenv("WORKSHOP_BACKEND_PORT", "8999")
    lmp.forget_probe()
    assert model_caps.efforts_for("big", "general") == ("low", "medium", "high")
    assert model_caps.scope_default_effort("big", "general") == "medium"
    monkeypatch.delenv("WORKSHOP_BACKEND_PORT")
    lmp.forget_probe()
    assert model_caps.efforts_for("big", "general") == ()
    assert model_caps.scope_default_effort("big", "general") is None
