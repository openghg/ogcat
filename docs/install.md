# Installation

## Requirements

- Python 3.11 or later

## Install from source

```bash
uv sync
```

## Optional extras

### netCDF metadata extraction

```bash
uv sync --extra netcdf
```

When ``xarray`` is available, ``ogcat`` extracts a lightweight summary of
dimension names and sizes from ``.nc`` files during ingest.

### Documentation build

```bash
uv sync --extra docs
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
