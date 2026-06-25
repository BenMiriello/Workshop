#!/usr/bin/env python3
"""One-time, idempotent bootstrap for the auth + ownership redesign.

This is the ONLY place the operator's email is consulted to assign ownership;
after it runs, nothing at request time branches on "is this the operator". Run
once on the deployment box (re-running is a no-op):

    cd backend && .venv/bin/python scripts/migrate_to_owned.py

It (1) flags the operator account (by WORKSHOP_OPERATOR_EMAIL) as the admin and
the data owner of the single-dataset domains, (2) moves the operator's existing
per-user files into their partition, and (3) mints the operator-owned service /
ingest tokens the machine callers need, writing them to
~/.config/workshop/secrets.env (0600).

No WORKSHOP_SESSION_SECRET is needed: sessions are opaque server-side tokens, not
signed cookies, so there is no signing secret to manage.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from services import auth_store, ownership  # noqa: E402

RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", "/home/clawd-a/workshop/.runtime"))
SECRETS = Path(
    os.environ.get("WORKSHOP_SECRETS_ENV", str(Path.home() / ".config/workshop/secrets.env"))
)

# Per-user flat-file stores to relocate into .runtime/users/<owner_id>/.
_FILE_MOVES = ["location.jsonl", "location-latest.json", "health.jsonl", "health-latest.json",
               "push-tokens.json"]
# Single-dataset domains owned by the operator (finance/budget share "finance").
_DOMAINS = ["finance", "commute", "location", "health"]
# Operator-owned machine credentials: (env var name, scope).
_TOKENS = [
    ("WORKSHOP_SVC_COMMUTE", "automation:commute"),
]


def _log(msg: str) -> None:
    print(f"[migrate] {msg}")


def ensure_operator() -> int:
    email = os.environ.get("WORKSHOP_OPERATOR_EMAIL", "").strip()
    if not email:
        _log("ERROR: WORKSHOP_OPERATOR_EMAIL is not set; cannot identify the operator.")
        sys.exit(2)
    user = auth_store.get_by_email(email)
    if user is None:
        _log(f"ERROR: no account for {email}. Register it first (POST /auth/register), then re-run.")
        sys.exit(2)
    auth_store.set_operator(user["id"])
    _log(f"operator = {email} (id={user['id']}), is_operator set")
    return user["id"]


def assign_domains(owner_id: int) -> None:
    for d in _DOMAINS:
        if auth_store.domain_owner(d) == owner_id:
            continue
        auth_store.set_domain_owner(d, owner_id)
        _log(f"domain '{d}' -> owner {owner_id}")


def move_files(owner_id: int) -> None:
    dest = ownership.user_runtime_dir(owner_id)
    for name in _FILE_MOVES:
        src = RUNTIME / name
        dst = dest / name
        if dst.exists():
            continue  # already migrated
        if src.exists():
            shutil.move(str(src), str(dst))
            _log(f"moved {name} -> users/{owner_id}/")
    # chat threads
    src_chat = RUNTIME / "chat"
    dst_chat = dest / "chat"
    if src_chat.is_dir() and not dst_chat.exists():
        shutil.move(str(src_chat), str(dst_chat))
        _log(f"moved chat/ -> users/{owner_id}/chat/")


def _read_secrets() -> str:
    try:
        return SECRETS.read_text()
    except OSError:
        return ""


def mint_tokens(owner_id: int) -> None:
    existing = _read_secrets()
    lines: list[str] = []
    for env_name, scope in _TOKENS:
        if f"{env_name}=" in existing:
            continue  # already minted; don't rotate on re-run
        raw = auth_store.create_service_token(owner_id, scope, label=env_name)
        lines.append(f"{env_name}={raw}")
        _log(f"minted {env_name} (scope {scope})")
    if not lines:
        _log("service tokens already present; nothing minted")
        return
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    body = existing
    if body and not body.endswith("\n"):
        body += "\n"
    body += "# auth-redesign machine tokens (operator-owned)\n" + "\n".join(lines) + "\n"
    SECRETS.write_text(body)
    os.chmod(SECRETS, 0o600)
    _log(f"wrote {len(lines)} token(s) to {SECRETS}")


def main() -> None:
    auth_store.init_db()
    owner_id = ensure_operator()
    assign_domains(owner_id)
    move_files(owner_id)
    mint_tokens(owner_id)
    _log("done. Re-running is a no-op.")


if __name__ == "__main__":
    main()
