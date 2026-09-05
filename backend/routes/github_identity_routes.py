"""HTTP surface for the signed-in GitHub account.

`services/github_identity.py` holds the reasoning; this file is the seven verbs
that reach it. Nothing here returns a token, a prefix of one, or its length. The
only secret-shaped values that cross this boundary do so INBOUND: a pasted token
on PUT, and a device code on poll.

Every route runs blocking work in a worker thread, because each one either
shells out to `git config` or makes a request to github.com and this application
serves a chat stream on the same loop.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services import github_identity, ownership

router = APIRouter(prefix="/github", tags=["github"])


class StatusOut(BaseModel):
    """What a settings surface may know about the connection. No secret."""
    connected: bool
    login: str
    source: str
    scopes: list[str]
    connected_at: str | None = None
    client_id_set: bool
    device_available: bool
    helper_installed: bool
    helper_path: str


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
    helper_installed: bool = False
    helper_path: str = ""


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
