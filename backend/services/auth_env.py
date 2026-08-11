"""The one place that knows where this install's secret env file is, and how to read it.

WHAT WENT WRONG WITHOUT IT. One file was reached by two different env var names
(`WORKSHOP_AUTH_ENV` in six shell scripts and one tool module, `CLAUDE_AUTH_ENV`
in llm_router), under two different default directories (`~/.config/workshop`
for most of them, `~/.config/clawdbot` hardcoded in the two commute modules and
in restart_backend.sh's fallback), parsed by four separate hand-rolled loops
that each stripped quotes slightly differently. It worked only because a symlink
happened to join the two directories. Anything that moved the file, or set one
of the two variable names, would have half the system reading the new location
and half still reading the old one, with no error from either side: a missing
key here reads as "not configured", which every consumer treats as a no-op.

That is the same failure shape as the credentials that could not fail visibly,
one level down, so it is fixed the same way: one name, one path, one parser.

  WORKSHOP_AUTH_ENV   the file. Default: <config dir>/auth.env.

`CLAUDE_AUTH_ENV` is still honoured, because an install may have it set in a
systemd unit that this commit cannot reach. It is a deprecated alias and logs a
warning once so the reason a second name exists is discoverable rather than
folklore.

READ AT CALL TIME, NEVER CACHED. This is load-bearing and was learned the hard
way in commute/poller: the backend's auto-restart does not always inject the
environment, and an import-time constant that missed the key stayed empty for
the life of the process. Every poll then returned `needs_key` and realtime went
dark for the whole commute, from a file that was sitting on disk the entire
time. The file is a few hundred bytes; reading it per call costs nothing worth
having.

VALUES ARE NEVER LOGGED. The parser handles the shape these files actually have,
including `export KEY=value` and quoted values, and it returns strings. Nothing
here prints one, and nothing here should ever be made to.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from services import paths

log = logging.getLogger("workshop.auth_env")

ENV_VAR = "WORKSHOP_AUTH_ENV"
# Kept working, not kept as a peer. See the module docstring.
LEGACY_ENV_VAR = "CLAUDE_AUTH_ENV"

_warned_legacy = False


def auth_env_path() -> Path:
    """The secret env file for this installation."""
    global _warned_legacy
    raw = os.environ.get(ENV_VAR)
    if not raw:
        legacy = os.environ.get(LEGACY_ENV_VAR)
        if legacy:
            if not _warned_legacy:
                _warned_legacy = True
                log.warning("%s is deprecated; set %s instead (same file)",
                            LEGACY_ENV_VAR, ENV_VAR)
            raw = legacy
    if raw:
        return Path(raw).expanduser()
    return paths.config("auth.env")


def parse(text: str) -> dict[str, str]:
    """The file's contents as key/value pairs. Pure, total, never raises."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # `export KEY=value` is valid in a file meant to be sourced by a shell,
        # and three of the six readers of this file are shell scripts.
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip ONE matched pair of surrounding quotes. The old loops each did
        # some subset of this, which is how the same file could yield a value
        # with a trailing quote in one consumer and without it in another.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_path(path: str | Path) -> dict[str, str]:
    """Every key in the file at `path`. Empty dict when it is not readable.

    Takes an explicit path so a module that already exposes its own file
    location (and whose tests point that at a fixture) keeps one parser without
    losing the seam it was tested through.
    """
    try:
        return parse(Path(path).read_text())
    except OSError:
        return {}


def load() -> dict[str, str]:
    """Every key in this install's auth env file.

    A missing file is normal, not an error: a CLI that is already logged in with
    its own credentials needs nothing here.
    """
    return load_path(auth_env_path())


def value(key: str, default: str = "", path: str | Path | None = None) -> str:
    """One secret: the process environment first, then the file.

    The environment wins because that is how a service is deliberately launched
    with a value; the file is the fallback so the same value keeps working when
    the launcher did not inject it.
    """
    env = os.environ.get(key)
    if env:
        return env
    values = load_path(path) if path is not None else load()
    return values.get(key, default)


def apply_to(env: dict[str, str]) -> dict[str, str]:
    """Overlay the file's keys onto a copied environment, in place.

    The FILE WINS here, which is the opposite of `value()` and is deliberate.
    This is used to build the environment for a spawned child, where the risk is
    an empty or stale value already sitting in the parent's environment (uvicorn
    launched without the secret) silently shadowing the real one on disk. For a
    single read, an explicitly-set variable is the more specific answer; for
    handing a child its credentials, the file is the source of truth.
    """
    env.update(load())
    return env
