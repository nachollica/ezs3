"""Exception hierarchy used across the package.

The error types mirror :mod:`pathlib` semantics and inherit from the closest
matching builtin (e.g. :class:`IsAPrefixError` derives from
:class:`IsADirectoryError`) so callers may catch either the ezs3 type or its
stdlib equivalent.
"""

from __future__ import annotations


class S3Error(Exception):
    """Root of the ezs3 exception hierarchy."""


class IsAPrefixError(S3Error, IsADirectoryError):
    """Raised when a key-only operation targets an S3 prefix.

    The S3 counterpart of :class:`IsADirectoryError`. Triggered by, for example,
    calling :meth:`~ezs3.S3Path.read_text` on a path that exists as a prefix.
    """


class NotAPrefixError(S3Error, NotADirectoryError):
    """Raised when a prefix-only operation targets an S3 key.

    The S3 counterpart of :class:`NotADirectoryError`. Triggered by, for example,
    calling :meth:`~ezs3.S3Path.iterdir` on a path that exists as a key.
    """


class S3KeyNotFoundError(S3Error, FileNotFoundError):
    """Raised when an S3 key (or path) does not exist."""


class BucketNotFoundError(S3Error, FileNotFoundError):
    """Raised when a referenced bucket does not exist."""


class BucketAlreadyExistsError(S3Error, FileExistsError):
    """Raised when creating a bucket that already exists.

    Set ``exists_ok=True`` on :meth:`~ezs3.Client.create_bucket` to suppress.
    """


class S3KeyExistsError(S3Error, FileExistsError):
    """Raised when writing a key that already exists and overwrite is disabled.

    Pass ``overwrite=True`` to :meth:`~ezs3.S3Path.upload` (or
    :meth:`~ezs3.Bucket.upload`) to replace the existing object instead.
    """


class PathNotAttachedError(S3Error, ValueError):
    """Raised when an operation requires a bucket but the path is free.

    Free paths are constructed via ``S3Path("a/b/c")`` and must be attached to
    a bucket (e.g. via :meth:`~ezs3.S3Path.attach`) before performing I/O.
    """


class BucketMismatchError(S3Error, ValueError):
    """Raised when a :class:`~ezs3.Bucket` is used with a path bound to another.

    Cross-bucket operations are disallowed to prevent silent data routing
    surprises. Use :meth:`~ezs3.S3Path.attach` to rebind the path explicitly.
    """


class HashMismatchError(S3Error, ValueError):
    """Raised when a content hash does not match the expected digest.

    Used by hash-checking helpers that prefer raising over reporting via a
    result record. The :class:`~ezs3.ConsistencyChecker` reports mismatches
    as :class:`~ezs3.CheckResult` records by default rather than raising.
    """


__all__ = [
    "BucketAlreadyExistsError",
    "BucketMismatchError",
    "BucketNotFoundError",
    "HashMismatchError",
    "IsAPrefixError",
    "NotAPrefixError",
    "PathNotAttachedError",
    "S3Error",
    "S3KeyExistsError",
    "S3KeyNotFoundError",
]
