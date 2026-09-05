"""The signed-in GitHub account for one owner, and the two environments that
spend it.

WHY THIS EXISTS. Until now this installation reached GitHub through whatever the
host user happened to have: the global git credential helper pointed at
`gh auth git-credential`, and `gh` was not logged in. The result, measured
2026-09-04 on a repository with 812 unpushed commits, was

    fatal: could not read Username for 'https://github.com': No such device or address

from an agent session, with NO surface anywhere in the product that could fix
it. A credential an operator has to go and find on disk is not a connected
account; it is a missing feature with a workaround.

THE MODEL. One account per OWNER, stored in that owner's own runtime partition
(`.runtime/users/<id>/secrets/github.json`, 0600, envelope-encrypted whenever a
KEK is configured), never in a shared env file and never in a global one. The
token is written once and never read back out through any status or API path;
only its login, its kind and its scopes are ever surfaced.

THREE CONSUMERS, ONE CREDENTIAL:

  git      `git_env()` hands the token to `git` as an `http.extraHeader`
           through `GIT_CONFIG_*` environment variables. Not in argv, because
           /proc/<pid>/cmdline is world readable; not in the remote URL,
           because that persists into `.git/config` forever.
  gh       `gh_env()` sets `GH_TOKEN` and drops `GITHUB_TOKEN`, which is the
           only thing `services/github.py`'s `gh` calls need to stop depending
           on an ambient login.
  a shell  `install_credential_helper()` points the global git credential
           helper for github.com at `scripts/git-credential-workshop`, so a
           `git push` typed in ANY session on this host authenticates from the
           connected account. This is the half that makes the connection real
           rather than internal, and `remove_credential_helper()` undoes it.

WHY ORG SCOPES ARE REFUSED. Ticket [SEC-G1] records the finding that this
installation's boundary against an excluded organisation lived only in string
matching in code (`services.github.reject_blocked`), never in the credential:
a classic PAT carrying `repo` plus `read:org` can technically read that
organisation and push to its private repositories. The option that ticket names
is a token that cannot, and this module makes that option the only one it will
store: a credential carrying an organisation scope is rejected at the point of
entry, with a message naming what to generate instead. The boundary is then in
the credential as well as in the code, which is what the ticket asked for.

WHY THE DEVICE FLOW NEEDS A CLIENT ID FIRST. GitHub's device authorization
grant needs no client secret and no redirect URL, which is exactly right for a
self-hosted box. It does need a registered application's client id, and
registering one is an action on github.com that cannot be performed from here.
So there are two ways in and both are first class: paste a fine-grained token,
or register an app once, save its client id, and sign in from then on. The
device flow issues a user-to-server token whose reach is the app's own
installations, which is narrower than any OAuth scope set and satisfies
[SEC-G1] by construction.

NO TOKEN VALUE IS EVER RETURNED, LOGGED OR PUT IN AN ERROR. Not by `status()`,
not by a failure path, not by a test fixture.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

# Read from the environment as a fallback so an installation that already knows
# its registered application does not have to re-enter it. The value is a public
# client id, not a secret; it is stored beside the token only because it is a
# fact about this installation rather than about the product.
CLIENT_ID_ENV = "WORKSHOP_GITHUB_CLIENT_ID"

# The store's keys. `token` is write-only from the outside.
_TOKEN = "token"
_SOURCE = "source"
_LOGIN = "login"
_SCOPES = "scopes"
_CONNECTED_AT = "connected_at"
_CLIENT_ID = "client_id"

SOURCES = ("pat", "device")

# Scopes that reach an organisation. See the module docstring: [SEC-G1] is the
# decision to keep the excluded-organisation boundary in the credential, and a
# token carrying any of these cannot honour it whatever the code around it does.
ORG_SCOPES = ("read:org", "write:org", "admin:org")

# What the device flow asks for. Deliberately no organisation scope, and no
# `repo` either: a GitHub App's user-to-server token is scoped by the app's
# installations, so the useful reach comes from where the operator installs the
# app rather than from a scope string.
DEVICE_SCOPE = ""

_HELPER_NAME = "git-credential-workshop"
_HELPER_HOSTS = ("https://github.com", "https://gist.github.com")


class GithubAuthError(RuntimeError):
    """A credential could not be accepted, with a sentence saying why.

    Its message reaches the operator's screen, so it names the fix. It never
    contains any part of a token.
    """


# ---- where the credential lives ---------------------------------------------

def _owner_id(owner_id: int | None = None) -> int:
    from services import ownership

    return int(owner_id) if owner_id is not None else ownership.current_owner()


def _slug(owner_id: int) -> str:
    """The AAD bound into the encrypted envelope. Not the file name: it is what
    stops one owner's file being renamed over another's."""
    return f"github:{owner_id}"


def _path(owner_id: int) -> Path:
    from services import ownership

    return ownership.user_runtime_dir(owner_id) / "secrets" / "github.json"


def _load(owner_id: int) -> dict[str, str]:
    from services import secrets_store

    return secrets_store.load_at(_path(owner_id), _slug(owner_id))


def _save(owner_id: int, values: dict[str, str]) -> None:
    from services import secrets_store

    secrets_store.set_values_at(_path(owner_id), _slug(owner_id), values)


# ---- reading the credential --------------------------------------------------

def token(owner_id: int | None = None) -> str | None:
    """The bearer token, or None. Runner-only: never return this over HTTP."""
    return (_load(_owner_id(owner_id)) or {}).get(_TOKEN) or None


def client_id(owner_id: int | None = None) -> str | None:
    """The registered application's client id, stored or from the environment."""
    stored = (_load(_owner_id(owner_id)) or {}).get(_CLIENT_ID) or ""
    return stored.strip() or (os.environ.get(CLIENT_ID_ENV) or "").strip() or None


