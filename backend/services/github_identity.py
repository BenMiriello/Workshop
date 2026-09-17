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
matching in code (`installation.reject_blocked`), never in the credential:
a classic PAT carrying `repo` plus `read:org` can technically read that
organisation and push to its private repositories. The option that ticket names
is a token that cannot, and this module makes that option the only one it will
store: a credential carrying an organisation scope is rejected at the point of
entry, with a message naming what to generate instead. The boundary is then in
the credential as well as in the code, which is what the ticket asked for.

HOW AN ACCOUNT ACTUALLY GETS CONNECTED (docs/platform-redesign/
github-connect-flow-2026-09-16.md is the design of record). Two legs, two
operator clicks, and nothing copied by hand:

  REGISTER   `begin_registration()` builds an App MANIFEST from this
             installation's declared origin and hands it to the browser, which
             POSTs it to the forge as a real form submission (the forge renders
             a page in reply, so it cannot be a fetch). The forge redirects back
             with a temporary code; `complete_registration()` exchanges it at
             the manifest conversion endpoint and stores the client id, the
             client secret and the app's id.
  AUTHORIZE  `begin_authorize()` sends the browser to the forge's authorize
             endpoint; `complete_authorize()` exchanges the returned code for a
             USER access token and stores it through `connect_token`, so the
             org-scope refusal and the blocked-account refusal below apply to
             it exactly as they do to a pasted one.

WHY A USER TOKEN AND NOT AN INSTALLATION TOKEN. Creating a repository in a
personal account is a user-token-only operation, so a server-to-server
installation token cannot do it. A GitHub App's user access token is a user
token and does satisfy it.

WHY THE MANIFEST CANNOT JUST TURN THE DEVICE FLOW ON. The manifest has no
device-flow key; enabling it is a checkbox in the created app's own settings.
So a plan of "manifest, then device flow" still ends with the operator back on
the forge ticking a box, which is the manual step the design exists to remove.
The manifest CAN set `callback_urls`, and the conversion endpoint returns the
client secret, which is exactly what the OAuth web flow needs.

WHY THE DEVICE FLOW STAYS. It needs no client secret and no redirect URL, which
is exactly right for a self-hosted box with no reachable public origin. It does
need a registered application's client id. So there are three ways in: the two
legs above, a pasted fine-grained token, and a device sign-in once a client id
exists. The last two are fallbacks, not the main path.

THE STATE NONCE IS THE ONLY THING THAT AUTHENTICATES A CALLBACK. The session
cookie is SameSite=Strict, so a redirect arriving FROM the forge does not carry
it and the callbacks cannot be behind the operator gate. What stands in for it
is the nonce: minted by an authenticated request, unguessable, single use,
bound to the owner who started the flow and to which leg it belongs to, and
expired after fifteen minutes. `complete_registration` and `complete_authorize`
take the owner FROM the nonce and never from the request.

NO TOKEN VALUE AND NO CLIENT SECRET IS EVER RETURNED, LOGGED OR PUT IN AN
ERROR. Not by `status()`, not by a failure path, not by a test fixture. The
client secret is written once by the registration leg and read only by this
module's own token exchange.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from services import installation

API_BASE = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
# Where a browser POSTs a generated App manifest. The forge renders a page in
# reply, which is why the client submits a form rather than fetching this.
APP_NEW_URL = "https://github.com/settings/apps/new"

# Our own two landing paths, as the forge will call them back on. They carry the
# `/api` prefix because that is the path a browser reaches this backend at
# through the edge; `main.strip_api_prefix` takes it off again on the way in.
MANIFEST_REDIRECT_PATH = "/api/github/registered"
OAUTH_CALLBACK_PATH = "/api/github/authorized"
# Where a finished or failed flow puts the operator back.
SETTINGS_PATH = "/settings/connectors"

# Read from the environment as a fallback so an installation that already knows
# its registered application does not have to re-enter it. The value is a public
# client id, not a secret; it is stored beside the token only because it is a
# fact about this installation rather than about the product.
CLIENT_ID_ENV = "WORKSHOP_GITHUB_CLIENT_ID"

