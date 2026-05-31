# ezs3

[![PyPI](https://img.shields.io/pypi/v/ezs3.svg)](https://pypi.org/project/ezs3/)
[![Python](https://img.shields.io/pypi/pyversions/ezs3.svg)](https://pypi.org/project/ezs3/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/nachollica/ezs3/blob/master/LICENSE)

A typed, **Path-like** abstraction over [boto3](https://pypi.org/project/boto3/)
for working with Amazon S3. Treat keys and prefixes the same way you treat
`pathlib.Path` objects — slash to compose, `read_text`/`write_text` to do I/O,
`iterdir`/`glob`/`rglob` to traverse — with proper type stubs and pathlib-style
exceptions.

```python
import ezs3

bucket = ezs3.Bucket("my-bucket")
(bucket / "reports" / "today.json").write_text('{"ok": true}')
for path in bucket.rglob("*.json"):
    print(path, path.read_text())
```

## Table of contents

- [Installation](#installation)
- [Usage](#usage)
- [API at a glance](#api-at-a-glance)
- [Exceptions](#exceptions)
- [Requirements](#requirements)
- [Development](#development)

## Installation

```bash
pip install ezs3
```

For full IDE autocomplete and static type-checking on boto3-derived kwargs
(`Unpack[...]` typed dicts, client responses, etc.), install the optional
`types` extra. It pulls in
[`boto3-stubs[s3]`](https://pypi.org/project/boto3-stubs/) so that the symbols
ezs3 references under `TYPE_CHECKING` resolve in the user's environment:

```bash
pip install "ezs3[types]"
```

Stubs are a type-check-time concern, so most projects only need them in their
dev dependency group.

Credentials follow the standard boto3 resolution chain: environment variables,
`~/.aws/credentials`, instance role, etc. Override per-client when needed
(see [Authentication in USAGE.md](USAGE.md#12-authentication)).

## Usage

These snippets show the most common interfaces. See [USAGE.md](USAGE.md) for a detailed guide with more examples and features.

### Paths and I/O

```python
import ezs3

# Implicit default client; or build one with explicit credentials.
bucket = ezs3.Bucket("my-bucket")

# Compose with `/` just like pathlib.
key = bucket / "reports" / "today.json"

# In-memory I/O.
key.write_text('{"ok": true}')
data = key.read_text()

# Existence and classification.
key.exists()                 # key OR prefix exists at this path
key.is_key()                 # key only (alias: is_file)
key.is_prefix()              # prefix only (alias: is_dir)

# Listing / globbing on a prefix.
for path in (bucket / "data").rglob("*.json"):
    print(path)

# Deletion.
key.remove()                 # one key (alias: rm)
(bucket / "tmp").rmtree()    # recursive
```

### Working with `Client`

`Client` wraps a boto3 session and owns bucket lifecycle. Build one
explicitly when you need a custom endpoint (MinIO, LocalStack, R2...),
non-default credentials, or to target several accounts in the same
process. Bucket-lifecycle calls can fail with the underlying
`botocore.exceptions.ClientError` when the IAM identity lacks
permission — catch it alongside the ezs3-specific errors:

```python
import ezs3
from botocore.exceptions import ClientError

client = ezs3.Client(
    endpoint_url="http://localhost:9000",  # omit for real AWS
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
)

# Get or create a Bucket called "ezs3-reports",
# raise if not allowed.
try:
    bucket = client.create_bucket("ezs3-reports")
except ezs3.BucketAlreadyExistsError:
    bucket = client.bucket("ezs3-reports")
except ClientError as exc:
    code = exc.response.get("Error", {}).get("Code", "")
    if code in ("AccessDenied", "AllAccessDisabled"):
        raise SystemExit("Missing permissions to read/create Buckets")
```

### Content-addressed uploads with `ManagedStore`

`ManagedStore` is a thin layer on top of a `Bucket` that stores every
blob at `<base_prefix>/<alg>:<digest>`. Two callers uploading the same
bytes land on the same key — free deduplication. The store echoes back
a `FileInfo` you can persist in your own database row:

```python
store = ezs3.ManagedStore(client, bucket, base_prefix="blobs/")

info = store.put_bytes(b"hello world", content_type="text/plain")
# info.hash == "sha256:b94d27b9934d3e08..."

assert store.get_bytes(info.hash) == b"hello world"
assert store.verify(info).ok
```

## API at a glance

| Type | Purpose |
| --- | --- |
| `ezs3.Client` | Boto3 wrapper for credentials and bucket lifecycle. |
| `ezs3.Bucket` | Named bucket handle. Supports `/` and path-style helpers. |
| `ezs3.S3Path` | Path-like representation of a key or prefix. |
| `ezs3.Prefix`, `ezs3.Key` | Aliases for `S3Path`. Use whichever documents intent best. |
| `ezs3.ManagedStore` | Content-addressed blob store on top of a bucket. Byte-identical uploads dedup automatically. |
| `ezs3.ConsistencyChecker` | Cross-validate S3 contents against caller-supplied `FileInfo` metadata. |
| `ezs3.FileInfo` | Dataclass: `filename`, `size`, `content_type`, `hash`. Mirrors FastAPI's `UploadFile`. |
| `ezs3.hash_bytes`, `ezs3.hash_stream` | Stdlib `hashlib` wrappers returning `"<alg>:<hex-digest>"`. |

Full API reference is auto-generated from docstrings — see
[Building the docs](#building-the-docs).

## Exceptions

Hierarchy mirrors `pathlib`. Every error inherits from both `ezs3.S3Error` and
the closest stdlib equivalent, so you can catch either:

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
try:
    prefix.read_text()
except ezs3.IsAPrefixError:
    ...

try:
    key.iterdir()
except ezs3.NotAPrefixError:
    ...
```

See [Error handling in USAGE.md](USAGE.md#3-error-handling) for full
recipes and the rationale for exceptions vs result records.

---

## Requirements

End users:

- **Python ≥ 3.9**.
- An S3-compatible service and credentials. AWS S3 works out of the box;
  [MinIO](https://min.io/) and [LocalStack](https://localstack.cloud/) work
  via `Client(endpoint_url=...)`.

Contributors additionally need:

- [**uv**](https://docs.astral.sh/uv/) for dependency management.
- [**just**](https://github.com/casey/just) as a task runner.
- [**Docker**](https://www.docker.com/) (only for integration tests, to run a
  local MinIO container).

## Development

Clone and install the dev dependencies:

```bash
git clone https://github.com/nachollica/ezs3
cd ezs3
uv sync --all-groups
```

### Running checks

```bash
just cc          # same as running `just lint tc test` (lint + typecheck + unit tests)
just fix         # ruff format + autofix
just test tox    # run the test suite under every supported Python
```

### Integration tests against MinIO

A local S3-compatible service is needed for the `integration` test marker.
This project bundles MinIO via Docker:

```bash
just s3-local up           # start MinIO on :9000 (console :9001)
just test integration      # run pytest -m integration
just s3-local down         # stop the container
```

### Building the docs

API documentation is generated from Google-style docstrings using
[**pdoc**](https://pdoc.dev):

```bash
just docs [build]    # build into ./site/
just docs serve      # serve with live reload at http://localhost:8080
just docs clean      # rm -rf site/
```

### Releasing

```bash
just build         # uv build (wheel + sdist in dist/)
just publish       # uv publish (requires UV_PUBLISH_TOKEN)
```
