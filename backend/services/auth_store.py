"""User store + opaque credentials for the Workshop auth layer.

Workshop is a deployable product app (operator decision 2026-06-24) and is being
hardened for public exposure (auth redesign 2026-06-25, docs/auth-redesign/).
Authentication is by IDENTITY, never network position, and data is owned per
user. This module owns the `users` table plus three opaque-credential tables in
a SQLite DB:

  * sessions       — browser / WebView login cookies (HttpOnly, revocable)
  * device_tokens  — native background producers (location/audio), revocable
  * service_tokens — machine callers (cron, ingest webhooks, the Letta sandbox),
                     each bound to a user AND a scope (least privilege)

Only the SHA-256 of each token is stored, so a DB read never yields a usable
credential; the raw token is returned once, at mint time. Passwords use the
stdlib scrypt KDF (memory-hard, no compiled dependency to ship to a host).

Ownership is data, not configuration: the operator account is just the user
flagged `is_operator` (set ONCE by the bootstrap migration, never at request
time). `is_operator` governs platform/infra access only; it grants NO access to
another user's personal data.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Default under the gitignored runtime dir; override for tests / prod via env.
DB_PATH = Path(
    os.environ.get(
        "WORKSHOP_AUTH_DB",
        str(Path(__file__).resolve().parents[2] / ".runtime" / "auth.db"),
    )
)

# scrypt cost parameters. n=2**15 is comfortably above interactive-login cost on
# a server while staying well under a second per hash.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

SESSION_TTL_DAYS = int(os.environ.get("WORKSHOP_SESSION_TTL_DAYS", "30"))


def _scrypt_maxmem(n: int, r: int, p: int) -> int:
    """scrypt needs ~128*n*r*p bytes; OpenSSL's default cap (32 MiB) is below
    that for n=2**15, so pass an explicit ceiling with headroom."""
    return 128 * n * r * p + (1 << 20)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=_scrypt_maxmem(n, r, p),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw: str) -> str:
    """SHA-256 hex of an opaque token. We store only this, never the raw token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name        TEXT,
                pw_hash     TEXT NOT NULL,
                is_operator INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                user_agent TEXT,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label      TEXT,
                created_at TEXT NOT NULL,
                last_seen  TEXT,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                scope      TEXT NOT NULL,
                label      TEXT,
                created_at TEXT NOT NULL,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        for tbl in ("sessions", "device_tokens", "service_tokens"):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{tbl}_user ON {tbl}(user_id)"
            )


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """Return a self-describing `scrypt$n$r$p$salt$hash` string."""
    salt = secrets.token_bytes(16)
    dk = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        dk = _scrypt(
            password, bytes.fromhex(salt_hex), int(n), int(r), int(p), len(expected)
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- users


def _row_to_user(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "is_operator": bool(row["is_operator"]),
        "created_at": row["created_at"],
    }


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Strip anything sensitive before returning a user over the wire."""
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "is_operator": bool(user.get("is_operator")),
    }


def count_users() -> int:
    init_db()
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


def get_by_email(email: str) -> Optional[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()
        return _row_to_user(row)


def get_by_id(user_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def operator_id() -> Optional[int]:
    """The id of the operator account, or None if bootstrap hasn't run. This is a
    DATA lookup, not a privilege check — used by automation set-points and the
    one-time migration. No request path branches on it to grant data access."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE is_operator = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None


def set_operator(user_id: int) -> None:
    """Flag exactly one account as operator (the rest demoted). Called ONLY by
    the bootstrap migration — never from a request handler."""
    init_db()
    with _connect() as conn:
        conn.execute("UPDATE users SET is_operator = 0")
        conn.execute("UPDATE users SET is_operator = 1 WHERE id = ?", (user_id,))


class EmailTaken(Exception):
    """Raised when registering an email that already has an account."""


def create_user(email: str, password: str, name: Optional[str] = None) -> dict[str, Any]:
    """Create a plain member account. Registration NEVER confers operator status:
    `is_operator` is set only by the bootstrap migration (set_operator). This is
    what stops 'anyone who registers the operator's email gets their setup'."""
    init_db()
    email = email.strip()
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")

    with _connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (email, name, pw_hash, is_operator, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (email, name, hash_password(password), _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise EmailTaken(email) from exc
        user_id = cur.lastrowid
    created = get_by_id(int(user_id))
    assert created is not None
    return created


def update_password(user_id: int, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("password must be at least 8 characters")
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET pw_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )


def verify_credentials(email: str, password: str) -> Optional[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()
    if row is None:
        # Hash anyway to keep timing roughly constant against email enumeration.
        verify_password(password, "scrypt$32768$8$1$" + "00" * 16 + "$" + "00" * 32)
        return None
    if not verify_password(password, row["pw_hash"]):
        return None
    return _row_to_user(row)


# ---------------------------------------------------------------- sessions (cookie)


def create_session(user_id: int, user_agent: Optional[str] = None,
                   ttl_days: int = SESSION_TTL_DAYS) -> str:
    """Mint an opaque session token (the browser/WebView cookie value). Returns
    the raw token; only its hash is persisted."""
    init_db()
    raw = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (_hash_token(raw), user_id, _now(), expires, (user_agent or "")[:300]),
        )
    return raw


def session_user(raw_token: Optional[str]) -> Optional[dict[str, Any]]:
    """Resolve a session cookie to its live, unexpired, unrevoked user, or None."""
    if not raw_token:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, revoked FROM sessions WHERE token_hash = ?",
            (_hash_token(raw_token),),
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
        urow = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    return _row_to_user(urow)


def revoke_session(raw_token: Optional[str]) -> None:
    if not raw_token:
        return
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (_hash_token(raw_token),)
        )


# ---------------------------------------------------------------- device tokens (native)


def create_device_token(user_id: int, label: Optional[str] = None) -> str:
    """Mint an opaque per-device bearer token for native background producers."""
    init_db()
    raw = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO device_tokens (token_hash, user_id, label, created_at, last_seen, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (_hash_token(raw), user_id, (label or "device")[:120], _now(), _now()),
        )
    return raw


def device_user(raw_token: Optional[str]) -> Optional[dict[str, Any]]:
    """Resolve a device token to its user (touching last_seen), or None."""
    if not raw_token:
        return None
    init_db()
    th = _hash_token(raw_token)
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, revoked FROM device_tokens WHERE token_hash = ?", (th,)
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        conn.execute("UPDATE device_tokens SET last_seen = ? WHERE token_hash = ?", (_now(), th))
        urow = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    return _row_to_user(urow)


def list_devices(user_id: int) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT token_hash, label, created_at, last_seen FROM device_tokens "
            "WHERE user_id = ? AND revoked = 0 ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [
        {"id": r["token_hash"][:12], "label": r["label"], "created_at": r["created_at"],
         "last_seen": r["last_seen"]}
        for r in rows
    ]


def revoke_device_by_prefix(user_id: int, id_prefix: str) -> int:
    """Revoke a device the user owns, identified by the short id from list_devices."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE device_tokens SET revoked = 1 "
            "WHERE user_id = ? AND substr(token_hash,1,12) = ? AND revoked = 0",
            (user_id, id_prefix),
        )
        return cur.rowcount


# ---------------------------------------------------------------- service tokens (machines)


def create_service_token(user_id: int, scope: str, label: Optional[str] = None) -> str:
    """Mint an opaque service token bound to a user AND a scope (e.g.
    'ingest:location', 'automation:commute'). Returns the raw token once."""
    if not scope:
        raise ValueError("a service token requires a scope")
    init_db()
    raw = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO service_tokens (token_hash, user_id, scope, label, created_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (_hash_token(raw), user_id, scope, (label or scope)[:120], _now()),
        )
    return raw


def service_identity(raw_token: Optional[str]) -> Optional[dict[str, Any]]:
    """Resolve a service token to {user, scope}, or None. The caller checks scope."""
    if not raw_token:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, scope, revoked FROM service_tokens WHERE token_hash = ?",
            (_hash_token(raw_token),),
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        urow = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    user = _row_to_user(urow)
    if user is None:
        return None
    return {"user": user, "scope": row["scope"]}


def revoke_all_for_user(user_id: int) -> None:
    """Sign out everywhere: revoke every session AND device token. Service tokens
    are infrastructure (cron/ingest) and are managed separately, so they are not
    swept here."""
    init_db()
    with _connect() as conn:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE device_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