# The store's keys. `token` and `client_secret` are write-only from the outside.
_TOKEN = "token"
_SOURCE = "source"
_LOGIN = "login"
_SCOPES = "scopes"
_CONNECTED_AT = "connected_at"
_CLIENT_ID = "client_id"
_CLIENT_SECRET = "client_secret"
_APP_ID = "app_id"
_APP_SLUG = "app_slug"

# The three global defaults (github-connect-flow-2026-09-16.md cites
# app-separation-and-github-2026-08-20.md section 3.6). They live beside the
# credential because they are the same owner's answer about the same forge, and
# none of them is a secret.
_AUTO_CREATE_REPO = "auto_create_repo"
_DEFAULT_VISIBILITY = "default_visibility"
_DEFAULT_BRANCH = "default_branch_name"

VISIBILITIES = ("private", "public")
DEFAULT_VISIBILITY = "private"
DEFAULT_BRANCH = "main"

SOURCES = ("pat", "device", "oauth")

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


def _client_secret(owner_id: int | None = None) -> str | None:
    """The registered application's secret. PRIVATE ON PURPOSE.

    Nothing outside this module reads it, no route returns it, `status()` does
    not mention it, and no error message quotes it. It is written once by
    `complete_registration` and spent only by `complete_authorize`.
    """
    return (_load(_owner_id(owner_id)) or {}).get(_CLIENT_SECRET) or None


def app_registered(owner_id: int | None = None) -> bool:
    """Does this owner have an application of their own, secret and all?

    A client id on its own is not a registration: it is enough for the device
    grant and not enough for the web flow, which has to exchange a code.
    """
    oid = _owner_id(owner_id)
    return bool(client_id(oid)) and bool(_client_secret(oid))


def connected(owner_id: int | None = None) -> bool:
    return bool(token(owner_id))


# ---- spending the credential -------------------------------------------------

def _basic(tok: str) -> str:
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
    about organisations, and nothing here can. An installation that names an
    organisation it must never touch (`WORKSHOP_BLOCKED_REPO_OWNERS`) would have
    that boundary crossed by the very act of enumerating memberships to check
    it, so the check is the scope refusal below instead.
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
    account is a hard installation rule (`installation.reject_blocked`). An
    organisation scope is [SEC-G1]: the token could reach an organisation
    whatever the code around it does, so the boundary would still be string
    matching rather than capability.
    """
    if login:
        installation.reject_blocked(login)
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

def _form_post(url: str, data: dict[str, str]) -> dict:
    """One form POST to the forge's OAuth endpoints, answered as JSON.

    Shared by the device grant and the web flow: both are the same
    `application/x-www-form-urlencoded` request answering with the same
    `{error}` or `{access_token}` shape. `data` may carry a client secret or a
    code verifier, so nothing here ever logs it or puts it in a raised message.
    """
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
    body = _form_post(DEVICE_CODE_URL, payload)
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
    body = _form_post(ACCESS_TOKEN_URL, {
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


# ---- the state nonce ---------------------------------------------------------
# A callback arrives as a redirect FROM the forge. The session cookie is
# SameSite=Strict, so it is not sent on that navigation and the callback cannot
# be behind the operator gate. The nonce is what stands in for it, and it is the
# same value that closes CSRF on an OAuth redirect anyway: unguessable, single
# use, bound to the owner who started the flow and to which leg it belongs to.
#
# IN MEMORY ON PURPOSE. A nonce is worth fifteen minutes and is meaningless
# afterwards, so writing it to the owner's encrypted store would be a durable
# home for a value whose whole job is to expire. A backend restart mid-flow
# costs one click of Start again.

REGISTER = "register"
AUTHORIZE = "authorize"

_STATE_TTL_SECONDS = 900
_STATE_REJECTED = ("That sign-in did not start here, or it took too long. "
                   "Start again from Settings.")

_states: dict[str, dict[str, Any]] = {}
_states_lock = threading.Lock()

# NONCES THAT WERE ALREADY SPENT SUCCESSFULLY, so pressing Back does not report a
# failure for a connection that worked.
#
# The browser's history after a successful connect is roughly
#     A: /settings/connectors   B: github.com/login/oauth/authorize?...state=S
#     C: /settings/connectors
# because `location.assign` pushes and the 303s replace. Back from C lands on B,
# still carrying S. For an app the operator has already authorized, GitHub does
# not re-prompt: it issues a fresh code and redirects straight back here, and
# `_take_state` then pops nothing and raises. So the ordinary act of pressing
# Back after a successful sign-in produced "That sign-in did not start here, or
# it took too long. Start again from Settings." on a page that was, in fact,
# connected.
#
# Nothing is weakened by remembering them. The replayed code is never exchanged:
# the branch that reads this returns the operator to the settings page and stops.
# A nonce is still redeemable exactly once for the purpose of COMPLETING a flow,
# which is the property that matters. This only distinguishes "never valid here"
# from "already done" -- and telling those apart is the whole complaint.
#
# Note the client cannot fix this. `location.replace()` would leave the stack as
# [B, C], so Back still reaches B with the spent nonce AND the operator loses the
# page they came from. The github.com entry is cross-origin and cannot be removed
# from the client at all.
_spent_states: dict[str, float] = {}
_SPENT_TTL_SECONDS = 900


def _new_state(owner_id: int, kind: str, verifier: str = "") -> str:
    """Mint a nonce for one leg of one owner's flow, and remember it."""
    nonce = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _states_lock:
        for key, rec in list(_states.items()):
            if rec["expires"] <= now:
                _states.pop(key, None)
        _states[nonce] = {"owner": int(owner_id), "kind": kind,
                          "verifier": verifier, "expires": now + _STATE_TTL_SECONDS}
    return nonce


