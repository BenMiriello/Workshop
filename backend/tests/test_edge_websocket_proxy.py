"""The public edge forwards WebSocket upgrades.

Voice mode runs entirely over one socket under /api. The edge is an httpx
reverse proxy and httpx cannot carry an Upgrade, so without a dedicated route
the socket fails on the BUILT origin (:8002, the one the operator actually uses)
while working perfectly on the Vite dev origin. That is the hardest class of
difference to notice, and it is what these tests exist to stop.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def edge(monkeypatch):
    import workshop_public_app as edge_app

    sent: list[object] = []

    class FakeUpstream:
        """Echoes back whatever it is sent, prefixed, so both directions show."""

        def __init__(self):
            self.q: asyncio.Queue = asyncio.Queue()
            self.closed = False

        async def send(self, data):
            sent.append(data)
            await self.q.put(f"echo:{data}" if isinstance(data, str) else data)

        async def close(self):
            self.closed = True
            await self.q.put(None)

        def __aiter__(self):
            return self

        async def __anext__(self):
            item = await self.q.get()
            if item is None:
                raise StopAsyncIteration
            return item

    made: list[dict] = []

    async def fake_connect(url, **kw):
        made.append({"url": url, **kw})
        return FakeUpstream()

    import sys
    import types

    fake_ws = types.ModuleType("websockets")
    fake_ws.connect = fake_connect
    fake_exc = types.ModuleType("websockets.exceptions")

    class ConnectionClosed(Exception):
        pass

    fake_exc.ConnectionClosed = ConnectionClosed
    fake_ws.exceptions = fake_exc
    monkeypatch.setitem(sys.modules, "websockets", fake_ws)
    monkeypatch.setitem(sys.modules, "websockets.exceptions", fake_exc)
    return edge_app, made, sent


class TestTheEdgeCarriesTheUpgrade:
    def test_a_voice_socket_is_proxied_to_the_backend(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect("/api/voice/stream"):
            pass
        assert made, "the edge did not dial the backend at all"
        # The /api prefix is stripped, exactly as it is on the HTTP path.
        assert made[0]["url"].endswith("/voice/stream")
        assert made[0]["url"].startswith("ws://")

    def test_identity_headers_are_forwarded(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect(
            "/api/voice/stream", headers={"cookie": "workshop_session=abc"}
        ):
            pass
        headers = dict(made[0]["additional_headers"])
        assert headers.get("cookie") == "workshop_session=abc", (
            "without the cookie the backend cannot authenticate and closes the socket"
        )

    def test_frames_reach_the_backend(self, edge):
        edge_app, _made, sent = edge
        with TestClient(edge_app.app).websocket_connect("/api/voice/stream") as ws:
            ws.send_text(json.dumps({"t": "hello"}))
            assert ws.receive_text() == 'echo:{"t": "hello"}'
        assert json.loads(sent[0])["t"] == "hello"

    def test_binary_audio_survives_the_hop(self, edge):
        edge_app, _made, sent = edge
        payload = bytes(range(64))
        with TestClient(edge_app.app).websocket_connect("/api/voice/stream") as ws:
            ws.send_bytes(payload)
            assert ws.receive_bytes() == payload
        assert sent[0] == payload

    def test_the_original_host_is_forwarded(self, edge):
        # Without this the backend compares the browser's Origin against its own
        # 127.0.0.1:8000 and refuses every genuine connection with a 403.
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect(
            "/api/voice/stream", headers={"origin": "http://testserver"}
        ):
            pass
        headers = dict(made[0]["additional_headers"])
        assert headers.get("x-forwarded-host") == "testserver"

    def test_a_cross_site_origin_is_refused_at_the_edge(self, edge):
        # The edge terminates the browser's socket, so it is the boundary that
        # knows which host the page was actually served from.
        edge_app, made, _sent = edge
        with pytest.raises(Exception):
            with TestClient(edge_app.app).websocket_connect(
                "/api/voice/stream", headers={"origin": "https://elsewhere.invalid"}
            ):
                pass
        assert made == [], "a cross-site socket was dialed upstream"

    def test_a_blocked_path_is_refused_without_dialing(self, edge):
        # The blocklist covers loopback-trusted endpoints, which would otherwise
        # inherit this proxy's loopback trust. It has to apply to the socket path
        # too, or the upgrade becomes a way around it.
        edge_app, made, _sent = edge
        with pytest.raises(Exception):
            with TestClient(edge_app.app).websocket_connect("/api/tools/anything"):
                pass
        assert made == [], "a blocked path was still dialed upstream"
