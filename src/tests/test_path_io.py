"""Unit tests exercising S3Path I/O against the moto in-process backend."""

from __future__ import annotations

import pytest

from ezs3 import (
    Bucket,
    IsAPrefixError,
    NotAPrefixError,
    PathNotAttachedError,
    Prefix,
    S3KeyNotFoundError,
    S3Path,
)


class TestExistence:
    def test_is_key_true(self, populated_bucket: Bucket) -> None:
        assert (populated_bucket / "readme.txt").is_key()

    def test_is_key_false_when_prefix(self, populated_bucket: Bucket) -> None:
        assert not (populated_bucket / "data").is_key()

    def test_is_prefix_true(self, populated_bucket: Bucket) -> None:
        assert (populated_bucket / "data").is_prefix()

    def test_is_prefix_false_when_nonexistent(self, populated_bucket: Bucket) -> None:
        assert not (populated_bucket / "no-such-thing").is_prefix()

    def test_exists_for_key_and_prefix(self, populated_bucket: Bucket) -> None:
        assert (populated_bucket / "readme.txt").exists()
        assert (populated_bucket / "data").exists()

    def test_exists_false_for_missing(self, populated_bucket: Bucket) -> None:
        assert not (populated_bucket / "ghost").exists()

    def test_is_dir_is_file_aliases(self, populated_bucket: Bucket) -> None:
        f = populated_bucket / "readme.txt"
        d = populated_bucket / "data"
        assert f.is_file()
        assert not f.is_dir()
        assert d.is_dir()
        assert not d.is_file()


class TestReadWrite:
    def test_write_read_text_roundtrip(self, bucket: Bucket) -> None:
        p = bucket / "hello.txt"
        n = p.write_text("hi there")
        assert n == len("hi there".encode())
        assert p.read_text() == "hi there"

    def test_write_read_bytes_roundtrip(self, bucket: Bucket) -> None:
        p = bucket / "blob.bin"
        p.write_bytes(b"\x00\x01\x02")
        assert p.read_bytes() == b"\x00\x01\x02"

    def test_read_missing_raises(self, bucket: Bucket) -> None:
        with pytest.raises(S3KeyNotFoundError):
            (bucket / "absent.txt").read_text()

    def test_read_prefix_raises(self, populated_bucket: Bucket) -> None:
        with pytest.raises(IsAPrefixError):
            (populated_bucket / "data").read_text()

    def test_read_text_encoding(self, bucket: Bucket) -> None:
        p = bucket / "latin.txt"
        p.write_text("café", encoding="latin-1")
        assert p.read_text(encoding="latin-1") == "café"

    def test_read_root_raises(self, bucket: Bucket) -> None:
        with pytest.raises(IsAPrefixError):
            bucket.root.read_text()

    def test_write_to_root_raises(self, bucket: Bucket) -> None:
        with pytest.raises(IsAPrefixError):
            bucket.root.write_text("nope")

    def test_free_path_read_raises(self) -> None:
        with pytest.raises(PathNotAttachedError):
            S3Path("a/b/c").read_text()


class TestIterdir:
    def test_iterdir_top_level(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in populated_bucket.root.iterdir())
        assert names == ["data", "logs", "readme.txt"]

    def test_iterdir_nested(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in (populated_bucket / "data").iterdir())
        assert names == ["a.json", "b.json", "nested"]

    def test_iterdir_on_key_raises(self, populated_bucket: Bucket) -> None:
        with pytest.raises(NotAPrefixError):
            list((populated_bucket / "readme.txt").iterdir())

    def test_iterdir_distinguishes_key_vs_prefix(self, populated_bucket: Bucket) -> None:
        children = {p.name: p for p in (populated_bucket / "data").iterdir()}
        assert children["a.json"].is_key()
        assert children["nested"].is_prefix()


