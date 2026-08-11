"""Encryption at rest for the secrets store. Dormant until a key is configured.

WHY IT IS OPTIONAL. On a one-person box, mode 0600 in the operator's own home
directory is the correct amount of protection: anything that can read the file
is already running as the operator and could read the process memory holding the
decrypted value anyway. Encrypting there buys nothing and costs a key to lose.

WHY IT EXISTS ANYWAY. The moment a SECOND person's API key is in the same store,
that reasoning stops holding: the file is now a pile of other people's
credentials, it will live in a backup and a snapshot and a disk image, and "the
process could read it anyway" is no longer an argument about one person's own
secret. That is stage 2 of
`docs/platform-redesign/model-access-and-byok-2026-08-11.md`, and building it
now, dormant, is cheaper than retrofitting it around live user data.

    WORKSHOP_SECRETS_KEK   base64 (or hex) 32-byte key encryption key.
                           Unset: the store stays plaintext, exactly as today.

THE SHAPE. Envelope encryption, the standard arrangement: a fresh 256-bit data
encryption key per write, wrapped by the KEK, both AES-256-GCM. The DEK is what
protects the payload and the KEK never touches it directly, so rotating the KEK
means re-wrapping a small header rather than rewriting every secret, and a
compromised DEK exposes one file rather than the store.

AES-GCM is authenticated, so a tampered file fails to decrypt rather than
decrypting to something attacker-chosen. The slug is bound in as additional
authenticated data, which means a file cannot be renamed to impersonate another
principal's secrets: `principal:2.json` moved over `principal:1.json` fails
authentication instead of handing principal 1 principal 2's keys.

MIGRATION IS ONE DIRECTIONAL AND EXPLICIT. A plaintext file is still readable
after a KEK is configured (and is re-written encrypted on the next write), so
turning this on does not strand existing secrets. An ENCRYPTED file read with no
KEK raises rather than returning empty: silently reporting "no credential
configured" because the key is missing would send the system down the
"unconfigured" path, and the operator would be told to paste their key again
rather than that the deployment cannot read its own store.
"""

from __future__ import annotations

import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEK_ENV = "WORKSHOP_SECRETS_KEK"

# Marks a file as this format. Present in the JSON envelope so a reader can tell
# encrypted from plaintext without guessing, and so the format can be versioned
# rather than sniffed.
FORMAT = "workshop-secrets-v1"

_KEY_BYTES = 32
_NONCE_BYTES = 12


class SecretsKeyError(RuntimeError):
    """The store is encrypted and the key is missing, wrong, or malformed.

    Deliberately not caught by the store's ordinary "unreadable file means no
    secrets" path: an unreadable file is a normal empty state, an undecryptable
    one is a broken deployment and must say so.
    """


def _decode_key(raw: str) -> bytes:
    """32 bytes from base64 or hex. Anything else is an error, not a hash.

    Accepting a passphrase and hashing it would be worse than refusing: it looks
    like it works and silently gives a key with whatever entropy the passphrase
    had.
    """
    raw = (raw or "").strip()
    for decode in (base64.b64decode, base64.urlsafe_b64decode,
                   lambda s: binascii.unhexlify(s)):
        try:
            key = decode(raw)  # type: ignore[operator]
        except (binascii.Error, ValueError):
            continue
        if len(key) == _KEY_BYTES:
            return key
    raise SecretsKeyError(
        f"{KEK_ENV} must be a 32 byte key, base64 or hex encoded. "
        "Generate one with: python -c "
        "\"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\"")


def kek() -> bytes | None:
    """The configured key encryption key, or None when the store is plaintext."""
    raw = os.environ.get(KEK_ENV)
    if not raw or not raw.strip():
        return None
    return _decode_key(raw)


def enabled() -> bool:
    return bool(os.environ.get(KEK_ENV, "").strip())


def is_encrypted(blob: str) -> bool:
    """Does this file content carry the encrypted envelope? Never raises."""
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("fmt") == FORMAT


def encrypt(values: dict[str, str], slug: str) -> str:
    """Serialise and encrypt. Caller must have checked `enabled()`."""
    key = kek()
    if key is None:  # pragma: no cover - guarded by the caller
        raise SecretsKeyError(f"{KEK_ENV} is not set")

    dek = os.urandom(_KEY_BYTES)
    dek_nonce = os.urandom(_NONCE_BYTES)
    data_nonce = os.urandom(_NONCE_BYTES)
    aad = slug.encode("utf-8")

    plaintext = json.dumps(values, separators=(",", ":")).encode("utf-8")
    # The slug is bound to BOTH layers, so neither the wrapped key nor the
    # payload can be lifted into another principal's file.
    wrapped = AESGCM(key).encrypt(dek_nonce, dek, aad)
    body = AESGCM(dek).encrypt(data_nonce, plaintext, aad)

    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return json.dumps({
        "fmt": FORMAT,
        "slug": slug,
        "dek_nonce": b64(dek_nonce),
        "dek": b64(wrapped),
        "nonce": b64(data_nonce),
        "body": b64(body),
    }, separators=(",", ":"))


def decrypt(blob: str, slug: str) -> dict[str, str]:
    """The values from an encrypted envelope.

    Raises SecretsKeyError when the key is absent or does not fit, and when the
    file has been tampered with or moved between principals.
    """
    key = kek()
    if key is None:
        raise SecretsKeyError(
            f"this secrets file is encrypted but {KEK_ENV} is not set, so it "
            "cannot be read. Set the key this deployment was written with.")
    try:
        env = json.loads(blob)
        raw = {k: base64.b64decode(env[k])
               for k in ("dek_nonce", "dek", "nonce", "body")}
    except (json.JSONDecodeError, ValueError, TypeError, KeyError,
            binascii.Error) as e:
        raise SecretsKeyError(f"secrets file is not readable: {e}") from e

    aad = slug.encode("utf-8")
    try:
        dek = AESGCM(key).decrypt(raw["dek_nonce"], raw["dek"], aad)
        plaintext = AESGCM(dek).decrypt(raw["nonce"], raw["body"], aad)
    except InvalidTag as e:
        raise SecretsKeyError(
            "secrets file failed authentication: the key is wrong, the file "
            "was modified, or it belongs to a different principal.") from e

    try:
        data = json.loads(plaintext)
    except (json.JSONDecodeError, ValueError) as e:  # pragma: no cover
        raise SecretsKeyError(f"decrypted secrets are not valid JSON: {e}") from e
    if not isinstance(data, dict):  # pragma: no cover
        raise SecretsKeyError("decrypted secrets are not an object")
    return {str(k): str(v) for k, v in data.items()}


def generate_kek() -> str:
    """A fresh base64 KEK, for install scripts and documentation."""
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")
