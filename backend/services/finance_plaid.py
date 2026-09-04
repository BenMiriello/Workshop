"""Self-contained Plaid integration for Workshop finance/budget.

Replaces the old delegation to the external ~/clawdbot Plaid MCP (node
subprocess). Workshop now owns its Plaid integration: it talks to the Plaid REST
API directly (httpx, like services/plaid_verify.py already does) using the
operator's Plaid app credentials, and stores each user's Items owner-scoped and
encrypted via services.finance_plaid_store.

Everything is owner-scoped: the owner is the authenticated user, resolved from
services.ownership (the request pins it; background passes set it explicitly).
A user with no linked Items gets empty balances/transactions — never anyone
else's data.

Public surface (shapes match the previous bridge so finance/budget UI + the
budget engine are unchanged):
  balances(realtime) -> {"items": [...]}
  transactions(days) -> {"transactions": [...]}
  link_token_create() -> {"link_token", "expiration"}
  exchange_public_token(public_token) -> {"item_id", "institution_name", "accounts"}
  remove_item(item_id) -> {"removed": bool}
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from services import ownership, finance_plaid_store as plaid_store

# PLAID_ENV picks the Plaid host: sandbox/development/production. PLAID_ENV_URL
# is an explicit override for anyone who needs a custom host (proxy, mock
# server) and always wins over PLAID_ENV. There is deliberately no default —
# see _plaid_base() — because guessing production for an unset/unrecognized
# PLAID_ENV would silently route a "sandbox" intent at real bank connections.
_PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}
# The redirect/webhook the SPA Link flow uses; Plaid posts item updates here.
PLAID_WEBHOOK = os.environ.get("WORKSHOP_PLAID_WEBHOOK_URL", "")
# Plaid Link products + country. Transactions is what budget needs.
_PRODUCTS = [p for p in os.environ.get("PLAID_PRODUCTS", "transactions").split(",") if p]
_COUNTRY = [c for c in os.environ.get("PLAID_COUNTRY_CODES", "US").split(",") if c]


def _plaid_base() -> str:
    """Resolve the Plaid API host. Raises rather than defaulting to production
    when PLAID_ENV is unset or not one of sandbox/development/production."""
    override = os.environ.get("PLAID_ENV_URL", "").strip()
    if override:
        return override
    env = os.environ.get("PLAID_ENV", "").strip().lower()
    host = _PLAID_HOSTS.get(env)
    if host is None:
        raise RuntimeError(
            "PLAID_ENV is not set to sandbox/development/production "
            "(refusing to guess a Plaid host — an unset PLAID_ENV must never "
            "silently resolve to production). Set PLAID_ENV, or PLAID_ENV_URL "
            "for an explicit custom host."
        )
    return host


def _creds() -> tuple[str, str]:
    cid = os.environ.get("PLAID_CLIENT_ID", "").strip()
    secret = os.environ.get("PLAID_SECRET", "").strip()
    if not (cid and secret):
        raise RuntimeError("PLAID_CLIENT_ID / PLAID_SECRET are not set")
    return cid, secret


async def _post(path: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    cid, secret = _creds()
    base = _plaid_base()
    body = {"client_id": cid, "secret": secret, **payload}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}{path}", json=body)
    if r.status_code >= 400:
        # Surface Plaid's error_message but never the request body (has secrets).
        try:
            err = r.json()
            msg = err.get("error_message") or err.get("error_code") or r.text[:200]
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"plaid {path} {r.status_code}: {msg}")
    return r.json()


# --------------------------------------------------------------------- linking

async def link_token_create(owner_id: Optional[int] = None) -> dict[str, Any]:
    """Create a Plaid Link token for the current owner. `client_user_id` is the
    owner id, so Plaid associates the Link session with this user."""
    oid = owner_id if owner_id is not None else ownership.current_owner()
    payload: dict[str, Any] = {
        "user": {"client_user_id": str(oid)},
        "client_name": "Workshop",
        "products": _PRODUCTS,
        "country_codes": _COUNTRY,
        "language": "en",
    }
    if PLAID_WEBHOOK:
        payload["webhook"] = PLAID_WEBHOOK
    r = await _post("/link/token/create", payload)
    return {"link_token": r.get("link_token"), "expiration": r.get("expiration")}


async def _institution_name(institution_id: Optional[str]) -> Optional[str]:
    if not institution_id:
        return None
    try:
        r = await _post("/institutions/get_by_id", {
            "institution_id": institution_id,
            "country_codes": _COUNTRY,
        })
        return (r.get("institution") or {}).get("name")
    except Exception:
        return None


def _account_rows(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Plaid /accounts rows to the stored/served account shape."""
    out = []
    for a in accounts:
        bal = a.get("balances") or {}
        out.append({
            "id": a.get("account_id"),
            "name": a.get("name"),
            "official_name": a.get("official_name"),
            "mask": a.get("mask"),
            "type": str(a.get("type")) if a.get("type") is not None else None,
            "subtype": str(a.get("subtype")) if a.get("subtype") is not None else None,
            "balance": {
                "current": bal.get("current"),
                "available": bal.get("available"),
                "iso_currency_code": bal.get("iso_currency_code"),
                "limit": bal.get("limit"),
            },
        })
    return out


