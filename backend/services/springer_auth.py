"""Standalone Springer Birds auth — app-native login for the public deployment.

The private Workshop app at /apps/birds is tailnet-gated and uses NONE of this:
it has no login, the operator just reaches it over Tailscale. This module backs
the SEPARATE hardened process (springer_app.py on :8001, behind the Cloudflare
tunnel at birds.benmiriello.com) where a real second person logs in.

Design (stdlib only — the box's egress guard blocks pip, and we don't need it):
  - Passwords: hashlib.scrypt (memory-hard, salted). Stored on users.password_hash
    as "scrypt$n$r$p$salt_hex$hash_hex". Verified in constant time.
  - Sessions: a 256-bit secrets token in the `sessions` table, bounded by
    expires_at. The cookie that carries it is set HttpOnly+Secure+SameSite=Strict
    by springer_app, so it never reaches JS and never rides a cross-site request.
  - Accounts are admin-created (scripts/springer_account.py) — there is no open
    signup endpoint to attack. This is the genuinely-shared carve-out to the
    no-multi-tenant-cruft rule, not single-user cruft.

Everything lives in the SAME browser-stations.db as the rest of the bird data, so
a login here is the same account (matched by email) as user 1 in the Workshop app.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from services import birds_analyze

# scrypt cost. n=2**14 (~16 MB, ~tens of ms here) is expensive to brute-force and
# stays under OpenSSL's default 32 MB maxmem; r/p standard. dklen 32 = 256-bit.
# We pass maxmem explicitly so a future cost bump can't trip the default ceiling.
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MAXMEM = 128 * 1024 * 1024

SESSION_TTL_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def hash_password(password: str) -> str:
    """Return a self-describing scrypt digest string for `password`."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of `password` against a stored scrypt digest."""
    if not stored or not password:
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2, maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def set_password(email: str, password: str, name: str | None = None,
                 role: str | None = None) -> dict[str, Any]:
    """Create the user if needed (by email) and set their login password. The
    admin CLI uses this to provision both the operator's and the friend's logins."""
    user = birds_analyze.upsert_user(email, name=name, role=role)
    digest = hash_password(password)
    with birds_analyze._conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (digest, user["id"]))
        c.commit()
    return user


def login(email: str, password: str, user_agent: str | None = None) -> dict[str, Any] | None:
    """Validate credentials and mint a session. Returns {token, user, expires_at}
    or None on bad credentials (caller must NOT reveal which field was wrong)."""
    email = (email or "").strip().lower()
    with birds_analyze._conn() as c:
        row = c.execute(
            "SELECT id,email,name,role,password_hash FROM users WHERE email=?", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        created = _now()
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
                   ).isoformat().replace("+00:00", "Z")
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at,user_agent) "
                  "VALUES(?,?,?,?,?)", (token, row["id"], created, expires, (user_agent or "")[:300]))
        c.commit()
    return {"token": token, "expires_at": expires,
            "user": {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}}


def session_user(token: str | None) -> dict[str, Any] | None:
    """Resolve a session token to its (live, unexpired) user, or None."""
    if not token:
        return None
    with birds_analyze._conn() as c:
        s = c.execute("SELECT user_id,expires_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not s:
            return None
        if s["expires_at"] and s["expires_at"] < _now():
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
            c.commit()
            return None
        u = c.execute("SELECT id,email,name,role FROM users WHERE id=?", (s["user_id"],)).fetchone()
    return dict(u) if u else None


def logout(token: str | None) -> None:
    if not token:
        return
    with birds_analyze._conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))
        c.commit()


def purge_expired() -> int:
    """Drop expired sessions. Cheap housekeeping the app can call on login."""
    with birds_analyze._conn() as c:
        n = c.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),)).rowcount
        c.commit()
    return n


# --- invites: admin mints a one-time link; the invitee sets their own password ---

INVITE_TTL_DAYS = 14


def create_invite(email: str, name: str | None = None) -> str:
    """Mint a one-time invite token for `email`. Returned token goes into a URL
    the admin shares out of band; the invitee redeems it to set their password."""
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email required")
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
               ).isoformat().replace("+00:00", "Z")
    with birds_analyze._conn() as c:
        c.execute("INSERT INTO invites(token,email,name,created_at,expires_at,used_at) "
                  "VALUES(?,?,?,?,?,NULL)", (token, email, (name or "").strip() or None, _now(), expires))
        c.commit()
    return token


def invite_info(token: str | None) -> dict | None:
    """Return {email, name} for a valid, unused, unexpired invite, else None."""
    if not token:
        return None
    with birds_analyze._conn() as c:
        r = c.execute("SELECT email,name,expires_at,used_at FROM invites WHERE token=?",
                      (token,)).fetchone()
    if not r or r["used_at"] or (r["expires_at"] and r["expires_at"] < _now()):
        return None
    return {"email": r["email"], "name": r["name"]}


def redeem_invite(token: str, password: str, name: str | None = None,
                  user_agent: str | None = None) -> dict | None:
    """Consume an invite: set the invitee's chosen password (creating their member
    account by email) and return a fresh login session. None if the invite is
    invalid/used/expired."""
    info = invite_info(token)
    if not info:
        return None
    set_password(info["email"], password, name=(name or info["name"]), role="member")
    with birds_analyze._conn() as c:
        c.execute("UPDATE invites SET used_at=? WHERE token=? AND used_at IS NULL", (_now(), token))
        c.commit()
    return login(info["email"], password, user_agent=user_agent)
