"""A cached "the database is down" must expire. It did not, and it cost a day.

THE FAILURE THIS PINS ACTUALLY HAPPENED, on 2026-09-16.

`tenancy.available()` cached its first answer in a module global for the whole
process lifetime. The backend restarted, its very first probe failed, and the
database was fine seconds later and stayed fine. Nothing re-probed, because
there was no code path that could: every relational read in the process --
apps, chat branches, tasks, leases, changes, worktree records, ticket sync --
answered "no database today" for eight hours, and was fixed by a restart that
changed not one line.

The operator saw it as his apps disappearing, which is the second half of this
file: `app_registry.launcher_cards()` caught the resulting exception and
returned the apps it could still read from the file-backed catalog, so a short
list arrived with no error on it. An OUTAGE presented as DELETED DATA.

These tests need no database, on purpose. The bug is in the caching, not in the
connecting, so it must be reproducible on a checkout that has no Postgres at
all -- including the ones where the original incident is unreproducible.
"""
from __future__ import annotations

import pytest

from services import app_registry, tenancy


class FakeClock:
    """A monotonic clock the test advances by hand.

    Real elapsed time cannot be asserted on: the retry window starts at a
    second and doubles, so a test that waited would either sleep for a minute
    or pin a race. This stands in for the `time` module `tenancy` reads.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def probe(monkeypatch):
    """A counting, scriptable stand-in for the database probe.

    `available()` probes by opening a session from `db.engine` and running
    `SELECT 1`, imported inside the function, so this replaces it at the source
    module. Returns the counter object; set `.fails` to steer the next answer.
    """
    import db.engine as engine

    class Probe:
        def __init__(self) -> None:
            self.calls = 0
            self.fails = True

        def __call__(self):
            self.calls += 1
            if self.fails:
                raise RuntimeError("connection refused (test)")

            class Session:
                def execute(self, *a, **kw):
                    return None

                def close(self):
                    return None

            return Session()

    p = Probe()
    monkeypatch.setattr(engine, "SessionLocal", p)
    monkeypatch.setenv("WORKSHOP_DATABASE_URL", "postgresql://test/does-not-matter")
    return p


@pytest.fixture
def clock(monkeypatch):
    """Injected with `raising=False` so these tests FAIL rather than ERROR
    against a `tenancy` that has no clock in it.

    That is not defensiveness, it is the point of the file. The version of
    `available()` these tests were written against did not import `time` at
    all, because it never expired anything; patching strictly made every test
    here error on a missing attribute, which proves the attribute is missing and
    says nothing about the bug. Failing on the assertion is what demonstrates
    it.
    """
    c = FakeClock()
    monkeypatch.setattr(tenancy, "time", c, raising=False)
    return c


def test_a_database_that_comes_back_is_noticed(probe, clock):
    """The whole incident in four lines.

    Probe fails, the database recovers, and a later caller must see it. Before
    the fix this assertion failed and only a process restart could satisfy it.
    """
    assert tenancy.available() is False
    probe.fails = False
    clock.advance(tenancy._RETRY_CEIL_S + 1)
    assert tenancy.available() is True


def test_a_failure_is_cached_briefly_rather_than_probed_every_call(probe, clock):
    """The reason the original cache existed, which the fix has to keep.

    A dead database must not cost a connect attempt per request: a hung one
    fails slowly, and that turns every read in the process into a stall. Inside
    the window the cached answer is served and the probe is not called again.
    """
    assert tenancy.available() is False
    assert probe.calls == 1
    for _ in range(50):
        assert tenancy.available() is False
    assert probe.calls == 1, "a cached failure must be served, not re-probed"


def test_the_retry_window_grows_and_is_capped(probe, clock):
    """Consecutive failures back off, so a box with no database stays cheap.

    Capped, because the window is also the RECOVERY LATENCY: unbounded doubling
    would mean a database that came back after a long outage went unnoticed for
    as long again, which is the original bug with extra steps.
    """
    assert tenancy.available() is False
    for _ in range(20):
        clock.advance(tenancy._RETRY_CEIL_S + 1)
        assert tenancy.available() is False
    assert tenancy._db_retry_after_s == tenancy._RETRY_CEIL_S


def test_no_database_configured_never_probes_at_all(monkeypatch, clock):
    """"No database on this box" is not an outage and must stay free.

    Every checkout that is not the operator's own runs this way, so this answer
    is read off the environment and never opens a connection. Distinguishing it
    from an outage is what lets the outage expire without making the ordinary
    case pay for a retry.
    """
    import db.engine as engine

    def explode():
        raise AssertionError("available() must not probe when none is configured")

    monkeypatch.setattr(engine, "SessionLocal", explode)
    monkeypatch.delenv("WORKSHOP_DATABASE_URL", raising=False)
    for _ in range(5):
        assert tenancy.available() is False


def test_success_is_still_cached(probe, clock):
    """A reachable database is asked once, not before every read.

    The failure expires; the success does not. A database that dies is noticed
    by the query that fails, and re-probing first would double the round trips
    to answer a question that is almost always yes.
    """
    probe.fails = False
    assert tenancy.available() is True
    clock.advance(10_000)
    for _ in range(20):
        assert tenancy.available() is True
    assert probe.calls == 1


def test_a_launcher_missing_apps_says_so(monkeypatch):
    """A short list must not read as a complete one.

    This is what the operator actually saw. The launcher is assembled from two
    sources -- a file-backed catalog and the `app` table -- and only the second
    needs the database, so an outage removes some apps and leaves others. The
    cards that survive are genuinely installed and worth serving; serving them
    as the whole list is the defect.
    """
    from services import apps as apps_service

    def unavailable(*a, **kw):
        raise apps_service.AppsUnavailable("no relational store (test)")

    monkeypatch.setattr(apps_service, "list_apps", unavailable)
    launcher = app_registry.launcher_cards()
    assert launcher.incomplete, "an unreadable app store must be reported"
    assert isinstance(launcher.cards, list)


def test_a_complete_launcher_reports_nothing(monkeypatch):
    """The ordinary answer carries no notice.

    Without this the fix could pass the test above by always claiming
    incompleteness, which would put a permanent outage banner on the shell.
    """
    from services import apps as apps_service

    monkeypatch.setattr(apps_service, "list_apps", lambda *a, **kw: [])
    launcher = app_registry.launcher_cards()
    assert launcher.incomplete is None
