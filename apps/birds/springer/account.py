#!/usr/bin/env python3
"""Admin CLI to provision Springer Birds standalone-app logins.

There is NO open signup — the admin (operator) creates accounts here, on the box.
This is the invite mechanism: you make your friend's account, set a password, and
share it with them out of band (they can be given a way to change it later).

Run from the backend dir with its venv:
    .venv/bin/python apps/birds/springer/account.py set <email> <password> [--name N] [--role admin|member]
    .venv/bin/python apps/birds/springer/account.py list

The account is keyed by email, so setting a password for a given address
attaches a login to the SAME user 1 that already owns all the Workshop history.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `services` importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import birds_analyze

import auth as springer_auth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision Springer Birds logins")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="create/update a user and set their password")
    s.add_argument("email")
    s.add_argument("password")
    s.add_argument("--name", default=None)
    s.add_argument("--role", default=None, choices=["admin", "member"])

    inv = sub.add_parser("invite", help="mint a one-time invite link (invitee sets their own password)")
    inv.add_argument("email")
    inv.add_argument("--name", default=None)

    sub.add_parser("list", help="list accounts")

    args = ap.parse_args()
    if args.cmd == "list":
        for u in birds_analyze.list_users():
            has_pw = "yes" if (birds_analyze.get_user(u["id"]) and _has_password(u["id"])) else "NO"
            print(f"  #{u['id']:<3} {u['email']:<32} {u['role']:<7} name={u['name'] or '-':<16} password={has_pw}")
        return 0

    if args.cmd == "set":
        try:
            user = springer_auth.set_password(args.email, args.password,
                                              name=args.name, role=args.role)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"ok: set password for #{user['id']} {user['email']} (role={user['role']})")
        return 0

    if args.cmd == "invite":
        import os
        try:
            token = springer_auth.create_invite(args.email, name=args.name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        base = os.environ.get("SPRINGER_BASE_URL", "https://birds.example.com").rstrip("/")
        print(f"Invite for {args.email.strip().lower()} (valid {springer_auth.INVITE_TTL_DAYS} days). Send them this link:")
        print(f"  {base}/invite/{token}")
        return 0
    return 1


def _has_password(user_id: int) -> bool:
    with birds_analyze._conn() as c:
        r = c.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(r and r["password_hash"])


if __name__ == "__main__":
    raise SystemExit(main())
