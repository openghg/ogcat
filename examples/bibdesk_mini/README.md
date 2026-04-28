# bibdesk_mini example

This example uses ogcat as a simple personal paper library.

Records are created from BibTeX entries in a small bundled fixture and stored
in a catalog with a ``paper`` record type and a declared metadata schema.

## Requirements

``ogcat`` must be installed:

```bash
pip install -e .
```

No external bibliography library is required.  The example includes a small
BibTeX parser.

## Run

```bash
python examples/bibdesk_mini/scripts/run.py
```

The script reads ``examples/bibdesk_mini/data/refs.bib``, creates a temporary
catalog, adds one record per BibTeX entry, runs searches by year and keyword,
prints a short report, and removes the temporary directory.

## Purpose

- Show that ogcat is useful outside atmospheric science.
- Demonstrate named record schemas (``paper``).
- Show ``add_artifact()`` for records without a managed local file.
- Demonstrate ``contains`` search with ``ignore_case=True``.

## Data

``data/refs.bib`` is a small vendored BibTeX fixture with four entries.  It
is committed to the repository and should not be modified in ways that break
the smoke tests.
