"""HTTP surface for the model-provider and credential store.

Stage 1 of docs/platform-redesign/providers-credentials-and-limits-2026-08-23.md:
a key can be registered, verified, stored encrypted, and removed. Nothing in
this module ever returns the stored key value -- only presence, label, provider
metadata, and the last four characters as a UI hint.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import model_credentials, model_providers, ownership

router = APIRouter(prefix="/model-providers", tags=["model-providers"])


async def _probe(provider_id: str, value: str) -> tuple[bool, str]:
    """Live-check a key against the provider's API. Returns (ok, error_msg)."""
    provider = model_providers.get(provider_id)
    if provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
        return True, ""
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


@router.get("")
async def list_providers() -> list[dict]:
    principal_id = ownership.current_owner()
    rows = model_credentials.status(principal_id)
    return [{**r, "last4": model_credentials.key_tail(principal_id, r["id"])} for r in rows]


class CredentialIn(BaseModel):
    value: str = Field(min_length=1)


@router.put("/{provider_id}/credential")
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
    rows = model_credentials.status(principal_id)
    row = next((r for r in rows if r["id"] == provider_id), None)
    if row is None:
        raise HTTPException(500, "provider not found after store")
    return {**row, "last4": model_credentials.key_tail(principal_id, provider_id)}


@router.delete("/{provider_id}/credential")
async def clear_credential(provider_id: str) -> dict:
    principal_id = ownership.current_owner()
    model_credentials.clear_credential(principal_id, provider_id)
    rows = model_credentials.status(principal_id)
    row = next((r for r in rows if r["id"] == provider_id), {})
    return {**row, "last4": None}
