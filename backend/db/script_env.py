"""Load the config env files that the systemd units load, for scripts run by hand.

WHY THIS EXISTS. `WORKSHOP_DATABASE_URL` and `WORKSHOP_DATABASE_ADMIN_URL` live
in `~/.config/workshop/secrets.env` and `db-admin.env`, and the backend unit
supplies them through `EnvironmentFile=`. Nothing supplies them to an
interactive shell, so every admin script here died on the first database call
with "WORKSHOP_DATABASE_URL is not set" when a human ran it, while working fine
from any process the service had already parented. That difference is invisible
from the outside and cost the operator a confusing traceback on 2026-08-15.

A provisioning script should be runnable as written. These files are the
declared home of those values (db/engine.py's own error message says so), so
reading them here is not a new source of truth, it is the same one the unit
uses.

NEVER OVERRIDES. A value already in the environment wins, so an explicit
`WORKSHOP_DATABASE_URL=... python db/whatever.py` still points where the caller
said, and a test harness pointing at a scratch database cannot be silently
redirected at the real one.

PRECEDENCE MATCHES THE UNIT, DELIBERATELY. `infra/systemd/workshop-backend.service`
loads `EnvironmentFile=-%h/.config/workshop/secrets.env` then
`EnvironmentFile=-%h/.config/workshop/instance.env`, and systemd's own semantics
for a key repeated across `EnvironmentFile=` lines is LAST WINS -- so the
running backend resolves a conflicting key to whatever instance.env says. The
loop below is the opposite shape: the FIRST file to set a key wins, so that an
already-exported value (see NEVER OVERRIDES above) is never revisited. To land
on the same answer as the unit, this module has to visit instance.env BEFORE
secrets.env. `db-admin.env` is not part of the unit at all -- the backend
process is deliberately never handed the OWNER database credential (see
`db/bootstrap.py`'s module docstring) -- so it is appended last and, in
practice, only ever supplies `WORKSHOP_DATABASE_ADMIN_URL`, a key neither of the
other two files declares.

`backend/tests/test_script_env_config_precedence.py` pins this against the
unit file itself rather than restating the expected order here, so the two
cannot drift apart silently again.
"""
from __future__ import annotations

import os
from pathlib import Path

# Order matters -- see "PRECEDENCE MATCHES THE UNIT" above. instance.env is
# visited first so it wins ties with secrets.env, matching systemd's
# last-EnvironmentFile-wins semantics for the two files the unit actually
# loads. db-admin.env is additional: the unit never loads it, and it is
# expected not to collide with the other two.
CONFIG_FILES = ("instance.env", "secrets.env", "db-admin.env")


def config_dir() -> Path:
    return Path(os.environ.get("WORKSHOP_CONFIG_DIR")
                or (Path.home() / ".config" / "workshop"))


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=VALUE lines, `export ` prefix and surrounding quotes tolerated.

    Deliberately not a dotenv library: these files are written by
    scripts/install_service.sh in exactly this shape, and systemd's own
    EnvironmentFile parser is no cleverer than this.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_config_env() -> list[str]:
    """Fill in any config values this process is missing. Returns the names set."""
    added: list[str] = []
    for name in CONFIG_FILES:
        path = config_dir() / name
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for key, value in parse_env_file(text).items():
            if key not in os.environ:
                os.environ[key] = value
                added.append(key)
    return added
