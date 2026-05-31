"""Content-addressed storage on top of a :class:`~ezs3.Bucket`.

The :class:`ManagedStore` writes every blob at
``<base_prefix>/<alg>:<digest>`` and treats that string as the canonical
identifier. Two callers uploading byte-identical content map to the same
key, which gives a free dedup. The store is intentionally minimal:
callers keep their own database row (filename, size, content_type,
hash); ezs3 owns only the S3 side.

Example:
    >>> import ezs3
    >>> client = ezs3.Client()
    >>> bucket = client.bucket("uploads")
    >>> store = ezs3.ManagedStore(client, bucket, "blobs/")
    >>> info = store.put_bytes(
    ...     b"hello world",
    ...     content_type="text/plain",
    ...     filename="greeting.txt",
    ... )
    >>> # A caller's plain dict standing in for a DB row:
    >>> row = {
    ...     "filename": info.filename,
    ...     "size": info.size,
    ...     "content_type": info.content_type,
    ...     "hash": info.hash,
    ... }
    >>> store.get_bytes(row["hash"])
    b'hello world'
    >>> store.verify(row["hash"]).ok
    True
"""

from __future__ import annotations

import hashlib
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, BinaryIO, Optional, Union, cast

from ._bucket import Bucket
from ._consistency import (
    CheckResult,
    FileInfo,
    IssueCode,
    _normalize_base,
)
from ._exceptions import HashMismatchError, S3KeyNotFoundError
from ._hashing import (
    DEFAULT_ALG,
    format_hash,
    hash_bytes,
    hash_stream,
    parse_hash,
)

if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import (
        PutObjectRequestObjectPutTypeDef as PutObjectKwargs,
    )

    from ._client import Client


_DEFAULT_CHUNK: int = 1 << 20
_SPOOL_THRESHOLD: int = 8 * 1024 * 1024


