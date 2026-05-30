"""Cross-validate S3 contents against caller-supplied file metadata.

The :class:`ConsistencyChecker` binds a :class:`~ezs3.Client`, a bucket
handle and a base prefix, and compares what exists under the prefix in
S3 against a sequence of :class:`FileInfo` records the caller produces
from their own source of truth (an ORM table, a manifest, a list of
``UploadFile`` instances from a FastAPI request, ...).

Checks are reported as :class:`CheckResult` records — one per failed
field for a given filename, or a single :class:`IssueCode.OK` result
when everything matches. Results can be written to disk as JSON Lines
using stdlib :mod:`json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from ._bucket import Bucket
from ._hashing import hash_bytes, parse_hash

if TYPE_CHECKING:
    from ._client import Client


@dataclass(frozen=True)
class FileInfo:
    """Caller-supplied metadata for one tracked file.

    Field names mirror FastAPI's ``UploadFile`` so callers can adapt a
    FastAPI upload into a :class:`FileInfo` trivially. ezs3 does **not**
    depend on FastAPI; the parallel exists only as a naming convenience.

    Attributes:
        filename: Path relative to
            :attr:`ConsistencyChecker.base_prefix`. Forward slashes, no
            leading slash.
        size: Size in bytes, or ``None`` if unknown. Size checks are
            skipped when ``None``.
        content_type: MIME type string, or ``None`` if unknown.
            Content-type checks are skipped when ``None``.
        hash: ``"<alg>:<hex-digest>"`` formatted hash string, or ``None``
            if the hash is not tracked. Used only when callers opt into
            hash checks via ``with_hash=True``.
    """

    filename: str
    size: Optional[int] = None
    content_type: Optional[str] = None
    hash: Optional[str] = None


class IssueCode(str, Enum):
    """Outcome codes for a single :class:`CheckResult`."""

    OK = "ok"
    MISSING_IN_S3 = "missing_in_s3"
    UNTRACKED_IN_S3 = "untracked_in_s3"
    SIZE_MISMATCH = "size_mismatch"
    CONTENT_TYPE_MISMATCH = "content_type_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    HASH_UNAVAILABLE = "hash_unavailable"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single consistency check.

    Attributes:
        filename: File path relative to the checker's ``base_prefix``.
        code: Outcome code.
        detail: Optional human-readable summary.
        expected: Serialized expected value, when applicable.
        actual: Serialized actual value, when applicable.
    """

    filename: str
    code: IssueCode
    detail: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Return ``True`` if this result represents a passing check."""
        return self.code is IssueCode.OK

    def to_json(self) -> str:
        """Serialize this result as a single-line JSON object.

        The output uses compact separators and stable key ordering so
        files written via :meth:`ConsistencyChecker.write_report` round
        trip cleanly under JSON Lines parsers.

        Returns:
            One-line JSON encoding of the result.
        """
        payload = {
            "filename": self.filename,
            "code": self.code.value,
            "detail": self.detail,
            "expected": self.expected,
            "actual": self.actual,
        }
        return json.dumps(payload, separators=(",", ":"))


def _normalize_base(prefix: str) -> str:
    """Return ``prefix`` without a leading slash and with at most one trailing slash.

    Args:
        prefix: Raw base prefix.

    Returns:
        Normalized prefix. Empty string represents the bucket root.
    """
    p = prefix.strip()
    if p.startswith("/"):
        p = p.lstrip("/")
    if p and not p.endswith("/"):
        p = p + "/"
    return p


def _normalize_content_type(value: str) -> str:
    """Return the bare MIME type for ``value``: lowercased, no parameters."""
    head, _, _ = value.partition(";")
    return head.strip().lower()


class ConsistencyChecker:
    """Cross-check S3 contents against :class:`FileInfo` metadata.

    The checker is a thin orchestrator over the ezs3 surface: it uses a
    :class:`~ezs3.Client` and a :class:`~ezs3.Bucket` to inspect remote
    state, then yields :class:`CheckResult` records. All iteration is
    lazy so callers can stream results into a JSON Lines report without
    materializing every record.

    Three primary directions are exposed:

    * :meth:`check_infos` walks the caller's metadata and verifies each
      file exists in S3 with the expected size, content type, and
      (optionally) hash.
    * :meth:`check_s3` walks S3 and reports any object not covered by a
      metadata entry, plus the same per-field checks for matched ones.
    * :meth:`check_both` runs both directions and deduplicates results
      by ``(filename, code)``.

    Example:
        >>> import ezs3
        >>> client = ezs3.Client()
        >>> bucket = client.bucket("uploads")
        >>> checker = ezs3.ConsistencyChecker(client, bucket, "user-42/")
        >>> infos = [
        ...     ezs3.FileInfo("a.txt", size=12, content_type="text/plain"),
        ... ]
        >>> for result in checker.check_both(infos, with_hash=False):
        ...     print(result)
    """

    def __init__(
        self,
        client: "Client",
        bucket: Union[str, Bucket],
        base_prefix: str = "",
    ) -> None:
        """Bind the checker to a client, bucket, and base prefix.

        Args:
            client: The :class:`~ezs3.Client` used for all S3 calls.
            bucket: Target bucket as a name or :class:`~ezs3.Bucket`.
                String values are wrapped in a :class:`~ezs3.Bucket`
                bound to ``client``.
            base_prefix: Prefix every :attr:`FileInfo.filename` is
                relative to. May be empty (the bucket root). The
                trailing slash is normalized.
        """
        self._client = client
        if isinstance(bucket, Bucket):
            self._bucket: Bucket = bucket
        else:
            self._bucket = client.bucket(bucket)
        self._base_prefix: str = _normalize_base(base_prefix)

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
        """Normalized base prefix (empty string or ``"...<trailing-slash>"``)."""
        return self._base_prefix

    # Internals

    def _resolve_key(self, filename: str) -> str:
        """Join :attr:`base_prefix` and ``filename`` into a full S3 key.

        Args:
            filename: Path relative to the base prefix.

        Returns:
            The absolute S3 key (no leading slash).
        """
        cleaned = filename.lstrip("/")
        return self._base_prefix + cleaned

    def _head(self, key: str) -> Tuple[int, str]:
        """Return ``(size, content_type)`` from S3 for ``key``.

        Args:
            key: Absolute S3 key.

        Returns:
            Tuple of object size in bytes and reported content type
            (empty string when the object has no ``Content-Type``).
        """
        head = self._client.boto_client.head_object(
            Bucket=self._bucket.name,
            Key=key,
        )
        size = int(head.get("ContentLength", 0))
        content_type = str(head.get("ContentType", "") or "")
        return size, content_type

    # Public API

    def check_one(
        self,
        info: FileInfo,
        *,
        with_hash: bool = False,
    ) -> List[CheckResult]:
        """Run all per-field checks for one :class:`FileInfo`.

        Args:
            info: Metadata entry to verify.
            with_hash: When ``True``, download the object and compare its
                hash against ``info.hash``. The download happens only for
                this method, never elsewhere.

        Returns:
            A list with one :class:`CheckResult` per failed field, or a
            single :class:`IssueCode.OK` result when every checked field
            matches. When the object is absent the list contains a single
            :class:`IssueCode.MISSING_IN_S3` result.
        """
        key = self._resolve_key(info.filename)
        path = self._bucket.path(key)
        if not path.is_key():
            return [
                CheckResult(
                    filename=info.filename,
                    code=IssueCode.MISSING_IN_S3,
                    detail=f"No object at s3://{self._bucket.name}/{key}",
                ),
            ]

        results: List[CheckResult] = []
        size, content_type = self._head(key)

        if info.size is not None and size != info.size:
            results.append(
                CheckResult(
                    filename=info.filename,
                    code=IssueCode.SIZE_MISMATCH,
                    detail=f"expected {info.size} bytes, got {size}",
                    expected=str(info.size),
                    actual=str(size),
                ),
            )

        if info.content_type is not None:
            expected_ct = _normalize_content_type(info.content_type)
            actual_ct = _normalize_content_type(content_type)
            if expected_ct != actual_ct:
                results.append(
                    CheckResult(
                        filename=info.filename,
                        code=IssueCode.CONTENT_TYPE_MISMATCH,
                        detail=f"expected {expected_ct!r}, got {actual_ct!r}",
                        expected=expected_ct,
                        actual=actual_ct,
                    ),
                )

        if with_hash:
            if info.hash is None:
                results.append(
                    CheckResult(
                        filename=info.filename,
                        code=IssueCode.HASH_UNAVAILABLE,
                        detail="with_hash=True but FileInfo.hash is None",
                    ),
                )
            else:
                alg, expected_digest = parse_hash(info.hash)
                data = path.read_bytes()
                actual_hash = hash_bytes(data, alg)
                _, actual_digest = parse_hash(actual_hash)
                if expected_digest.lower() != actual_digest.lower():
                    results.append(
                        CheckResult(
                            filename=info.filename,
                            code=IssueCode.HASH_MISMATCH,
                            detail=f"{alg}: expected {expected_digest}, got {actual_digest}",
                            expected=info.hash,
                            actual=actual_hash,
                        ),
                    )

        if not results:
            return [CheckResult(filename=info.filename, code=IssueCode.OK)]
        return results

    def check_infos(
        self,
        infos: Iterable[FileInfo],
        *,
        with_hash: bool = False,
    ) -> Iterator[CheckResult]:
        """Yield :class:`CheckResult` records for every entry in ``infos``.

        Args:
            infos: Metadata entries to verify.
            with_hash: See :meth:`check_one`.

        Yields:
            One or more :class:`CheckResult` records per :class:`FileInfo`.
        """
        for info in infos:
            for result in self.check_one(info, with_hash=with_hash):
                yield result

    def check_s3(
        self,
        infos: Iterable[FileInfo],
        *,
        with_hash: bool = False,
    ) -> Iterator[CheckResult]:
        """List S3 under the base prefix and verify coverage by ``infos``.

        Every key found under the base prefix is matched against an entry
        in ``infos``: matches run through :meth:`check_one`; objects with
        no corresponding entry yield an :class:`IssueCode.UNTRACKED_IN_S3`
        result.

        Args:
            infos: Metadata entries. Consumed eagerly so the lookup table
                is ready before iteration begins.
            with_hash: See :meth:`check_one`.

        Yields:
            :class:`CheckResult` records, lazily as S3 is paginated.
        """
        by_name: Dict[str, FileInfo] = {info.filename: info for info in infos}
        base = self._base_prefix
        prefix_len = len(base)
        root = self._bucket.path(base) if base else self._bucket.root
        for path in root.find():
            key = path.key
            relative = key[prefix_len:] if key.startswith(base) else key
            info = by_name.get(relative)
            if info is None:
                yield CheckResult(
                    filename=relative,
                    code=IssueCode.UNTRACKED_IN_S3,
                    detail=f"Object exists in S3 but no FileInfo covers it: {key!r}",
                )
                continue
            for result in self.check_one(info, with_hash=with_hash):
                yield result

    def check_both(
        self,
        infos: Iterable[FileInfo],
        *,
        with_hash: bool = False,
    ) -> Iterator[CheckResult]:
        """Run :meth:`check_infos` and :meth:`check_s3`, deduplicated.

        Results are deduplicated by ``(filename, code)``. The metadata
        iterable is materialized once and reused for both directions so
        callers can pass a generator.

        Args:
            infos: Metadata entries.
            with_hash: See :meth:`check_one`.

        Yields:
            Deduplicated :class:`CheckResult` records.
        """
        materialized: List[FileInfo] = list(infos)
        seen: Set[Tuple[str, IssueCode]] = set()
        for result in self.check_infos(materialized, with_hash=with_hash):
            key = (result.filename, result.code)
            if key in seen:
                continue
            seen.add(key)
            yield result
        for result in self.check_s3(materialized, with_hash=with_hash):
            key = (result.filename, result.code)
            if key in seen:
                continue
            seen.add(key)
            yield result

    def write_report(
        self,
        results: Iterable[CheckResult],
        path: Union[str, Path],
        *,
        include_ok: bool = False,
    ) -> int:
        """Write ``results`` as JSON Lines to ``path``.

        One JSON object per line, compact separators, no indentation.
        :class:`IssueCode.OK` records are skipped by default.

        Args:
            results: Result records to persist. Consumed lazily.
            path: Destination file path.
            include_ok: When ``True``, also write passing
                (:class:`IssueCode.OK`) results.

        Returns:
            The number of records written.
        """
        count = 0
        out_path = Path(path)
        with out_path.open("w", encoding="utf-8") as fh:
            for result in results:
                if result.ok and not include_ok:
                    continue
                fh.write(result.to_json())
                fh.write("\n")
                count += 1
        return count


__all__ = [
    "CheckResult",
    "ConsistencyChecker",
    "FileInfo",
    "IssueCode",
]