def set_client_id(value: str, owner_id: int | None = None) -> None:
    """Record the registered application's client id. Blank clears it."""
    _save(_owner_id(owner_id), {_CLIENT_ID: (value or "").strip()})


def connected(owner_id: int | None = None) -> bool:
    return bool(token(owner_id))


# ---- spending the credential -------------------------------------------------

def _basic(tok: str) -> str:
    import base64

    raw = f"x-access-token:{tok}".encode()
    return base64.b64encode(raw).decode()


def git_env(owner_id: int | None = None, base: dict[str, str] | None = None) -> dict[str, str]:
    """`base` (default the ambient environment) plus whatever `git` needs to
    authenticate to github.com as the connected account.

    The token travels as an `http.extraHeader` supplied through `GIT_CONFIG_*`,
    which keeps it out of argv (`/proc/<pid>/cmdline` is world readable) and out
    of `.git/config` (a token in a remote URL persists there forever, which is
    the documented `actions/checkout` leak). Unconnected, this returns `base`
    unchanged rather than a half-configured environment.

    `GIT_TERMINAL_PROMPT=0` is set whenever a token is present so a stale
    credential fails fast with git's own message instead of blocking a
    non-interactive subprocess on a username prompt, which is the exact shape of
    the failure this module exists to end.
    """
    env = dict(os.environ if base is None else base)
    tok = token(owner_id)
    if not tok:
        return env
    # Layer onto any GIT_CONFIG_COUNT the caller already set rather than
    # overwriting it: git reads every index below the count, so clobbering one
    # would silently drop the caller's own configuration.
    try:
        start = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        start = 0
    env["GIT_CONFIG_COUNT"] = str(start + 1)
    env[f"GIT_CONFIG_KEY_{start}"] = "http.https://github.com/.extraHeader"
    env[f"GIT_CONFIG_VALUE_{start}"] = f"Authorization: Basic {_basic(tok)}"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def gh_env(owner_id: int | None = None, base: dict[str, str] | None = None) -> dict[str, str]:
    """`base` (default the ambient environment) with `GH_TOKEN` set to the
    connected account's token, so a `gh` call stops depending on whether the host
    user happens to have run `gh auth login`.

    `GITHUB_TOKEN` is removed rather than left: `gh` prefers it, so leaving a
    stale one in place would silently spend a different credential.
    """
    env = dict(os.environ if base is None else base)
    tok = token(owner_id)
    if not tok:
        return env
    env["GH_TOKEN"] = tok
    env.pop("GITHUB_TOKEN", None)
    return env


# ---- checking a credential ---------------------------------------------------

def _scopes_from(headers: Any) -> list[str]:
    raw = ""
    try:
        raw = headers.get("x-oauth-scopes") or ""
    except AttributeError:
        raw = ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def offending_scopes(scopes: list[str]) -> list[str]:
    """Which of `scopes` reach an organisation. See ORG_SCOPES."""
    lowered = {s.lower() for s in scopes}
    return [s for s in ORG_SCOPES if s in lowered]


