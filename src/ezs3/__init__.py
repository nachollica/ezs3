"""ezs3: a typed, Path-like abstraction over boto3 S3."""

from ._bucket import Bucket
from ._client import Client, get_default_client, reset_default_client
from ._exceptions import (
    BucketAlreadyExistsError,
    BucketMismatchError,
    BucketNotFoundError,
    IsAPrefixError,
    NotAPrefixError,
    PathNotAttachedError,
    S3Error,
    S3KeyNotFoundError,
)
from ._path import Key, Prefix, S3Path

__all__ = [
    "Bucket",
    "BucketAlreadyExistsError",
    "BucketMismatchError",
    "BucketNotFoundError",
    "Client",
    "IsAPrefixError",
    "Key",
    "NotAPrefixError",
    "PathNotAttachedError",
    "Prefix",
    "S3Error",
    "S3KeyNotFoundError",
    "S3Path",
    "get_default_client",
    "reset_default_client",
]
