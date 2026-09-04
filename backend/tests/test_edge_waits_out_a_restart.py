"""The edge waits out a brief backend outage instead of 502ing into it.

THE FAILURE THIS LOCKS DOWN, measured 2026-09-04. `apply.sh` arms a deferred
backend restart that fires after the current reply lands. That is "a pause, not
a break" for an in-flight turn, which is adopted on the way back up. It was NOT
a pause for a request that ARRIVED during the window: the edge retried exactly
once, immediately, so the retry landed inside the same few-second outage and
failed identically. The operator pressed compact at that moment and got
"Server unavailable (502)" from a backend that was healthy by the time he read
the message. Second user-visible 502 from a self-inflicted restart in two days.

WHY RETRYING A WRITE IS SAFE HERE, and it is the point of the last test:
`_RETRYABLE` is exactly the failures that prove the backend never saw the
request. A ReadError or ReadTimeout is excluded, because those can mean the
backend read it and was working on it. Widening that set would turn this from a
recovery into a double-write, so it is asserted rather than trusted.
"""
from __future__ import annotations

import httpx
import pytest

import workshop_public_app as edge


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Req:
    """The bits of a starlette Request that the edge reads. Never dialled."""

    def __init__(self, method="POST"):
        self.method = method
        self.headers = {}
        self.url = httpx.URL("http://127.0.0.1:8000/api/chat/threads/t/compact")


def _install(monkeypatch, outcomes):
    """Drive the transport with a scripted list of exceptions/responses."""
    calls = {"n": 0}
    monkeypatch.setattr(edge, "_build_upstream_request", lambda *a, **k: object())

    async def handle(_req):
        i = calls["n"]
        calls["n"] += 1
        out = outcomes[min(i, len(outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(edge._transport, "handle_async_request", handle)
    return calls


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """The waits are real seconds in production and must not be here."""

    async def _sleep(_s):
        return None

    monkeypatch.setattr(edge.asyncio, "sleep", _sleep)


@pytest.mark.anyio
async def test_a_request_arriving_during_a_restart_succeeds(monkeypatch):
    """Three connection failures then a healthy backend: the caller sees the
    response, not a 502. This is the compact request that failed."""
    ok = httpx.Response(200)
    calls = _install(monkeypatch, [
        httpx.ConnectError("backend down"),
        httpx.ConnectError("backend down"),
        httpx.RemoteProtocolError("closed before response"),
        ok,
    ])
    got = await edge._send_upstream(_Req(), "/api/chat/threads/t/compact", {}, b"", stream=False)
    assert got is ok
    assert calls["n"] == 4, "did not keep trying across the outage"


def test_one_immediate_retry_would_not_have_been_enough():
    """The regression guard, stated as the OLD behaviour: two attempts inside a
    multi-second outage both fail. The fix is that there are more than two, and
    that they add up to longer than a restart."""
    assert len(edge._RETRY_WAITS) >= 3, (
        "an outage lasting seconds needs more than one retry to survive; "
        "this is the exact shape that produced a 502 on 2026-09-04")
    assert sum(edge._RETRY_WAITS) >= 5, (
        "total patience is under 5s, which is shorter than a backend restart")


@pytest.mark.anyio
async def test_a_backend_that_is_really_down_still_fails(monkeypatch):
    """Bounded on purpose. A genuine outage must surface as the honest error,
    not as an unexplained hang."""
    _install(monkeypatch, [httpx.ConnectError("really down")])
    with pytest.raises(httpx.ConnectError):
        await edge._send_upstream(_Req(), "/api/x", {}, b"", stream=False)


@pytest.mark.anyio
async def test_a_read_failure_is_never_retried(monkeypatch):
    """THE SAFETY PROPERTY. A ReadError can mean the backend read the request
    and was working on it, so re-sending a POST would be a double-write. It must
    propagate on the first attempt."""
    calls = _install(monkeypatch, [httpx.ReadError("mid-flight")])
    with pytest.raises(httpx.ReadError):
        await edge._send_upstream(_Req(), "/api/x", {}, b"", stream=False)
    assert calls["n"] == 1, "a read failure was retried; that is a double-write"


def test_the_retryable_set_still_excludes_read_failures():
    """Asserted on the constant, not only through behaviour: widening this set
    is what would silently turn recovery into a double-write."""
    assert httpx.ReadError not in edge._RETRYABLE
    assert httpx.ReadTimeout not in edge._RETRYABLE
    assert httpx.ConnectError in edge._RETRYABLE
    assert httpx.RemoteProtocolError in edge._RETRYABLE
