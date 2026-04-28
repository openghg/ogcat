# local_file_catalog example

This example shows the basic ogcat workflow: create a catalog, add a few
generated files with metadata, and search the records.

## Requirements

``ogcat`` must be installed:

```bash
pip install -e .
```

## Run

```bash
python examples/local_file_catalog/scripts/run.py
```

The script creates a temporary catalog under the system temporary directory,
adds three generated text files with metadata, runs a few searches, prints a
short report, and removes the temporary directory when done.

## Purpose

- Show the minimal Python API surface: ``Catalog.create``, ``add_file``,
  ``search``, and ``path``.
- Demonstrate metadata field declarations in a ``RecordSchema``.
- Produce output that is easy to inspect and understand.

## Data

The example generates small text files at runtime.  No pre-existing data
files are required.
