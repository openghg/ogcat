# ogcat

`ogcat` is a lightweight, local file catalog for storing files in a managed layout,
tracking flexible metadata, and searching records from either a CLI or Python.

This repository is a **starter skeleton** for the MVP. It is intentionally small,
readable, and easy to hand off to Codex for completion.

## Current scope

- self-describing catalog directory with `catalog.json`, `db.json`, and `files/`
- `CatalogSpec` and `Catalog` API
- TinyDB-backed repository behind a repository protocol
- managed `copy` / `move` add flow
- simple directory / filename templates
- equality, contains, and regex search
- flattened field lookup with dotted-path override
- CLI based on Typer

## Not yet implemented

- netCDF metadata extraction
- sidecar metadata files
- in-place file indexing
- in-place directory indexing
- richer manifest import
- advanced query expressions
- multiple repository backends

See `MVP_SPEC.md` for the frozen MVP specification.
