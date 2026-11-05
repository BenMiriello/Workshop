"""The app proxy reuses one client and does not probe the app per request.

`run_proxy` used to build a fresh httpx client (~11ms of CPU) and run
`app_status`, which makes blocking HTTP and TCP health probes, for every request
it forwarded. Both ran on the event loop, so a page of thumbnails stalled the
whole backend.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import base_apps
from services import app_runner

_real_sleep = base_apps.asyncio.sleep


async def _no_wait(_seconds):
    """The hold's pauses, made instant; the 1s budget is real time spent looping."""
    await _real_sleep(0.01)


def _setup(monkeypatch, status="running", fail=False):
    """`status` is one status, or a list consumed one lookup at a time (the last
    one sticks). `fail` is True, or how many sends fail before one gets through."""
    calls = {"status": 0, "clients": 0, "paths": [], "sends": 0}
    statuses = status if isinstance(status, list) else [status]

    def app_status(slug):
        now = statuses[min(calls["status"], len(statuses) - 1)]
        calls["status"] += 1
        return {"services": [{"name": "api", "port": 9, "status": now}]}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["sends"] += 1
        if fail is True or calls["sends"] <= int(fail):
            raise httpx.ConnectError("refused", request=request)
        calls["paths"].append(request.url.path)
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"),
                              headers={"cache-control": "x", "content-length": "2"})

    real = httpx.AsyncClient

    def client(**kw):
        calls["clients"] += 1
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(app_runner, "app_status", app_status)
    monkeypatch.setattr(base_apps, "_HOLD_S", 1.0)
    monkeypatch.setattr(base_apps.asyncio, "sleep", _no_wait)
    monkeypatch.setattr(base_apps.httpx, "AsyncClient", client)
    app = FastAPI()
    app.include_router(base_apps.router, prefix="/base-apps")
    return TestClient(app), calls


def test_requests_share_one_client_and_one_status_lookup(monkeypatch):
    tc, calls = _setup(monkeypatch)
    with tc:
        for n in range(5):
            r = tc.get(f"/base-apps/run/x/proxy/api/media/{n}/thumb")
            assert r.status_code == 200 and r.content == b"ok"
            assert r.headers["cache-control"] == "x"
    assert calls["clients"] == 1
    assert calls["status"] == 1
    assert calls["paths"][-1] == "/base-apps/run/x/proxy/api/media/4/thumb"


def test_a_failed_connect_forgets_the_port(monkeypatch):
    tc, calls = _setup(monkeypatch, fail=True)
    with tc:
        assert tc.post("/base-apps/run/x/proxy/api/a").status_code == 502
        assert tc.post("/base-apps/run/x/proxy/api/a").status_code == 502
    assert calls["status"] == 2
    assert calls["sends"] == 2


def test_a_get_waits_out_a_restart(monkeypatch):
    tc, calls = _setup(monkeypatch, status=["running", "failed", "starting", "running"], fail=1)
    with tc:
        r = tc.get("/base-apps/run/x/proxy/api/media/1/thumb")
    assert r.status_code == 200 and r.content == b"ok"
    assert calls["sends"] == 2


def test_a_get_gives_up_when_the_service_never_returns(monkeypatch):
    tc, calls = _setup(monkeypatch, fail=True)
    with tc:
        assert tc.get("/base-apps/run/x/proxy/api/a").status_code == 502
    assert calls["sends"] > 1


def test_a_post_is_never_resent(monkeypatch):
    tc, calls = _setup(monkeypatch, status=["starting", "running"])
    with tc:
        assert tc.post("/base-apps/run/x/proxy/api/a", content=b"x").status_code == 409
    assert calls["sends"] == 0


def test_a_stopped_service_is_refused_and_not_remembered(monkeypatch):
    tc, calls = _setup(monkeypatch, status="stopped")
    with tc:
        assert tc.get("/base-apps/run/x/proxy/api/a").status_code == 409
        assert tc.get("/base-apps/run/x/proxy/api/a").status_code == 409
    assert calls["status"] == 2


def test_a_get_waits_out_a_restart_of_an_app_the_runner_wants_up(monkeypatch):
    """Mid-restart the service reads `stopped` (old process exiting), then
    `failed` (gone, port released), then `starting`: all of it is a pause for an
    app the runner means to keep running, never a 409 (2026-10-04 08:21, 08:41)."""
    tc, calls = _setup(monkeypatch, status=["stopped", "failed", "stopped", "starting", "running"])
    monkeypatch.setitem(app_runner._STATE, "x", {"desired": "running"})
    with tc:
        r = tc.get("/base-apps/run/x/proxy/api/a")
    assert r.status_code == 200 and r.content == b"ok"
    assert calls["status"] == 5


def test_a_stopped_app_the_runner_does_not_want_up_is_still_refused(monkeypatch):
    tc, calls = _setup(monkeypatch, status="stopped")
    monkeypatch.setitem(app_runner._STATE, "x", {"desired": "stopped"})
    with tc:
        assert tc.get("/base-apps/run/x/proxy/api/a").status_code == 409
    assert calls["status"] == 1


def test_an_answer_keeps_its_length(monkeypatch):
    """The body is relayed byte for byte, so the app's length is the true one.
    Dropped, a video range reached the browser chunked, of unknown size."""
    tc, _ = _setup(monkeypatch)
    with tc:
        r = tc.get("/base-apps/run/x/proxy/api/media/1/file")
    assert r.headers["content-length"] == "2"
    assert "transfer-encoding" not in r.headers