def _take_state(nonce: str, kind: str) -> dict[str, Any]:
    """Redeem a nonce exactly once, or refuse.

    Popped BEFORE it is checked, so a wrong `kind` or an expired record burns
    the value rather than leaving it available to try again. The refusal reads
    the same for every reason there is, because the differences between them
    are only useful to someone guessing.
    """
    with _states_lock:
        rec = _states.pop((nonce or "").strip(), None) if nonce else None
    if rec is None or rec["kind"] != kind or rec["expires"] <= time.monotonic():
        raise GithubAuthError(_STATE_REJECTED)
    return rec


def _mark_spent(nonce: str) -> None:
    """Remember a nonce that completed its leg. See `_spent_states`."""
    now = time.monotonic()
    with _states_lock:
        for key, expires in list(_spent_states.items()):
            if expires <= now:
                _spent_states.pop(key, None)
        _spent_states[(nonce or "").strip()] = now + _SPENT_TTL_SECONDS


def was_spent(nonce: str) -> bool:
    """Did this nonce already complete a leg successfully?

    True means the browser is replaying a finished flow, which is what Back does.
    The caller sends the operator back to the settings page WITHOUT exchanging
    the code and without an error, because nothing went wrong.
    """
    key = (nonce or "").strip()
    if not key:
        return False
    with _states_lock:
        expires = _spent_states.get(key)
    return expires is not None and expires > time.monotonic()


# ---- registering an application from a manifest -------------------------------

NO_ORIGIN = ("This installation has no address a browser can reach it at, so "
             "GitHub cannot redirect back to it. Paste a token instead.")
NOT_REGISTERED = "No GitHub application is registered for this installation yet."

# GitHub caps an App name at 34 characters and requires it to be unique across
# the whole forge, so it is built from the one thing that is already unique to
# this installation: the host it answers on. A collision is recoverable, because
# the forge's own page lets the operator edit the name before creating it.
_NAME_PREFIX = "Workshop"
_NAME_MAX = 34


def _app_name(origin: str) -> str:
    host = urlsplit(origin).hostname or ""
    tail = re.sub(r"[^A-Za-z0-9]+", "-", host).strip("-")
    name = (f"{_NAME_PREFIX} {tail}" if tail else _NAME_PREFIX)[:_NAME_MAX]
    # A cut can land on a separator, and the forge rejects a name ending in one.
    return name.rstrip("- ") or _NAME_PREFIX


def app_manifest(origin: str) -> dict:
    """The App this installation asks the forge to create on its behalf.

    `public: False` keeps it installable only by the account that created it.
    The permissions are the smallest set that lets the platform do what it does
    with a repository: read and write contents, read metadata, read and write
    pull requests. There is no organisation permission and no webhook, because
    nothing here consumes one.
    """
    return {
        "name": _app_name(origin),
        "url": origin,
        "redirect_url": f"{origin}{MANIFEST_REDIRECT_PATH}",
        "callback_urls": [f"{origin}{OAUTH_CALLBACK_PATH}"],
        "public": False,
        "default_events": [],
        "default_permissions": {
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
        },
        "request_oauth_on_install": False,
    }


