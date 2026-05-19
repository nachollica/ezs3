"""Exception hierarchy mirroring :mod:`pathlib` semantics, adapted to S3."""

from __future__ import annotations


class S3Error(Exception):
    """Base class for all ezs3 errors."""


class IsAPrefixError(S3Error, IsADirectoryError):
    """Raised when a key operation is attempted on an S3 prefix."""


class NotAPrefixError(S3Error, NotADirectoryError):
    """Raised when a prefix operation is attempted on an S3 key."""


class S3KeyNotFoundError(S3Error, FileNotFoundError):
    """Raised when an S3 key (or path) does not exist."""


class BucketNotFoundError(S3Error, FileNotFoundError):
    """Raised when a bucket does not exist."""


class BucketAlreadyExistsError(S3Error, FileExistsError):
    """Raised when attempting to create a bucket that already exists."""


class PathNotAttachedError(S3Error, ValueError):
    """Raised when an operation requires a bucket but the path has none."""


class BucketMismatchError(S3Error, ValueError):
    """Raised when a :class:`Bucket` is used with a path attached to another bucket."""


__all__ = [
    "BucketAlreadyExistsError",
    "BucketMismatchError",
    "BucketNotFoundError",
    "IsAPrefixError",
    "NotAPrefixError",
    "PathNotAttachedError",
    "S3Error",
    "S3KeyNotFoundError",
]