def verify(tok: str) -> dict:
    """Does this token work, whose is it, and what can it reach?

    Returns `{ok, login, scopes, detail}` and never raises, so a typo is a
    sentence on the form rather than a 500. Checked BEFORE the token is stored:
    a credential is not a setting whose effect you discover at the next push.

    One request, `GET /user`, which needs no scope at all. Nothing here asks
    about organisations, and nothing here can: that is the veritus separation,
    and probing it would itself cross the boundary the check is defending.
    """
    tok = (tok or "").strip()
    if not tok:
        return {"ok": False, "login": "", "scopes": [], "detail": "No token was given."}
    try:
        r = httpx.get(
            f"{API_BASE}/user",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        return {"ok": False, "login": "", "scopes": [],
                "detail": f"GitHub could not be reached: {type(e).__name__}."}
    if r.status_code == 401:
        return {"ok": False, "login": "", "scopes": [],
                "detail": "GitHub rejected this token. It is wrong, revoked or expired."}
    if r.status_code != 200:
        return {"ok": False, "login": "", "scopes": [],
                "detail": f"GitHub answered {r.status_code} to a read of the signed-in user."}
    try:
        login = str((r.json() or {}).get("login") or "")
    except ValueError:
        login = ""
    return {"ok": True, "login": login, "scopes": _scopes_from(r.headers),
            "detail": f"Signed in as {login}." if login else "Signed in."}


def _reject_unusable(login: str, scopes: list[str]) -> None:
    """Refuse a credential this installation must not hold. Raises with the fix.

    Two refusals, and they are different in kind. A login that names a blocked
    account is a hard installation rule (`services.github.reject_blocked`). An
    organisation scope is [SEC-G1]: the token could reach an organisation
    whatever the code around it does, so the boundary would still be string
    matching rather than capability.
    """
    from services import github

    if login:
        github.reject_blocked(login)
    bad = offending_scopes(scopes)
    if bad:
        raise GithubAuthError(
            f"This token carries {', '.join(bad)}, which reaches organizations you "
            "belong to. Generate a fine-grained token instead, with Resource owner "
            "set to your personal account, Repository permissions Contents "
            "read and write, Metadata read, Pull requests read and write."
        )


def connect_token(tok: str, owner_id: int | None = None, *, source: str = "pat") -> dict:
    """Verify a token, refuse it if it is too broad, and store it. Returns status.

    Raises `GithubAuthError` with a sentence for the screen. The token itself is
    never part of that sentence.
    """
    if source not in SOURCES:
        raise GithubAuthError(f"unknown credential source: {source}")
    check = verify(tok)
    if not check["ok"]:
        raise GithubAuthError(check["detail"])
    _reject_unusable(check["login"], check["scopes"])

    from datetime import datetime, timezone

    oid = _owner_id(owner_id)
    _save(oid, {
        _TOKEN: tok.strip(),
        _SOURCE: source,
        _LOGIN: check["login"],
        _SCOPES: ",".join(check["scopes"]),
        _CONNECTED_AT: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return status(oid)


def disconnect(owner_id: int | None = None) -> dict:
    """Forget the token and stand the credential helper down. The client id is
    kept: it is not a credential, and re-registering an application to sign in
    again would be pointless work."""
    oid = _owner_id(owner_id)
    _save(oid, {_TOKEN: "", _SOURCE: "", _LOGIN: "", _SCOPES: "", _CONNECTED_AT: ""})
    remove_credential_helper()
    return status(oid)


# ---- the git credential helper ----------------------------------------------

def helper_path() -> Path:
    """The helper script, resolved from this checkout rather than from a home
    directory, so a relocated instance points at its own copy."""
    from wsplatform import paths

    return paths.scripts(_HELPER_NAME)


def _helper_value() -> str:
    return f"!{helper_path()}"


def _git_config(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "config", "--global", *args],
                          capture_output=True, text=True, timeout=15, check=False)


def helper_installed() -> bool:
    """Is github.com's credential helper this installation's own?"""
    want = _helper_value()
    for host in _HELPER_HOSTS:
        r = _git_config(["--get-all", f"credential.{host}.helper"])
        if want not in (r.stdout or ""):
            return False
    return True


def install_credential_helper() -> None:
    """Point github.com's global git credential helper at this installation.

    An empty value FIRST, then ours: that is git's documented way of discarding
    helpers configured earlier in the chain, and it is what stops a previously
    configured helper (here, a `gh auth git-credential` left behind by a `gh`
    that is no longer logged in) from answering first and failing the push.
    """
    for host in _HELPER_HOSTS:
        key = f"credential.{host}.helper"
        _git_config(["--unset-all", key])
        _git_config(["--add", key, ""])
        _git_config(["--add", key, _helper_value()])


def remove_credential_helper() -> None:
    """Undo `install_credential_helper`, leaving nothing of ours behind.

    Both entries go, including the empty resetting one, because leaving that
    behind would silently disable whatever helper the host had configured for
    itself before this installation was ever connected.
    """
    for host in _HELPER_HOSTS:
        key = f"credential.{host}.helper"
        r = _git_config(["--get-all", key])
        values = [v for v in (r.stdout or "").splitlines()]
        if _helper_value() not in values:
            continue
        _git_config(["--unset-all", key])
        for v in values:
            if v == _helper_value() or v == "":
                continue
            _git_config(["--add", key, v])


# ---- the device authorization grant ------------------------------------------

def _device_post(url: str, data: dict[str, str]) -> dict:
    try:
        r = httpx.post(url, data=data, headers={"Accept": "application/json"},
                       timeout=20.0)
    except httpx.HTTPError as e:
        raise GithubAuthError(f"GitHub could not be reached: {type(e).__name__}.")
    try:
        body = r.json()
    except ValueError:
        raise GithubAuthError(f"GitHub answered {r.status_code} in a shape this cannot read.")
    if not isinstance(body, dict):
        raise GithubAuthError("GitHub answered in a shape this cannot read.")
    return body


def start_device(owner_id: int | None = None) -> dict:
    """Ask GitHub for a device code. Returns what the screen has to show:
    `{user_code, verification_uri, device_code, interval, expires_in}`.

    `device_code` is the polling ticket, not a credential for anything, and it
    expires in fifteen minutes. It goes to the client because the client is what
    polls; nothing is stored until a token comes back.
    """
    cid = client_id(owner_id)
    if not cid:
        raise GithubAuthError(
            "No GitHub application is registered for this installation yet. "
            "Create one at github.com under Settings, Developer settings, then "
            "save its Client ID here. Device flow must be enabled on it."
        )
    payload = {"client_id": cid}
    if DEVICE_SCOPE:
        payload["scope"] = DEVICE_SCOPE
    body = _device_post(DEVICE_CODE_URL, payload)
    if body.get("error"):
        raise GithubAuthError(_device_error(str(body.get("error"))))
    return {
        "user_code": str(body.get("user_code") or ""),
        "verification_uri": str(body.get("verification_uri") or "https://github.com/login/device"),
        "device_code": str(body.get("device_code") or ""),
        "interval": int(body.get("interval") or 5),
        "expires_in": int(body.get("expires_in") or 900),
    }


# GitHub's device-flow error codes, in the words the operator needs. The first
# two are not failures at all; they are the flow working.
_DEVICE_ERRORS = {
    "authorization_pending": "Waiting for you to enter the code on github.com.",
    "slow_down": "Waiting for you to enter the code on github.com.",
    "expired_token": "The code expired. Start again to get a new one.",
    "access_denied": "The sign-in was cancelled on github.com.",
    "incorrect_client_credentials": "That Client ID is not one GitHub recognizes.",
    "device_flow_disabled": ("Device flow is turned off on that application. "
                             "Enable it in the application's settings on github.com."),
    "unsupported_grant_type": "GitHub refused the device grant for this application.",
}


def _device_error(code: str) -> str:
    return _DEVICE_ERRORS.get(code, f"GitHub refused the sign-in ({code}).")


def poll_device(device_code: str, owner_id: int | None = None) -> dict:
    """One poll. `{status: "pending"|"connected", detail, ...}`.

    `pending` covers both `authorization_pending` and `slow_down`; the caller
    backs off on its own cadence, so the two are one answer here. Anything else
    raises with the sentence for the screen. On success the token is verified,
    checked against the same refusals a pasted one gets, and stored.
    """
    cid = client_id(owner_id)
    if not cid:
        raise GithubAuthError("No GitHub application is registered for this installation.")
    body = _device_post(ACCESS_TOKEN_URL, {
        "client_id": cid,
        "device_code": (device_code or "").strip(),
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })
    err = str(body.get("error") or "")
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending", "detail": _device_error(err)}
    if err:
        raise GithubAuthError(_device_error(err))
    tok = str(body.get("access_token") or "")
    if not tok:
        raise GithubAuthError("GitHub returned no token for a completed sign-in.")
    return {"status": "connected", "detail": "Connected.",
            **connect_token(tok, owner_id, source="device")}


# ---- what a screen may see ---------------------------------------------------

def status(owner_id: int | None = None) -> dict:
    """Everything the settings surface needs, and nothing that is a secret.

    No token, no prefix, no length. The account's login, how it was obtained,
    what it can reach, when it was connected, whether a shell's `git push` will
    use it, and whether a device sign-in is even available here.
    """
    oid = _owner_id(owner_id)
    rec = _load(oid) or {}
    scopes = [s for s in (rec.get(_SCOPES) or "").split(",") if s]
    return {
        "connected": bool(rec.get(_TOKEN)),
        "login": rec.get(_LOGIN) or "",
        "source": rec.get(_SOURCE) or "",
        "scopes": scopes,
        "connected_at": rec.get(_CONNECTED_AT) or None,
        "client_id_set": bool(client_id(oid)),
        "device_available": bool(client_id(oid)),
        "helper_installed": helper_installed(),
        "helper_path": str(helper_path()),
    }
