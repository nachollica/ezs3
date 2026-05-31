# set shell := ["bash", "-c"]
# set dotenv-load := true

[doc("Print available commands.")]
help:
    @just --list

[doc("Set up local environment for development.")]
init:
    uv sync --dev --extra=types

# Build and release

[doc("Prepare wheel in `dist` directory.")]
[group("build")]
build:
    uv build

[doc("Clean build files and Python caches.")]
[group("build")]
clean:
    rm -rf dist/ .coverage .tox {.,src,src/*}/.{ruff,mypy,pytest}_cache
    find src/ -name "__pycache__" | xargs rm -r 2>/dev/null || true

[doc("Publish a new release to the given index (`pypi` or `test-pypi`). Requires `UV_PUBLISH_TOKEN` to be exported in the env.")]
[group("build")]
[confirm("Are you sure you want to publish?")]
publish index="test-pypi":
    uv publish --index {{index}}

# Linters and Testing

[doc("Run all code checks.")]
[group("dev")]
cc: lint tc (test "unit")

[doc("Autofix and lint Python code.")]
[group("dev")]
fix:
    uv run ruff format ./src
    uv run ruff check --fix ./src

[doc("Check code linters.")]
[group("dev")]
lint:
    uv run ruff check ./src

[doc("Static type checking.")]
[group("dev")]
tc:
    uv run mypy ./src

[doc("Test recipes: `just test <unit|integration|tox>` (default: unit).")]
[group("dev")]
test cmd="unit":
    @just test-{{cmd}}

[private]
test-unit:
    uv run pytest

[private]
test-integration:
    EZS3_S3_ENDPOINT_URL=http://localhost:9000 \
    EZS3_S3_ACCESS_KEY=minioadmin \
    EZS3_S3_SECRET_KEY=minioadmin \
    uv run pytest -m integration --no-cov

[private]
test-tox:
    uv run tox

# Documentation

[doc("Docs recipes: `just docs <build|serve|clean>` (default: build).")]
[group("docs")]
docs cmd="build":
    @just docs-{{cmd}}

[private]
docs-build:
    uv run pdoc ezs3 --docformat google --output-directory site

[private]
docs-serve:
    uv run pdoc ezs3 --docformat google --host localhost --port 8080

[private]
docs-clean:
    rm -rf site/

# Local S3-compatible container (using MinIO server).

[doc("Local MinIO container: `just s3-local <up|down|logs|status>` (default: status).")]
[group("s3-local")]
s3-local cmd="status":
    @just s3-local-{{cmd}}

[private]
s3-local-status:
    @docker ps --filter name=ezs3-minio --format json | jq -r '.Names + " " + .Status' 2>/dev/null || docker ps --filter name=ezs3-minio

[private]
s3-local-up:
    docker run -d --rm \
        --name ezs3-minio \
        -p 9000:9000 \
        -p 9001:9001 \
        -e MINIO_ROOT_USER=minioadmin \
        -e MINIO_ROOT_PASSWORD=minioadmin \
        quay.io/minio/minio server /data --console-address ":9001"
    @echo "MinIO ready at http://localhost:9000 (console: http://localhost:9001)"

[private]
s3-local-down:
    docker stop ezs3-minio

[private]
s3-local-logs:
    docker logs -f ezs3-minio
