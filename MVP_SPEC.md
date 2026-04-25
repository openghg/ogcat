# ogcat frozen MVP specification

## Purpose

`ogcat` is a lightweight local file catalog for:

1. adding files with flexible JSON-serialisable metadata,
2. storing them under a managed catalog layout,
3. searching them by metadata, and
4. returning the stored file path immediately.

The MVP focuses on **managed file storage** for curated flux-like data products.
It does not yet cover in-place indexing or artefact-directory indexing.

## Catalog directory layout

A catalog root is a directory containing:

```text
<catalog_root>/
  catalog.json
  db.json
  files/
```

- `catalog.json`: catalog specification and configuration.
- `db.json`: TinyDB database file.
- `files/`: managed file storage root.

## Core concepts

### CatalogSpec

A self-describing configuration object stored in `catalog.json`.

Fields:
- `catalog_name: str`
- `db_backend: str = "tinydb"`
- `db_path: str = "db.json"`
- `files_root: str = "files"`
- `default_operation: Literal["copy", "move"] = "copy"`
- `field_resolution_order: list[str] = ["top_level", "user_metadata", "derived_metadata"]`
- `default_schema: RecordSchema`
- `record_schemas: dict[str, RecordSchema] = {}`

Default schema naming templates:
- `directory_template = "{year_added}/{original_stem}"`
- `filename_template = "{title_slug|original_stem}{original_suffix}"`

Each schema can define `metadata_fields`. Each entry is JSON-serialisable and human-readable:
- `name: str`
- `description: str`
- `example: JSON value | null = null`
- `required: bool = false`

Flux-specific schemas remain valid examples:
- `directory_template = "{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/{version|unversioned}/{flux_type|misc}"`
- `filename_template = "{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}{original_suffix}"`

### Catalog

Primary public Python API object. Constructed from a catalog root.

Public methods in MVP:
- `Catalog.create(root, spec) -> Catalog`
- `Catalog.open(root) -> Catalog`
- `Catalog.add_file(path, metadata=None, operation=None) -> CatalogRecord`
- `Catalog.search(where=None, contains=None, regex=None, ignore_case=False) -> list[CatalogRecord]`
- `Catalog.get(record_id) -> CatalogRecord | None`
- `Catalog.path(record_id) -> Path | None`

### CatalogRecord

A single stored file record with reserved top-level fields:
- `id`
- `catalog`
- `stored_abspath`
- `stored_relpath`
- `storage_mode`
- `time_added`
- `original_path`
- `original_filename`
- `suffixes`
- `user_metadata`
- `derived_metadata`
- `naming_metadata`

## Metadata model

Metadata is split into:
- top-level reserved fields,
- `user_metadata`, and
- `derived_metadata`.

Catalog schemas may also describe important metadata through `metadata_fields`,
but this is not yet used for strict validation.

Only JSON-serialisable values are supported.

## Naming and storage

Files are added in one of two operations:
- `copy`
- `move`

The add flow:
1. build a naming context from system fields and user metadata,
2. render the directory template,
3. render the filename template,
4. create nested directories under `files/`,
5. copy or move the file,
6. insert a `CatalogRecord` into the repository.

### Template syntax

MVP template syntax supports:
- `{field}`
- `{field|fallback}`

The context includes:
- `original_stem`
- `original_suffix`
- `date_added`
- `year_added`
- `year_month_or_original_stem`
- `title_slug` when `title` metadata is present
- all user metadata fields
- the generated record id

### Collision handling

If a rendered filename already exists, append `_2`, `_3`, etc. before the suffix.
For files with multiple suffixes such as `.tar.gz`, the numeric suffix is inserted
before the full joined suffix string.

### Suffix semantics

`suffixes` means the full suffix list from `Path.suffixes`.
Examples:
- `example.nc` -> `[".nc"]`
- `archive.tar.gz` -> `[".tar", ".gz"]`

`original_suffix` in the naming context is the joined suffix string used for rendering,
for example `.nc` or `.tar.gz`.

## Search semantics

MVP search supports:
- equality filters via `where`
- substring filters via `contains`
- regex filters via `regex`
- optional `ignore_case`

Equality is exact value matching. Numeric values are supported for exact equality when
the stored value and query value are both numeric.

Substring and regex matching are string-oriented.

### Field resolution

Unqualified fields are resolved in this order:
1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

Explicit dotted paths always take precedence, for example:
- `user_metadata.species`
- `derived_metadata.netcdf.dims.time`

Numeric comparison operators and richer query expressions are not part of the MVP.
That includes operators such as `>`, `<`, `>=`, `<=`, and range queries.

## Repository abstraction

The rest of the code should depend on a `CatalogRepository` protocol, not directly on TinyDB.
The MVP backend is TinyDB.

## CLI

Executable command: `ogcat`

Commands:
- `ogcat init`
- `ogcat add`
- `ogcat search`
- `ogcat show`
- `ogcat path`

### Catalog selection

A command uses:
1. `--catalog PATH` if provided,
2. otherwise `OGCAT_CATALOG` environment variable.

## Python API

The CLI is a thin wrapper around the Python API.

Example:

```python
from pathlib import Path
from ogcat import Catalog, CatalogSpec, RecordSchema

spec = CatalogSpec(
    catalog_name="fluxes",
    default_schema=RecordSchema(
        directory_template="{species|UNKNOWN_SPECIES}/{domain|GLOBAL}/{product|unknown}/{version|unversioned}/{flux_type|misc}",
        filename_template="{product}_{version}_{species}_{domain}_{flux_type}_{year_month_or_original_stem}{original_suffix}",
    ),
)

catalog = Catalog.create("/tmp/fluxes", spec)
record = catalog.add_file(
    Path("anthropogenic.202401.nc"),
    metadata={
        "product": "CTE-HR",
        "version": "v4.2",
        "species": "CO2",
        "domain": "EUROPE",
        "flux_type": "anthropogenic",
        "year": 2024,
        "month": 1,
    },
)
results = catalog.search(where={"species": "CO2"})
```

## Deliberately deferred

- sidecar metadata files
- netCDF extraction
- in-place indexing
- directory records
- manifest import
- alternate backends
- intake integration
