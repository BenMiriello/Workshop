"""The latitude CLI is a shell entrypoint, so it must pin a data owner itself.

Measured in the live ralph log on 2026-08-22: every unattended run emitted a
full OwnerUnset traceback from `python -m services.latitude`, and then logged
`latitude=standard (source=default ...)` as though the cascade had run. ralph.sh
swallows the failure with `|| true` and its JSON parse falls back to `default`,
which is the SAME answer the cascade gives when nothing is configured. So a
resolver crash was indistinguishable from a clean resolution.

No ticket or project sets `latitude` today, so the wrong answer happened to
match the right one. The mechanism was dead regardless: `strict` is the only
value that makes reviewer.sh block instead of guess, and a strict ticket would
have been silently downgraded to standard.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RALPH = BACKEND.parent / "scripts" / "ralph.sh"


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "services.latitude", *args],
        cwd=BACKEND, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND),
             "HOME": str(Path.home())},
    )


def test_an_owner_is_pinned_BEFORE_the_store_is_read(monkeypatch):
    """The regression, asserted deterministically.

    Doing this as a subprocess would not discriminate: the crash only happens
    where the scope resolves as RELATIONAL, which needs the deployment config
    the backend loads and a confined test run does not have. So the assertion
    is on the ordering that actually matters -- an owner is pinned by the time
    the store is touched -- rather than on whether this particular environment
    happens to reach Postgres."""
    from services import latitude, ownership

    seen: list[object] = []

    def _spy(root, task_id):
        try:
            seen.append(ownership.current_owner())
        except ownership.OwnerUnset:
            seen.append(None)
        return None

    import services.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "get_task", _spy)
    ownership.clear_current_owner()

    latitude.main(["resolve", "--scope", "development",
                   "--store-dir", str(BACKEND.parent), "--task-id", "x"])

    assert seen, "the store was never read; the test is not exercising the path"
    assert seen[0] is not None, (
        "no data owner was pinned before the task store was read: this is the "
        "OwnerUnset seen in the live ralph log on 2026-08-22")


def test_main_pins_the_operator_through_the_one_set_point():
    """Pinned via ownership.pin_operator, not a fifth private copy. Its own
    docstring names the four earlier entrypoints that each rewrote this."""
    src = (BACKEND / "services" / "latitude.py").read_text()
    assert "ownership.pin_operator()" in src
    assert "def pin_operator" not in src, "must not grow a private copy"


def _shell_latitude_source(payload: str) -> str:
    """Run ralph.sh's own LATITUDE_SOURCE derivation over `payload`."""
    src = RALPH.read_text()
    start = src.index("LATITUDE_SOURCE=$(printf")
    end = src.index("\nexport LATITUDE", start)
    prog = f"LATITUDE_JSON={payload!r}\n{src[start:end]}\nprintf '%s' \"$LATITUDE_SOURCE\"\n"
    out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_resolved_lookup_still_reports_its_real_source():
    assert _shell_latitude_source('{"latitude":"open","source":"task"}') == "task"
    assert _shell_latitude_source('{"latitude":"standard","source":null}') == "default"


def test_a_FAILED_lookup_does_not_report_itself_as_a_default():
    """The lie being removed: an empty or unparseable payload means the resolver
    did not run, and must not be reported as the cascade finding nothing."""
    for broken in ("", "Traceback (most recent call last):", "not json"):
        got = _shell_latitude_source(broken)
        assert got.startswith("unresolved"), f"{broken!r} -> {got!r}"
        assert got != "default"
