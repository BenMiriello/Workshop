"""The body of `git-credential-workshop`. See that script for why it exists.

git calls a credential helper with one argument (`get`, `store` or `erase`) and
a description of the request on stdin as `key=value` lines, terminated by a
blank line. A helper answers `get` by printing the fields it can supply and
exits; printing nothing means "I do not know this one", which is the correct
answer for any host that is not github.com and for an installation with no
account connected.

THREE RULES THIS FILE OBEYS, EACH FOR A MEASURED REASON:

  Exit 0 whatever happens. A helper that exits non-zero aborts the whole git
  operation, so a missing credential would turn "you are not signed in" into a
  crash in an unrelated push.

  Never write to stdout except the credential fields. git parses stdout; a
  stray print is a protocol error.

  Never write the token anywhere else. Not to stderr, not to a log, not into an
  error message. The only place it goes is the `password=` line git asked for.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOSTS = ("github.com", "gist.github.com")


def _read_request() -> dict[str, str]:
    """git's key=value block from stdin, up to the blank line or EOF."""
    out: dict[str, str] = {}
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line:
            break
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v
    return out


def _token() -> str | None:
    """The connected account's token for the single operator, or None.

    Imported lazily and inside a broad guard: this runs on git's timing, in
    whatever working directory git happened to be in, and no failure here may
    become a failure of the push it was asked about. `pin_operator` returns None
    on an installation that has not bootstrapped, which reads as "no account".
    """
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("WORKSHOP_RUNTIME", str(ROOT / ".runtime"))
    try:
        from services import github_identity, ownership

        if ownership.pin_operator() is None:
            return None
        return github_identity.token()
    except Exception:
        return None


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action != "get":
        # `store` and `erase` are accepted and ignored. This helper's store is
        # the operator's connected account, which only Settings may change.
        return 0
    req = _read_request()
    if req.get("protocol") not in (None, "https"):
        return 0
    if req.get("host") not in HOSTS:
        return 0
    tok = _token()
    if not tok:
        return 0
    # `x-access-token` is the username GitHub expects for a token used over
    # HTTPS basic auth. The token is the password and appears nowhere else.
    sys.stdout.write("username=x-access-token\n")
    sys.stdout.write(f"password={tok}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # See the module docstring: a helper that fails loudly fails the push.
        raise SystemExit(0)
