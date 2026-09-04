"""Idempotent migration to per-user Plaid + budget (one place, in Workshop).

Run once after deploying the self-contained Plaid integration. It:
  1. ensures WORKSHOP_PLAID_ENC_KEY exists in ~/.config/workshop/secrets.env
     (Fernet key for encrypting Plaid access tokens at rest);
  2. moves the operator's global budget.json + txn-overrides.json into their
     per-owner partition (.runtime/users/<op>/);
  3. imports the operator's existing Plaid Items (from the old ~/clawdbot Plaid
     token file) into the operator's encrypted Workshop store, so finance keeps
     working WITHOUT a re-link. Skips if the operator already has items.

Safe to run repeatedly. Reads the operator id from auth_store (the recorded
operator), never from email at request time.

Usage:  backend/.venv/bin/python -m scripts.migrate_plaid_per_user
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet
from wsplatform import paths

SECRETS = Path(os.path.expanduser("~/.config/workshop/secrets.env"))
RUNTIME = paths.runtime_dir()
LEGACY_PLAID_TOKENS = paths.env_path("PLAID_TOKENS_FILE", "plaid-tokens.json", base="config")


def ensure_enc_key() -> str:
    """Ensure WORKSHOP_PLAID_ENC_KEY is in secrets.env; return it. Generates a
    new Fernet key if absent and also exports it into this process's env."""
    existing = os.environ.get("WORKSHOP_PLAID_ENC_KEY", "").strip()
    if existing:
        return existing
    text = SECRETS.read_text() if SECRETS.exists() else ""
    for line in text.splitlines():
        if line.startswith("WORKSHOP_PLAID_ENC_KEY="):
            val = line.split("=", 1)[1].strip()
            if val:
                os.environ["WORKSHOP_PLAID_ENC_KEY"] = val
                return val
    key = Fernet.generate_key().decode()
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    with SECRETS.open("a") as f:
        f.write(f"\n# Encrypts Plaid access tokens at rest (per-user finance)\nWORKSHOP_PLAID_ENC_KEY={key}\n")
    os.chmod(SECRETS, stat.S_IRUSR | stat.S_IWUSR)
    os.environ["WORKSHOP_PLAID_ENC_KEY"] = key
    print(f"generated WORKSHOP_PLAID_ENC_KEY -> {SECRETS}")
    return key


def move_global_budget(op_id: int) -> None:
    user_dir = RUNTIME / "users" / str(op_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    for name in ("budget.json", "txn-overrides.json"):
        src = RUNTIME / name
        dst = user_dir / name
        if src.exists() and not dst.exists():
            src.replace(dst)
            print(f"moved {src} -> {dst}")
        elif src.exists() and dst.exists():
            print(f"skip {name}: both global and per-owner exist (leaving per-owner)")


def import_legacy_items(op_id: int) -> None:
    from services import ownership, finance_plaid_store as plaid_store
    ownership.set_current_owner(op_id)
    if plaid_store.has_items(op_id):
        print("operator already has Plaid items in the Workshop store; skipping import")
        return
    if not LEGACY_PLAID_TOKENS.exists():
        print(f"no legacy Plaid tokens at {LEGACY_PLAID_TOKENS}; nothing to import")
        return
    data = json.loads(LEGACY_PLAID_TOKENS.read_text())
    items = data.get("items", []) or []
    n = 0
    for it in items:
        tok = it.get("access_token")
        iid = it.get("item_id")
        if not (tok and iid):
            continue
        plaid_store.upsert_item(
            item_id=iid,
            access_token=tok,
            institution_name=it.get("institution_name"),
            accounts=it.get("accounts", []),
            owner_id=op_id,
        )
        n += 1
    print(f"imported {n} Plaid item(s) into the operator's encrypted Workshop store")


def main() -> None:
    ensure_enc_key()
    from services import auth_store
    op_id = auth_store.operator_id()
    if op_id is None:
        print("no operator recorded (run migrate_to_owned first); aborting")
        return
    print(f"operator id = {op_id}")
    move_global_budget(op_id)
    import_legacy_items(op_id)
    print("done.")


if __name__ == "__main__":
    main()
