"""HTTP surface for the model-provider and credential store.

WHAT A CALLER CAN DO HERE: list the providers this deployment offers, add or
replace a credential for one, and remove it. Nothing in this module ever returns
a stored credential value. What comes back out is presence, provenance, whether
a turn can actually run on it, and the last four characters as a UI hint.

THE SUBSCRIPTION IS SETTABLE HERE TOO, and that is the point of this surface
rather than a detail of it. The one credential every turn depends on used to be
readable only from a dotfile the product never mentions, so "change my Claude
credential" was an ssh session and a restart. It is now a paste and a Save, and
`llm_router._claude_env()` reads the store per call, so the next turn uses it.

VERIFY BEFORE STORE, for every provider, following `routes/trackers.py` exactly:
a cheap shape check catches a paste error, then a real call decides. For an API
key that call is the vendor's own models endpoint. For the subscription it is
one Haiku turn, because `claude auth status` does not contact Anthropic and
would pass a token that 401s on the next real turn (`services/auth_health`).
Skipping it would be worse than a slow Save: the environment variable overrides
the credentials the CLI holds for itself, so an unverified bad paste takes every
turn down rather than sitting inert.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import auth_health, model_credentials, model_providers, ownership

router = APIRouter(prefix="/model-providers", tags=["model-providers"])


class ProviderRow(BaseModel):
    """One provider as a settings screen sees it. Carries no credential."""

    id: str
    label: str
    auth_mode: str
    multi_user: bool
    restriction: str
    console_url: str
    runtime_ready: bool
    # This principal supplied a credential through this surface.
    stored: bool
    # A credential resolves at all: stored here, in the deployment's auth file,
    # or held by the CLI itself.
    configured: bool
    # Configured AND this deployment can execute a turn on it. The two are not
    # the same and used to be reported as one boolean, which is how a key that
    # nothing routes to displayed exactly like the subscription serving every
    # turn.
    usable: bool
    # "principal" | "deployment" | "cli" | null. Answers "I pasted a token, why
    # does it still say it is reading the file".
    source: str | None = None
    last4: str | None = None
    # For the subscription row only: what the CLI itself reports about its own
    # sign-in state, and the caveat that reading local config is not proof.
    cli_state: str | None = None
    cli_detail: str | None = None


async def _probe(provider_id: str, value: str) -> tuple[bool, str]:
    """Live-check a credential against the provider. Returns (ok, error_msg).

    The message is the PROVIDER's own words wherever it gave any, because
    "invalid_api_key: this key was revoked" tells the operator what to do and
    "verification failed" does not.
    """
    provider = model_providers.get(provider_id)
    if provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
        return await auth_health.verify_claude_token(value)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if provider_id == "anthropic_api":
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": value,
                        "anthropic-version": "2023-06-01",
                    },
                )
            elif provider_id == "openai_api":
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {value}"},
                )
            else:
                return True, ""
    except httpx.TimeoutException:
        return False, f"connection to {provider.label} timed out"
    except Exception as e:
        return False, f"could not reach {provider.label}: {e}"

    if r.status_code in (200, 202):
        return True, ""
    try:
        body = r.json()
        err = body.get("error", {})
        msg = (
            err.get("message")
            or body.get("detail")
            or body.get("message")
            or str(r.status_code)
        )
    except Exception:
        msg = r.text[:200] if r.text else str(r.status_code)
    return False, msg


async def _rows(principal_id: int) -> list[dict]:
    """Every offerable provider, with the credential facts filled in.

    The CLI probe runs ONLY for the subscription row and only when nothing is
    configured through the app or the auth file, because that is the one case
    where the answer is otherwise wrong: on an installation whose CLI is signed
    in with its own stored credentials, the store and the file are both empty
    and the screen would report "not signed in" while every turn works.
    `model_credentials.status()` cannot answer this itself and must not learn
    to: it is a synchronous store, and this is a subprocess.
    """
    rows = [
        {**r, "last4": model_credentials.key_tail(principal_id, r["id"])}
        for r in model_credentials.status(principal_id)
    ]
    for row in rows:
        if row["auth_mode"] != model_providers.AUTH_SUBSCRIPTION:
            continue
        cli, _cached = await auth_health.cached_check_claude()
        row["cli_state"] = cli.get("state") or None
        row["cli_detail"] = cli.get("detail") or cli.get("unverified_because") or None
        if not row["configured"] and cli.get("kind") == auth_health.KIND_OK:
            row["configured"] = True
            row["usable"] = model_providers.get(row["id"]).runtime_ready
            row["source"] = "cli"
    return rows


@router.get("", response_model=list[ProviderRow])
async def list_providers() -> list[dict]:
    return await _rows(ownership.current_owner())


class CredentialIn(BaseModel):
    value: str = Field(min_length=1)


def _one(rows: list[dict], provider_id: str) -> dict:
    row = next((r for r in rows if r["id"] == provider_id), None)
    if row is None:
        raise HTTPException(500, "provider not found after the write")
    return row


@router.put("/{provider_id}/credential", response_model=ProviderRow)
async def set_credential(provider_id: str, body: CredentialIn) -> dict:
    principal_id = ownership.current_owner()
    value = body.value.strip()
    if not model_providers.looks_like_key(provider_id, value):
        try:
            p = model_providers.get(provider_id)
            hint = (
                f" (expected prefix: {', '.join(p.key_prefix)})"
                if p.key_prefix
                else ""
            )
        except ValueError:
            hint = ""
        raise HTTPException(400, f"key shape check failed{hint}")
    ok, err = await _probe(provider_id, value)
    if not ok:
        raise HTTPException(400, err)
    try:
        model_credentials.set_credential(principal_id, provider_id, value)
    except model_credentials.ProviderNotPermitted as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # The health row is cached for a minute and has just become wrong about the
    # credential it describes. Forget it, or the screen that made the change
    # reads a stale answer about the token it replaced.
    auth_health.invalidate_claude_cache()
    return _one(await _rows(principal_id), provider_id)


@router.delete("/{provider_id}/credential", response_model=ProviderRow)
async def clear_credential(provider_id: str) -> dict:
    principal_id = ownership.current_owner()
    model_credentials.clear_credential(principal_id, provider_id)
    auth_health.invalidate_claude_cache()
    return _one(await _rows(principal_id), provider_id)
