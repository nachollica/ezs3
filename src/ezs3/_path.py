"""Path-like abstraction over S3 keys and prefixes.

A single class :class:`S3Path` covers both keys (files) and prefixes
(directories), the same way :class:`pathlib.PurePath` covers both. ``Prefix`` and
``Key`` are exported as aliases so callers may choose whichever name best
documents intent.

The actual nature (key vs prefix) is determined by introspecting the remote
state: :meth:`S3Path.is_key` and :meth:`S3Path.is_prefix`. Both are ``False``
for paths that have not been materialized yet.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterator, List, Optional, Tuple, Union

from botocore.exceptions import ClientError

from ._exceptions import (
    BucketMismatchError,
    IsAPrefixError,
    NotAPrefixError,
    PathNotAttachedError,
    S3KeyNotFoundError,
)

if TYPE_CHECKING:
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from ._bucket import Bucket


_S3_SCHEME = "s3://"


def _split(s: str) -> List[str]:
    """Split a posix-style string into non-empty parts."""
    return [p for p in s.split("/") if p]


def _coerce_part(arg: Union[str, S3Path]) -> List[str]:
    if isinstance(arg, S3Path):
        return list(arg._parts)
    if isinstance(arg, str):
        return _split(arg)
    raise TypeError(
        f"S3Path parts must be str or S3Path, got {type(arg).__name__}",
    )


class S3Path:
    """A path-like object representing an S3 location.

    Construct in one of three forms:

    * ``S3Path("a/b/c")`` — *free* path with no bucket attached.
    * ``S3Path(bucket, "a", "b/c")`` — attached to a :class:`Bucket` instance.
    * ``S3Path("my-bucket", "a/b/c")`` — attached via bucket name (2+ args).
    * ``S3Path("s3://my-bucket/a/b/c")`` — parsed from a full S3 URI.
    """

    __slots__ = ("_bucket", "_parts")

    def __init__(
        self,
        *args: Union[str, Bucket, S3Path],
    ) -> None:
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
        return self._bucket

    @property
    def parts(self) -> Tuple[str, ...]:
        return self._parts

    @property
    def key(self) -> str:
        """Object key string (no leading slash, no scheme)."""
        return "/".join(self._parts)

    @property
    def name(self) -> str:
        """Final component of the path, or ``''`` for the root."""
        return self._parts[-1] if self._parts else ""

    @property
    def stem(self) -> str:
        n = self.name
        if not n or "." not in n:
            return n
        return n.rsplit(".", 1)[0]

    @property
    def suffix(self) -> str:
        n = self.name
        if not n or "." not in n:
            return ""
        return "." + n.rsplit(".", 1)[1]

    @property
    def parent(self) -> S3Path:
        if len(self._parts) <= 1:
            if self._bucket is not None:
                return S3Path(self._bucket)
            return S3Path("")
        if self._bucket is not None:
            return S3Path(self._bucket, *self._parts[:-1])
        return S3Path("/".join(self._parts[:-1]))

    @property
    def parents(self) -> List[S3Path]:
        result: List[S3Path] = []
        node = self
        while node._parts:
            node = node.parent
            result.append(node)
        return result

    # Bucket-attachment helpers

    def is_attached(self) -> bool:
        return self._bucket is not None

    def attach(self, bucket: Union[str, Bucket]) -> S3Path:
        """Return a copy of this path attached to ``bucket``."""
        from ._bucket import Bucket

        new_bucket = bucket if isinstance(bucket, Bucket) else Bucket(bucket)
        return S3Path(new_bucket, *self._parts)

    def detach(self) -> S3Path:
        """Return a free copy of this path."""
        return S3Path("/".join(self._parts)) if self._parts else S3Path("")

    def _require_bucket(self) -> Bucket:
        if self._bucket is None:
            raise PathNotAttachedError(
                f"Path {self!s} is not attached to a bucket; "
                "use S3Path(bucket, ...) or path.attach(bucket).",
            )
        return self._bucket

    def _assert_same_bucket(self, bucket: Bucket) -> None:
        if self._bucket is not None and self._bucket != bucket:
            raise BucketMismatchError(
                f"Path {self!s} belongs to {self._bucket.name!r}, not {bucket.name!r}",
            )

    # Existence / classification

    def is_key(self) -> bool:
        """``True`` if this path identifies an existing S3 object."""
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
        """``True`` if any object exists under this path treated as a prefix."""
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
        return self.is_key() or self.is_prefix()

    # I/O

    def read_bytes(self) -> bytes:
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
        return self.read_bytes().decode(encoding)

    def write_bytes(self, data: bytes, **put_object_kwargs: object) -> int:
        bucket = self._require_bucket()
        if not self._parts:
            raise IsAPrefixError(f"Cannot write to bucket root: {self!s}")
        bucket.client.boto_client.put_object(
            Bucket=bucket.name,
            Key=self.key,
            Body=data,
            **put_object_kwargs,  # type: ignore[arg-type]
        )
        return len(data)

    def write_text(
        self,
        data: str,
        encoding: str = "utf-8",
        **put_object_kwargs: object,
    ) -> int:
        return self.write_bytes(data.encode(encoding), **put_object_kwargs)

    # Listing / traversal

    def iterdir(self) -> Iterator[S3Path]:
        """Yield immediate children (one level deep). Like ``Path.iterdir``."""
        bucket = self._require_bucket()
        if self.is_key():
            raise NotAPrefixError(f"Not a prefix: {self!s}")
        prefix = self.key
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        paginator = bucket.client.boto_client.get_paginator("list_objects_v2")
        seen: set = set()
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
        """Recursively yield every key under this prefix."""
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
        of segments) — same semantics as :meth:`pathlib.Path.glob`.
        """
        if not pattern:
            raise ValueError("glob pattern must not be empty")
        return self._glob(pattern)

    def rglob(self, pattern: str) -> Iterator[S3Path]:
        return self._glob("**/" + pattern)

    def _glob(self, pattern: str) -> Iterator[S3Path]:
        bucket = self._require_bucket()
        base = self.key
        if base and not base.endswith("/"):
            base += "/"

        static = _static_prefix(pattern)
        full_prefix = base + static
        recursive = "**" in pattern
        regex = _glob_to_regex(pattern)

        paginator = bucket.client.boto_client.get_paginator("list_objects_v2")
        paginate_kwargs: dict = {"Bucket": bucket.name, "Prefix": full_prefix}
        if not recursive:
            paginate_kwargs["Delimiter"] = "/"

        seen: set = set()
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
        """Delete a single key. Raises :class:`IsAPrefixError` for prefixes."""
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
        """Recursively delete every key under this prefix."""
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
        if not self._parts:
            raise ValueError("S3Path root has no name to replace")
        return self.parent / name

    def with_suffix(self, suffix: str) -> S3Path:
        if suffix and not suffix.startswith("."):
            raise ValueError(f"Invalid suffix {suffix!r}")
        return self.with_name(self.stem + suffix)


def _static_prefix(pattern: str) -> str:
    """Return the portion of ``pattern`` before its first wildcard."""
    out: List[str] = []
    for ch in pattern:
        if ch in "*?[":
            break
        out.append(ch)
    return "".join(out)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a pathlib-style glob into a regex matching a key relative to its base."""
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
Key = S3Path


__all__ = ["Key", "Prefix", "S3Path"]
