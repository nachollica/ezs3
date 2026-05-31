"""Unit tests for the hashing helpers."""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

from ezs3 import (
    DEFAULT_ALG,
    format_hash,
    hash_bytes,
    hash_stream,
    parse_hash,
    supported_algorithms,
)


class TestSupportedAlgorithms:
    def test_includes_sha256(self) -> None:
        assert "sha256" in supported_algorithms()

    def test_is_frozen(self) -> None:
        algs = supported_algorithms()
        assert isinstance(algs, frozenset)


class TestHashBytes:
    def test_default_alg_is_sha256(self) -> None:
        assert DEFAULT_ALG == "sha256"
        assert hash_bytes(b"abc").startswith("sha256:")

    def test_matches_hashlib(self) -> None:
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert hash_bytes(data) == f"sha256:{expected}"

    def test_md5_alg(self) -> None:
        data = b"hello"
        expected = hashlib.md5(data).hexdigest()  # noqa: S324
        assert hash_bytes(data, "md5") == f"md5:{expected}"

    def test_unknown_alg_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            hash_bytes(b"x", "not-a-real-alg")


class TestHashStream:
    def test_matches_hash_bytes(self) -> None:
        data = b"the quick brown fox jumps over the lazy dog" * 1000
        assert hash_stream(BytesIO(data)) == hash_bytes(data)

    def test_small_chunk_size(self) -> None:
        data = b"abcdefghij" * 100
        assert hash_stream(BytesIO(data), chunk_size=7) == hash_bytes(data)

    def test_invalid_chunk_size(self) -> None:
        with pytest.raises(ValueError, match="chunk_size"):
            hash_stream(BytesIO(b""), chunk_size=0)

    def test_unknown_alg_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            hash_stream(BytesIO(b"x"), "totally-bogus")


class TestParseHash:
    def test_roundtrip(self) -> None:
        original = hash_bytes(b"abc")
        alg, digest = parse_hash(original)
        assert alg == "sha256"
        assert format_hash(alg, digest) == original

    def test_lowercases_alg(self) -> None:
        # An algorithm with extraneous case is rejected (strict).
        with pytest.raises(ValueError, match="Invalid algorithm in hash"):
            parse_hash("SHA256:abc123")

    def test_rejects_missing_colon(self) -> None:
        with pytest.raises(ValueError, match="<alg>:<digest>"):
            parse_hash("sha256abc")

    def test_rejects_empty_digest(self) -> None:
        with pytest.raises(ValueError, match="hex digest"):
            parse_hash("sha256:")

    def test_rejects_non_hex_digest(self) -> None:
        with pytest.raises(ValueError, match="hex digest"):
            parse_hash("sha256:zzz")

    def test_rejects_unknown_alg(self) -> None:
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            parse_hash("nope:abcdef")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match="string"):
            parse_hash(123)  # type: ignore[arg-type]


class TestFormatHash:
    def test_lowercases_both(self) -> None:
        assert format_hash("SHA256", "ABCDEF") == "sha256:abcdef"

    def test_rejects_empty_alg(self) -> None:
        with pytest.raises(ValueError, match="alg"):
            format_hash("", "abc")

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValueError, match="hex digest"):
            format_hash("md5", "zzz")
