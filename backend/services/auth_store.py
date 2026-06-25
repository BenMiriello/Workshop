"""User + refresh-token store for the Workshop product-auth layer.

Workshop is being repositioned as a real, deployable product app (operator
decision 2026-06-24), so it grows proper accounts: this module owns the `users`
and `refresh_tokens` tables in a SQLite DB. Passwords are hashed with the stdlib
scrypt KDF (memory-hard, no compiled dependency to ship to a production host).

Scope note: the system stays single-owner for now (data is partitioned per-user
incrementally, app by app). The *operator* is a distinguished user — the first
account created, or the one whose email matches WORKSHOP_OPERATOR_EMAIL — and
only the operator reaches the existing single-owner routers (finance, location,
etc.). New accounts get a valid session but no operator data until an app is
migrated to per-user ownership.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
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
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                jti        TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                issued_at  TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)"
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


class EmailTaken(Exception):
    """Raised when registering an email that already has an account."""


def create_user(email: str, password: str, name: Optional[str] = None) -> dict[str, Any]:
    """Create a user. The first account, or one matching WORKSHOP_OPERATOR_EMAIL,
    is flagged as the operator."""
    init_db()
    email = email.strip()
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")

    operator_email = os.environ.get("WORKSHOP_OPERATOR_EMAIL", "").strip().lower()
    with _connect() as conn:
        existing_ops = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE is_operator = 1"
        ).fetchone()["c"]
        # Operator = explicit env match, or (when none configured) the first user.
        if operator_email:
            is_operator = email.lower() == operator_email
        else:
            is_operator = existing_ops == 0
        try:
            cur = conn.execute(
                "INSERT INTO users (email, name, pw_hash, is_operator, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, name, hash_password(password), 1 if is_operator else 0, _now()),
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


# ---------------------------------------------------------------- refresh tokens


def store_refresh(jti: str, user_id: int, expires_at: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO refresh_tokens (jti, user_id, issued_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, 0)",
            (jti, user_id, _now(), expires_at),
        )


def refresh_is_valid(jti: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT expires_at, revoked FROM refresh_tokens WHERE jti = ?", (jti,)
        ).fetchone()
    if row is None or row["revoked"]:
        return False
    try:
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
    except ValueError:
        return False


def revoke_refresh(jti: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE jti = ?", (jti,))


def revoke_all_for_user(user_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
