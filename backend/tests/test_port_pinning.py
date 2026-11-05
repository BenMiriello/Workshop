"""A service that declares a port keeps it, across restarts and across neighbours.

One app's api drifted from its pinned port through three others inside
nineteen minutes and the app showed "Could not reach the server" through every move. The mechanism was not
a missing preference - `restart_service` already passed `preferred=` the old port.
It was that `preferred` is only a hint: `_stop_service` releases the allocation
while the dying process still holds the socket, so the free-on-OS check on the
preferred port fails and the linear scan hands out the next one instead.

So the fix is a PIN, and these are the three properties a pin has to have. Each
one of them was violated by the old behaviour.
"""
from __future__ import annotations

import importlib
import os
import socket
import tempfile
import threading
import time

import pytest


@pytest.fixture()
def registry(monkeypatch):
    """A port registry with its own empty store, so tests never see real claims."""
    monkeypatch.setenv("WORKSHOP_PORT_REGISTRY", tempfile.mktemp(suffix=".json"))
    from services import port_registry
    return importlib.reload(port_registry)


def _bind(port: int) -> socket.socket:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def _free_port(registry) -> int:
    """A port inside the app window that nothing currently holds."""
    lo, hi = registry.WINDOWS["app"]
    for p in range(lo, hi + 1):
        if p not in registry.RESERVED and registry.port_free_on_os(p):
            return p
    raise RuntimeError("no free port to test with")


def test_a_pinned_port_is_not_handed_to_a_dynamic_service(registry):
    """The restart window is exactly when someone else is allocating.

    A pinned service is DOWN while it restarts, so its port looks free. Without
    this the neighbour takes it and the pinned service then cannot come home.
    """
    pinned = _free_port(registry)
    registry.reserve_pinned(pinned)
    got = registry.allocate("neighbour/app", "app")
    assert got != pinned


def test_a_pin_waits_for_its_own_port_rather_than_moving(registry):
    """The actual regression: the old process still holds the socket.

    `--timeout-graceful-shutdown` means the port is busy for a moment after the
    restart begins. Answering "busy, take another" is what moved the address.
    """
    pinned = _free_port(registry)
    held = _bind(pinned)
    threading.Timer(1.0, held.close).start()

    started = time.monotonic()
    got = registry.allocate("widgets/api", "app", preferred=pinned,
                            pinned=True, pinned_wait_s=15)
    waited = time.monotonic() - started

    assert got == pinned
    assert waited >= 0.5, "it should have waited for the port, not raced it"


def test_a_pin_that_cannot_be_honoured_raises_instead_of_drifting(registry):
    """Silence is the failure mode being removed.

    Moving the service is worse than not starting it: the proxy, the scripts and
    the operator all still point at the declared port, and the only symptom is
    "could not reach the server" somewhere else entirely.
    """
    pinned = _free_port(registry)
    held = _bind(pinned)
    try:
        with pytest.raises(RuntimeError, match=str(pinned)):
            registry.allocate("widgets/api", "app", preferred=pinned,
                              pinned=True, pinned_wait_s=1)
    finally:
        held.close()


def test_the_manifest_port_is_parsed_and_registered_as_a_pin(registry):
    """The declaration has to reach the registry, or none of the above applies."""
    from services import app_runner
    spec = app_runner._spec_from_raw(
        {"name": "api", "start": "run me", "port": 8700}, 0)
    assert spec.port == 8700
    from services import port_registry
    assert 8700 in port_registry._PINNED


def test_a_service_without_a_declared_port_still_floats(registry):
    """Pinning is opt-in. Most services have no fixed address and want none."""
    from services import app_runner
    spec = app_runner._spec_from_raw({"name": "worker", "start": "run me"}, 0)
    assert spec.port is None
