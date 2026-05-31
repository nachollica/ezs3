"""S3 :class:`Bucket` handle: name + client + path-style helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, List, Optional, Union

from ._client import Client, get_default_client

if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from ._path import S3Path


class Bucket:
    """Handle on a named S3 bucket bound to a :class:`~ezs3.Client`.

    A :class:`Bucket` is identified by the pair *(name, client)*: two handles
    are equal only when their names match **and** their bound clients compare
    equal. The same bucket name reached through clients backed by different
    credentials is therefore treated as a different handle, since the two
    clients may carry different IAM permissions. The slash operator returns
    an :class:`~ezs3.S3Path` attached to this bucket:

    Example:
        >>> import ezs3
        >>> bucket = ezs3.Bucket("my-bucket")
        >>> path = bucket / "logs" / "2024-01.txt"
        >>> path.write_text("hello")
    """

    __slots__ = ("_client", "name")

    def __init__(self, name: str, *, client: Optional[Client] = None) -> None:
        """Initialize a bucket handle.

        Args:
            name: The bucket name. Must be a non-empty string.
            client: The :class:`~ezs3.Client` to use for I/O. Defaults to the
                process-wide default client.

        Raises:
            ValueError: If ``name`` is empty or not a string.
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"Bucket name must be a non-empty str, got {name!r}")
        self.name: str = name
        self._client: Client = client if client is not None else get_default_client()

    # Dunders

    def __repr__(self) -> str:
        return f"Bucket({self.name!r})"

    def __str__(self) -> str:
        return f"s3://{self.name}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Bucket):
            return NotImplemented
        return self.name == other.name and self._client == other._client

    def __hash__(self) -> int:
        return hash(("ezs3.Bucket", self.name, self._client))

    def __truediv__(self, other: Union[str, S3Path]) -> S3Path:
        from ._path import S3Path

        return S3Path(self) / other

    # Properties

    @property
    def client(self) -> Client:
        """The :class:`~ezs3.Client` this bucket talks to."""
        return self._client

    @property
    def root(self) -> S3Path:
        """The :class:`~ezs3.S3Path` representing this bucket's root."""
        from ._path import S3Path

        return S3Path(self)

    # Bucket lifecycle

    def exists(self) -> bool:
        """Check whether this bucket exists and is accessible.

        Returns:
            ``True`` if the bucket exists on the configured endpoint.
        """
        return self._client.bucket_exists(self.name)

    def create(self, *, exists_ok: bool = False, **extra: Any) -> Bucket:
        """Create this bucket on the remote service.

        Args:
            exists_ok: When ``True``, suppress
                :class:`~ezs3.BucketAlreadyExistsError`.
            **extra: Additional kwargs forwarded to
                :meth:`Client.create_bucket`.

        Returns:
            This :class:`Bucket` (for chaining).
        """
        return self._client.create_bucket(self.name, exists_ok=exists_ok, **extra)

    def delete(self, *, force: bool = False, missing_ok: bool = False) -> None:
        """Delete this bucket.

        Args:
            force: When ``True``, empty the bucket first.
            missing_ok: When ``True``, suppress
                :class:`~ezs3.BucketNotFoundError`.
        """
        self._client.delete_bucket(self.name, force=force, missing_ok=missing_ok)

    def clear(self) -> None:
        """Delete every object (and version) in this bucket.

        Equivalent to :meth:`S3Path.rmtree` on the bucket root.
        """
        self.root.rmtree()

    # Path-style operations (delegate to S3Path)

    def path(self, *parts: Union[str, S3Path]) -> S3Path:
        """Return an :class:`~ezs3.S3Path` attached to this bucket.

        Args:
            *parts: Path components, joined with ``/``. Strings containing
                ``/`` are split. Existing :class:`~ezs3.S3Path` values
                contribute their parts (but not their bucket).

        Returns:
            The composed :class:`~ezs3.S3Path`.
        """
        from ._path import S3Path

        return S3Path(self, *parts)

    def exists_key(self, key: Union[str, S3Path]) -> bool:
        """Check whether ``key`` exists in this bucket (as key or prefix)."""
        return self.path(key).exists()

    def is_prefix(self, key: Union[str, S3Path]) -> bool:
        """Check whether ``key`` resolves to a prefix in this bucket."""
        return self.path(key).is_prefix()

    def is_key(self, key: Union[str, S3Path]) -> bool:
        """Check whether ``key`` resolves to an object in this bucket."""
        return self.path(key).is_key()

    def read_bytes(self, key: Union[str, S3Path]) -> bytes:
        """Read the contents of ``key`` as raw bytes.

        Args:
            key: Key to read, relative to this bucket.

        Returns:
            The raw object body.
        """
        return self.path(key).read_bytes()

    def read_text(
        self,
        key: Union[str, S3Path],
        encoding: str = "utf-8",
    ) -> str:
        """Read the contents of ``key`` as decoded text.

        Args:
            key: Key to read, relative to this bucket.
            encoding: Text codec used to decode the body.

        Returns:
            The decoded text payload.
        """
        return self.path(key).read_text(encoding=encoding)

    def write_bytes(self, key: Union[str, S3Path], data: bytes) -> int:
        """Write ``data`` to ``key`` as raw bytes.

        Args:
            key: Destination key relative to this bucket.
            data: Payload to upload.

        Returns:
            The number of bytes written.
        """
        return self.path(key).write_bytes(data)

    def write_text(
        self,
        key: Union[str, S3Path],
        data: str,
        encoding: str = "utf-8",
    ) -> int:
        """Write ``data`` to ``key`` as encoded text.

        Args:
            key: Destination key relative to this bucket.
            data: Unicode payload to upload.
            encoding: Codec used to encode ``data``.

        Returns:
            The number of bytes written.
        """
        return self.path(key).write_text(data, encoding=encoding)

    def remove(
        self,
        *keys: Union[str, S3Path],
        missing_ok: bool = False,
    ) -> None:
        """Delete one or more keys in batched ``DeleteObjects`` calls.

        Args:
            *keys: Keys to delete. Strings are resolved relative to this
                bucket. :class:`~ezs3.S3Path` values attached to another
                bucket raise :class:`~ezs3.BucketMismatchError`.
            missing_ok: When ``True``, silently ignore per-key errors
                returned by S3.

        Raises:
            BucketMismatchError: If any passed path is attached to a
                different bucket.
            S3Error: If S3 reports a partial deletion failure and
                ``missing_ok`` is ``False``.
        """
        from ._exceptions import BucketMismatchError
        from ._path import S3Path

        resolved: List[str] = []
        for k in keys:
            if isinstance(k, S3Path):
                if k.bucket is not None and k.bucket != self:
                    raise BucketMismatchError(
                        f"Path {k!s} belongs to bucket {k.bucket.name!r}, not {self.name!r}",
                    )
                resolved.append(k.key)
            else:
                resolved.append(self.path(k).key)
        _delete_keys(self, resolved, missing_ok=missing_ok)

    rm = remove

    def find(
        self,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        """Recursively yield every key under ``prefix``.

        Args:
            prefix: Sub-prefix to walk. Defaults to the bucket root.

        Yields:
            One :class:`~ezs3.S3Path` per object under ``prefix``.
        """
        return self.path(prefix).find()

    def iterdir(
        self,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        """Yield immediate children of ``prefix`` (one level deep).

        Args:
            prefix: Sub-prefix to list. Defaults to the bucket root.

        Yields:
            One :class:`~ezs3.S3Path` per direct child, which may be either a
            key or a sub-prefix.
        """
        return self.path(prefix).iterdir()

    def glob(
        self,
        pattern: str,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        """Glob ``pattern`` relative to ``prefix`` in this bucket.

        Args:
            pattern: Pathlib-style glob pattern.
            prefix: Sub-prefix to anchor the glob to. Defaults to the
                bucket root.

        Yields:
            Each matching :class:`~ezs3.S3Path`.
        """
        return self.path(prefix).glob(pattern)

    def rglob(
        self,
        pattern: str,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        """Recursive glob (equivalent to :meth:`S3Path.rglob`).

        Args:
            pattern: Pathlib-style glob pattern.
            prefix: Sub-prefix to anchor the glob to. Defaults to the
                bucket root.

        Yields:
            Each matching :class:`~ezs3.S3Path`, recursively.
        """
        return self.path(prefix).rglob(pattern)


def _delete_keys(bucket: Bucket, keys: List[str], *, missing_ok: bool) -> None:
    """Batch ``DeleteObjects`` calls in chunks of 1000.

    Args:
        bucket: Target bucket.
        keys: Resolved key strings to delete.
        missing_ok: Suppress partial-failure errors when ``True``.

    Raises:
        S3Error: When S3 reports failures and ``missing_ok`` is ``False``.
    """
    if not keys:
        return
    client = bucket._client._boto_client
    chunk: List[ObjectIdentifierTypeDef] = []
    for k in keys:
        if not k:
            continue
        chunk.append({"Key": k})
        if len(chunk) == 1000:
            client.delete_objects(Bucket=bucket.name, Delete={"Objects": chunk})
            chunk = []
    if chunk:
        result = client.delete_objects(Bucket=bucket.name, Delete={"Objects": chunk})
        if not missing_ok:
            errors = result.get("Errors") or []
            if errors:
                from ._exceptions import S3Error

                raise S3Error(f"Failed to delete keys: {errors!r}")


__all__ = ["Bucket"]
