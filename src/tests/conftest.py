"""Shared fixtures for the ezs3 test suite.

Most tests run against the in-process ``moto`` mock; integration tests
(``-m integration``) run against a real S3-compatible service.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from moto import mock_aws

import ezs3
from ezs3 import _client as _client_module

if TYPE_CHECKING:
    from ezs3 import Bucket, Client


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fake AWS credentials so boto3 doesn't try to use real ones."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def mocked_s3(aws_env: None) -> Iterator[None]:
    """Provide an in-process S3 service via moto."""
    with mock_aws():
        _client_module.reset_default_client()
        yield
        _client_module.reset_default_client()


@pytest.fixture
def client(mocked_s3: None) -> Client:
    return ezs3.Client()


@pytest.fixture
def bucket_name() -> str:
    return "test-bucket"


@pytest.fixture
def bucket(client: Client, bucket_name: str) -> Bucket:
    return client.create_bucket(bucket_name)


@pytest.fixture
def populated_bucket(bucket: Bucket) -> Bucket:
    """A bucket pre-loaded with a small directory tree of objects."""
    bucket.write_text("readme.txt", "top-level readme")
    bucket.write_text("data/a.json", '{"k": 1}')
    bucket.write_text("data/b.json", '{"k": 2}')
    bucket.write_text("data/nested/c.json", '{"k": 3}')
    bucket.write_text("data/nested/deep/d.txt", "deep file")
    bucket.write_text("logs/2024/01.log", "jan")
    bucket.write_text("logs/2024/02.log", "feb")
    return bucket
