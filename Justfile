# set shell := ["bash", "-c"]
# set dotenv-load := true

[doc("Print available commands.")]
help:
    @just --list

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
cc: lint tc test

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

[doc("Run tests under `src/tests/`.")]
[group("dev")]
test:
    uv run pytest

[doc("Run tests for all supported Python versions.")]
[group("dev")]
tox:
    uv run tox
