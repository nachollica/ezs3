"""Unit tests for ``ConsistencyChecker`` using moto."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from ezs3 import (
    Bucket,
    CheckResult,
    Client,
    ConsistencyChecker,
    FileInfo,
    IssueCode,
    hash_bytes,
)


@pytest.fixture
def checker(client: Client, bucket: Bucket) -> ConsistencyChecker:
    return ConsistencyChecker(client, bucket, base_prefix="uploads/")


def _put(bucket: Bucket, key: str, body: bytes, content_type: str = "text/plain") -> None:
    bucket.path(key).write_bytes(body, ContentType=content_type)


class TestBasePrefix:
    def test_empty(self, client: Client, bucket: Bucket) -> None:
        c = ConsistencyChecker(client, bucket)
        assert c.base_prefix == ""

    def test_adds_trailing_slash(self, client: Client, bucket: Bucket) -> None:
        c = ConsistencyChecker(client, bucket, "foo/bar")
        assert c.base_prefix == "foo/bar/"

    def test_strips_leading_slash(self, client: Client, bucket: Bucket) -> None:
        c = ConsistencyChecker(client, bucket, "/foo/")
        assert c.base_prefix == "foo/"

    def test_accepts_bucket_name(self, client: Client, bucket: Bucket) -> None:
        c = ConsistencyChecker(client, bucket.name, "x/")
        assert c.bucket.name == bucket.name


class TestCheckOne:
    def test_happy_path_ok(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        body = b"hello world"
        _put(bucket, "uploads/a.txt", body, "text/plain")
        info = FileInfo("a.txt", size=len(body), content_type="text/plain")
        results = checker.check_one(info)
        assert len(results) == 1
        assert results[0].ok
        assert results[0].code is IssueCode.OK

    def test_missing_in_s3(self, checker: ConsistencyChecker) -> None:
        info = FileInfo("ghost.txt", size=1)
        results = checker.check_one(info)
        assert len(results) == 1
        assert results[0].code is IssueCode.MISSING_IN_S3

    def test_size_mismatch(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/a.txt", b"hello")
        info = FileInfo("a.txt", size=999)
        results = checker.check_one(info)
        assert [r.code for r in results] == [IssueCode.SIZE_MISMATCH]
        assert results[0].expected == "999"
        assert results[0].actual == "5"

    def test_size_skipped_when_none(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/a.txt", b"hello", "text/plain")
        info = FileInfo("a.txt", size=None, content_type="text/plain")
        results = checker.check_one(info)
        assert results[0].ok

    def test_content_type_mismatch(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hi", "application/json")
        info = FileInfo("a.txt", content_type="text/plain")
        results = checker.check_one(info)
        assert [r.code for r in results] == [IssueCode.CONTENT_TYPE_MISMATCH]

    def test_content_type_match_ignores_params(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hi", "text/plain; charset=utf-8")
        info = FileInfo("a.txt", content_type="TEXT/Plain")
        results = checker.check_one(info)
        assert results[0].ok

    def test_multiple_field_failures(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hello", "application/json")
        info = FileInfo("a.txt", size=999, content_type="text/plain")
        results = checker.check_one(info)
        codes = sorted(r.code.value for r in results)
        assert codes == [
            IssueCode.CONTENT_TYPE_MISMATCH.value,
            IssueCode.SIZE_MISMATCH.value,
        ]

    def test_hash_match(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        body = b"hash me"
        _put(bucket, "uploads/a.txt", body)
        info = FileInfo("a.txt", hash=hash_bytes(body))
        results = checker.check_one(info, with_hash=True)
        assert results[0].ok

    def test_hash_mismatch(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/a.txt", b"actual content")
        info = FileInfo("a.txt", hash=hash_bytes(b"other content"))
        results = checker.check_one(info, with_hash=True)
        assert [r.code for r in results] == [IssueCode.HASH_MISMATCH]

    def test_hash_unavailable(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"x")
        info = FileInfo("a.txt", hash=None)
        results = checker.check_one(info, with_hash=True)
        assert [r.code for r in results] == [IssueCode.HASH_UNAVAILABLE]

    def test_hash_skipped_when_not_requested(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"x")
        info = FileInfo("a.txt", hash="sha256:" + "0" * 64)
        results = checker.check_one(info, with_hash=False)
        assert results[0].ok


class TestCheckInfos:
    def test_streams_results(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/a.txt", b"a")
        _put(bucket, "uploads/b.txt", b"bb")
        infos = [
            FileInfo("a.txt", size=1),
            FileInfo("b.txt", size=999),
            FileInfo("missing.txt", size=4),
        ]
        results = list(checker.check_infos(infos))
        codes = [r.code for r in results]
        assert IssueCode.OK in codes
        assert IssueCode.SIZE_MISMATCH in codes
        assert IssueCode.MISSING_IN_S3 in codes


class TestCheckS3:
    def test_untracked_in_s3(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/tracked.txt", b"x")
        _put(bucket, "uploads/orphan.txt", b"x")
        infos = [FileInfo("tracked.txt", size=1)]
        results = list(checker.check_s3(infos))
        untracked = [r for r in results if r.code is IssueCode.UNTRACKED_IN_S3]
        assert len(untracked) == 1
        assert untracked[0].filename == "orphan.txt"

    def test_matched_runs_field_checks(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hi")
        infos = [FileInfo("a.txt", size=999)]
        results = list(checker.check_s3(infos))
        assert [r.code for r in results] == [IssueCode.SIZE_MISMATCH]

    def test_only_walks_base_prefix(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"x")
        _put(bucket, "elsewhere/b.txt", b"x")
        results = list(checker.check_s3([FileInfo("a.txt", size=1)]))
        # only uploads/a.txt should be visited (it matches), no UNTRACKED
        # for elsewhere/b.txt
        assert all(r.filename == "a.txt" for r in results)


class TestCheckBoth:
    def test_dedupes_results(self, bucket: Bucket, checker: ConsistencyChecker) -> None:
        _put(bucket, "uploads/a.txt", b"hello")
        infos = [FileInfo("a.txt", size=999)]
        results = list(checker.check_both(infos))
        # Both directions detect SIZE_MISMATCH for a.txt; should appear once.
        size_mismatches = [
            r for r in results if r.filename == "a.txt" and r.code is IssueCode.SIZE_MISMATCH
        ]
        assert len(size_mismatches) == 1

    def test_combines_directions(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
    ) -> None:
        _put(bucket, "uploads/present.txt", b"x")
        _put(bucket, "uploads/orphan.txt", b"x")
        infos = [
            FileInfo("present.txt", size=1),
            FileInfo("missing.txt", size=1),
        ]
        results = list(checker.check_both(infos))
        codes_by_name = {(r.filename, r.code) for r in results}
        assert ("present.txt", IssueCode.OK) in codes_by_name
        assert ("missing.txt", IssueCode.MISSING_IN_S3) in codes_by_name
        assert ("orphan.txt", IssueCode.UNTRACKED_IN_S3) in codes_by_name


class TestWriteReport:
    def test_jsonl_skips_ok_by_default(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
        tmp_path: Path,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hello")
        infos = [FileInfo("a.txt", size=999), FileInfo("ghost.txt", size=1)]
        out = tmp_path / "report.jsonl"
        n = checker.write_report(checker.check_infos(infos), out)
        assert n == 2
        lines = out.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert "\n" not in line
            payload = json.loads(line)
            assert "filename" in payload
            assert "code" in payload

    def test_jsonl_include_ok(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
        tmp_path: Path,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"x")
        infos = [FileInfo("a.txt", size=1)]
        out = tmp_path / "report.jsonl"
        n = checker.write_report(checker.check_infos(infos), out, include_ok=True)
        assert n == 1

    def test_jsonl_no_indent(
        self,
        bucket: Bucket,
        checker: ConsistencyChecker,
        tmp_path: Path,
    ) -> None:
        _put(bucket, "uploads/a.txt", b"hello")
        # Use a hash check so the failing record has no commas / colons
        # in its detail field, letting us assert on raw separators.
        infos = [FileInfo("a.txt", hash="sha256:" + "0" * 64)]
        out = tmp_path / "report.jsonl"
        checker.write_report(checker.check_infos(infos, with_hash=True), out)
        text = out.read_text()
        assert text.count("\n") == 1
        # Verify the record itself uses compact separators: encoding the
        # parsed payload back with the same separators must round-trip.
        payload = json.loads(text)
        assert json.dumps(payload, separators=(",", ":")) + "\n" == text


class TestCheckResult:
    def test_to_json_keys(self) -> None:
        r = CheckResult("a.txt", IssueCode.OK)
        payload = json.loads(r.to_json())
        assert set(payload) == {"filename", "code", "detail", "expected", "actual"}
        assert payload["code"] == "ok"

    def test_ok_property(self) -> None:
        assert CheckResult("a", IssueCode.OK).ok
        assert not CheckResult("a", IssueCode.SIZE_MISMATCH).ok


class TestPublicSurface:
    def test_exports(self) -> None:
        import ezs3

        expected: List[str] = [
            "CheckResult",
            "ConsistencyChecker",
            "FileInfo",
            "IssueCode",
            "DEFAULT_ALG",
            "hash_bytes",
            "hash_stream",
            "parse_hash",
            "format_hash",
            "supported_algorithms",
            "HashMismatchError",
        ]
        for name in expected:
            assert name in ezs3.__all__, name
            assert hasattr(ezs3, name), name
