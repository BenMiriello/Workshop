"""Plaid webhook JWT signature verification — full crypto, not shape-only.

Plaid signs every webhook with an ES256 JWT in the `Plaid-Verification` header.
The JWT's body has `request_body_sha256` (hex sha256 of the raw POST body).

Verify protocol per Plaid docs:
  1. Parse the JWT header to get `kid` (verification key id).
  2. POST /webhook_verification_key/get with {key_id: kid} to Plaid → returns
     a JWK with the public key + status.
  3. Reject if key.expired_at is not null (key was rotated; refuse webhooks
     signed by an expired key — replay risk).
  4. Verify JWT signature (ES256, kty=EC, crv=P-256) using the public key.
  5. Compare `request_body_sha256` claim to actual sha256 of the request body.
  6. Reject if iat is older than 5 minutes (replay protection).

Cache keys by `kid` indefinitely in-process — Plaid only rotates rarely; on
cache miss we fetch. (No need to invalidate: a new kid means a new fetch.)
"""
from __future__ import annotations

import hashlib
import os
import time

import httpx
import jwt
from jwt.algorithms import ECAlgorithm


# PLAID_ENV picks the Plaid host: sandbox/development/production. PLAID_ENV_URL
# is an explicit override for anyone who needs a custom host and always wins.
# There is deliberately no default — see _plaid_base() — because guessing
# production for an unset/unrecognized PLAID_ENV would silently verify
# webhooks against the wrong environment.
_PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}
PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.environ.get("PLAID_SECRET", "")
MAX_AGE_S = 300  # 5 min — replay protection

# Process-local key cache. {kid: (jwk_dict, fetched_at_ts)}.
_key_cache: dict[str, tuple[dict, float]] = {}


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
            "(refusing to guess a Plaid host). Set PLAID_ENV, or PLAID_ENV_URL "
            "for an explicit custom host."
        )
    return host


def _fetch_key(kid: str) -> dict | None:
    """POST /webhook_verification_key/get → returns the JWK or None on error.

    Any misconfiguration (missing creds, unset/unknown PLAID_ENV) also
    returns None here rather than raising: this is on the webhook-receive
    path, where the right failure mode is "drop the webhook", not a 500.
    """
    if not (PLAID_CLIENT_ID and PLAID_SECRET):
        return None
    try:
        r = httpx.post(
            f"{_plaid_base()}/webhook_verification_key/get",
            json={"client_id": PLAID_CLIENT_ID, "secret": PLAID_SECRET, "key_id": kid},
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        body = r.json()
        key = body.get("key") or {}
        # Refuse expired keys (Plaid rotates; expired key = replay risk).
        if key.get("expired_at"):
            return None
        return key
    except Exception:
        return None


def verify(plaid_verification_header: str, body_bytes: bytes) -> tuple[bool, str]:
    """Returns (ok, reason). reason is human-readable, suitable for the
    /events/{source} 401 response detail."""
    if not plaid_verification_header or plaid_verification_header.count(".") != 2:
        return False, "missing_or_malformed_plaid_verification_jwt"

    # Decode header without verifying to get kid.
    try:
        hdr = jwt.get_unverified_header(plaid_verification_header)
    except Exception:
        return False, "jwt_header_unparseable"
    kid = hdr.get("kid")
    alg = hdr.get("alg")
    if alg != "ES256":
        return False, f"unexpected_alg={alg}_expected_ES256"
    if not kid:
        return False, "jwt_header_missing_kid"

    # Look up verification key.
    cached = _key_cache.get(kid)
    if cached is None:
        jwk = _fetch_key(kid)
        if jwk is None:
            # Plaid creds not set OR Plaid rejected the key id. Refuse —
            # better to drop a webhook than accept an unverifiable one.
            return False, f"verification_key_unavailable_for_kid={kid}"
        _key_cache[kid] = (jwk, time.time())
    else:
        jwk, _fetched = cached

    # Construct the public key for ES256 verify.
    try:
        pub = ECAlgorithm.from_jwk(jwk)
    except Exception as e:
        return False, f"jwk_unloadable: {type(e).__name__}"

    # Verify signature + standard claims.
    try:
        payload = jwt.decode(
            plaid_verification_header,
            pub,
            algorithms=["ES256"],
            options={"require": ["iat", "request_body_sha256"]},
        )
    except jwt.ExpiredSignatureError:
        return False, "jwt_expired"
    except jwt.InvalidSignatureError:
        return False, "jwt_invalid_signature"
    except Exception as e:
        return False, f"jwt_verify_failed: {type(e).__name__}"

    # Replay protection — refuse webhooks older than MAX_AGE_S.
    now = int(time.time())
    iat = int(payload.get("iat", 0))
    if now - iat > MAX_AGE_S:
        return False, f"jwt_too_old age={now - iat}s max={MAX_AGE_S}s"

    # Body integrity — sha256 of POST body must match the claim.
    expected_sha = payload.get("request_body_sha256", "")
    actual_sha = hashlib.sha256(body_bytes).hexdigest()
    if expected_sha != actual_sha:
        return False, "body_sha_mismatch"

    return True, "ok_full_crypto_verify"
