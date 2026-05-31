"""Path-like abstraction over S3 keys and prefixes.

A single class :class:`S3Path` covers both keys (files) and prefixes
(directories), the same way :class:`pathlib.PurePath` covers both. ``Prefix``
and ``Key`` are exported as aliases so callers may choose whichever name best
documents intent.

The actual nature (key vs prefix) is determined by introspecting the remote
state: :meth:`S3Path.is_key` and :meth:`S3Path.is_prefix`. Both are ``False``
for paths that have not been materialized yet.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO, TYPE_CHECKING, Iterator, List, Optional, Set, Tuple, Union

from botocore.exceptions import ClientError

from ._exceptions import (
    BucketMismatchError,
    IsAPrefixError,
    NotAPrefixError,
    PathNotAttachedError,
    S3KeyExistsError,
    S3KeyNotFoundError,
)

#: Accepted destination for :meth:`S3Path.download` (or upload source).
#:
#: A ``str`` is interpreted as a filesystem path and coerced to
#: :class:`pathlib.Path`. :class:`pathlib.Path` is opened in binary mode.
#: Anything else must be a binary file-like object (e.g. :class:`io.BytesIO`
#: or a file opened in ``"rb"``/``"wb"`` mode).
LocalTarget = Union[str, Path, IO[bytes]]

if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import (
        ListObjectsV2RequestPaginateTypeDef,
        ObjectIdentifierTypeDef,
        PutObjectRequestTypeDef,
    )
    from mypy_boto3_s3.type_defs import (
        PutObjectRequestObjectPutTypeDef as PutObjectKwargs,
    )
    from typing_extensions import Unpack

    from ._bucket import Bucket


_S3_SCHEME = "s3://"


def _split(s: str) -> List[str]:
    """Split a posix-style string into non-empty parts.

    Args:
        s: String to split on ``/``.

    Returns:
        Non-empty components in order.
    """
    return [p for p in s.split("/") if p]


def _coerce_part(arg: Union[str, S3Path]) -> List[str]:
    """Normalize a single ``__init__`` part argument into key components.

    Args:
        arg: A string (possibly containing ``/``) or another :class:`S3Path`.

    Returns:
        The contributed key components.

    Raises:
        TypeError: If ``arg`` is neither a string nor an :class:`S3Path`.
    """
    if isinstance(arg, S3Path):
        return list(arg._parts)
    if isinstance(arg, str):
        return _split(arg)
    raise TypeError(
        f"S3Path parts must be str or S3Path, got {type(arg).__name__}",
    )


class S3Path:
    """A path-like object representing an S3 location.

    There are four supported construction forms:

    * ``S3Path("a/b/c")`` — a *free* path with no bucket attached.
    * ``S3Path(bucket, "a", "b/c")`` — attached to a :class:`~ezs3.Bucket`.
    * ``S3Path("my-bucket", "a/b/c")`` — attached via bucket name (2+ args).
    * ``S3Path("s3://my-bucket/a/b/c")`` — parsed from a full S3 URI.

    Free paths are useful as keys or prefixes that may later be attached to a
    bucket via :meth:`attach`. Attached paths can perform I/O directly.

    Example:
        >>> import ezs3
        >>> bucket = ezs3.Bucket("my-bucket")
        >>> path = bucket / "logs" / "2024.txt"
        >>> path.write_text("hello")
        >>> path.read_text()
        'hello'
    """

    __slots__ = ("_bucket", "_parts")

    def __init__(
        self,
        *args: Union[str, Bucket, S3Path],
    ) -> None:
        """Build a new :class:`S3Path`.

        Args:
            *args: Path components or a leading bucket designator. See the
                class docstring for the four supported forms.

        Raises:
            TypeError: If no arguments are given, the first argument has an
                unsupported type, or a non-leading argument is a
                :class:`~ezs3.Bucket`.
            ValueError: If an ``s3://`` URI is given with an empty bucket
                name.
        """
        from ._bucket import Bucket

        if not args:
            raise TypeError("S3Path requires at least one argument")

        first = args[0]
        rest: Tuple[Union[str, Bucket, S3Path], ...] = args[1:]
        bucket: Optional[Bucket] = None
        parts: List[str] = []

        if isinstance(first, Bucket):
            bucket = first
        elif isinstance(first, S3Path):
            bucket = first._bucket
            parts.extend(first._parts)
        elif isinstance(first, str):
            if first.startswith(_S3_SCHEME):
                stripped = first[len(_S3_SCHEME) :]
                name, _, key_part = stripped.partition("/")
                if not name:
                    raise ValueError(f"Invalid S3 URI: {first!r}")
                bucket = Bucket(name)
                parts.extend(_split(key_part))
            elif len(args) >= 2:
                bucket = Bucket(first)
            else:
                parts.extend(_split(first))
        else:
            raise TypeError(
                f"S3Path first argument must be str, Bucket, or S3Path, "
                f"got {type(first).__name__}",
            )

        for a in rest:
            if isinstance(a, Bucket):
                raise TypeError("Only the first positional argument may be a Bucket")
            parts.extend(_coerce_part(a))

        self._bucket: Optional[Bucket] = bucket
        self._parts: Tuple[str, ...] = tuple(parts)

    # Dunders

    def __str__(self) -> str:
        joined = "/".join(self._parts)
        if self._bucket is None:
            return joined
        return f"s3://{self._bucket.name}/{joined}" if joined else f"s3://{self._bucket.name}/"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, S3Path):
            return NotImplemented
        return self._bucket == other._bucket and self._parts == other._parts

    def __hash__(self) -> int:
        name = self._bucket.name if self._bucket is not None else None
        return hash(("ezs3.S3Path", name, self._parts))

    def __fspath__(self) -> str:
        return str(self)

    def __truediv__(self, other: Union[str, S3Path]) -> S3Path:
        more = _coerce_part(other)
        new_parts = (*self._parts, *more)
        if self._bucket is not None:
            return S3Path(self._bucket, *new_parts) if new_parts else S3Path(self._bucket)
        return S3Path("/".join(new_parts)) if new_parts else S3Path("")

    def __rtruediv__(self, other: str) -> S3Path:
        if isinstance(other, str):
            return S3Path(other) / self
        return NotImplemented  # type: ignore[return-value]

    # Properties

    @property
    def bucket(self) -> Optional[Bucket]:
        """The :class:`~ezs3.Bucket` this path is attached to, or ``None``."""
        return self._bucket

    @property
    def parts(self) -> Tuple[str, ...]:
        """The tuple of non-empty key components, in order."""
        return self._parts

    @property
    def key(self) -> str:
        """The object key string for this path (no leading slash, no scheme)."""
        return "/".join(self._parts)

    @property
    def name(self) -> str:
        """The final component of the path, or ``''`` for the root."""
        return self._parts[-1] if self._parts else ""

    @property
    def stem(self) -> str:
        """The final component of the path without its suffix."""
        n = self.name
        if not n or "." not in n:
            return n
        return n.rsplit(".", 1)[0]

    @property
    def suffix(self) -> str:
        """The final component's extension (including the leading dot)."""
        n = self.name
        if not n or "." not in n:
            return ""
        return "." + n.rsplit(".", 1)[1]

    @property
    def parent(self) -> S3Path:
        """The path with the final component removed.

        For one-component paths the parent is the bucket root (or the empty
        free path); the root is its own parent.
        """
        if len(self._parts) <= 1:
            if self._bucket is not None:
                return S3Path(self._bucket)
            return S3Path("")
        if self._bucket is not None:
            return S3Path(self._bucket, *self._parts[:-1])
        return S3Path("/".join(self._parts[:-1]))

    @property
    def parents(self) -> List[S3Path]:
        """List of ancestors, from immediate parent to root."""
        result: List[S3Path] = []
        node = self
        while node._parts:
            node = node.parent
            result.append(node)
        return result

    # Bucket-attachment helpers

    def is_attached(self) -> bool:
        """Return ``True`` if the path is bound to a :class:`~ezs3.Bucket`."""
        return self._bucket is not None

    def attach(self, bucket: Union[str, Bucket]) -> S3Path:
        """Return a copy of this path attached to ``bucket``.

        Args:
            bucket: Target bucket as a name or :class:`~ezs3.Bucket`.

        Returns:
            A new :class:`S3Path` with the same key parts bound to ``bucket``.
        """
        from ._bucket import Bucket

        new_bucket = bucket if isinstance(bucket, Bucket) else Bucket(bucket)
        return S3Path(new_bucket, *self._parts)

    def detach(self) -> S3Path:
        """Return a free copy of this path (no bucket attached)."""
        return S3Path("/".join(self._parts)) if self._parts else S3Path("")

    def _require_bucket(self) -> Bucket:
        """Return the attached bucket or raise.

        Raises:
            PathNotAttachedError: If the path is free.
        """
        if self._bucket is None:
            raise PathNotAttachedError(
                f"Path {self!s} is not attached to a bucket; "
                "use S3Path(bucket, ...) or path.attach(bucket).",
            )
        return self._bucket

    def _assert_same_bucket(self, bucket: Bucket) -> None:
        """Raise if this path is attached to a different bucket.

        Args:
            bucket: Bucket expected to match.

        Raises:
            BucketMismatchError: If buckets differ.
        """
        if self._bucket is not None and self._bucket != bucket:
            raise BucketMismatchError(
                f"Path {self!s} belongs to {self._bucket.name!r}, not {bucket.name!r}",
            )

    # Existence / classification

    def is_key(self) -> bool:
        """Check whether this path identifies an existing S3 object.

        Returns:
            ``True`` if a ``HEAD`` request for the key succeeds.
        """
        if not self._parts:
            return False
        bucket = self._require_bucket()
        try:
            bucket.client.boto_client.head_object(Bucket=bucket.name, Key=self.key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def is_prefix(self) -> bool:
        """Check whether this path is a non-empty prefix.

        Returns:
            ``True`` if at least one object exists with this path as a
            prefix.
        """
        bucket = self._require_bucket()
        prefix = self.key
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        response = bucket.client.boto_client.list_objects_v2(
            Bucket=bucket.name,
            Prefix=prefix,
            MaxKeys=1,
        )
        return response.get("KeyCount", 0) > 0 or bool(response.get("Contents"))

    is_dir = is_prefix
    is_file = is_key

    def exists(self) -> bool:
        """Return ``True`` if the path exists as either a key or a prefix."""
        return self.is_key() or self.is_prefix()

    # I/O

    def read_bytes(self) -> bytes:
        """Read the object body for this key as raw bytes.

        Returns:
            The full object body.

        Raises:
            IsAPrefixError: If the path is the bucket root or resolves to a
                prefix rather than a key.
            S3KeyNotFoundError: If the key does not exist.
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        if not self._parts:
            raise IsAPrefixError(f"Cannot read root of bucket as key: {self!s}")
        try:
            obj = bucket.client.boto_client.get_object(
                Bucket=bucket.name,
                Key=self.key,
            )
            return obj["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                if self.is_prefix():
                    raise IsAPrefixError(
                        f"Path is a prefix, not a key: {self!s}",
                    ) from exc
                raise S3KeyNotFoundError(f"Key not found: {self!s}") from exc
            raise

    def read_text(self, encoding: str = "utf-8") -> str:
        """Read the object body for this key as decoded text.

        Args:
            encoding: Text codec used to decode the body.

        Returns:
            The decoded text payload.

        Raises:
            IsAPrefixError: See :meth:`read_bytes`.
            S3KeyNotFoundError: See :meth:`read_bytes`.
            PathNotAttachedError: See :meth:`read_bytes`.
        """
        return self.read_bytes().decode(encoding)

    def write_bytes(
        self,
        data: bytes,
        **put_object_kwargs: Unpack[PutObjectKwargs],
    ) -> int:
        """Upload ``data`` to this key.

        Args:
            data: Raw bytes to upload as the object body.
            **put_object_kwargs: Additional kwargs forwarded to
                ``boto_client.put_object`` (e.g. ``ContentType``,
                ``Metadata``). Statically typed by
                :class:`mypy_boto3_s3.type_defs.PutObjectRequestObjectPutTypeDef`. ``Bucket`` and
                ``Key`` are filled by ezs3 and absent from the TypedDict;
                ``Body`` may technically appear but will be overridden
                by ``data``.

        Returns:
            The number of bytes written.

        Raises:
            IsAPrefixError: If the path is the bucket root.
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        if not self._parts:
            raise IsAPrefixError(f"Cannot write to bucket root: {self!s}")
        params: PutObjectRequestTypeDef = {
            **put_object_kwargs,
            "Bucket": bucket.name,
            "Key": self.key,
            "Body": data,
        }
        bucket.client.boto_client.put_object(**params)
        return len(data)

    def write_text(
        self,
        data: str,
        encoding: str = "utf-8",
        **put_object_kwargs: Unpack[PutObjectKwargs],
    ) -> int:
        """Encode and upload ``data`` to this key.

        Args:
            data: Unicode payload to upload.
            encoding: Codec used to encode ``data``.
            **put_object_kwargs: Forwarded to :meth:`write_bytes`.
                Statically typed by
                :class:`mypy_boto3_s3.type_defs.PutObjectRequestObjectPutTypeDef`.

        Returns:
            The number of bytes written.

        Raises:
            IsAPrefixError: See :meth:`write_bytes`.
            PathNotAttachedError: See :meth:`write_bytes`.
        """
        return self.write_bytes(data.encode(encoding), **put_object_kwargs)

    def download(
        self,
        dest: LocalTarget,
        *,
        create_parents: bool = False,
    ) -> int:
        """Download this key to a local file or binary stream.

        The transfer is always byte-exact: text and binary payloads are
        written verbatim. Decode afterwards if you need text (e.g. open the
        downloaded :class:`~pathlib.Path` with ``encoding="utf-8"``).

        Args:
            dest: Destination. A :class:`str` is coerced to
                :class:`pathlib.Path`. A :class:`~pathlib.Path` is created
                (or truncated) and written in binary mode. Any other value
                must implement ``write(bytes)`` (e.g. :class:`io.BytesIO` or
                a file opened in ``"wb"``).
            create_parents: When ``True`` and ``dest`` is a path, missing
                parent directories are created (``mkdir -p`` semantics).
                Ignored for stream destinations.

        Returns:
            Number of bytes written.

        Raises:
            IsAPrefixError: If this path is the bucket root or resolves to
                a prefix.
            S3KeyNotFoundError: If the key does not exist.
            PathNotAttachedError: If the path has no attached bucket.
            FileNotFoundError: If ``dest`` parent directory does not exist
                and ``create_parents`` is ``False``.
        """
        data = self.read_bytes()
        if isinstance(dest, str):
            dest = Path(dest)
        if isinstance(dest, Path):
            if create_parents:
                dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        else:
            dest.write(data)
        return len(data)

    def upload(
        self,
        src: LocalTarget,
        *,
        overwrite: bool = False,
        **put_object_kwargs: Unpack[PutObjectKwargs],
    ) -> int:
        """Upload a local file or binary stream to this key.

        The payload is uploaded byte-exact: encode text upstream if needed.

        Args:
            src: Source. A :class:`str` is coerced to :class:`pathlib.Path`
                and read in binary mode (must exist). A
                :class:`~pathlib.Path` is read in binary mode. Any other
                value must implement ``read() -> bytes`` (e.g.
                :class:`io.BytesIO` or a file opened in ``"rb"``).
            overwrite: When ``False`` (the default), refuse to clobber an
                existing key and raise :class:`~ezs3.S3KeyExistsError`.
                When ``True``, replace silently.
            **put_object_kwargs: Forwarded to :meth:`write_bytes`.
                Statically typed by
                :class:`mypy_boto3_s3.type_defs.PutObjectRequestObjectPutTypeDef`.

        Returns:
            Number of bytes uploaded.

        Raises:
            IsAPrefixError: If this path is the bucket root.
            S3KeyExistsError: If the key already exists and ``overwrite`` is
                ``False``.
            FileNotFoundError: If ``src`` is a path that does not exist.
            PathNotAttachedError: If the path has no attached bucket.
        """
        if isinstance(src, str):
            src = Path(src)
        if isinstance(src, Path):
            data = src.read_bytes()
        else:
            data = src.read()
        if not overwrite and self.is_key():
            raise S3KeyExistsError(
                f"Key already exists; pass overwrite=True to replace: {self!s}",
            )
        return self.write_bytes(data, **put_object_kwargs)

    # Listing / traversal

    def iterdir(self) -> Iterator[S3Path]:
        """Yield immediate children (one level deep).

        Mirrors :meth:`pathlib.Path.iterdir`: children that exist as keys are
        yielded as keys, and children that exist as sub-prefixes are yielded
        as prefixes.

        Yields:
            One :class:`S3Path` per direct child.

        Raises:
            NotAPrefixError: If the path resolves to a key.
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        if self.is_key():
            raise NotAPrefixError(f"Not a prefix: {self!s}")
        prefix = self.key
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = bucket.client.boto_client.get_paginator("list_objects_v2")
        seen: Set[str] = set()
        for page in paginator.paginate(
            Bucket=bucket.name,
            Prefix=prefix,
            Delimiter="/",
        ):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if key == prefix:
                    continue
                tail = key[len(prefix) :]
                if not tail:
                    continue
                if tail in seen:
                    continue
                seen.add(tail)
                yield S3Path(bucket, prefix + tail)
            for cp in page.get("CommonPrefixes") or []:
                full = cp["Prefix"]
                tail = full[len(prefix) :].rstrip("/")
                if not tail or tail in seen:
                    continue
                seen.add(tail)
                yield S3Path(bucket, prefix + tail)

    def find(self) -> Iterator[S3Path]:
        """Recursively yield every key under this prefix.

        Yields:
            One :class:`S3Path` per object, in S3 lexicographic order.

        Raises:
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        prefix = self.key
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = bucket.client.boto_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket.name, Prefix=prefix):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if key == prefix:
                    continue
                yield S3Path(bucket, key)

    def glob(self, pattern: str) -> Iterator[S3Path]:
        """Glob children of this prefix.

        Supports ``*``, ``?``, ``[abc]`` (one segment) and ``**`` (any number
        of segments). Same semantics as :meth:`pathlib.Path.glob`.

        Args:
            pattern: Pathlib-style glob pattern. Must not be empty.

        Yields:
            Each matching :class:`S3Path`.

        Raises:
            ValueError: If ``pattern`` is empty.
            PathNotAttachedError: If the path has no attached bucket.
        """
        if not pattern:
            raise ValueError("glob pattern must not be empty")
        return self._glob(pattern)

    def rglob(self, pattern: str) -> Iterator[S3Path]:
        """Recursive glob.

        Equivalent to ``self.glob("**/" + pattern)``.

        Args:
            pattern: Pathlib-style glob pattern.

        Yields:
            Each matching :class:`S3Path`, at any depth under this prefix.

        Raises:
            PathNotAttachedError: If the path has no attached bucket.
        """
        return self._glob("**/" + pattern)

    def _glob(self, pattern: str) -> Iterator[S3Path]:
        """Shared implementation for :meth:`glob` and :meth:`rglob`."""
        bucket = self._require_bucket()
        base = self.key
        if base and not base.endswith("/"):
            base += "/"

        static = _static_prefix(pattern)
        full_prefix = base + static
        recursive = "**" in pattern
        regex = _glob_to_regex(pattern)

        paginator = bucket.client.boto_client.get_paginator("list_objects_v2")
        paginate_kwargs: ListObjectsV2RequestPaginateTypeDef = {
            "Bucket": bucket.name,
            "Prefix": full_prefix,
        }
        if not recursive:
            paginate_kwargs["Delimiter"] = "/"

        seen: Set[str] = set()
        for page in paginator.paginate(**paginate_kwargs):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if key == base:
                    continue
                relative = key[len(base) :]
                if regex.match(relative) and relative not in seen:
                    seen.add(relative)
                    yield S3Path(bucket, key)
            for cp in page.get("CommonPrefixes") or []:
                full = cp["Prefix"]
                relative = full[len(base) :].rstrip("/")
                if regex.match(relative) and relative not in seen:
                    seen.add(relative)
                    yield S3Path(bucket, full.rstrip("/"))

    # Deletion

    def remove(self, *, missing_ok: bool = False) -> None:
        """Delete this key.

        Args:
            missing_ok: When ``True``, suppress
                :class:`~ezs3.S3KeyNotFoundError`.

        Raises:
            IsAPrefixError: If the path resolves to a prefix. Use
                :meth:`rmtree` for recursive deletion.
            S3KeyNotFoundError: If the key does not exist and ``missing_ok``
                is ``False``.
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        if not self._parts:
            raise IsAPrefixError(f"Cannot remove bucket root: {self!s}")
        if not self.is_key():
            if self.is_prefix():
                raise IsAPrefixError(
                    f"Path is a prefix; use rmtree() to delete recursively: {self!s}",
                )
            if missing_ok:
                return
            raise S3KeyNotFoundError(f"Key not found: {self!s}")
        bucket.client.boto_client.delete_object(Bucket=bucket.name, Key=self.key)

    rm = remove

    def rmtree(self) -> None:
        """Recursively delete every key under this prefix.

        Equivalent to :func:`shutil.rmtree` on a directory: silently
        succeeds even if the prefix has no children.

        Raises:
            PathNotAttachedError: If the path has no attached bucket.
        """
        bucket = self._require_bucket()
        prefix = self.key
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        client = bucket.client.boto_client
        paginator = client.get_paginator("list_objects_v2")
        chunk: List[ObjectIdentifierTypeDef] = []
        for page in paginator.paginate(Bucket=bucket.name, Prefix=prefix):
            for obj in page.get("Contents") or []:
                chunk.append({"Key": obj["Key"]})
                if len(chunk) == 1000:
                    client.delete_objects(
                        Bucket=bucket.name,
                        Delete={"Objects": chunk},
                    )
                    chunk = []
        if chunk:
            client.delete_objects(Bucket=bucket.name, Delete={"Objects": chunk})

    # Convenience

    def with_name(self, name: str) -> S3Path:
        """Return a new path with the final component replaced by ``name``.

        Args:
            name: Replacement final component.

        Returns:
            New :class:`S3Path` with the same parent and the given name.

        Raises:
            ValueError: If this path has no final component (root).
        """
        if not self._parts:
            raise ValueError("S3Path root has no name to replace")
        return self.parent / name

    def with_suffix(self, suffix: str) -> S3Path:
        """Return a new path with ``suffix`` replacing the current one.

        Args:
            suffix: New suffix. Must start with ``.`` unless empty.

        Returns:
            New :class:`S3Path` with the same parent and stem but a different
            suffix.

        Raises:
            ValueError: If ``suffix`` is non-empty and does not start with
                ``.``.
        """
        if suffix and not suffix.startswith("."):
            raise ValueError(f"Invalid suffix {suffix!r}")
        return self.with_name(self.stem + suffix)


def _static_prefix(pattern: str) -> str:
    """Return the portion of ``pattern`` before its first wildcard.

    Args:
        pattern: Glob pattern.

    Returns:
        Leading literal portion of the pattern.
    """
    out: List[str] = []
    for ch in pattern:
        if ch in "*?[":
            break
        out.append(ch)
    return "".join(out)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a pathlib-style glob into a regex matching relative keys.

    Wildcard semantics match :mod:`pathlib`:

    * ``*`` — any number of characters except ``/``.
    * ``?`` — exactly one character except ``/``.
    * ``[abc]`` — single character class.
    * ``**`` — any number of path segments.

    Args:
        pattern: Glob pattern.

    Returns:
        Compiled :class:`re.Pattern` anchored at the end (an optional trailing
        slash is allowed so prefix entries match as well as keys).
    """
    parts: List[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i)
            if j == -1:
                parts.append(re.escape(c))
                i += 1
            else:
                parts.append(pattern[i : j + 1])
                i = j + 1
        elif c == "/":
            parts.append("/")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("".join(parts) + r"/?$")


# Aliases reflecting intent at call sites. They are the same class.
Prefix = S3Path
"""Alias for :class:`S3Path` emphasizing intent: a directory-like S3 prefix."""

Key = S3Path
"""Alias for :class:`S3Path` emphasizing intent: a file-like S3 key."""


__all__ = ["Key", "Prefix", "S3Path"]
