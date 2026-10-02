# Getting started

This page covers the basic workflow: create a catalog, add a file, search for
records, and retrieve the stored path.

## Create a catalog

```bash
uv run ogcat init ./my-catalog --name demo
```

This writes ``my-catalog/catalog.json`` and ``my-catalog/db.json`` and creates
``my-catalog/data/files/`` and ``my-catalog/data/objects/``. Creation refuses an
existing specification or database. Opening an existing catalog refuses a
missing or corrupt database; a legacy zero-byte database is accepted as empty.

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
with Catalog.create("./my-catalog", spec) as catalog:
    record = catalog.add_file(
        Path("report.pdf"),
        metadata={"title": "Q1 Report", "author": "Alice", "year": 2024},
    )
    print(record.id)
    print(catalog.path(record.id))

    matches = catalog.search(where={"author": "Alice"})

# Open a separate read-only view for queries in another process.
with Catalog.open("./my-catalog", read_only=True) as reader:
    print(reader.get_one(where={"title": "Q1 Report", "author": "Alice"}).id)
```

``get_one()`` raises an error if no record or more than one record matches.
Use enough metadata to identify the intended artifact. A read-only catalog
rejects writes and takes no writer lock. Its repository queries read fresh
records after another process writes; previously returned results remain snapshots.
The context manager closes the database handle on exit, including when an
exception occurs. Already returned records and search results remain usable.
For a long-lived notebook variable, call ``catalog.close()`` when finished.
Closing does not roll back completed operations; see
[transactions and resource lifetime](concepts/transactions-and-logging.md).

A writable catalog holds a POSIX advisory database lock until it closes.
Opening another writable instance, even in the same process, fails immediately.
Close a notebook's old instance before reopening it. Run long computations
outside writable catalog sessions, then register finished outputs through one
writer.
See the [CLI reference](cli.md) for registering existing paths or collections
without copying them.

Use ``ogcat update-metadata ID --catalog ROOT --meta KEY=VALUE`` to edit a
record's user metadata. Existing storage names remain unchanged. Run
``ogcat check --catalog ROOT`` to inspect registered local paths; see
[checking and backing up](how-to/check-and-back-up.md) for its limits and a
backup procedure.

See [Catalog records](concepts/catalog-records.md) for a fuller explanation of
the data model, and [Basic catalog tutorial](tutorials/basic-catalog.md) for a
runnable example.
