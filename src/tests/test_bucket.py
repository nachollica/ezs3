"""Bucket-level unit tests against moto."""

from __future__ import annotations

import pytest

from ezs3 import (
    Bucket,
    BucketMismatchError,
    Client,
    S3Path,
)


class TestBucketBasic:
    def test_repr_and_str(self) -> None:
        b = Bucket.__new__(Bucket)
        b.name = "x"
        # client unset; only string formatting
        assert repr(b) == "Bucket('x')"
        assert str(b) == "s3://x"

    def test_empty_name_raises(self, mocked_s3: None) -> None:
        with pytest.raises(ValueError):
            Bucket("")

    def test_eq_and_hash(self, mocked_s3: None) -> None:
        a = Bucket("same")
        b = Bucket("same")
        assert a == b
        assert hash(a) == hash(b)
        assert a != Bucket("other")
        assert a != "same"

    def test_truediv_returns_attached_path(self, bucket: Bucket) -> None:
        p = bucket / "a/b"
        assert isinstance(p, S3Path)
        assert p.bucket == bucket
        assert p.parts == ("a", "b")


class TestBucketLifecycle:
    def test_create_via_handle(self, client: Client) -> None:
        Bucket("hand-created", client=client).create()
        assert client.bucket_exists("hand-created")

    def test_exists(self, bucket: Bucket) -> None:
        assert bucket.exists()

    def test_clear(self, populated_bucket: Bucket) -> None:
        populated_bucket.clear()
        assert list(populated_bucket.root.find()) == []

    def test_delete_force(self, client: Client, populated_bucket: Bucket) -> None:
        populated_bucket.delete(force=True)
        assert not client.bucket_exists(populated_bucket.name)


class TestBucketIO:
    def test_read_write_via_bucket(self, bucket: Bucket) -> None:
        bucket.write_text("foo.txt", "hello")
        assert bucket.read_text("foo.txt") == "hello"
        assert bucket.read_bytes("foo.txt") == b"hello"

    def test_remove_batch(self, bucket: Bucket) -> None:
        bucket.write_text("a", "x")
        bucket.write_text("b", "x")
        bucket.write_text("c", "x")
        bucket.remove("a", "b", "c")
        assert list(bucket.root.find()) == []

    def test_remove_with_path_from_other_bucket(self, client: Client, bucket: Bucket) -> None:
        other = client.create_bucket("another")
        wrong = other / "k"
        with pytest.raises(BucketMismatchError):
            bucket.remove(wrong)

    def test_glob_and_iterdir(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in populated_bucket.iterdir())
        assert names == ["data", "logs", "readme.txt"]
        json_under_data = sorted(p.key for p in populated_bucket.rglob("*.json", prefix="data"))
        assert json_under_data == [
            "data/a.json",
            "data/b.json",
            "data/nested/c.json",
        ]
