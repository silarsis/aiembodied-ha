# aiembodied-ha

Home Assistant integration for AI Embodied.

## Development environment

This project targets Python 3.13. The recommended workflow uses [uv](https://docs.astral.sh/uv/)
to provision the interpreter and manage dependencies locally:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements-dev.txt
```

With the virtual environment prepared, run the linters and tests via uv to ensure they use the
Python 3.13 toolchain:

```bash
uv run --python .venv ruff check
uv run --python .venv pytest --cov=custom_components
```

## Continuous integration

Merge requests are validated by GitLab CI before they can be merged to `main`. The pipeline uses
the `python:3.13-slim` container image, provisions the uv environment, runs `ruff check`, and then
executes the unit test suite with coverage enabled. Coverage (`cov.xml`) and JUnit (`junit.xml`)
artifacts are uploaded automatically so merge request widgets can surface their results.
