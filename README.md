# ogcat

`ogcat` stands for OpenGHG Catalog.

`ogcat` is a lightweight artifact catalog with a managed-file MVP. Today it provides a
self-describing on-disk catalog layout, a small Python API, and a CLI for creating catalogs,
adding files by copy or move, listing metadata field descriptions, and locating stored paths.

## Scope

- local catalogs centred on managed file ingest
- a self-describing catalog layout with `catalog.json`, `db.json`, and `files/`
- path-based managed ingest using `copy` or `move`
- flexible JSON-serialisable user metadata
- simple derived metadata extraction for supported file types
- template-based storage naming
- exact, contains, and regex search from Python and CLI
- shell-friendly CLI outputs for ids, paths, and JSON where appropriate

## Non-goals

- domain-specific validation or workflow logic
- strict typed schemas for records or metadata
- in-place indexing of arbitrary existing directories
- reader or manager APIs beyond the current small extractor layer
- promising richer catalog backends or integrations that do not exist yet

## Design Overview

`ogcat` is organised around a small catalog specification and a narrow catalog API.

- Catalog spec: `catalog.json` stores the catalog name, storage layout templates, default ingest mode, field resolution order, and descriptive metadata field definitions.
- Repository abstraction: catalog records are stored through a repository protocol so the rest of the package does not depend directly on TinyDB details.
- Records: each record stores reserved top-level fields plus `user_metadata`, `derived_metadata`,
  and `naming_metadata`. Records now also carry a small `record_type` and `locator` so the model
  can grow beyond copied or moved local files without changing the basic catalog shape.
- Naming and templates: file placement under `files/` is driven by simple directory and filename templates evaluated from record id, source filename parts, timestamps, and user metadata.
- Derived metadata extractors: optional extractors can add lightweight summaries after ingest. The current implementation includes a netCDF extractor when `xarray` is installed.
- Search and CLI: search supports exact equality, substring contains, and regex matching, with flattened field lookup and dotted-path access for nested metadata. The CLI exposes the same search model and adds shell-oriented output modes.

## Catalog Layout

Each catalog root is self-describing:

```text
<catalog-root>/
  catalog.json
  db.json
  files/
```

- `catalog.json`: catalog specification and descriptive metadata field definitions
- `db.json`: TinyDB-backed record store
- `files/`: managed storage root for ingested files

## Installation

```bash
pip install -e .
```

Optional netCDF metadata extraction:

```bash
pip install -e '.[netcdf]'
```

## Python API

Create a catalog, add a file, and search by metadata:

```python
from pathlib import Path

from ogcat import Catalog, CatalogSpec

spec = CatalogSpec(catalog_name="fluxes")
catalog = Catalog.create("example-catalog", spec)

record = catalog.add_file(
    Path("anthropogenic.202401.nc"),
    metadata={
        "title": "Anthropogenic test flux",
        "product": "CTE-HR",
        "species": "CO2",
        "version": "v4.2",
        "year": 2024,
        "month": 1,
    },
)

print(record.id)
print(catalog.path(record.id))

matches = catalog.search(where={"species": "CO2"})
regex_matches = catalog.search(regex={"version": r"^v4\.[0-9]+$"})
```

Field lookup supports both flattened names and explicit dotted paths:

```python
catalog.search(contains={"title": "anthropogenic"}, ignore_case=True)
catalog.search(where={"user_metadata.product.family.revision": 2})
catalog.search(where={"derived_metadata.netcdf.dims.time": 12})
```

## CLI

Initialise a catalog:

```bash
ogcat init ./example-catalog --name fluxes
```

Add a file with metadata:

```bash
ogcat add ./anthropogenic.202401.nc \
  --catalog ./example-catalog \
  --meta species=CO2 \
  product=CTE-HR \
  'version="v4.2"' \
  'title="Anthropogenic test flux"'
```

Search records:

```bash
ogcat search --catalog ./example-catalog --where species=CO2
ogcat search --catalog ./example-catalog --contains title=anthropogenic --ignore-case
ogcat search --catalog ./example-catalog --regex version='^v4\.[0-9]+$'
ogcat search --catalog ./example-catalog --where derived_metadata.netcdf.dims.time=12 --paths
```

Show a record or print its stored path:

```bash
ogcat show rec_000001 --catalog ./example-catalog
ogcat path rec_000001 --catalog ./example-catalog
```

Inspect catalog info and declared metadata fields:

```bash
ogcat info --catalog ./example-catalog
ogcat fields --catalog ./example-catalog
ogcat fields --catalog ./example-catalog --json
```

`ogcat search` supports exact equality through `--where`, substring matching through `--contains`, and regular expressions through `--regex`. For automation and shell use, `--json`, `--ids`, and `--paths` provide stable machine-friendly outputs.

## Search Semantics

Unqualified field names are resolved in this order:

1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

If you need to bypass flattened lookup, use an explicit dotted path such as `user_metadata.species` or `derived_metadata.netcdf.dims.time`.

Current search is intentionally small. It does not support numeric range queries or richer expressions such as `>`, `<`, `>=`, `<=`, or boolean query composition.

## Storage Model

Current storage is still path-based managed ingest for the MVP. Files are copied or moved into the
catalog's `files/` tree, and the resulting stored path is recorded in the catalog database
alongside metadata and naming information.

Records now also include a minimal locator block:

- `record_type`: what kind of artifact the record represents, for example `managed_file`
- `locator`: how that artifact is located, currently most often a local `path`

For compatibility, managed local files still keep `stored_abspath` and `stored_relpath`. Those
fields remain the simple path-facing surface for today's workflows while the locator model opens a
path toward external references, directory-like stores, and future transform targets.

`catalog.json` stores the naming templates and descriptive metadata field definitions so a catalog remains understandable without additional application state. `metadata_fields` are descriptive only today; they are not enforced as typed schemas.

## Current Limitations

- the only supported backend today is TinyDB behind the repository abstraction
- non-file record types are only partially modelled so far; readers, managers, and richer URI
  handling are still future work
- derived metadata extraction is intentionally small and currently focused on optional netCDF summaries
- there are no typed per-record schemas, readers, or manager bindings yet
- richer readers, managers, and import workflows are future work

## Roadmap

The current direction is:

- today: spec-driven file catalog with metadata, naming, and search
- next: generalise from managed files to catalogued artefacts with clearer record typing and locator handling
- later: typed schemas, reader hooks, manager bindings, and scan or import workflows

See [docs/architecture.md](docs/architecture.md),
[docs/design-note-artifact-locators.md](docs/design-note-artifact-locators.md),
and [docs/roadmap.md](docs/roadmap.md) for more detail.
