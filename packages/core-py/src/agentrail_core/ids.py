"""Lexicographically sortable identifiers (ULID, Crockford base32).

Sortable identifiers are used for rows that are frequently listed in creation
order (jobs, runs, trajectory steps) so that pagination can rely on the primary
key instead of a secondary timestamp index.
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODED_LENGTH = 26
_TIMESTAMP_BITS = 48
_RANDOM_BYTES = 10


def _encode(value: int, length: int) -> str:
    chars = [""] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_sortable_id(*, timestamp_ms: int | None = None, randomness: bytes | None = None) -> str:
    """Return a 26-character ULID.

    ``timestamp_ms`` and ``randomness`` exist so tests can produce deterministic
    identifiers; production callers pass neither.
    """
    ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if ms < 0 or ms >= (1 << _TIMESTAMP_BITS):
        raise ValueError("timestamp_ms out of ULID range")
    entropy = os.urandom(_RANDOM_BYTES) if randomness is None else randomness
    if len(entropy) != _RANDOM_BYTES:
        raise ValueError(f"randomness must be exactly {_RANDOM_BYTES} bytes")
    value = (ms << 80) | int.from_bytes(entropy, "big")
    return _encode(value, _ENCODED_LENGTH)


def is_sortable_id(candidate: str) -> bool:
    """Return ``True`` when ``candidate`` is a syntactically valid ULID."""
    if len(candidate) != _ENCODED_LENGTH:
        return False
    return all(character in _CROCKFORD for character in candidate)
