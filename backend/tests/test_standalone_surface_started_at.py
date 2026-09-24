"""`app_runner` records a subprocess app's `started_at` as epoch seconds, so
the project surface model must accept a number as well as a string, or every
project page with a running subprocess app answers 500."""
from routes.projects import StandaloneSurface


def test_epoch_seconds_are_accepted():
    s = StandaloneSurface(runner="app", started_at=1758650000.25)
    assert s.started_at == 1758650000.25


def test_a_string_timestamp_still_is():
    s = StandaloneSurface(runner="app", started_at="2026-09-23T12:00:00+00:00")
    assert s.started_at == "2026-09-23T12:00:00+00:00"
