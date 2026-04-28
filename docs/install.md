# Installation

## Requirements

- Python 3.11 or later

## Install from source

```bash
pip install -e .
```

## Optional extras

### netCDF metadata extraction

```bash
pip install -e '.[netcdf]'
```

When ``xarray`` is available, ``ogcat`` extracts a lightweight summary of
dimension names and sizes from ``.nc`` files during ingest.

### Documentation build

```bash
pip install -e '.[docs]'
sphinx-build docs docs/_build/html
```
