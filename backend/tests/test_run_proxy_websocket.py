"""A WebSocket on the app runner's proxy path reaches the app's service.

`run_proxy` is HTTP; an app whose page loads through it could not open its own
socket, because nothing on `/base-apps/run/<slug>/proxy/<service>/...` accepted
an upgrade. These run the real relay against a real WebSocket server on a
loopback port this test opens, with the runner's lookup and the session store
stubbed, so no live service is involved.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

import deps_user
from routes import base_apps, base_apps_ws
from services import app_runner

_real_sleep = base_apps.asyncio.sleep


async def _no_wait(_seconds):
    await _real_sleep(0.01)


@pytest.fixture
def echo():
    """A WebSocket server that echoes frames with their type, closes with 4002
    when asked to, and records each connection it took."""
    seen: list[dict] = []

    def handler(conn):
        rec = {"path": conn.request.path, "headers": conn.request.headers,
               "subprotocol": conn.subprotocol, "closed": None}
        seen.append(rec)
        try:
            for msg in conn:
                if msg == "close-me":
                    conn.close(4002, "done here")
                    break
                conn.send(msg)
        except ConnectionClosed:
            pass
        rec["closed"] = (conn.close_code, conn.close_reason)

    def pick(conn, offered):
        return "v2" if "v2" in offered else None

    server = serve(handler, "127.0.0.1", 0, select_subprotocol=pick)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.socket.getsockname()[1], seen
    server.shutdown()
    thread.join(timeout=5)


def _client(monkeypatch, port, status="running", wanted=False):
    statuses = status if isinstance(status, list) else [status]
    calls = {"n": 0}

    def service_target(slug, svc):
        now = statuses[min(calls["n"], len(statuses) - 1)]
        calls["n"] += 1
        return now, (port if now else None)

    users = {"op": {"id": 1, "is_operator": True},
             "plain": {"id": 2, "is_operator": False}}
    monkeypatch.setattr(deps_user.auth_store, "session_user", lambda c: users.get(c))
    monkeypatch.setattr(app_runner, "service_target", service_target)
    monkeypatch.setattr(app_runner, "wants_running", lambda slug: wanted)
    monkeypatch.setattr(base_apps, "_HOLD_S", 1.0)
    monkeypatch.setattr(base_apps.asyncio, "sleep", _no_wait)
    app = FastAPI()
    app.include_router(base_apps_ws.router, prefix="/base-apps")
    tc = TestClient(app)
    tc.cookies.set("workshop_session", "op")
    return tc


def _wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestTheSocketReachesTheService:
    def test_text_and_binary_frames_round_trip(self, monkeypatch, echo):
        port, _seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/live") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "hello"
            ws.send_bytes(bytes(range(256)))
            assert ws.receive_bytes() == bytes(range(256))

    def test_the_full_path_and_query_arrive_verbatim(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/?token=a%2Fb&n=1"):
            pass
        assert seen[0]["path"] == "/base-apps/run/x/proxy/web/?token=a%2Fb&n=1"

    def test_the_subprotocol_is_negotiated_with_the_service(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/",
                                  subprotocols=["v1", "v2"]) as ws:
            assert ws.accepted_subprotocol == "v2"
        assert seen[0]["subprotocol"] == "v2"

    def test_the_service_close_code_reaches_the_browser(self, monkeypatch, echo):
        port, _seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/") as ws:
            ws.send_text("close-me")
            msg = ws.receive()
        assert msg == {"type": "websocket.close", "code": 4002, "reason": "done here"}

    def test_the_browser_close_code_reaches_the_service(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/") as ws:
            ws.send_text("ready")
            assert ws.receive_text() == "ready"
            ws.close(code=4001, reason="bye")
        assert _wait_for(lambda: seen and seen[0]["closed"] is not None)
        assert seen[0]["closed"] == (4001, "bye")

    def test_the_caller_headers_are_forwarded_and_the_handshake_is_not(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/",
                                  headers={"x-forwarded-host": "testserver"}):
            pass
        headers = seen[0]["headers"]
        assert headers["x-forwarded-host"] == "testserver"
        assert "workshop_session=op" in headers["cookie"]
        assert len(headers.get_all("sec-websocket-key")) == 1

    def test_the_signed_in_user_is_named_and_a_forged_copy_is_dropped(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/",
                                  headers={"x-workshop-user": "99"}):
            pass
        assert seen[0]["headers"].get_all("x-workshop-user") == ["1"]

    def test_a_restarting_service_is_waited_for(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port, status=["starting", "starting", "running"])
        with tc.websocket_connect("/base-apps/run/x/proxy/web/") as ws:
            ws.send_text("up")
            assert ws.receive_text() == "up"
        assert len(seen) == 1


def _refused(tc, path="/base-apps/run/x/proxy/web/", **kw) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(path, **kw):
            pass
    return e.value.code


class TestTheSameGateAsTheHttpRoute:
    def test_no_credential_is_refused_without_dialing(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        tc.cookies.clear()
        assert _refused(tc) == 4401
        assert seen == []

    def test_a_non_operator_is_refused(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        tc.cookies.set("workshop_session", "plain")
        assert _refused(tc) == 4403
        assert seen == []

    def test_a_cross_site_origin_is_refused(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        assert _refused(tc, headers={"origin": "https://elsewhere.invalid"}) == 4403
        assert seen == []

    def test_a_same_host_origin_is_accepted(self, monkeypatch, echo):
        port, _seen = echo
        tc = _client(monkeypatch, port)
        with tc.websocket_connect("/base-apps/run/x/proxy/web/",
                                  headers={"origin": "http://testserver"}) as ws:
            ws.send_text("ok")
            assert ws.receive_text() == "ok"

    def test_an_unknown_service_is_refused(self, monkeypatch, echo):
        port, _seen = echo
        tc = _client(monkeypatch, port, status=None)
        assert _refused(tc) == 4404

    def test_a_stopped_service_nobody_wants_is_refused(self, monkeypatch, echo):
        port, _seen = echo
        tc = _client(monkeypatch, port, status="stopped")
        assert _refused(tc) == 4409

    def test_an_unreachable_service_is_refused_after_the_hold(self, monkeypatch, echo):
        port, seen = echo
        tc = _client(monkeypatch, port)
        monkeypatch.setattr(app_runner, "service_target", lambda s, v: ("running", 1))
        assert _refused(tc) == 1011
        assert seen == []


def test_the_backend_mounts_the_socket_route():
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes
             if type(r).__name__ == "APIWebSocketRoute"}
    assert "/base-apps/run/{slug}/proxy/{service}/{path:path}" in paths
