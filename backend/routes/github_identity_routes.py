"""HTTP surface for the signed-in GitHub account.

`services/github_identity.py` holds the reasoning; this file is the verbs that
reach it. Nothing here returns a token, a prefix of one, its length, or the
registered application's client secret. The only secret-shaped values that cross
this boundary do so INBOUND: a pasted token on PUT, a device code on poll, and
an authorization code on a callback.

Every route runs blocking work in a worker thread, because each one either
shells out to `git config` or makes a request to github.com and this application
serves a chat stream on the same loop.

TWO ROUTERS, AND THE SPLIT IS NOT COSMETIC. `router` is mounted behind the
operator gate. `callback_router` is not, and cannot be: its two routes are the
addresses the forge redirects a browser BACK to, the session cookie is
SameSite=Strict so it is not sent on that navigation, and a gate there would
401 every real completion. What authenticates them instead is the state nonce,
which `github_identity._take_state` mints under an authenticated request, binds
to an owner and a leg, accepts once and expires in fifteen minutes. Both
callbacks take the owner from the nonce and never from the request.

A callback answers with a REDIRECT, never with JSON. The browser arrived here
by following the forge, so whatever happened it has to land back on a page, and
a failure travels as a message in the query string rather than as a status code
nobody will see.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services import github_identity, ownership

router = APIRouter(prefix="/github", tags=["github"])
callback_router = APIRouter(prefix="/github", tags=["github"])


class DefaultsOut(BaseModel):
    """What a new app's repository gets when nobody says otherwise."""
    auto_create_repo: bool
    default_visibility: str
    default_branch_name: str


class StatusOut(BaseModel):
    """What a settings surface may know about the connection. No secret."""
    connected: bool
    login: str
    source: str
    scopes: list[str]
    connected_at: str | None = None
    client_id_set: bool
    device_available: bool
    # Whether this owner has an application of their own (id AND secret), and
    # whether this installation declares an address the forge could redirect
    # back to. The screen offers the manifest flow only when both can be true.
    app_registered: bool
    manifest_available: bool
    helper_installed: bool
    helper_path: str
    defaults: DefaultsOut


class TokenIn(BaseModel):
    # Write-only. The value is verified, checked for reach, stored, and dropped.
    token: str = Field(min_length=1)


class ClientIdIn(BaseModel):
    # A registered application's PUBLIC client id, not a secret. Blank clears it.
    client_id: str = ""


class DeviceStartOut(BaseModel):
    user_code: str
    verification_uri: str
    device_code: str
    interval: int
    expires_in: int


class DevicePollIn(BaseModel):
    device_code: str = Field(min_length=1)


class DevicePollOut(BaseModel):
    """`pending` while the operator is still on github.com, `connected` after.

    The connection fields are the same ones `GET /github` returns, flattened in
    so a client that just finished the flow does not have to re-fetch to render.
    """
    status: str
    detail: str
    connected: bool = False
    login: str = ""
    source: str = ""
    scopes: list[str] = []
    connected_at: str | None = None
    client_id_set: bool = False
    device_available: bool = False
    app_registered: bool = False
    manifest_available: bool = False
    helper_installed: bool = False
    helper_path: str = ""
    defaults: DefaultsOut | None = None


class HelperIn(BaseModel):
    # Whether a `git push` typed in any shell on this host should authenticate
    # from the connected account.
    enabled: bool


def _owner() -> int:
    return ownership.current_owner()


def _fail(e: github_identity.GithubAuthError) -> HTTPException:
    """A refused credential is an ordinary operational state, so it is a 400
    carrying the sentence the operator has to act on."""
    return HTTPException(400, str(e))


@router.get("", response_model=StatusOut)
async def get_status() -> dict:
    owner = _owner()
    return await run_in_threadpool(github_identity.status, owner)


@router.put("/token", response_model=StatusOut)
async def put_token(body: TokenIn) -> dict:
    owner = _owner()
    try:
        return await run_in_threadpool(
            github_identity.connect_token, body.token, owner)
    except github_identity.GithubAuthError as e:
        raise _fail(e)
    except ValueError as e:
        # services.github.reject_blocked names an account this installation must
        # not touch. Its message already says which and why.
        raise HTTPException(400, str(e))


@router.delete("", response_model=StatusOut)
async def delete_connection() -> dict:
    owner = _owner()
    return await run_in_threadpool(github_identity.disconnect, owner)


@router.put("/client-id", response_model=StatusOut)
async def put_client_id(body: ClientIdIn) -> dict:
    owner = _owner()

    def _set() -> dict:
        github_identity.set_client_id(body.client_id, owner)
        return github_identity.status(owner)

    return await run_in_threadpool(_set)


