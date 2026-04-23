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
- simple directory / filename templates with small computed naming fields
- lightweight descriptive `metadata_fields` in `catalog.json`
- optional netCDF metadata extraction when `xarray` is installed
- equality, contains, and regex search
- flattened field lookup with dotted-path override
- CLI based on Typer

## Search options

`ogcat` currently supports three search modes plus optional case-insensitive matching.
The same semantics apply through the Python API and the CLI.

- `where`: exact equality matching
- `contains`: substring matching
- `regex`: regular-expression matching
- `ignore_case`: case-insensitive matching for string equality, substring, and regex

### Field resolution

Unqualified field names are resolved in this order:
1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

That means a top-level record field such as `id` or `catalog` wins over metadata fields
with the same name.

If you want to bypass flattened lookup, use an explicit dotted path such as:

- `user_metadata.species`
- `derived_metadata.netcdf.dims.time`
- `user_metadata.product.family.name`

### CLI examples

```bash
ogcat search --where species=CO2
ogcat search --contains title=anthropogenic --ignore-case
ogcat search --regex version='^v4\.[0-9]+$'
ogcat search --where derived_metadata.netcdf.dims.time=12
```

`--where` values are parsed as JSON when possible, so numeric equality works for exact
matches such as `--where derived_metadata.netcdf.dims.time=12`.

### Python examples

```python
results = catalog.search(where={"species": "CO2"})
results = catalog.search(contains={"title": "anthropogenic"}, ignore_case=True)
results = catalog.search(regex={"version": r"^v4\.[0-9]+$"})
results = catalog.search(where={"user_metadata.product.family.revision": 2})
```

### Not yet supported

Advanced query expressions are still out of scope for the MVP. In particular, search
does not yet support numeric comparison operators or ranges such as `>`, `<`, `>=`,
`<=`, or "between" queries.

## Not yet implemented

- sidecar metadata files
- in-place file indexing
- in-place directory indexing
- richer manifest import
- advanced query expressions
- multiple repository backends

See `MVP_SPEC.md` for the frozen MVP specification.
