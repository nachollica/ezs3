"""ezs3: a typed, Path-like abstraction over boto3 S3.

The package exposes four primary types and one set of exceptions:

* :class:`Client` — credentials + bucket lifecycle.
* :class:`Bucket` — handle on a named bucket.
* :class:`S3Path` — path-like representation of a key or prefix, aliased as
  :class:`Prefix` and :class:`Key`.
* :class:`S3Error` and subclasses — pathlib-style exception hierarchy.

Quickstart:
    >>> import ezs3
    >>> client = ezs3.Client()
    >>> bucket = client.create_bucket("my-bucket")
    >>> path = bucket / "logs" / "today.txt"
    >>> path.write_text("hello world")
    >>> path.read_text()
    'hello world'
"""

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
