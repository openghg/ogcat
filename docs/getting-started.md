# Getting started

This page covers the basic workflow: create a catalog, add a file, search for
records, and retrieve the stored path.

## Create a catalog

```bash
uv run ogcat init ./my-catalog --name demo
```

This writes ``my-catalog/catalog.json`` and creates ``my-catalog/data/files/``
and ``my-catalog/data/objects/``.

## Add a file

```bash
uv run ogcat add ./report.pdf \
  --catalog ./my-catalog \
  --meta title="Q1 Report" author="Alice" year=2024
```

The file is copied into the catalog's ``data/objects/`` tree.  The record id is
printed to stdout.

## Search

```bash
# equality
uv run ogcat search --catalog ./my-catalog author=Alice

# substring
uv run ogcat search --catalog ./my-catalog title:report

# print stored paths instead of records
uv run ogcat search --catalog ./my-catalog year=2024 --paths
```

## Show a record or its stored path

```bash
uv run ogcat show 1 --catalog ./my-catalog
uv run ogcat path 1 --catalog ./my-catalog
```

## Python API

```python
from pathlib import Path
from ogcat import Catalog, CatalogSpec

spec = CatalogSpec(catalog_name="demo")
catalog = Catalog.create("./my-catalog", spec)

record = catalog.add_file(
    Path("report.pdf"),
    metadata={"title": "Q1 Report", "author": "Alice", "year": 2024},
)
print(record.id)
print(catalog.path(record.id))

matches = catalog.search(where={"author": "Alice"})
```

See [Catalog records](concepts/catalog-records.md) for a fuller explanation of
the data model, and [Basic catalog tutorial](tutorials/basic-catalog.md) for a
runnable example.
