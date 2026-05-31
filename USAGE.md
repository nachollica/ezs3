# Using ezs3

A typed, [`pathlib.Path`](https://docs.python.org/3/library/pathlib.html)-like
abstraction over [boto3](https://pypi.org/project/boto3/) for Amazon S3 (and any
S3-compatible service: MinIO, LocalStack, Cloudflare R2, Backblaze B2, …).

This guide is organized in three big sections:

1. [Core concepts and usage](#1-core-concepts-and-usage)
   1. [The four public types](#11-the-four-public-types)
   2. [Authentication](#12-authentication)
   3. [Bucket lifecycle](#13-bucket-lifecycle)
   4. [Path composition](#14-path-composition)
   5. [Existence and classification](#15-existence-and-classification)
   6. [Reading and writing in-memory payloads](#16-reading-and-writing-in-memory-payloads)
   7. [Uploading and downloading local files](#17-uploading-and-downloading-local-files)
   8. [Listing and traversal](#18-listing-and-traversal)
   9. [Deletion](#19-deletion)
   10. [Escape hatch to raw boto3](#110-escape-hatch-to-raw-boto3)
2. [Managed store and consistency checks](#2-managed-store-and-consistency-checks)
   1. [Hash helpers](#21-hash-helpers)
   2. [ManagedStore: content-addressed storage](#22-managedstore-content-addressed-storage)
   3. [FileInfo: the shared metadata record](#23-fileinfo-the-shared-metadata-record)
   4. [ConsistencyChecker: S3 ↔ metadata cross-validation](#24-consistencychecker-s3--metadata-cross-validation)
3. [Error handling](#3-error-handling)
   1. [Hierarchy](#31-hierarchy)
   2. [Common scenarios](#32-common-scenarios)
   3. [Choosing between exceptions and result records](#33-choosing-between-exceptions-and-result-records)

Every snippet is self-contained and ready to copy-paste into a Python REPL or a
script. Replace bucket names and credentials with values for your environment.

---

## 1. Core concepts and usage

### 1.1 The four public types

| Type | Purpose |
| --- | --- |
| `ezs3.Client` | Wraps a boto3 session. Owns credentials + bucket lifecycle. |
| `ezs3.Bucket` | Named bucket handle bound to a `Client`. Composes with `/`. |
| `ezs3.S3Path` | Path-like representation of a key or prefix. |
| `ezs3.Prefix`, `ezs3.Key` | Aliases for `S3Path`. Use whichever documents intent. |

A single `S3Path` covers both keys and prefixes; the nature is determined by
inspecting remote state (`is_key()` / `is_prefix()`). This mirrors how
`pathlib.PurePath` is a single type for both files and directories.

### 1.2 Authentication

`ezs3.Client` is a thin wrapper around `boto3.session.Session`. Credential
resolution is therefore identical to boto3's:

```python
import ezs3

# 1. Default chain: env vars, ~/.aws/credentials, instance role, ...
client = ezs3.Client()

# 2. Named profile from ~/.aws/credentials.
client = ezs3.Client(profile_name="my-profile")

# 3. Explicit static credentials.
client = ezs3.Client(
    aws_access_key_id="AKIA...",
    aws_secret_access_key="...",
    region_name="eu-west-1",
)

# 4. STS / temporary credentials.
client = ezs3.Client(
    aws_access_key_id="ASIA...",
    aws_secret_access_key="...",
    aws_session_token="...",
    region_name="us-east-1",
)

# 5. Custom endpoint (MinIO, LocalStack, R2, ...).
client = ezs3.Client(
    endpoint_url="http://localhost:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
)

# 6. Pre-built boto3 session (ignores the other credential kwargs).
import boto3
client = ezs3.Client(session=boto3.session.Session(profile_name="my-profile"))

# 7. Forward botocore Config (retries, signature version, ...).
from botocore.config import Config
client = ezs3.Client(
    config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
)
```

For one-off scripts you usually do not need to build a `Client` at all: every
`Bucket` (and `S3Path`) implicitly uses the process-wide default client, which
is created from the default credential chain on first use:

```python
import ezs3

bucket = ezs3.Bucket("my-bucket")  # uses ezs3.get_default_client() internally
bucket.write_text("hello.txt", "hi")

# Inside tests, swap credentials and reset the cache.
ezs3.reset_default_client()
```

The default client and an explicit `Client` are interchangeable; pass
`client=...` to `Bucket(...)` whenever you need to target a different account,
region, or endpoint within the same process.

### 1.3 Bucket lifecycle

```python
import ezs3

client = ezs3.Client(region_name="eu-west-1")

# List every bucket visible to the configured credentials.
for bucket in client.list_buckets():
    print(bucket.name)

# Check existence without raising.
client.bucket_exists("my-bucket")             # -> bool

# Create / delete (requires permission). For any non us-east-1 region the
# CreateBucketConfiguration is added automatically.
tmp = client.create_bucket("ezs3-tmp", exists_ok=True)
client.delete_bucket(tmp, force=True, missing_ok=True)

# Bucket-bound shortcuts: same operations via the handle itself.
b = ezs3.Bucket("ezs3-tmp", client=client)
b.create(exists_ok=True)
b.exists()
b.clear()        # delete every object (keeps the bucket)
b.delete(force=True, missing_ok=True)
```

`Bucket` equality compares `(name, client)`, and `Client` equality compares
frozen credentials. Two handles with the same name but different credentials
are intentionally treated as different identities, since they may have
different IAM permissions.

### 1.4 Path composition

The `/` operator builds an `S3Path`. The same path can be constructed four
ways, depending on what reads best at the call site:

```python
import ezs3

bucket = ezs3.Bucket("my-bucket")

# 1. Slash composition (the idiomatic form).
key = bucket / "project" / "raw" / "events.json"

# 2. Direct construction with the bucket as the first positional arg.
key = ezs3.S3Path(bucket, "project", "raw/events.json")

# 3. Two-or-more string args: the first becomes the bucket name.
key = ezs3.S3Path("my-bucket", "project/raw/events.json")

# 4. Full s3:// URI.
key = ezs3.S3Path("s3://my-bucket/project/raw/events.json")

# All four compare equal.
assert str(key) == "s3://my-bucket/project/raw/events.json"
```

Path properties mirror `pathlib`:

```python
key.parts        # ('project', 'raw', 'events.json')
key.key          # 'project/raw/events.json'  (no scheme, no leading slash)
key.name         # 'events.json'
key.stem         # 'events'
key.suffix       # '.json'
key.parent       # S3Path('s3://my-bucket/project/raw/')
key.parents      # [parent, grandparent, ..., bucket root]
key.with_name("events.csv")
key.with_suffix(".csv")
```

#### 1.4.1 Free paths and `attach` / `detach`

A path constructed from a single string is *free* — it has no bucket attached
and cannot perform I/O until one is bound. This is useful when you want to
manipulate key shapes without committing to a destination:

```python
import ezs3

template = ezs3.Prefix("tenants") / "default" / "config.json"
assert template.bucket is None
assert template.key == "tenants/default/config.json"

prod = template.attach("prod-bucket")
dev = template.attach(ezs3.Bucket("dev-bucket"))

# Drop the binding to make the path free again.
template_again = prod.detach()
```

The aliases `ezs3.Prefix` and `ezs3.Key` are the same class as `ezs3.S3Path`.
Use them to document intent at construction sites.

### 1.5 Existence and classification

```python
key = bucket / "config.json"

key.exists()       # True if either a key or a prefix exists at this path
key.is_key()       # True only if an object exists at this exact key
key.is_prefix()    # True if at least one object exists under this prefix
key.is_file()      # alias for is_key()
key.is_dir()       # alias for is_prefix()

# Bucket-level shortcuts use the same semantics.
bucket.exists_key("config.json")
bucket.is_prefix("logs/2024/")
bucket.is_key("config.json")
```

Both `is_key()` and `is_prefix()` return `False` for paths that do not exist
remotely yet, just like a brand-new `pathlib.Path` returns `False` for
`exists()`.

### 1.6 Reading and writing in-memory payloads

```python
key = bucket / "config.json"

key.write_text('{"flag": true}')               # -> bytes-written count
key.write_bytes(b"\x00\x01\x02")

key.read_text()                                # -> str
key.read_text(encoding="latin-1")
key.read_bytes()                               # -> bytes

# Bucket-level shortcuts (handy in scripts).
bucket.write_text("hello.txt", "hi")
bucket.read_text("hello.txt")
bucket.write_bytes("blob.bin", b"\x00\x01")
bucket.read_bytes("blob.bin")

# Forward boto3 PutObject kwargs (Content-Type, Metadata, ACL, SSE, ...).
key.write_text(
    '{"flag": true}',
    ContentType="application/json",
    Metadata={"owner": "alice"},
    CacheControl="max-age=3600",
)
```

The `**put_object_kwargs` are statically typed via
`mypy_boto3_s3.type_defs.PutObjectRequestObjectPutTypeDef` — your IDE will
autocomplete supported keys when `ezs3[types]` is installed.

### 1.7 Uploading and downloading local files

Both directions accept a path string, a `pathlib.Path`, or a binary file-like
object (anything implementing `read() -> bytes` for upload, or `write(bytes)`
for download).

```python
import io
from pathlib import Path

key = bucket / "uploads" / "report.pdf"

# Upload from disk. The default refuses to clobber existing keys.
key.upload("./report.pdf")
key.upload(Path("./report.pdf"), overwrite=True, ContentType="application/pdf")

# Upload from an in-memory stream.
buffer = io.BytesIO(b"hello")
(bucket / "uploads" / "hello.txt").upload(buffer, ContentType="text/plain")

# Download to disk. Missing parent dirs are created on request.
key.download("./out/report.pdf", create_parents=True)

# Download into a stream.
sink = io.BytesIO()
key.download(sink)
sink.seek(0)
data = sink.read()

# Bucket-level shortcuts.
bucket.upload("./report.pdf", "uploads/report.pdf", overwrite=True)
bucket.download("uploads/report.pdf", "./out/report.pdf", create_parents=True)
```

Transfers are byte-exact: text payloads should be encoded by the caller before
upload (or decoded after download). The `upload` method refuses to overwrite
unless `overwrite=True`, raising `S3KeyExistsError` otherwise.

### 1.8 Listing and traversal

```python
data = bucket / "data"

# One level deep (mirrors pathlib.Path.iterdir).
for child in data.iterdir():
    print(child)              # may be a key OR a sub-prefix

# Recursive: every key under the prefix, in S3 lexicographic order.
for path in data.find():
    print(path.key, path.read_bytes()[:32])

# Glob patterns: *, ?, [abc], and ** (recursive).
for path in data.glob("*.json"):          # one level
    ...
for path in data.rglob("*.parquet"):      # any depth, == glob("**/*.parquet")
    ...

# Bucket-level shortcuts. The `prefix` arg defaults to bucket root.
for path in bucket.iterdir():
    ...
for path in bucket.find("logs/2024/"):
    ...
for path in bucket.rglob("*.json", prefix="data/"):
    ...
```

All listing helpers are lazy generators that paginate `list_objects_v2` for
you, so they are safe over prefixes containing millions of keys.

### 1.9 Deletion

```python
# Single key (== rm alias).
(bucket / "tmp" / "draft.txt").remove()
(bucket / "tmp" / "draft.txt").rm(missing_ok=True)

# Recursive prefix deletion. No-ops cleanly on empty prefixes.
(bucket / "tmp").rmtree()

# Batched deletion (one DeleteObjects per chunk of 1000).
bucket.remove("a.txt", "b.txt", "c.txt")
bucket.remove(bucket / "x.txt", "y.txt", missing_ok=True)

# Wipe every object in a bucket without dropping the bucket itself.
bucket.clear()
```

`remove()` on a path that is actually a prefix raises `IsAPrefixError` —
deletion of a directory-like thing must be requested explicitly via `rmtree`
or `clear`.

### 1.10 Escape hatch to raw boto3

When ezs3 does not yet wrap an S3 feature you need (presigned URLs,
multipart-specific tunables, bucket policies, ...), reach for the underlying
boto3 client or resource:

```python
client = ezs3.Client()
url = client.boto_client.generate_presigned_url(
    "get_object",
    Params={"Bucket": "my-bucket", "Key": "report.pdf"},
    ExpiresIn=3600,
)

# The resource API is also exposed.
obj = client.boto_resource.Object("my-bucket", "report.pdf")
obj.acl().put(ACL="private")
```

---

## 2. Managed store and consistency checks

ezs3 ships two higher-level building blocks on top of the path API:

- `ManagedStore` — content-addressed storage. Blobs are written at
  `<base_prefix>/<alg>:<digest>` so byte-identical uploads dedup for free.
- `ConsistencyChecker` — cross-validate what exists in S3 against a sequence
  of caller-supplied `FileInfo` metadata records.

Both share two simple primitives: the `FileInfo` dataclass and a hash string
format `"<alg>:<hex-digest>"` (e.g. `"sha256:e3b0c4..."`).

### 2.1 Hash helpers

```python
import ezs3

ezs3.DEFAULT_ALG                  # "sha256"
ezs3.supported_algorithms()       # frozenset of algorithm names

# Hash an in-memory payload.
h = ezs3.hash_bytes(b"hello world")           # "sha256:b94d27..."
h = ezs3.hash_bytes(b"hello", alg="md5")      # "md5:5d41..."

# Stream-hash without loading into memory.
with open("big.bin", "rb") as fh:
    h = ezs3.hash_stream(fh, alg="sha256")

# Round-trip the string format.
alg, digest = ezs3.parse_hash("sha256:e3b0c4...")
formatted = ezs3.format_hash("SHA256", "E3B0C4...")   # normalized to lowercase
```

Any algorithm accepted by `hashlib.new()` works. The strings are lowercased on
both sides for stable comparisons.

### 2.2 ManagedStore: content-addressed storage

`ManagedStore` is a minimal blob store: callers keep their own database row
(filename, size, content type, hash), and the store owns only the S3 side.

```python
import ezs3

client = ezs3.Client()
bucket = client.create_bucket("uploads", exists_ok=True)
store = ezs3.ManagedStore(client, bucket, base_prefix="blobs/", alg="sha256")

# Put a bytes payload. Uploads are idempotent: a second call with identical
# bytes is a no-op (the key already exists).
info = store.put_bytes(
    b"hello world",
    content_type="text/plain",
    filename="greeting.txt",
)
# info is a FileInfo:
#   FileInfo(filename='greeting.txt', size=11,
#            content_type='text/plain',
#            hash='sha256:b94d27b9934d3e08...')

# Persist the metadata in your own database. The store does NOT remember
# filenames; the canonical identity is info.hash.
row = {
    "filename": info.filename,
    "size": info.size,
    "content_type": info.content_type,
    "hash": info.hash,
}

# Retrieve by hash (or by FileInfo, which carries the hash).
assert store.get_bytes(info.hash) == b"hello world"
assert store.exists(row["hash"]) is True

# Stream-friendly variants.
with open("./big.bin", "rb") as fh:
    info = store.put_stream(fh, content_type="application/octet-stream")

with store.open(info) as body:           # boto3 StreamingBody
    chunk = body.read(8192)

# Verify integrity by re-hashing the stored blob.
result = store.verify(info)
assert result.ok                          # CheckResult with code IssueCode.OK

# Raising variant for use in pipelines.
store.verify_strict(info)                 # -> None or HashMismatchError

# Delete a blob (refcounting is the caller's job).
store.delete(info)
```

Two callers uploading the same bytes will see the same `info.hash` and
therefore the same S3 key — that is the dedup guarantee.

### 2.3 FileInfo: the shared metadata record

`FileInfo` is a frozen dataclass with four fields, named to mirror FastAPI's
`UploadFile` for an easy adapter:

```python
import ezs3

info = ezs3.FileInfo(
    filename="user-42/avatar.png",      # path relative to a base_prefix
    size=10_240,                        # bytes; None to skip size checks
    content_type="image/png",           # MIME; None to skip CT checks
    hash="sha256:9f86d081...",          # None to skip hash checks
)
```

The same dataclass is what `ManagedStore` returns from `put_*` and what
`ConsistencyChecker` consumes.

#### 2.3.1 Adapting FastAPI's `UploadFile`

```python
from fastapi import UploadFile
import ezs3

async def to_info(upload: UploadFile, base_prefix: str) -> ezs3.FileInfo:
    body = await upload.read()
    return ezs3.FileInfo(
        filename=f"{base_prefix.rstrip('/')}/{upload.filename}",
        size=len(body),
        content_type=upload.content_type,
        hash=ezs3.hash_bytes(body),
    )
```

### 2.4 ConsistencyChecker: S3 ↔ metadata cross-validation

A `ConsistencyChecker` binds a client, a bucket and a base prefix, then yields
`CheckResult` records. It is intentionally lazy so callers can stream results
into a report without materializing everything in memory.

```python
import ezs3

client = ezs3.Client()
bucket = client.bucket("uploads")
checker = ezs3.ConsistencyChecker(client, bucket, base_prefix="user-42/")

infos = [
    ezs3.FileInfo("avatar.png", size=10_240, content_type="image/png"),
    ezs3.FileInfo("notes.txt",  size=12,     content_type="text/plain"),
]

# 1. Walk the caller's metadata, verify every entry exists in S3 with the
#    expected size / content-type / (optionally) hash.
for r in checker.check_infos(infos):
    print(r.filename, r.code.value, r.detail)

# 2. Walk S3 and flag anything not covered by infos.
for r in checker.check_s3(infos):
    print(r.filename, r.code.value)

# 3. Run both directions, deduplicated by (filename, code).
for r in checker.check_both(infos, with_hash=True):
    if not r.ok:
        print("FAIL", r.filename, r.code.value, r.detail)

# 4. Single-entry check — useful in request handlers.
results = checker.check_one(infos[0], with_hash=False)
```

Outcome codes (`ezs3.IssueCode`):

| Code | Meaning |
| --- | --- |
| `OK` | All checked fields match. |
| `MISSING_IN_S3` | A `FileInfo` has no matching object in S3. |
| `UNTRACKED_IN_S3` | An S3 object has no `FileInfo` covering it. |
| `SIZE_MISMATCH` | `FileInfo.size` disagrees with the object's `ContentLength`. |
| `CONTENT_TYPE_MISMATCH` | Bare MIME type (no parameters) disagrees. |
| `HASH_MISMATCH` | Recomputed hash disagrees with `FileInfo.hash`. |
| `HASH_UNAVAILABLE` | `with_hash=True` but `FileInfo.hash` is `None`. |

#### 2.4.1 Writing JSONL reports

`CheckResult` records serialize to compact one-line JSON, perfect for JSON
Lines reports consumable by any downstream tool (jq, BigQuery, ClickHouse…):

```python
results = checker.check_both(infos, with_hash=True)
n = checker.write_report(results, "report.jsonl", include_ok=False)
print(f"wrote {n} non-OK records")

# Equivalent manual loop if you want to interleave logic.
import json
with open("report.jsonl", "w", encoding="utf-8") as fh:
    for r in checker.check_both(infos):
        if r.ok:
            continue
        fh.write(r.to_json() + "\n")
```

`include_ok=False` (the default) skips passing records, so the report only
captures actionable items.

---

## 3. Error handling

### 3.1 Hierarchy

Every ezs3 exception inherits from both `ezs3.S3Error` (the root) and the
closest stdlib equivalent. You can therefore catch either side depending on
what reads best at the call site:

| ezs3 exception | Stdlib parent |
| --- | --- |
| `IsAPrefixError` | `IsADirectoryError` |
| `NotAPrefixError` | `NotADirectoryError` |
| `S3KeyNotFoundError` | `FileNotFoundError` |
| `BucketNotFoundError` | `FileNotFoundError` |
| `BucketAlreadyExistsError` | `FileExistsError` |
| `S3KeyExistsError` | `FileExistsError` |
| `PathNotAttachedError` | `ValueError` |
| `BucketMismatchError` | `ValueError` |
| `HashMismatchError` | `ValueError` |

```python
import ezs3

try:
    (bucket / "missing.json").read_text()
except ezs3.S3KeyNotFoundError:        # ezs3-flavored catch
    ...
except FileNotFoundError:               # stdlib catch — also matches
    ...

# Catch every ezs3-originated error in one place.
try:
    do_s3_work()
except ezs3.S3Error as exc:
    log.exception("S3 failure: %s", exc)
```

### 3.2 Common scenarios

```python
import ezs3

bucket = ezs3.Bucket("my-bucket")

# Reading a prefix as if it were a key.
prefix = bucket / "logs/"
try:
    prefix.read_text()
except ezs3.IsAPrefixError:
    # Switch to traversal.
    for path in prefix.find():
        print(path)

# Listing a key as if it were a prefix.
try:
    (bucket / "config.json").iterdir()
except ezs3.NotAPrefixError:
    ...

# Refusing to clobber an existing key.
try:
    (bucket / "config.json").upload("./config.json")
except ezs3.S3KeyExistsError:
    (bucket / "config.json").upload("./config.json", overwrite=True)

# Deleting something that may not exist.
(bucket / "maybe.txt").remove(missing_ok=True)
bucket.delete(missing_ok=True)

# Creating a bucket idempotently.
bucket.create(exists_ok=True)
client = ezs3.Client()
client.create_bucket("my-bucket", exists_ok=True)

# Free path with no bucket attached.
free = ezs3.Prefix("a/b/c")
try:
    free.read_text()
except ezs3.PathNotAttachedError:
    free.attach(bucket).read_text()

# Mixing buckets accidentally.
other = ezs3.Bucket("other-bucket")
try:
    bucket.remove(other / "x.txt")
except ezs3.BucketMismatchError:
    other.remove("x.txt")

# Hash mismatch during managed-store verification.
store = ezs3.ManagedStore(client, bucket, "blobs/")
try:
    store.verify_strict("sha256:deadbeef" + "00" * 28)
except ezs3.HashMismatchError as exc:
    log.error("Corrupted blob: %s", exc)
except ezs3.S3KeyNotFoundError:
    log.warning("Blob no longer present")
```

### 3.3 Choosing between exceptions and result records

ezs3 has two different idioms for surfacing problems, and they coexist on
purpose:

- I/O helpers (`read_*`, `write_*`, `remove`, `upload`, `download`, …) raise.
  The errors mirror what the standard library would raise for the same logical
  failure (`FileNotFoundError`, `IsADirectoryError`, …).
- The `ConsistencyChecker` returns `CheckResult` records by default, so a
  single pass over thousands of files yields a structured report instead of
  bailing on the first mismatch. Use `ManagedStore.verify_strict` (or check
  `result.ok` yourself) when you prefer to raise on the first problem.

A reasonable default in service code is to let raise-style errors propagate
and surface them at request boundaries, while running checker-style sweeps
periodically (cron, post-deploy, …) and writing JSONL reports to object
storage for follow-up.
