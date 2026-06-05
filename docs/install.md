# Installation

## Requirements

- Python 3.11 or later

## Install from PyPI

Once ``ogcat`` has a published release, install it like a normal Python package:

```bash
uv add ogcat
```

For one-off CLI use without adding it to a project:

```bash
uvx ogcat --help
```

Or with pip:

```bash
python -m pip install ogcat
```

## Install from source for development

```bash
uv sync
```

## Optional extras

### netCDF metadata extraction

For a project dependency:

```bash
uv add "ogcat[netcdf]"
```

For local source development:

```bash
uv sync --extra netcdf
```

When ``xarray`` is available, ``ogcat`` extracts a lightweight summary of
dimension names and sizes from ``.nc`` files during ingest.

### fsspec-backed storage URLs

For a project dependency:

```bash
uv add "ogcat[fsspec]"
```

For local source development:

```bash
uv sync --extra fsspec
```

### Documentation build

```bash
uv sync --group docs
uv run sphinx-build -b html docs docs/_build/html
```

For CI-style warning checks:

```bash
uv run sphinx-build -W -b html docs docs/_build/html
```

To serve the generated HTML locally:

```bash
cd docs/_build/html
uv run python -m http.server 8000
```