class ManagedStore:
    """Content-addressed wrapper around a :class:`~ezs3.Bucket`.

    Blobs are stored flat under ``base_prefix`` at keys of the form
    ``<alg>:<digest>``. The canonical identifier for a blob is the hash
    string itself, which round-trips through :func:`parse_hash` and
    :func:`format_hash`.

    The store does not refcount, deduplicate metadata, or track
    filenames — callers own their database. :meth:`put_bytes` and
    :meth:`put_stream` echo back a :class:`FileInfo` so the caller can
    persist the row alongside their own keys.

    Attributes:
        alg: Hash algorithm used for new uploads. Existing blobs may use
            any algorithm accepted by :func:`parse_hash`.
    """

    def __init__(
        self,
        client: "Client",
        bucket: Union[str, Bucket],
        base_prefix: str = "",
        alg: str = DEFAULT_ALG,
    ) -> None:
        """Bind the store to a client, bucket, base prefix, and algorithm.

        Args:
            client: The :class:`~ezs3.Client` used for S3 calls.
            bucket: Target bucket as a name or :class:`~ezs3.Bucket`.
            base_prefix: Prefix under which blob keys live. May be the
                empty string (the bucket root). The trailing slash is
                normalized.
            alg: Hash algorithm for new uploads. Must be accepted by
                :func:`hashlib.new`. Defaults to :data:`DEFAULT_ALG`.

        Raises:
            ValueError: If ``alg`` is not a supported hash algorithm.
        """
        try:
            hashlib.new(alg)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Unsupported hash algorithm: {alg!r}") from exc

        self._client = client
        if isinstance(bucket, Bucket):
            self._bucket: Bucket = bucket
        else:
            self._bucket = client.bucket(bucket)
        self._base_prefix: str = _normalize_base(base_prefix)
        self.alg: str = alg.strip().lower()

    @property
    def client(self) -> "Client":
        """The bound :class:`~ezs3.Client`."""
        return self._client

    @property
    def bucket(self) -> Bucket:
        """The bound :class:`~ezs3.Bucket`."""
        return self._bucket

    @property
    def base_prefix(self) -> str:
        """Normalized base prefix (empty or trailing-slashed)."""
        return self._base_prefix

    # Internals

    def _resolve_hash(self, x: Union[FileInfo, str]) -> str:
        """Return the canonical hash string from a :class:`FileInfo` or string.

        Args:
            x: A :class:`FileInfo` whose ``hash`` field is set, or a
                ``"<alg>:<digest>"`` string.

        Returns:
            The hash string, validated as well-formed.

        Raises:
            TypeError: If ``x`` is neither a :class:`FileInfo` nor a
                string.
            ValueError: If the hash is missing or malformed.
        """
        if isinstance(x, FileInfo):
            if x.hash is None:
                raise ValueError("FileInfo.hash is None; cannot resolve key")
            value = x.hash
        elif isinstance(x, str):
            value = x
        else:
            raise TypeError(
                f"Expected FileInfo or str, got {type(x).__name__}",
            )
        alg, digest = parse_hash(value)
        return format_hash(alg, digest)

    def _key_for(self, h: str) -> str:
        """Return the absolute S3 key for hash string ``h``."""
        return self._base_prefix + h

    # Put

    def put_bytes(
        self,
        data: bytes,
        *,
        content_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> FileInfo:
        """Hash ``data`` and upload it if absent.

        The upload is skipped when an object already exists at the
        target key, which makes :meth:`put_bytes` idempotent for
        byte-identical inputs. ``filename`` is echoed back into the
        returned :class:`FileInfo` verbatim; it does not influence the
        S3 key.

        Args:
            data: Raw bytes to store.
            content_type: Optional MIME type, forwarded as
                ``ContentType`` on the underlying ``put_object`` call.
            filename: Optional caller-facing filename to echo back into
                the returned :class:`FileInfo`. Defaults to the hash
                string itself when omitted.

        Returns:
            A :class:`FileInfo` covering the stored object.
        """
        h = hash_bytes(data, self.alg)
        key = self._key_for(h)
        path = self._bucket.path(key)
        if not path.is_key():
            put_kwargs: PutObjectKwargs = {}
            if content_type is not None:
                put_kwargs["ContentType"] = content_type
            path.write_bytes(data, **put_kwargs)
        return FileInfo(
            filename=filename if filename is not None else h,
            size=len(data),
            content_type=content_type,
            hash=h,
        )

    def put_stream(
        self,
        stream: BinaryIO,
        *,
        content_type: Optional[str] = None,
        filename: Optional[str] = None,
        chunk_size: int = _DEFAULT_CHUNK,
    ) -> FileInfo:
        """Hash and upload ``stream`` in a single pass.

        The stream is tee'd into an in-memory + on-disk spooled buffer
        so the body can be uploaded without re-reading the original
        source. Memory use is bounded by ``_SPOOL_THRESHOLD``; larger
        payloads spill to a temp file.

        Args:
            stream: Binary file-like supporting ``.read(n)``.
            content_type: Optional MIME type forwarded as
                ``ContentType``.
            filename: Optional caller-facing filename echoed back into
                the returned :class:`FileInfo`.
            chunk_size: Bytes read per iteration. Must be positive.

        Returns:
            A :class:`FileInfo` covering the stored object.

        Raises:
            ValueError: If ``chunk_size`` is not positive.
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size!r}")
        hasher = hashlib.new(self.alg)
        size = 0
        with SpooledTemporaryFile(max_size=_SPOOL_THRESHOLD) as tmp:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                tmp.write(chunk)
                size += len(chunk)
            digest = hasher.hexdigest()
            h = format_hash(self.alg, digest)
            key = self._key_for(h)
            path = self._bucket.path(key)
            if not path.is_key():
                tmp.seek(0)
                if content_type is not None:
                    self._client.boto_client.put_object(
                        Bucket=self._bucket.name,
                        Key=key,
                        Body=tmp,
                        ContentType=content_type,
                    )
                else:
                    self._client.boto_client.put_object(
                        Bucket=self._bucket.name,
                        Key=key,
                        Body=tmp,
                    )
        return FileInfo(
            filename=filename if filename is not None else h,
            size=size,
            content_type=content_type,
            hash=h,
        )

    # Get / inspect

    def get_bytes(self, info_or_hash: Union[FileInfo, str]) -> bytes:
        """Read the stored blob as raw bytes.

        Args:
            info_or_hash: A :class:`FileInfo` with ``hash`` set, or the
                hash string directly.

        Returns:
            The full object body.

        Raises:
            S3KeyNotFoundError: If the blob does not exist.
        """
        h = self._resolve_hash(info_or_hash)
        return self._bucket.path(self._key_for(h)).read_bytes()

    def open(self, info_or_hash: Union[FileInfo, str]) -> BinaryIO:
        """Return a binary file-like for the stored blob.

        The returned object is the underlying boto3 ``StreamingBody``;
        it supports ``.read(n)`` and iteration but must be consumed (or
        closed) by the caller.

        Args:
            info_or_hash: A :class:`FileInfo` with ``hash`` set, or the
                hash string directly.

        Returns:
            A binary stream positioned at the start of the body.

        Raises:
            S3KeyNotFoundError: If the blob does not exist.
        """
        h = self._resolve_hash(info_or_hash)
        key = self._key_for(h)
        try:
            obj = self._client.boto_client.get_object(
                Bucket=self._bucket.name,
                Key=key,
            )
        except self._client.boto_client.exceptions.NoSuchKey as exc:
            raise S3KeyNotFoundError(
                f"No managed blob at s3://{self._bucket.name}/{key}",
            ) from exc
        return cast("BinaryIO", obj["Body"])

    def exists(self, info_or_hash: Union[FileInfo, str]) -> bool:
        """Return ``True`` if a blob is present for the given hash."""
        h = self._resolve_hash(info_or_hash)
        return self._bucket.path(self._key_for(h)).is_key()

    # Delete

    def delete(self, info_or_hash: Union[FileInfo, str]) -> None:
        """Delete the stored blob unconditionally.

        Refcounting (if any) is the caller's job — they own the
        database. This method is a no-op when the blob is already
        absent so it is safe to call from a teardown path.

        Args:
            info_or_hash: A :class:`FileInfo` with ``hash`` set, or the
                hash string directly.
        """
        h = self._resolve_hash(info_or_hash)
        self._bucket.path(self._key_for(h)).remove(missing_ok=True)

    # Verify

    def verify(self, info_or_hash: Union[FileInfo, str]) -> CheckResult:
        """Stream the blob, recompute its hash, and compare.

        Args:
            info_or_hash: A :class:`FileInfo` with ``hash`` set, or the
                hash string directly.

        Returns:
            A :class:`CheckResult` with code :class:`IssueCode.OK` on
            match or :class:`IssueCode.HASH_MISMATCH` otherwise. The
            ``filename`` field carries the hash string.

        Raises:
            S3KeyNotFoundError: If the blob does not exist.
        """
        h = self._resolve_hash(info_or_hash)
        alg, expected_digest = parse_hash(h)
        key = self._key_for(h)
        path = self._bucket.path(key)
        if not path.is_key():
            raise S3KeyNotFoundError(
                f"No managed blob at s3://{self._bucket.name}/{key}",
            )
        obj = self._client.boto_client.get_object(
            Bucket=self._bucket.name,
            Key=key,
        )
        actual = hash_stream(cast("BinaryIO", obj["Body"]), alg)
        _, actual_digest = parse_hash(actual)
        if actual_digest != expected_digest:
            return CheckResult(
                filename=h,
                code=IssueCode.HASH_MISMATCH,
                detail=f"{alg}: expected {expected_digest}, got {actual_digest}",
                expected=h,
                actual=actual,
            )
        return CheckResult(filename=h, code=IssueCode.OK)

    def verify_strict(self, info_or_hash: Union[FileInfo, str]) -> None:
        """Like :meth:`verify` but raise on mismatch.

        Args:
            info_or_hash: A :class:`FileInfo` with ``hash`` set, or the
                hash string directly.

        Raises:
            HashMismatchError: If the recomputed hash disagrees with the
                requested one.
            S3KeyNotFoundError: If the blob does not exist.
        """
        result = self.verify(info_or_hash)
        if result.code is IssueCode.HASH_MISMATCH:
            raise HashMismatchError(result.detail or "Hash mismatch")


__all__ = ["ManagedStore"]
