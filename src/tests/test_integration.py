"""Integration tests against a real S3-compatible service (MinIO by default).

Run with ``just s3-local-up`` first, then ``just test-integration`` (which
exports the required env vars and passes ``-m integration``).
"""

from __future__ import annotations

import os
import socket
import uuid
from typing import Iterator

import pytest
from botocore.exceptions import EndpointConnectionError

import ezs3
from ezs3 import Bucket, Client

pytestmark = pytest.mark.integration


def _endpoint() -> str:
    return os.environ.get("EZS3_S3_ENDPOINT_URL", "http://localhost:9000")


def _reachable(url: str) -> bool:
    """Return ``True`` iff the TCP port behind ``url`` accepts connections."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def endpoint_url() -> str:
    url = _endpoint()
    if not _reachable(url):
        pytest.skip(f"S3 endpoint {url} is not reachable; run `just s3-local-up`")
    return url


@pytest.fixture(scope="session")
def integration_client(endpoint_url: str) -> Client:
    return ezs3.Client(
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ.get("EZS3_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("EZS3_S3_SECRET_KEY", "minioadmin"),
        region_name=os.environ.get("EZS3_S3_REGION", "us-east-1"),
    )


@pytest.fixture
def integration_bucket(integration_client: Client) -> Iterator[Bucket]:
    name = f"ezs3-it-{uuid.uuid4().hex[:10]}"
    try:
        bucket = integration_client.create_bucket(name)
    except EndpointConnectionError:
        pytest.skip("Cannot reach S3 endpoint")
    try:
        yield bucket
    finally:
        bucket.delete(force=True, missing_ok=True)


class TestIntegration:
    def test_roundtrip_text(self, integration_bucket: Bucket) -> None:
        key = integration_bucket / "hello.txt"
        key.write_text("hi from minio")
        assert key.read_text() == "hi from minio"

    def test_listing_and_glob(self, integration_bucket: Bucket) -> None:
        for path in ["a/1.json", "a/2.json", "a/sub/3.json", "b/4.txt"]:
            (integration_bucket / path).write_text(path)
        keys = sorted(p.key for p in (integration_bucket / "a").rglob("*.json"))
        assert keys == ["a/1.json", "a/2.json", "a/sub/3.json"]
        top = sorted(p.name for p in integration_bucket.iterdir())
        assert top == ["a", "b"]

    def test_rmtree(self, integration_bucket: Bucket) -> None:
        for path in ["x/1", "x/2", "x/nested/3"]:
            (integration_bucket / path).write_text("data")
        (integration_bucket / "x").rmtree()
        assert not (integration_bucket / "x").exists()

    def test_client_lists_bucket(
        self, integration_client: Client, integration_bucket: Bucket
    ) -> None:
        names = {b.name for b in integration_client.list_buckets()}
        assert integration_bucket.name in names
