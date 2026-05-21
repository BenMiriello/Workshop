from fastapi import APIRouter, HTTPException, Query

import plaid_bridge
from services import finance_cache

router = APIRouter()


@router.get("/balances")
async def get_balances(refresh: bool = Query(False)):
    try:
        data, meta = await finance_cache.get_or_fetch(
            "balances",
            plaid_bridge.balances,
            force_refresh=refresh,
        )
        return {**data, "_cache": meta}
    except Exception as e:
        raise HTTPException(502, f"plaid bridge failed: {e}")


@router.get("/transactions")
async def get_transactions(days: int = Query(30, ge=1, le=730), refresh: bool = Query(False)):
    try:
        data, meta = await finance_cache.get_or_fetch(
            f"transactions:{days}",
            lambda: plaid_bridge.transactions(days=days),
            force_refresh=refresh,
        )
        return {**data, "_cache": meta}
    except Exception as e:
        raise HTTPException(502, f"plaid bridge failed: {e}")


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
