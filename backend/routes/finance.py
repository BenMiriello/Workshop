"""Finance API — owner-scoped, self-contained Plaid (no external MCP).

Every endpoint runs under the authenticated owner (the router is mounted with
require_user; the owner is pinned in the ownership contextvar). A user sees only
their own linked banks; a user with no linked banks gets empty balances/txns.
"""
from fastapi import APIRouter, Body, HTTPException, Query

from services import finance_cache, finance_plaid, plaid_store

router = APIRouter()


@router.get("/balances")
async def get_balances(refresh: bool = Query(False)):
    try:
        data, meta = await finance_cache.get_or_fetch(
            "balances",
            lambda: finance_plaid.balances(realtime=refresh),
            force_refresh=refresh,
        )
        return {**data, "_cache": meta}
    except Exception as e:
        raise HTTPException(502, f"plaid balances failed: {e}")


@router.get("/transactions")
async def get_transactions(days: int = Query(30, ge=1, le=730), refresh: bool = Query(False)):
    try:
        data, meta = await finance_cache.get_or_fetch(
            f"transactions:{days}",
            lambda: finance_plaid.transactions(days=days),
            force_refresh=refresh,
        )
        return {**data, "_cache": meta}
    except Exception as e:
        raise HTTPException(502, f"plaid transactions failed: {e}")


# --------------------------------------------------------------- bank linking

@router.get("/items")
async def list_items():
    """The owner's linked institutions (no access tokens)."""
    return {"items": plaid_store.list_items()}


@router.post("/link/token")
async def create_link_token():
    """Create a Plaid Link token for the owner; the SPA opens Plaid Link with it."""
    try:
        return await finance_plaid.link_token_create()
    except Exception as e:
        raise HTTPException(502, f"could not create link token: {e}")


@router.post("/link/exchange")
async def exchange(public_token: str = Body(..., embed=True)):
    """Exchange the Link public_token for an access token, store it owner-scoped
    (encrypted), and drop the finance cache so the new bank shows immediately."""
    try:
        res = await finance_plaid.exchange_public_token(public_token)
    except Exception as e:
        raise HTTPException(502, f"link exchange failed: {e}")
    finance_cache.invalidate()
    return res


@router.delete("/items/{item_id}")
async def remove_item(item_id: str):
    """Unlink an institution (removes it at Plaid + from the owner's store)."""
    try:
        res = await finance_plaid.remove_item(item_id)
    except Exception as e:
        raise HTTPException(502, f"unlink failed: {e}")
    finance_cache.invalidate()
    return res


@router.get("/cache")
async def cache_status():
    """Diagnostic: what's cached, how stale. UI's 'Last updated' reads this."""
    return finance_cache.status()


@router.post("/cache/invalidate")
async def cache_invalidate(prefix: str = Query("")):
    """Force a refresh on next request. Prefix filters which keys to drop;
    empty prefix drops everything."""
    if prefix:
        removed = finance_cache.invalidate(prefix)
    else:
        removed = finance_cache.invalidate()
    return {"removed": removed}