async def exchange_public_token(public_token: str, owner_id: Optional[int] = None) -> dict[str, Any]:
    """Exchange a Link public_token for an access_token, fetch the institution +
    accounts, and store the Item owner-scoped (token encrypted). Returns
    non-secret metadata for the UI."""
    oid = owner_id if owner_id is not None else ownership.current_owner()
    ex = await _post("/item/public_token/exchange", {"public_token": public_token})
    access_token = ex["access_token"]
    item_id = ex["item_id"]

    # Institution + accounts for display.
    institution_name = None
    accounts: list[dict[str, Any]] = []
    try:
        acc = await _post("/accounts/get", {"access_token": access_token})
        accounts = _account_rows(acc.get("accounts", []))
        institution_id = (acc.get("item") or {}).get("institution_id")
        institution_name = await _institution_name(institution_id)
    except Exception:
        pass

    plaid_store.upsert_item(
        item_id=item_id,
        access_token=access_token,
        institution_name=institution_name,
        accounts=accounts,
        linked_at=datetime.now(timezone.utc).isoformat(),
        owner_id=oid,
    )
    return {"item_id": item_id, "institution_name": institution_name, "accounts": accounts}


async def remove_item(item_id: str, owner_id: Optional[int] = None) -> dict[str, Any]:
    """Unlink an Item at Plaid and delete it from the owner's store."""
    oid = owner_id if owner_id is not None else ownership.current_owner()
    toks = {t["item_id"]: t["access_token"] for t in plaid_store.access_tokens(oid)}
    tok = toks.get(item_id)
    if tok:
        try:
            await _post("/item/remove", {"access_token": tok})
        except Exception:
            pass  # remove locally regardless; the token is being discarded
    removed = plaid_store.remove_item(item_id, oid)
    return {"removed": removed}


# --------------------------------------------------------------- read surface

async def balances(realtime: bool = False, owner_id: Optional[int] = None) -> dict[str, Any]:
    """{"items":[{item_id, institution_name, accounts:[...]}]} for the owner's
    linked banks. realtime=True forces a live /accounts/balance/get; otherwise the
    faster /accounts/get (Plaid's cached balances). Fetched per-item in parallel."""
    oid = owner_id if owner_id is not None else ownership.current_owner()
    items = plaid_store.access_tokens(oid)
    if not items:
        return {"items": []}
    path = "/accounts/balance/get" if realtime else "/accounts/get"

    async def one(it: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await _post(path, {"access_token": it["access_token"]})
            accounts = _account_rows(r.get("accounts", []))
            plaid_store.update_accounts(it["item_id"], accounts, oid)
            return {"item_id": it["item_id"], "institution_name": it["institution_name"], "accounts": accounts}
        except Exception as e:
            return {"item_id": it["item_id"], "institution_name": it["institution_name"], "accounts": [], "error": str(e)}

    out = await asyncio.gather(*(one(it) for it in items))
    return {"items": list(out)}


async def transactions(days: int = 30, owner_id: Optional[int] = None) -> dict[str, Any]:
    """{"transactions":[...]} across the owner's banks for the last `days`,
    sorted newest-first. Shape matches the previous bridge."""
    oid = owner_id if owner_id is not None else ownership.current_owner()
    items = plaid_store.access_tokens(oid)
    if not items:
        return {"transactions": []}
    now = datetime.now(timezone.utc)
    start = (now.timestamp() - days * 86400)
    start_date = datetime.fromtimestamp(start, tz=timezone.utc).date().isoformat()
    end_date = now.date().isoformat()

    async def one(it: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            r = await _post("/transactions/get", {
                "access_token": it["access_token"],
                "start_date": start_date,
                "end_date": end_date,
                "options": {"count": 500},
            })
        except Exception:
            return []
        rows = []
        for t in r.get("transactions", []):
            pfc = t.get("personal_finance_category") or {}
            rows.append({
                "id": t.get("transaction_id"),
                "item_id": it["item_id"],
                "institution_name": it["institution_name"],
                "account_id": t.get("account_id"),
                "date": t.get("date"),
                "name": t.get("name"),
                "merchant_name": t.get("merchant_name"),
                "amount": t.get("amount"),
                "iso_currency_code": t.get("iso_currency_code"),
                "category": pfc.get("primary") or " / ".join(t.get("category") or []),
                "category_detailed": pfc.get("detailed"),
                "pending": t.get("pending"),
            })
        return rows

    per_item = await asyncio.gather(*(one(it) for it in items))
    allrows = [r for rows in per_item for r in rows]
    allrows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"transactions": allrows}
