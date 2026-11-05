"""Signed claims a turn's tool server calls carry: who the caller is.

The workshop tool server is served by the backend over HTTP
(`routes/mcp_workshop.py`) and reached from inside any turn's sandbox over
loopback. A call proves which turn it belongs to by carrying the claims the
backend minted when it built that turn's tool configuration: the same values
the stdio transport puts in its child's environment (scope, owner, chat, turn,
tool confinement, page access), signed, with an expiry.

The key lives in the runtime root so a turn adopted across a backend restart
keeps calling with the claims it was started with. Nothing here is secret
beyond the signature: a turn that reads its own claims learns only what its
tools already know.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from services import stores as _stores

# Long enough for a turn adopted across restarts, short enough that a claim
# copied out of a finished turn stops working on its own.
TTL_S = 7 * 24 * 3600

_KEY: bytes | None = None


def _key() -> bytes:
    global _KEY
    if _KEY is None:
        path = _stores._runtime_root() / "turn-claims.key"
        try:
            _KEY = path.read_bytes()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                _KEY = path.read_bytes()
                return _KEY
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            _KEY = key
    return _KEY


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(payload: str) -> str:
    return hmac.new(_key(), f"turn-claims:{payload}".encode(), hashlib.sha256).hexdigest()


def mint(claims: dict[str, str], ttl_s: int = TTL_S) -> str:
    body = {"c": {str(k): str(v) for k, v in claims.items()}, "exp": int(time.time()) + ttl_s}
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_mac(payload)}"


def verify(token: Optional[str]) -> Optional[dict[str, str]]:
    """The claims a token carries, or None when it is not ours, altered or expired."""
    payload, _, mac = (token or "").partition(".")
    if not payload or not mac or not hmac.compare_digest(_mac(payload), mac):
        return None
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict) or int(body.get("exp") or 0) < time.time():
        return None
    claims = body.get("c")
    return {str(k): str(v) for k, v in claims.items()} if isinstance(claims, dict) else None
