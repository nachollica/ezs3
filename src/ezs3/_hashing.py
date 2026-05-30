"""Content-hash helpers built on top of stdlib :mod:`hashlib`.

The public string format for a hash is ``"<alg>:<hex-digest>"`` — for
example ``"sha256:e3b0c442..."`` or ``"md5:9e107d9d..."``. The algorithm
name is anything :func:`hashlib.new` accepts; the digest is the
lowercase hexadecimal representation. Helpers in this module produce and
parse that format consistently.
"""

from __future__ import annotations

import hashlib
import re
from typing import BinaryIO, FrozenSet, Tuple

DEFAULT_ALG: str = "sha256"
"""Default hashing algorithm used when no algorithm is specified."""

_HEX_RE = re.compile(r"^[0-9a-f]+$")


def supported_algorithms() -> FrozenSet[str]:
    """Return algorithm names accepted by :func:`hashlib.new`.

    The set is taken from :data:`hashlib.algorithms_guaranteed`, which is
    stable across platforms and Python builds.

    Returns:
        Frozen set of lowercase algorithm names.
    """
    return frozenset(hashlib.algorithms_guaranteed)


def _new_hasher(alg: str) -> "hashlib._Hash":
    """Instantiate a hashlib hasher for ``alg`` or raise ``ValueError``.

    Args:
        alg: Algorithm name.

    Returns:
        A fresh hasher object.

    Raises:
        ValueError: If ``alg`` is not accepted by :func:`hashlib.new`.
    """
    try:
        return hashlib.new(alg)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Unsupported hash algorithm: {alg!r}") from exc


def hash_bytes(data: bytes, alg: str = DEFAULT_ALG) -> str:
    """Hash a bytes payload.

    Args:
        data: Raw bytes to hash.
        alg: Hash algorithm name. Defaults to :data:`DEFAULT_ALG`.

    Returns:
        Formatted hash string ``"<alg>:<hex-digest>"`` with the digest in
        lowercase hexadecimal.

    Raises:
        ValueError: If ``alg`` is not a supported algorithm.
    """
    hasher = _new_hasher(alg)
    hasher.update(data)
    return format_hash(alg, hasher.hexdigest())


def hash_stream(
    stream: BinaryIO,
    alg: str = DEFAULT_ALG,
    chunk_size: int = 1 << 20,
) -> str:
    """Hash a binary file-like object in chunks.

    The stream is read until EOF in pieces of ``chunk_size`` bytes. Useful
    to avoid loading large payloads into memory.

    Args:
        stream: Binary file-like supporting ``.read(n)``.
        alg: Hash algorithm name. Defaults to :data:`DEFAULT_ALG`.
        chunk_size: Maximum number of bytes read per iteration. Must be
            positive.

    Returns:
        Formatted hash string ``"<alg>:<hex-digest>"``.

    Raises:
        ValueError: If ``alg`` is not a supported algorithm or
            ``chunk_size`` is not positive.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size!r}")
    hasher = _new_hasher(alg)
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    return format_hash(alg, hasher.hexdigest())


def parse_hash(value: str) -> Tuple[str, str]:
    """Split a ``"<alg>:<digest>"`` string into its parts.

    The algorithm name is lowercased; the digest is validated as
    hexadecimal (digits and ``a-f``) and returned unchanged.

    Args:
        value: Formatted hash string.

    Returns:
        A ``(alg, digest)`` tuple.

    Raises:
        ValueError: If ``value`` is malformed, the digest is not
            hexadecimal, or the algorithm is not supported.
    """
    if not isinstance(value, str):
        raise ValueError(f"Hash must be a string, got {type(value).__name__}")
    if ":" not in value:
        raise ValueError(f"Hash must be '<alg>:<digest>', got {value!r}")
    alg_raw, _, digest = value.partition(":")
    alg = alg_raw.strip().lower()
    if not alg or alg != alg_raw:
        raise ValueError(f"Invalid algorithm in hash {value!r}")
    if not digest or not _HEX_RE.match(digest):
        raise ValueError(f"Invalid hex digest in hash {value!r}")
    if alg not in supported_algorithms():
        # Fall back to hashlib.new to catch platform-specific algorithms
        # that are not in algorithms_guaranteed but still usable.
        _new_hasher(alg)
    return alg, digest


def format_hash(alg: str, digest: str) -> str:
    """Build a ``"<alg>:<digest>"`` string from its parts.

    Args:
        alg: Algorithm name. Lowercased before formatting.
        digest: Hexadecimal digest. Lowercased before formatting.

    Returns:
        The formatted hash string.

    Raises:
        ValueError: If ``alg`` is empty, ``digest`` is empty, or ``digest``
            is not hexadecimal.
    """
    alg_clean = alg.strip().lower()
    if not alg_clean:
        raise ValueError("alg must not be empty")
    digest_clean = digest.strip().lower()
    if not digest_clean or not _HEX_RE.match(digest_clean):
        raise ValueError(f"Invalid hex digest: {digest!r}")
    return f"{alg_clean}:{digest_clean}"


__all__ = [
    "DEFAULT_ALG",
    "format_hash",
    "hash_bytes",
    "hash_stream",
    "parse_hash",
    "supported_algorithms",
]
