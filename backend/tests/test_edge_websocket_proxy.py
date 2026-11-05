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
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def edge(monkeypatch):
    import workshop_public_app as edge_app

    sent: list[object] = []

    class FakeUpstream:
        """Echoes back whatever it is sent, prefixed, so both directions show."""

        def __init__(self, subprotocols):
            self.q: asyncio.Queue = asyncio.Queue()
            self.closed = False
            self.subprotocol = (subprotocols or [None])[-1]
            self.close_code = None
            self.close_reason = None

        async def send(self, data):
            sent.append(data)
            if data == "close-me":
                self.close_code, self.close_reason = 4002, "done here"
                await self.q.put(None)
                return
            await self.q.put(f"echo:{data}" if isinstance(data, str) else data)

        async def close(self, code=1000, reason=""):
            if not self.closed:
                self.closed = True
                self.close_code = self.close_code or code
                self.close_reason = self.close_reason or reason
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
        made[-1]["upstream"] = FakeUpstream(kw.get("subprotocols"))
        return made[-1]["upstream"]

    from services import ws_relay

    monkeypatch.setattr(ws_relay.websockets, "connect", fake_connect)
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
        #
        # THE PREFIX IS DERIVED, NOT TYPED. This asserted "/api/tools/anything"
        # and had been failing for as long as the baseline records, because
        # "/tools" is not on the blocklist and there is no sign it ever was --
        # the list is `/relay`, `/events`, `/auto-pr`, and one entry (`/services`)
        # was deliberately REMOVED on 2026-08-20 when blocking it broke two real
        # features. A test that hardcodes one member of a list somebody curates
        # fails for the list changing rather than for the property breaking, and
        # a red test nobody can act on is a test nobody reads. Reading the real
        # list means this keeps testing the property no matter which paths are
        # on it.
        edge_app, made, _sent = edge
        assert edge_app._BLOCKED_PREFIXES, "nothing is blocked; this proves nothing"
        for prefix in edge_app._BLOCKED_PREFIXES:
            made.clear()
            with pytest.raises(Exception):
                with TestClient(edge_app.app).websocket_connect(f"/api{prefix}/anything"):
                    pass
            assert made == [], f"blocked path {prefix} was still dialed upstream"


class TestTheEdgeCarriesAnAppSocket:
    """An app framed through the runner opens its socket on whichever prefix
    its page was served from, so both have to reach the backend's runner proxy."""

    def test_the_runner_path_is_dialed_verbatim(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect(
                "/base-apps/run/x/proxy/web/?token=a%2Fb&n=1"):
            pass
        assert made[0]["url"].endswith("/base-apps/run/x/proxy/web/?token=a%2Fb&n=1")

    def test_the_api_prefix_reaches_the_same_path(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect(
                "/api/base-apps/run/x/proxy/web/live"):
            pass
        assert made[0]["url"].endswith("/base-apps/run/x/proxy/web/live")
        assert "/api/" not in made[0]["url"]

    def test_the_subprotocol_is_negotiated_end_to_end(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect(
                "/base-apps/run/x/proxy/web/", subprotocols=["v1", "v2"]) as ws:
            assert ws.accepted_subprotocol == "v2"
        assert made[0]["subprotocols"] == ["v1", "v2"]

    def test_the_upstream_close_code_reaches_the_browser(self, edge):
        edge_app, _made, _sent = edge
        with TestClient(edge_app.app).websocket_connect("/base-apps/run/x/proxy/web/") as ws:
            ws.send_text("close-me")
            msg = ws.receive()
        assert msg == {"type": "websocket.close", "code": 4002, "reason": "done here"}

    def test_the_browser_close_code_reaches_the_upstream(self, edge):
        edge_app, made, _sent = edge
        with TestClient(edge_app.app).websocket_connect("/base-apps/run/x/proxy/web/") as ws:
            ws.close(code=4001, reason="bye")
        upstream = made[0]["upstream"]
        for _ in range(100):
            if upstream.closed:
                break
            time.sleep(0.01)
        assert (upstream.close_code, upstream.close_reason) == (4001, "bye")

    def test_a_cross_site_origin_is_refused_without_dialing(self, edge):
        edge_app, made, _sent = edge
        with pytest.raises(Exception):
            with TestClient(edge_app.app).websocket_connect(
                    "/base-apps/run/x/proxy/web/",
                    headers={"origin": "https://elsewhere.invalid"}):
                pass
        assert made == []
