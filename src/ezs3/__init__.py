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
from ._consistency import CheckResult, ConsistencyChecker, FileInfo, IssueCode
from ._exceptions import (
    BucketAlreadyExistsError,
    BucketMismatchError,
    BucketNotFoundError,
    HashMismatchError,
    IsAPrefixError,
    NotAPrefixError,
    PathNotAttachedError,
    S3Error,
    S3KeyExistsError,
    S3KeyNotFoundError,
)
from ._hashing import (
    DEFAULT_ALG,
    format_hash,
    hash_bytes,
    hash_stream,
    parse_hash,
    supported_algorithms,
)
from ._managed import ManagedStore
from ._path import Key, Prefix, S3Path

__all__ = [
    "DEFAULT_ALG",
    "Bucket",
    "BucketAlreadyExistsError",
    "BucketMismatchError",
    "BucketNotFoundError",
    "CheckResult",
    "Client",
    "ConsistencyChecker",
    "FileInfo",
    "HashMismatchError",
    "IsAPrefixError",
    "IssueCode",
    "Key",
    "ManagedStore",
    "NotAPrefixError",
    "PathNotAttachedError",
    "Prefix",
    "S3Error",
    "S3KeyExistsError",
    "S3KeyNotFoundError",
    "S3Path",
    "format_hash",
    "get_default_client",
    "hash_bytes",
    "hash_stream",
    "parse_hash",
    "reset_default_client",
    "supported_algorithms",
]
