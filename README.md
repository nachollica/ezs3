# ezs3

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
- [License](#license)

## Installation

```bash
pip install ezs3
```

Credentials follow the standard boto3 resolution chain: environment variables,
`~/.aws/credentials`, instance role, etc. Override per-client when needed
(see [Custom endpoint](#custom-endpoint-minio--localstack)).

## Usage

### Clients and buckets

```python
import ezs3

client = ezs3.Client()                       # default credentials
buckets = client.list_buckets()              # list[ezs3.Bucket]

# Create / delete buckets (requires permission).
tmp = client.create_bucket("ezs3-tmp")
client.delete_bucket(tmp, force=True)        # force=True empties first

# Reach an existing bucket without listing.
bucket = client.bucket("my-bucket")          # local handle (no API call)
bucket = ezs3.Bucket("my-bucket")            # equivalent, uses default client
```

### Path composition

Slash composes paths the same way as `pathlib.Path`. The result is an
`ezs3.S3Path` (also exported as `ezs3.Prefix` and `ezs3.Key`):

```python
prefix = bucket / "project" / "raw"
assert str(prefix) == "s3://my-bucket/project/raw"

key = prefix / "events.json"
assert key.name == "events.json"
assert key.suffix == ".json"
assert key.parent == prefix
```

`S3Path` may also be constructed directly. *Free* paths are not attached to any
bucket; attach them later when you know where they belong:

```python
free = ezs3.Prefix("project/raw")            # bucket is None
attached = free.attach(bucket)               # now bound

# Other equivalent forms:
ezs3.Prefix(bucket, "project/raw")
ezs3.Prefix("my-bucket", "project/raw")
ezs3.Prefix("s3://my-bucket/project/raw")
```

### Reading and writing

```python
key = bucket / "config.json"
key.write_text('{"flag": true}')
key.write_bytes(b"\x00\x01\x02")

key.read_text()            # -> str
key.read_bytes()           # -> bytes

bucket.write_text("hello.txt", "hi")   # bucket-level shortcut
bucket.read_text("hello.txt")
```

### Existence checks

```python
key.exists()       # True if either a key or a prefix exists at this path
key.is_key()       # True only if a key exists (== is_file alias)
key.is_prefix()    # True if any object exists under this prefix (== is_dir alias)
```

### Listing and globbing

```python
for child in (bucket / "data").iterdir():    # one level deep
    print(child)

for path in (bucket / "data").find():        # recursive, every key
    print(path)

for path in bucket.glob("*.json", prefix="data"):     # one level
    ...

for path in bucket.rglob("*.json", prefix="data"):    # recursive
    ...
```

### Deletion

```python
key.remove()                # delete a single key (== rm alias)
prefix.rmtree()             # recursive delete
bucket.remove("a.txt", "b.txt", "c.txt")   # batched DeleteObjects
bucket.clear()              # empty the bucket
```

### Custom endpoint (MinIO / LocalStack)

```python
client = ezs3.Client(
    endpoint_url="http://localhost:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
)
```

## API at a glance

| Type | Purpose |
| --- | --- |
| `ezs3.Client` | Boto3 wrapper for credentials and bucket lifecycle. |
| `ezs3.Bucket` | Named bucket handle. Supports `/` and path-style helpers. |
| `ezs3.S3Path` | Path-like representation of a key or prefix. |
| `ezs3.Prefix`, `ezs3.Key` | Aliases for `S3Path`. Use whichever documents intent best. |

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
| `PathNotAttachedError` | `ValueError` |
| `BucketMismatchError` | `ValueError` |

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
just cc            # lint + typecheck + unit tests
just lint          # ruff check
just tc            # mypy
just test          # pytest (unit, excludes integration marker)
just fix           # ruff format + autofix
just tox           # run the test suite under every supported Python
```

### Integration tests against MinIO

A local S3-compatible service is needed for the `integration` test marker.
This project bundles MinIO via Docker:

```bash
just s3-local-up           # start MinIO on :9000 (console :9001)
just test-integration      # run pytest -m integration
just s3-local-down         # stop the container
```

### Building the docs

API documentation is generated from Google-style docstrings using
[**pdoc**](https://pdoc.dev):

```bash
just docs          # build into ./site/
just docs-serve    # serve with live reload at http://localhost:8080
just docs-clean    # rm -rf site/
```

### Releasing

```bash
just build         # uv build (wheel + sdist in dist/)
just publish       # uv publish (requires UV_PUBLISH_TOKEN)
```

## License

[MIT](https://github.com/nachollica/ezs3/blob/master/LICENSE).