def begin_registration(owner_id: int | None = None) -> dict:
    """Everything the browser needs to POST a manifest at the forge.

    `{post_url, manifest}`. The manifest is a JSON string because the forge
    takes it as a single form field, and the client submits a real form: the
    forge answers with a page for the operator to look at, which a fetch could
    not show them.
    """
    oid = _owner_id(owner_id)
    origin = installation.public_origin()
    if not origin:
        raise GithubAuthError(NO_ORIGIN)
    state = _new_state(oid, REGISTER)
    return {
        "post_url": f"{APP_NEW_URL}?state={quote(state, safe='')}",
        "manifest": json.dumps(app_manifest(origin), separators=(",", ":")),
    }


def _conversion(code: str) -> dict:
    """Trade a temporary manifest code for the created application's identity."""
    try:
        r = httpx.post(
            f"{API_BASE}/app-manifests/{quote(code, safe='')}/conversions",
            headers={"Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        raise GithubAuthError(f"GitHub could not be reached: {type(e).__name__}.")
    if r.status_code not in (200, 201):
        raise GithubAuthError(
            f"GitHub answered {r.status_code} to the application registration.")
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise GithubAuthError("GitHub answered the registration in a shape this cannot read.")
    return body


def complete_registration(code: str, state: str) -> int:
    """Store the application the forge just created. Returns its owner's id.

    The owner comes from the nonce, never from the request: a redirect from the
    forge carries no session. Nothing is connected here, because registering an
    application and signing in as a user are different acts; the caller sends
    the browser on to `begin_authorize` next.
    """
    rec = _take_state(state, REGISTER)
    owner = int(rec["owner"])
    code = (code or "").strip()
    if not code:
        raise GithubAuthError("GitHub sent no registration code back.")
    body = _conversion(code)
    cid = str(body.get("client_id") or "").strip()
    secret = str(body.get("client_secret") or "").strip()
    if not cid or not secret:
        raise GithubAuthError("GitHub registered no application this can sign in with.")
    _save(owner, {
        _CLIENT_ID: cid,
        _CLIENT_SECRET: secret,
        _APP_ID: str(body.get("id") or ""),
        _APP_SLUG: str(body.get("slug") or ""),
    })
    return owner


# ---- the OAuth web flow -------------------------------------------------------

def _pkce() -> tuple[str, str]:
    """A verifier and its S256 challenge.

    PKCE is carried because the design of record asks for it and it costs one
    hash. It is belt to the nonce's braces rather than the load-bearing part:
    the forge is free to ignore an unknown parameter, so what actually binds a
    callback to the request that started it is `_take_state`.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def begin_authorize(owner_id: int | None = None) -> str:
    """The URL to send the browser to so the operator can authorize this app."""
    oid = _owner_id(owner_id)
    cid = client_id(oid)
    if not cid:
        raise GithubAuthError(NOT_REGISTERED)
    origin = installation.public_origin()
    if not origin:
        raise GithubAuthError(NO_ORIGIN)
    verifier, challenge = _pkce()
    state = _new_state(oid, AUTHORIZE, verifier)
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": cid,
        "redirect_uri": f"{origin}{OAUTH_CALLBACK_PATH}",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


# What the web flow's token exchange can say, in the words the operator needs.
_OAUTH_ERRORS = {
    "access_denied": "The sign-in was cancelled on github.com.",
    "bad_verification_code": "That sign-in expired before it finished. Start again.",
    "incorrect_client_credentials": "GitHub did not recognize this installation's application.",
    "redirect_uri_mismatch": ("GitHub would not redirect back to this address. "
                              "Register the application again."),
    "unsupported_grant_type": "GitHub refused the sign-in for this application.",
}


def complete_authorize(code: str, state: str) -> int:
    """Exchange an authorization code for a user token and connect it.

    Returns the owner id, taken from the nonce. The token goes in through
    `connect_token`, which is the one door: the org-scope refusal, the blocked
    account refusal and the login probe all apply here exactly as they do to a
    pasted token.
    """
    rec = _take_state(state, AUTHORIZE)
    owner = int(rec["owner"])
    cid = client_id(owner)
    secret = _client_secret(owner)
    if not cid or not secret:
        raise GithubAuthError(NOT_REGISTERED)
    origin = installation.public_origin()
    if not origin:
        raise GithubAuthError(NO_ORIGIN)
    body = _form_post(ACCESS_TOKEN_URL, {
        "client_id": cid,
        "client_secret": secret,
        "code": (code or "").strip(),
        "redirect_uri": f"{origin}{OAUTH_CALLBACK_PATH}",
        "code_verifier": str(rec.get("verifier") or ""),
    })
    err = str(body.get("error") or "")
    if err:
        raise GithubAuthError(
            _OAUTH_ERRORS.get(err, f"GitHub refused the sign-in ({err})."))
    tok = str(body.get("access_token") or "")
    if not tok:
        raise GithubAuthError("GitHub returned no token for a completed sign-in.")
    connect_token(tok, owner, source="oauth")
    # Remember it BEFORE returning, so pressing Back and replaying this exact
    # nonce is recognised as a finished flow rather than reported as a failure.
    _mark_spent(state)
    return owner


def landing(message: str = "") -> str:
    """Where a finished or failed callback puts the operator back.

    An absolute URL when the installation declares an origin, a path otherwise,
    which still works because the redirect is relative to the same host the
    browser already reached.
    """
    base = installation.public_origin() + SETTINGS_PATH
    if not message:
        return base
    return f"{base}?{urlencode({'github_error': message})}"


# ---- the global defaults ------------------------------------------------------

_BRANCH_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def defaults(owner_id: int | None = None) -> dict:
    """What a new app's repository gets when nobody says otherwise.

    Unset reads as the shipped default rather than as off: an installation that
    has never opened this screen still creates private repositories on `main`.
    """
    rec = _load(_owner_id(owner_id)) or {}
    visibility = (rec.get(_DEFAULT_VISIBILITY) or "").strip().lower()
    branch = (rec.get(_DEFAULT_BRANCH) or "").strip()
    return {
        "auto_create_repo": (rec.get(_AUTO_CREATE_REPO) or "").strip() != "0",
        "default_visibility": visibility if visibility in VISIBILITIES else DEFAULT_VISIBILITY,
        "default_branch_name": branch or DEFAULT_BRANCH,
    }


def set_defaults(
    *,
    auto_create_repo: bool | None = None,
    default_visibility: str | None = None,
    default_branch_name: str | None = None,
    owner_id: int | None = None,
) -> dict:
    """Write the ones that were named, leave the rest. Returns `defaults()`."""
    oid = _owner_id(owner_id)
    values: dict[str, str] = {}
    if auto_create_repo is not None:
        values[_AUTO_CREATE_REPO] = "1" if auto_create_repo else "0"
    if default_visibility is not None:
        want = default_visibility.strip().lower()
        if want not in VISIBILITIES:
            raise GithubAuthError(
                f"Visibility is one of {', '.join(VISIBILITIES)}.")
        values[_DEFAULT_VISIBILITY] = want
    if default_branch_name is not None:
        want = default_branch_name.strip()
        if not want or ".." in want or want.endswith("/") or not _BRANCH_OK.match(want):
            raise GithubAuthError(
                "A branch name starts with a letter or a digit and may contain "
                "letters, digits, dots, dashes, underscores and slashes.")
        values[_DEFAULT_BRANCH] = want
    if values:
        _save(oid, values)
    return defaults(oid)


# ---- what a screen may see ---------------------------------------------------

def status(owner_id: int | None = None) -> dict:
    """Everything the settings surface needs, and nothing that is a secret.

    No token, no prefix, no length, AND NO CLIENT SECRET. The account's login,
    how it was obtained, what it can reach, when it was connected, whether a
    shell's `git push` will use it, which ways in are available here, and the
    three defaults a new repository gets.

    `manifest_available` is false on an installation that declares no origin.
    That is the honest answer rather than a button that fails invisibly: the
    forge has nowhere to redirect back to, so the screen offers the fallbacks.
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
        "app_registered": app_registered(oid),
        "manifest_available": bool(installation.public_origin()),
        "helper_installed": helper_installed(),
        "helper_path": str(helper_path()),
        "defaults": defaults(oid),
    }
