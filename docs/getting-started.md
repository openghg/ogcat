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

# fail unless the filters identify exactly one record
uv run ogcat search --catalog ./my-catalog title="Q1 Report" author=Alice --one --ids
```

## Show a record or its stored path

```bash
uv run ogcat show 1 --catalog ./my-catalog
uv run ogcat path 1 --catalog ./my-catalog
uv run ogcat path 1 --catalog ./my-catalog --readable
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

# Open a separate read-only view for queries in another process.
reader = Catalog.open("./my-catalog", read_only=True)
print(reader.get_one(where={"title": "Q1 Report", "author": "Alice"}).id)
```

``get_one()`` raises an error if no record or more than one record matches.
Use enough metadata to identify the intended artifact. A read-only catalog
rejects writes; reopen it to see writes made by another process.

Only one process should write a TinyDB catalog. Run long computations outside
catalog transactions, then register their finished outputs through one writer.
See the [CLI reference](cli.md) for registering existing paths or collections
without copying them.

See [Catalog records](concepts/catalog-records.md) for a fuller explanation of
the data model, and [Basic catalog tutorial](tutorials/basic-catalog.md) for a
runnable example.
