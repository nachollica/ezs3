"""S3 :class:`Bucket` handle: name + client + path-style helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, List, Optional, Union

from ._client import Client, get_default_client

if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from ._path import S3Path


class Bucket:
    """Handle on a named S3 bucket bound to a :class:`Client`.

    A :class:`Bucket` is identified solely by its name; equality/hash ignore the
    bound client so the same bucket reached through different clients compares
    equal.
    """

    __slots__ = ("_client", "name")

    def __init__(self, name: str, *, client: Optional[Client] = None) -> None:
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
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(("ezs3.Bucket", self.name))

    def __truediv__(self, other: Union[str, S3Path]) -> S3Path:
        from ._path import S3Path

        return S3Path(self) / other

    # Properties

    @property
    def client(self) -> Client:
        return self._client

    @property
    def root(self) -> S3Path:
        """Return the :class:`S3Path` representing the bucket root."""
        from ._path import S3Path

        return S3Path(self)

    # Bucket lifecycle

    def exists(self) -> bool:
        return self._client.bucket_exists(self.name)

    def create(self, *, exists_ok: bool = False, **extra: Any) -> Bucket:
        return self._client.create_bucket(self.name, exists_ok=exists_ok, **extra)

    def delete(self, *, force: bool = False, missing_ok: bool = False) -> None:
        self._client.delete_bucket(self.name, force=force, missing_ok=missing_ok)

    def clear(self) -> None:
        """Delete every object (and version) in this bucket."""
        self.root.rmtree()

    # Path-style operations (delegate to S3Path)

    def path(self, *parts: Union[str, S3Path]) -> S3Path:
        """Return an :class:`S3Path` attached to this bucket."""
        from ._path import S3Path

        return S3Path(self, *parts)

    def exists_key(self, key: Union[str, S3Path]) -> bool:
        return self.path(key).exists()

    def is_prefix(self, key: Union[str, S3Path]) -> bool:
        return self.path(key).is_prefix()

    def is_key(self, key: Union[str, S3Path]) -> bool:
        return self.path(key).is_key()

    def read_bytes(self, key: Union[str, S3Path]) -> bytes:
        return self.path(key).read_bytes()

    def read_text(
        self,
        key: Union[str, S3Path],
        encoding: str = "utf-8",
    ) -> str:
        return self.path(key).read_text(encoding=encoding)

    def write_bytes(self, key: Union[str, S3Path], data: bytes) -> int:
        return self.path(key).write_bytes(data)

    def write_text(
        self,
        key: Union[str, S3Path],
        data: str,
        encoding: str = "utf-8",
    ) -> int:
        return self.path(key).write_text(data, encoding=encoding)

    def remove(
        self,
        *keys: Union[str, S3Path],
        missing_ok: bool = False,
    ) -> None:
        """Delete one or more keys. Batches into ``DeleteObjects`` calls."""
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
        """Recursively yield every key under ``prefix``."""
        return self.path(prefix).find()

    def iterdir(
        self,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        return self.path(prefix).iterdir()

    def glob(
        self,
        pattern: str,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        return self.path(prefix).glob(pattern)

    def rglob(
        self,
        pattern: str,
        prefix: Union[str, S3Path] = "",
    ) -> Iterator[S3Path]:
        return self.path(prefix).rglob(pattern)


def _delete_keys(bucket: Bucket, keys: List[str], *, missing_ok: bool) -> None:
    """Batch ``DeleteObjects`` in chunks of 1000."""
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
