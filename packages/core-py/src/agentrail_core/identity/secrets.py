"""Generation and verification of session tokens and API keys.

Neither a session token nor an API key is ever stored in a form that could be
replayed if the database leaked. API keys are stored as bcrypt hashes; session
tokens use deterministic PBKDF2-HMAC-SHA256 digests so cookie lookup stays
indexed.

Human passwords are not part of this system — sign-in is delegated to an OAuth
provider. The KDF here exists for stored bearer tokens and scanner clarity, not
for password authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

import bcrypt

#: Everything after this prefix is secret. The prefix makes a leaked key
#: greppable in logs and recognisable to secret scanners.
API_KEY_PREFIX = "ar"
#: Bytes of entropy in the secret half of an API key and in a session token.
SECRET_BYTES = 32
#: Length of the public, indexed identifier half of an API key.
KEY_ID_LENGTH = 16
_DIGEST_SALT = b"agentrail-token-digest-v1"
_DIGEST_ITERATIONS = 120_000


def _digest(value: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", value.encode("utf-8"), _DIGEST_SALT, _DIGEST_ITERATIONS
    ).hex()


def hash_api_key_secret(value: str) -> str:
    """Return the bcrypt hash stored for an API key secret."""
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def api_key_hash_needs_upgrade(stored_hash: str) -> bool:
    return not stored_hash.startswith(("$2a$", "$2b$", "$2y$"))


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    """A freshly minted key.

    ``token`` is the only time the full value exists. It is returned to the
    caller once and never stored.
    """

    key_id: str
    token: str
    secret_hash: str


def generate_api_key() -> GeneratedApiKey:
    """Mint an API key of the form ``ar_<key_id>_<secret>``."""
    key_id = secrets.token_hex(KEY_ID_LENGTH // 2)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    token = f"{API_KEY_PREFIX}_{key_id}_{secret}"
    return GeneratedApiKey(key_id=key_id, token=token, secret_hash=hash_api_key_secret(secret))


def parse_api_key(token: str) -> tuple[str, str] | None:
    """Split a presented key into ``(key_id, secret)``.

    Returns ``None`` for anything malformed. The caller looks the key up by
    ``key_id`` — an indexed, non-secret column — and then verifies the secret,
    so verification never requires scanning the table.
    """
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    prefix, key_id, secret = parts
    if prefix != API_KEY_PREFIX or not key_id or not secret:
        return None
    if len(key_id) != KEY_ID_LENGTH:
        return None
    return key_id, secret


def verify_secret(presented: str, stored_hash: str) -> bool:
    """Verify a presented API key secret against its stored bcrypt hash."""
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return bcrypt.checkpw(presented.encode("utf-8"), stored_hash.encode("ascii"))
    return hmac.compare_digest(_digest(presented), stored_hash)


def generate_session_token() -> tuple[str, str]:
    """Return ``(token, token_hash)`` for a new session.

    The token goes into an HttpOnly cookie; only the hash is persisted.
    """
    token = secrets.token_urlsafe(SECRET_BYTES)
    return token, _digest(token)


def hash_session_token(token: str) -> str:
    return _digest(token)


def generate_oauth_state() -> str:
    """Opaque anti-CSRF value for the OAuth authorisation redirect."""
    return secrets.token_urlsafe(SECRET_BYTES)
