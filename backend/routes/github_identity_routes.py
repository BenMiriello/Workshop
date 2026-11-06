"""HTTP surface for the signed-in GitHub account.

`services/github_identity.py` holds the reasoning; this file is the verbs that
reach it. Nothing here returns a token, a prefix of one, or its length. The only
secret-shaped value that crosses this boundary does so INBOUND: a device code on
poll.

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


class DefaultsOut(BaseModel):
    """What a new app's repository gets when nobody says otherwise."""
    auto_create_repo: bool
    default_visibility: str
    default_branch_name: str


class InstallationOut(BaseModel):
    """Where the GitHub App is installed, for a signed-in account."""
    state: str
    id: int | None = None
    manage_url: str = ""
    repository_selection: str
    repository_count: int | None = None
    blocked: list[str]


class StatusOut(BaseModel):
    """What a settings surface may know about the connection. No secret."""
    connected: bool
    login: str
    source: str
    scopes: list[str]
    connected_at: str | None = None
    device_available: bool
    install_url: str
    installation: InstallationOut | None = None
    helper_installed: bool
    helper_path: str
    defaults: DefaultsOut


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
    device_available: bool = False
    install_url: str = ""
    installation: InstallationOut | None = None
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


@router.delete("", response_model=StatusOut)
async def delete_connection() -> dict:
    owner = _owner()
    return await run_in_threadpool(github_identity.disconnect, owner)


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
