"""Client-level unit tests against moto."""

from __future__ import annotations

import pytest

from ezs3 import (
    Bucket,
    BucketAlreadyExistsError,
    BucketNotFoundError,
    Client,
)


class TestClient:
    def test_list_buckets_empty(self, client: Client) -> None:
        assert client.list_buckets() == []

    def test_create_and_list(self, client: Client) -> None:
        client.create_bucket("bucket-a")
        client.create_bucket("bucket-b")
        names = sorted(b.name for b in client.list_buckets())
        assert names == ["bucket-a", "bucket-b"]

    def test_create_duplicate_raises(self, mocked_s3: None) -> None:
        # Outside us-east-1, S3 returns BucketAlreadyOwnedByYou.
        client = Client(region_name="eu-west-1")
        client.create_bucket("dup-bucket")
        with pytest.raises(BucketAlreadyExistsError):
            client.create_bucket("dup-bucket")

    def test_create_duplicate_exists_ok(self, mocked_s3: None) -> None:
        client = Client(region_name="eu-west-1")
        client.create_bucket("dup-bucket-ok")
        b = client.create_bucket("dup-bucket-ok", exists_ok=True)
        assert b.name == "dup-bucket-ok"

    def test_bucket_handle_does_not_check_existence(self, client: Client) -> None:
        # Returning a handle is purely local; should not raise.
        b = client.bucket("not-yet-created")
        assert b.name == "not-yet-created"
        assert not client.bucket_exists("not-yet-created")

    def test_delete_bucket(self, client: Client) -> None:
        client.create_bucket("doomed")
        client.delete_bucket("doomed")
        assert not client.bucket_exists("doomed")

    def test_delete_missing_raises(self, client: Client) -> None:
        with pytest.raises(BucketNotFoundError):
            client.delete_bucket("nope")

    def test_delete_missing_ok(self, client: Client) -> None:
        client.delete_bucket("nope", missing_ok=True)

    def test_delete_by_bucket_object(self, client: Client) -> None:
        b = client.create_bucket("by-obj")
        client.delete_bucket(b)
        assert not client.bucket_exists("by-obj")

    def test_create_via_bucket_instance(self, client: Client) -> None:
        b = Bucket("via-instance", client=client)
        client.create_bucket(b)
        assert client.bucket_exists("via-instance")

    def test_force_delete_clears_content(self, client: Client) -> None:
        b = client.create_bucket("with-junk")
        b.write_text("a/b/c.txt", "stuff")
        client.delete_bucket(b, force=True)
        assert not client.bucket_exists("with-junk")

    def test_repr_mentions_region(self, client: Client) -> None:
        assert "region=" in repr(client)