class TestFind:
    def test_find_yields_all(self, populated_bucket: Bucket) -> None:
        keys = sorted(p.key for p in populated_bucket.root.find())
        assert keys == [
            "data/a.json",
            "data/b.json",
            "data/nested/c.json",
            "data/nested/deep/d.txt",
            "logs/2024/01.log",
            "logs/2024/02.log",
            "readme.txt",
        ]

    def test_find_under_prefix(self, populated_bucket: Bucket) -> None:
        keys = sorted(p.key for p in (populated_bucket / "data").find())
        assert keys == [
            "data/a.json",
            "data/b.json",
            "data/nested/c.json",
            "data/nested/deep/d.txt",
        ]


class TestGlob:
    def test_glob_star(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in (populated_bucket / "data").glob("*.json"))
        assert names == ["a.json", "b.json"]

    def test_glob_does_not_recurse(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in (populated_bucket / "data").glob("*"))
        # only direct children, not nested ones
        assert "nested" in names
        assert "c.json" not in names

    def test_rglob_recursive(self, populated_bucket: Bucket) -> None:
        keys = sorted(p.key for p in (populated_bucket / "data").rglob("*.json"))
        assert keys == [
            "data/a.json",
            "data/b.json",
            "data/nested/c.json",
        ]

    def test_glob_question_mark(self, populated_bucket: Bucket) -> None:
        names = sorted(p.name for p in (populated_bucket / "logs/2024").glob("0?.log"))
        assert names == ["01.log", "02.log"]

    def test_glob_empty_pattern_raises(self, bucket: Bucket) -> None:
        with pytest.raises(ValueError):
            list(bucket.root.glob(""))


class TestRemove:
    def test_remove_key(self, populated_bucket: Bucket) -> None:
        p = populated_bucket / "readme.txt"
        assert p.exists()
        p.remove()
        assert not p.exists()

    def test_remove_missing_raises(self, bucket: Bucket) -> None:
        with pytest.raises(S3KeyNotFoundError):
            (bucket / "ghost.txt").remove()

    def test_remove_missing_ok(self, bucket: Bucket) -> None:
        (bucket / "ghost.txt").remove(missing_ok=True)

    def test_remove_prefix_raises(self, populated_bucket: Bucket) -> None:
        with pytest.raises(IsAPrefixError):
            (populated_bucket / "data").remove()

    def test_rm_alias(self, bucket: Bucket) -> None:
        p = bucket / "x.txt"
        p.write_text("a")
        p.rm()
        assert not p.exists()

    def test_rmtree(self, populated_bucket: Bucket) -> None:
        prefix = populated_bucket / "data"
        prefix.rmtree()
        assert not prefix.exists()
        # logs sibling untouched
        assert (populated_bucket / "logs").is_prefix()


class TestStrAndRepr:
    def test_str_attached(self, bucket: Bucket) -> None:
        assert str(bucket / "a/b") == f"s3://{bucket.name}/a/b"

    def test_str_root(self, bucket: Bucket) -> None:
        assert str(bucket.root) == f"s3://{bucket.name}/"

    def test_repr(self, bucket: Bucket) -> None:
        p = bucket / "x"
        assert repr(p) == f"S3Path('s3://{bucket.name}/x')"


class TestSpecExample:
    """Mirrors the expected-interface example from the project README."""

    def test_spec_full_example(self, client, bucket_name: str) -> None:  # noqa: ANN001
        bucket = client.create_bucket(bucket_name)
        prefix = bucket / "project-name" / "some-prefix"
        assert str(prefix) == f"s3://{bucket_name}/project-name/some-prefix"

        key = prefix / "file.json"
        key.write_text('{"some_ke": 123}')
        assert key.is_file()
        assert key.read_text() == '{"some_ke": 123}'
        assert key.read_bytes() == b'{"some_ke": 123}'

        with pytest.raises(IsAPrefixError):
            prefix.read_text()

        with pytest.raises(NotAPrefixError):
            list(key.iterdir())

        assert key.exists()
        key.remove()
        assert not key.exists()

    def test_free_then_attached_prefix(self, bucket: Bucket) -> None:
        free = Prefix("project-name/some-prefix")
        assert free.bucket is None
        attached = free.attach(bucket)
        assert attached.bucket == bucket
        assert attached.key == "project-name/some-prefix"
