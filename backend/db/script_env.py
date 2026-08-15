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
"""
from __future__ import annotations

import os
from pathlib import Path

# Both files are optional: a checkout with no database configured should fail
# on the missing variable with engine.py's pointed message, not on a missing
# file here.
_FILES = ("secrets.env", "db-admin.env")


def _config_dir() -> Path:
    return Path(os.environ.get("WORKSHOP_CONFIG_DIR")
                or (Path.home() / ".config" / "workshop"))


def _parse(text: str) -> dict[str, str]:
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
    for name in _FILES:
        path = _config_dir() / name
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for key, value in _parse(text).items():
            if key not in os.environ:
                os.environ[key] = value
                added.append(key)
    return added
