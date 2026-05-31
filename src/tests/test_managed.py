"""Unit tests for ``ManagedStore`` using moto."""

from __future__ import annotations

from io import BytesIO

import pytest

from ezs3 import (
    Bucket,
    Client,
    FileInfo,
    HashMismatchError,
    IssueCode,
    ManagedStore,
    S3KeyNotFoundError,
    hash_bytes,
)


@pytest.fixture
def store(client: Client, bucket: Bucket) -> ManagedStore:
    return ManagedStore(client, bucket, base_prefix="blobs/")


class TestConstruction:
    def test_base_prefix_normalized(self, client: Client, bucket: Bucket) -> None:
        s = ManagedStore(client, bucket, "blobs")
        assert s.base_prefix == "blobs/"

    def test_empty_base_prefix(self, client: Client, bucket: Bucket) -> None:
        s = ManagedStore(client, bucket)
        assert s.base_prefix == ""

    def test_accepts_bucket_name(self, client: Client, bucket: Bucket) -> None:
        s = ManagedStore(client, bucket.name)
        assert s.bucket.name == bucket.name

    def test_unknown_alg_raises(self, client: Client, bucket: Bucket) -> None:
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            ManagedStore(client, bucket, alg="not-an-alg")

    def test_default_alg(self, client: Client, bucket: Bucket) -> None:
        assert ManagedStore(client, bucket).alg == "sha256"


class TestPutBytes:
    def test_roundtrip(self, store: ManagedStore) -> None:
        data = b"hello world"
        info = store.put_bytes(data, content_type="text/plain", filename="g.txt")
        assert info.size == len(data)
        assert info.content_type == "text/plain"
        assert info.filename == "g.txt"
        assert info.hash == hash_bytes(data)
        assert store.get_bytes(info) == data

    def test_filename_defaults_to_hash(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"x")
        assert info.filename == info.hash

    def test_dedup_same_content_single_key(
        self,
        store: ManagedStore,
        bucket: Bucket,
    ) -> None:
        store.put_bytes(b"same", filename="first.txt")
        store.put_bytes(b"same", filename="second.txt")
        keys = [p.key for p in bucket.find("blobs/")]
        assert len(keys) == 1

    def test_get_bytes_by_hash_string(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"data")
        assert info.hash is not None
        assert store.get_bytes(info.hash) == b"data"

    def test_content_type_persisted(self, store: ManagedStore, bucket: Bucket) -> None:
        info = store.put_bytes(b"x", content_type="application/json")
        # HEAD the actual object to confirm ContentType was forwarded
        head = bucket.client.boto_client.head_object(
            Bucket=bucket.name,
            Key=f"blobs/{info.hash}",
        )
        assert head["ContentType"] == "application/json"


class TestPutStream:
    def test_roundtrip(self, store: ManagedStore) -> None:
        data = b"streamed payload" * 1000
        info = store.put_stream(BytesIO(data), content_type="application/octet-stream")
        assert info.size == len(data)
        assert info.hash == hash_bytes(data)
        assert store.get_bytes(info) == data

    def test_dedup_with_put_bytes(self, store: ManagedStore, bucket: Bucket) -> None:
        data = b"identical"
        store.put_bytes(data)
        store.put_stream(BytesIO(data))
        keys = [p.key for p in bucket.find("blobs/")]
        assert len(keys) == 1

    def test_invalid_chunk_size(self, store: ManagedStore) -> None:
        with pytest.raises(ValueError, match="chunk_size"):
            store.put_stream(BytesIO(b"x"), chunk_size=0)


class TestExistsAndDelete:
    def test_exists_after_put(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"abc")
        assert info.hash is not None
        assert store.exists(info)
        assert store.exists(info.hash)

    def test_exists_false_for_missing(self, store: ManagedStore) -> None:
        unknown = hash_bytes(b"never uploaded")
        assert not store.exists(unknown)

    def test_delete_removes_object(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"bye")
        store.delete(info)
        assert not store.exists(info)

    def test_delete_missing_is_noop(self, store: ManagedStore) -> None:
        unknown = hash_bytes(b"phantom")
        store.delete(unknown)  # no exception


class TestOpen:
    def test_returns_readable(self, store: ManagedStore) -> None:
        data = b"opened content"
        info = store.put_bytes(data)
        stream = store.open(info)
        try:
            assert stream.read() == data
        finally:
            stream.close()

    def test_missing_raises(self, store: ManagedStore) -> None:
        unknown = hash_bytes(b"phantom")
        with pytest.raises(S3KeyNotFoundError):
            store.open(unknown)


class TestVerify:
    def test_ok_on_fresh_put(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"verify me")
        result = store.verify(info)
        assert result.code is IssueCode.OK
        assert result.ok

    def test_hash_mismatch_after_overwrite(
        self,
        store: ManagedStore,
        bucket: Bucket,
    ) -> None:
        info = store.put_bytes(b"original")
        # Force a corruption via raw boto3 — only valid in this test.
        bucket.client.boto_client.put_object(
            Bucket=bucket.name,
            Key=f"blobs/{info.hash}",
            Body=b"tampered",
        )
        result = store.verify(info)
        assert result.code is IssueCode.HASH_MISMATCH
        assert result.expected == info.hash
        assert result.actual is not None
        assert result.actual.startswith("sha256:")
        assert result.actual != info.hash

    def test_missing_raises(self, store: ManagedStore) -> None:
        unknown = hash_bytes(b"never put")
        with pytest.raises(S3KeyNotFoundError):
            store.verify(unknown)


class TestVerifyStrict:
    def test_ok_silent(self, store: ManagedStore) -> None:
        info = store.put_bytes(b"verify me")
        store.verify_strict(info)

    def test_mismatch_raises(self, store: ManagedStore, bucket: Bucket) -> None:
        info = store.put_bytes(b"original")
        bucket.client.boto_client.put_object(
            Bucket=bucket.name,
            Key=f"blobs/{info.hash}",
            Body=b"tampered",
        )
        with pytest.raises(HashMismatchError):
            store.verify_strict(info)

    def test_missing_raises(self, store: ManagedStore) -> None:
        unknown = hash_bytes(b"never put")
        with pytest.raises(S3KeyNotFoundError):
            store.verify_strict(unknown)


class TestResolveHash:
    def test_fileinfo_without_hash_raises(self, store: ManagedStore) -> None:
        info = FileInfo("x.txt", hash=None)
        with pytest.raises(ValueError, match="FileInfo.hash"):
            store.get_bytes(info)

    def test_bad_type_raises(self, store: ManagedStore) -> None:
        with pytest.raises(TypeError):
            store.get_bytes(123)  # type: ignore[arg-type]

    def test_malformed_hash_string(self, store: ManagedStore) -> None:
        with pytest.raises(ValueError, match="<alg>:<digest>"):
            store.get_bytes("not-a-hash-string")


class TestPublicSurface:
    def test_managed_store_exported(self) -> None:
        import ezs3

        assert "ManagedStore" in ezs3.__all__
        assert hasattr(ezs3, "ManagedStore")
