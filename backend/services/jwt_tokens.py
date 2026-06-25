"""Access + refresh JWT issuance/verification for the product-auth layer.

HS256 over a signing secret from WORKSHOP_JWT_SECRET. For local/dev runs where
that env is unset we persist a generated secret to `.runtime/jwt-secret` so a
backend restart doesn't silently invalidate every session; a production deploy
sets the env explicitly. Access tokens are short-lived; refresh tokens are
long-lived and individually revocable (their `jti` is tracked in auth_store).
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import jwt

_ALGO = "HS256"
ACCESS_TTL = timedelta(minutes=int(os.environ.get("WORKSHOP_ACCESS_TTL_MIN", "30")))
REFRESH_TTL = timedelta(days=int(os.environ.get("WORKSHOP_REFRESH_TTL_DAYS", "30")))

_SECRET_FILE = Path(__file__).resolve().parents[2] / ".runtime" / "jwt-secret"


def _load_secret() -> str:
    env = os.environ.get("WORKSHOP_JWT_SECRET", "").strip()
    if env:
        return env
    try:
        return _SECRET_FILE.read_text().strip()
    except OSError:
        pass
    # Generate-and-persist so dev sessions survive restarts. 0600 so it isn't
    # world-readable on a shared host.
    secret = secrets.token_urlsafe(48)
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(secret)
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


# Resolved once at import. (Tests that need a fresh secret set the env first.)
_SECRET = _load_secret()


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or the wrong type."""


def _encode(claims: dict[str, Any], ttl: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def make_access(user: dict[str, Any]) -> str:
    return _encode(
        {"sub": str(user["id"]), "email": user["email"], "op": bool(user.get("is_operator"))},
        ACCESS_TTL,
        "access",
    )


def make_refresh(user: dict[str, Any]) -> tuple[str, str, str]:
    """Return (token, jti, expires_at_iso) so the caller can persist the jti."""
    jti = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + REFRESH_TTL
    token = _encode({"sub": str(user["id"]), "jti": jti}, REFRESH_TTL, "refresh")
    return token, jti, expires_at.isoformat()


def decode(token: str, expected_type: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token")
    return claims


def access_ttl_seconds() -> int:
    return int(ACCESS_TTL.total_seconds())


def claims_to_identity(claims: dict[str, Any]) -> dict[str, Any]:
    """Map verified access-token claims to a lightweight identity dict."""
    return {
        "id": int(claims["sub"]),
        "email": claims.get("email"),
        "is_operator": bool(claims.get("op")),
    }


def optional_secret_for_test() -> Optional[str]:
    """Test hook: expose the resolved secret so suites can sign fixtures."""
    return _SECRET