@router.post("/device", response_model=DeviceStartOut)
async def start_device() -> dict:
    owner = _owner()
    try:
        return await run_in_threadpool(github_identity.start_device, owner)
    except github_identity.GithubAuthError as e:
        raise _fail(e)


@router.post("/device/poll", response_model=DevicePollOut)
async def poll_device(body: DevicePollIn) -> dict:
    owner = _owner()
    try:
        return await run_in_threadpool(
            github_identity.poll_device, body.device_code, owner)
    except github_identity.GithubAuthError as e:
        raise _fail(e)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/helper", response_model=StatusOut)
async def put_helper(body: HelperIn) -> dict:
    owner = _owner()

    def _apply() -> dict:
        if body.enabled:
            github_identity.install_credential_helper()
        else:
            github_identity.remove_credential_helper()
        return github_identity.status(owner)

    return await run_in_threadpool(_apply)


# ---- the global defaults ------------------------------------------------------

class DefaultsIn(BaseModel):
    """Only the fields that are sent are written."""
    auto_create_repo: bool | None = None
    default_visibility: str | None = None
    default_branch_name: str | None = None


@router.put("/defaults", response_model=StatusOut)
async def put_defaults(body: DefaultsIn) -> dict:
    owner = _owner()

    def _apply() -> dict:
        github_identity.set_defaults(
            auto_create_repo=body.auto_create_repo,
            default_visibility=body.default_visibility,
            default_branch_name=body.default_branch_name,
            owner_id=owner,
        )
        return github_identity.status(owner)

    try:
        return await run_in_threadpool(_apply)
    except github_identity.GithubAuthError as e:
        raise _fail(e)


# ---- registering an application, then signing in ------------------------------

class ManifestOut(BaseModel):
    """What the browser POSTs at the forge, and where.

    `manifest` is a JSON string because the forge takes it as one form field.
    This is the only route whose answer is meant to be submitted rather than
    read: the forge replies with a page for the operator, so the client has to
    navigate to it, not fetch it.
    """
    post_url: str
    manifest: str


@router.post("/app/manifest", response_model=ManifestOut)
async def post_manifest() -> dict:
    owner = _owner()
    try:
        return await run_in_threadpool(github_identity.begin_registration, owner)
    except github_identity.GithubAuthError as e:
        raise _fail(e)


class AuthorizeOut(BaseModel):
    """Where to send the browser to authorize the registered application."""
    url: str


@router.post("/authorize", response_model=AuthorizeOut)
async def post_authorize() -> dict:
    owner = _owner()
    try:
        url = await run_in_threadpool(github_identity.begin_authorize, owner)
    except github_identity.GithubAuthError as e:
        raise _fail(e)
    return {"url": url}


# ---- where the forge sends the browser back -----------------------------------
# Unauthenticated by necessity, authenticated by the state nonce. See the module
# docstring. `response_model=None` is the honest declaration for a route whose
# whole answer is a Location header.

_SEE_OTHER = 303  # the browser arrived on a POST-driven redirect; land it on a GET


def _back(message: str = "") -> RedirectResponse:
    return RedirectResponse(github_identity.landing(message), status_code=_SEE_OTHER)


@callback_router.get("/registered", response_model=None)
async def registered(code: str = "", state: str = "") -> RedirectResponse:
    """The forge created the application and sent its code back.

    Storing it is only half a connection, so this does not stop here: it mints
    the next nonce and sends the browser straight on to the authorize leg, which
    is what makes connecting two clicks instead of four.
    """
    try:
        owner = await run_in_threadpool(
            github_identity.complete_registration, code, state)
        url = await run_in_threadpool(github_identity.begin_authorize, owner)
    except github_identity.GithubAuthError as e:
        return _back(str(e))
    except ValueError as e:
        return _back(str(e))
    return RedirectResponse(url, status_code=_SEE_OTHER)


@callback_router.get("/authorized", response_model=None)
async def authorized(code: str = "", state: str = "",
                     error: str = "", error_description: str = "") -> RedirectResponse:
    """The operator authorized the application, or declined it.

    A decline comes back as `error`, which is an outcome rather than a fault, so
    it lands on the settings page saying so. The description is the forge's own
    sentence and carries no credential.
    """
    if error:
        return _back(error_description.strip() or f"GitHub refused the sign-in ({error}).")
    try:
        await run_in_threadpool(github_identity.complete_authorize, code, state)
    except github_identity.GithubAuthError as e:
        return _back(str(e))
    except ValueError as e:
        return _back(str(e))
    return _back()
