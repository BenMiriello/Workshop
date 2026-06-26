"""Per-owner Plaid Item store — self-contained in Workshop, owner-scoped.

Each Workshop user links their OWN banks; their Plaid Items (one per
institution) live under their own runtime partition:

    .runtime/users/<owner_id>/plaid-items.json   (0600)

The Plaid `access_token` is a bank-read credential, so it is ENCRYPTED at rest
with Fernet (key from `WORKSHOP_PLAID_ENC_KEY`). The token is never returned to a
client and never logged. `list_items()` returns only the non-secret metadata the
UI needs; `access_tokens()` (decrypted) is for the server-side Plaid client only.

There is no shared/global file and no "main assistant" special case: ownership is
the authenticated user, resolved from the request via services.ownership. This
replaces the old delegation to the external ~/clawdbot Plaid MCP.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from services import ownership


class PlaidKeyMissing(RuntimeError):
    """Raised when WORKSHOP_PLAID_ENC_KEY is unset — we refuse to store or read a
    bank credential without encryption (fail closed)."""


def _fernet() -> Fernet:
    key = os.environ.get("WORKSHOP_PLAID_ENC_KEY", "").strip()
    if not key:
        raise PlaidKeyMissing(
            "WORKSHOP_PLAID_ENC_KEY is not set; cannot encrypt/decrypt Plaid "
            "access tokens. Generate one with Fernet.generate_key() and add it to "
            "~/.config/workshop/secrets.env."
        )
    return Fernet(key.encode())


def _path(owner_id: Optional[int] = None) -> Path:
    """The owner's plaid-items.json (created dir 0700, file 0600)."""
    d = ownership.user_runtime_dir(owner_id)
    return d / "plaid-items.json"


def _load(owner_id: Optional[int] = None) -> dict[str, Any]:
    p = _path(owner_id)
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": []}


def _save(data: dict[str, Any], owner_id: Optional[int] = None) -> None:
    p = _path(owner_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — bank credentials
    tmp.replace(p)


def list_items(owner_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Non-secret metadata for the owner's linked institutions (no access_token)."""
    out = []
    for it in _load(owner_id).get("items", []):
        out.append({
            "item_id": it.get("item_id"),
            "institution_name": it.get("institution_name"),
            "accounts": it.get("accounts", []),
            "linked_at": it.get("linked_at"),
        })
    return out


def access_tokens(owner_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Decrypted [{item_id, institution_name, access_token}] for the Plaid client.
    Server-side only. Skips any item whose token won't decrypt (key rotated)."""
    f = _fernet()
    out = []
    for it in _load(owner_id).get("items", []):
        enc = it.get("access_token_enc")
        if not enc:
            continue
        try:
            tok = f.decrypt(enc.encode()).decode()
        except InvalidToken:
            continue
        out.append({
            "item_id": it.get("item_id"),
            "institution_name": it.get("institution_name"),
            "access_token": tok,
        })
    return out


def has_items(owner_id: Optional[int] = None) -> bool:
    return bool(_load(owner_id).get("items"))


def upsert_item(
    item_id: str,
    access_token: str,
    institution_name: Optional[str],
    accounts: Optional[list[dict[str, Any]]] = None,
    linked_at: Optional[str] = None,
    owner_id: Optional[int] = None,
) -> None:
    """Add or replace an item (by item_id) in the owner's store, token encrypted."""
    f = _fernet()
    data = _load(owner_id)
    items = [it for it in data.get("items", []) if it.get("item_id") != item_id]
    items.append({
        "item_id": item_id,
        "institution_name": institution_name,
        "accounts": accounts or [],
        "linked_at": linked_at,
        "access_token_enc": f.encrypt(access_token.encode()).decode(),
    })
    data["items"] = items
    _save(data, owner_id)


def update_accounts(item_id: str, accounts: list[dict[str, Any]], owner_id: Optional[int] = None) -> None:
    data = _load(owner_id)
    for it in data.get("items", []):
        if it.get("item_id") == item_id:
            it["accounts"] = accounts
    _save(data, owner_id)


def remove_item(item_id: str, owner_id: Optional[int] = None) -> bool:
    data = _load(owner_id)
    before = len(data.get("items", []))
    data["items"] = [it for it in data.get("items", []) if it.get("item_id") != item_id]
    _save(data, owner_id)
    return len(data["items"]) < before
