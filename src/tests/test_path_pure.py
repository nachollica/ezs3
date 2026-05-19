"""Pure-path tests: construction, parsing, slash operator. No S3 traffic."""

from __future__ import annotations

import pytest

import ezs3
from ezs3 import Bucket, Key, Prefix, S3Path


class TestConstruction:
    def test_free_path_single_string(self) -> None:
        p = S3Path("project-name/some-prefix")
        assert p.bucket is None
        assert p.parts == ("project-name", "some-prefix")
        assert str(p) == "project-name/some-prefix"

    def test_free_path_strips_empty_segments(self) -> None:
        assert S3Path("a//b/").parts == ("a", "b")

    def test_attached_via_bucket_name(self, mocked_s3: None) -> None:
        p = S3Path("my-bucket", "a/b")
        assert p.bucket is not None
        assert p.bucket.name == "my-bucket"
        assert p.parts == ("a", "b")
        assert str(p) == "s3://my-bucket/a/b"

    def test_attached_via_bucket_instance(self, mocked_s3: None) -> None:
        bucket = Bucket("my-bucket")
        p = S3Path(bucket, "a/b/c")
        assert p.bucket is bucket
        assert p.parts == ("a", "b", "c")

    def test_attached_via_uri(self, mocked_s3: None) -> None:
        p = S3Path("s3://my-bucket/a/b/c.json")
        assert p.bucket is not None
        assert p.bucket.name == "my-bucket"
        assert p.key == "a/b/c.json"

    def test_invalid_uri_no_bucket(self, mocked_s3: None) -> None:
        with pytest.raises(ValueError):
            S3Path("s3:///key-without-bucket")

    def test_no_args_raises(self) -> None:
        with pytest.raises(TypeError):
            S3Path()

    def test_non_string_raises(self) -> None:
        with pytest.raises(TypeError):
            S3Path(123)  # type: ignore[arg-type]

    def test_only_first_arg_may_be_bucket(self, mocked_s3: None) -> None:
        b = Bucket("b")
        with pytest.raises(TypeError):
            S3Path("a", b)


class TestSlashOperator:
    def test_slash_appends_string(self, mocked_s3: None) -> None:
        bucket = Bucket("my-bucket-name")
        prefix = bucket / "project-name" / "some-prefix"
        assert str(prefix) == "s3://my-bucket-name/project-name/some-prefix"

    def test_slash_keeps_bucket(self, mocked_s3: None) -> None:
        bucket = Bucket("my-bucket")
        p = S3Path(bucket, "a") / "b"
        assert p.bucket == bucket
        assert p.parts == ("a", "b")

    def test_slash_with_compound_string(self, mocked_s3: None) -> None:
        p = S3Path("free") / "x/y/z"
        assert p.parts == ("free", "x", "y", "z")

    def test_rtruediv(self, mocked_s3: None) -> None:
        p = "prefix" / S3Path("file.json")
        assert p.parts == ("prefix", "file.json")

    def test_slash_rejects_non_str(self, mocked_s3: None) -> None:
        with pytest.raises(TypeError):
            _ = S3Path("a") / 5  # type: ignore[operator]


class TestProperties:
    def test_name_stem_suffix(self) -> None:
        p = S3Path("a/b/file.tar.gz")
        assert p.name == "file.tar.gz"
        assert p.stem == "file.tar"
        assert p.suffix == ".gz"

    def test_parent_free(self) -> None:
        assert S3Path("a/b/c").parent.parts == ("a", "b")
        assert S3Path("a").parent.parts == ()

    def test_parent_attached(self, mocked_s3: None) -> None:
        b = Bucket("b")
        p = S3Path(b, "a/b/c")
        parent = p.parent
        assert parent.bucket == b
        assert parent.parts == ("a", "b")

    def test_parents(self) -> None:
        ancestors = [p.key for p in S3Path("a/b/c").parents]
        assert ancestors == ["a/b", "a", ""]

    def test_with_name_and_suffix(self) -> None:
        p = S3Path("a/b/file.json")
        assert p.with_name("other.txt").key == "a/b/other.txt"
        assert p.with_suffix(".csv").key == "a/b/file.csv"
        assert p.with_suffix("").key == "a/b/file"

    def test_with_suffix_invalid(self) -> None:
        with pytest.raises(ValueError):
            S3Path("a/b.json").with_suffix("csv")


class TestEquality:
    def test_eq_by_bucket_and_parts(self, mocked_s3: None) -> None:
        b1 = Bucket("x")
        b2 = Bucket("x")
        assert S3Path(b1, "a/b") == S3Path(b2, "a/b")

    def test_neq_different_bucket(self, mocked_s3: None) -> None:
        assert S3Path("a", "k") != S3Path("b", "k")

    def test_neq_free_vs_attached(self, mocked_s3: None) -> None:
        assert S3Path("a/b") != S3Path("bucket", "a/b")

    def test_hashable(self, mocked_s3: None) -> None:
        b = Bucket("x")
        s = {S3Path(b, "a"), S3Path(b, "a")}
        assert len(s) == 1


class TestAttachDetach:
    def test_attach(self, mocked_s3: None) -> None:
        p = S3Path("a/b").attach("my-bucket")
        assert p.bucket is not None
        assert p.bucket.name == "my-bucket"

    def test_detach(self, mocked_s3: None) -> None:
        b = Bucket("x")
        p = S3Path(b, "a/b").detach()
        assert p.bucket is None
        assert p.parts == ("a", "b")


class TestAliases:
    def test_prefix_and_key_are_s3path(self) -> None:
        assert Prefix is S3Path
        assert Key is S3Path
        # both names usable for construction
        p: ezs3.Prefix = ezs3.Prefix("a/b")
        k: ezs3.Key = ezs3.Key("a/b/file.json")
        assert isinstance(p, S3Path)
        assert isinstance(k, S3Path)


class TestFspath:
    def test_fspath_returns_str(self) -> None:
        import os

        assert os.fspath(S3Path("a/b")) == "a/b"
