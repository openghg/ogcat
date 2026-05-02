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
- domain-specific built-in schemas
- in-place indexing of arbitrary existing directories
- reader or manager APIs beyond the current small extractor layer
- promising richer catalog backends or integrations that do not exist yet

## Design Overview

`ogcat` is organised around a small catalog specification and a narrow catalog API.

- Catalog spec: `catalog.json` stores the catalog name, default ingest mode, field resolution
  order, and a default record schema with optional named schemas.
- Repository abstraction: catalog records are stored through a repository protocol so the rest of the package does not depend directly on TinyDB details.
- Records: each record stores reserved top-level fields plus `user_metadata`, `derived_metadata`,
  and `naming_metadata`. Records now also carry a small `record_type` and `locator` so the model
  can grow beyond copied or moved local files without changing the basic catalog shape.
- Naming and templates: file placement under `files/` is driven by simple directory and filename templates evaluated from record id, source filename parts, timestamps, and user metadata.
- Derived metadata extractors: optional extractors can add lightweight summaries after ingest. The current implementation includes a netCDF extractor when `xarray` is installed.
- Hooks and plugins: projects can register Python hook objects to add domain-specific metadata,
  validation, rollback, and lifecycle behavior without adding that logic to `ogcat` core.
- Search and CLI: search supports exact equality, substring contains, and regex matching, with flattened field lookup and dotted-path access for nested metadata. The CLI exposes the same search model and adds shell-oriented output modes.

## Catalog Layout

Each catalog root is self-describing:

```text
<catalog-root>/
  catalog.json
  db.json
  files/
```

- `catalog.json`: catalog specification, default schema, and optional named record schemas
- `db.json`: TinyDB-backed record store
- `files/`: managed storage root for ingested files

## Installation

```bash
uv sync
```

Optional netCDF metadata extraction:

```bash
uv sync --extra netcdf
```

Optional fsspec-backed storage URLs:

```bash
uv sync --extra fsspec
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
from ogcat import SearchQuery

catalog.search(contains={"title": "anthropogenic"}, ignore_case=True)
catalog.search(where={"user_metadata.product.family.revision": 2})
catalog.search(where={"derived_metadata.netcdf.dims.time": 12})
catalog.search(SearchQuery.eq("species", "CO2").contains("tags", "paris"))
catalog.search(exists=["user.site.code"], missing=["user.platform"])
```

The CLI accepts both explicit flags and simple positional expressions:

```bash
uv run ogcat search --catalog example-catalog species=CO2
uv run ogcat search --catalog example-catalog tags:paris user.site.code? --json
uv run ogcat search --catalog example-catalog 'locator.uri~s3://bucket/*.zarr' --match title=paris --ids
```

Register simple hooks directly in Python:

```python
from ogcat import Catalog, CatalogSpec, PluginRegistry
from ogcat.hooks import OperationContext


class FilenameTitlePlugin:
    def before_validate_metadata(self, context: OperationContext) -> None:
        if context.source_path is not None:
            context.user_metadata.setdefault("title", context.source_path.stem)


spec = CatalogSpec(catalog_name="files")
plugins = PluginRegistry([FilenameTitlePlugin()])
catalog = Catalog.create("example-catalog", spec, plugins=plugins)
```

See [docs/design-note-hooks-plugins.md](docs/design-note-hooks-plugins.md) for hook lifecycle,
rollback, and transaction examples.

In this hook model, `add_artifact()` records an artifact locator but does not write artifact data by
default. Pass an `OperationSource` and artifact writer to `add_artifact()` when a plugin or helper
should materialise data before the record is written. `add_file()` is the bundled local-file
operation that uses the same writer path to copy or move data before writing the record.
See `ogcat.writers` for small helper wrappers around in-memory data, path-backed transforms, and zip
extraction examples.

Use `catalog.plan_artifact(...)` to dry-run a planned target before writing. The returned
`StoragePlan` is available to hooks and artifact writers as `context.storage_plan`; for older
`add_artifact(locator=..., artifact_writer=...)` flows, ogcat derives a plan from the writer's
declared `target_kind` and `write_mode` when available. Domain logic can create directory-like
artifacts such as NetCDF collections or `.zarr` stores while ogcat core records only generic locators
and metadata. Artifact writers remain the place where filesystem work and rollback registration
happen.

## CLI

Initialise a catalog:

```bash
uv run ogcat init ./example-catalog --name fluxes
```

Add a file with metadata:

```bash
uv run ogcat add ./anthropogenic.202401.nc \
  --catalog ./example-catalog \
  --meta species=CO2 \
  product=CTE-HR \
  'version="v4.2"' \
  'title="Anthropogenic test flux"'
```

Search records:

```bash
uv run ogcat search --catalog ./example-catalog --where species=CO2
uv run ogcat search --catalog ./example-catalog species=CO2 tags:paris
uv run ogcat search --catalog ./example-catalog --contains title=anthropogenic --ignore-case
uv run ogcat search --catalog ./example-catalog --regex version='^v4\.[0-9]+$'
uv run ogcat search --catalog ./example-catalog --where derived.netcdf.dims.time=12 --paths
uv run ogcat search --catalog ./example-catalog --where species=CO2 --limit 20
uv run ogcat search --catalog ./example-catalog --where species=CO2 --fields id,species,user_metadata.domain,path
uv run ogcat search --catalog ./example-catalog --where species=CO2 --fields id,species,path --format tsv
uv run ogcat search --catalog ./example-catalog --where species=CO2 --all
```

Show a record or print its stored path:

```bash
uv run ogcat show 1 --catalog ./example-catalog
uv run ogcat path 1 --catalog ./example-catalog
```

Inspect catalog info and declared metadata fields:

```bash
uv run ogcat info --catalog ./example-catalog
uv run ogcat fields --catalog ./example-catalog
uv run ogcat fields --catalog ./example-catalog --json
```

`ogcat search` supports compact positional filters: `field=value` for equality, `field:value` for contains/list membership, `field~pattern` for glob or substring matching, `field?` for exists, and `!field?` for missing. Compatibility flags remain available: `--where`, `--contains`, `--match`, `--regex`, `--exists`, and `--missing`. Human-readable search output is capped by default; use `--limit N` to choose a cap or `--all` to show every match. Use `--fields a,b,c` to choose displayed fields, and `--format table|plain|csv|tsv|pipe` to choose the display format. For automation and shell use, `--json`, `--ids`, and `--paths` provide stable machine-friendly outputs; `--json` prints full matching records and ignores `--fields`, `--format`, and the default display cap.

## Search Semantics

Unqualified field names are resolved in this order:

1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

If you need to bypass flattened lookup, use an explicit dotted path such as `user_metadata.species` or `derived_metadata.netcdf.dims.time`. The shorter `user.species` and `derived.netcdf.dims.time` aliases are also accepted.

Current search is intentionally small. It does not support numeric range queries or richer expressions such as `>`, `<`, `>=`, `<=`, or boolean query composition.

## Development

Use the project-local `.venv/` managed by `uv`; do not rely on a global Python
or ad-hoc `pip install`.

```bash
uv sync --extra dev --extra docs
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run pyright
uv run pytest
```

Build the documentation with:

```bash
uv run sphinx-build -b html docs docs/_build/html
uv run sphinx-build -W -b html docs docs/_build/html
```

To host the built docs locally:

```bash
cd docs/_build/html
uv run python -m http.server 8000
```

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

`catalog.json` stores schemas in `record_schemas` and identifies the fallback with
`default_record_schema`, so a catalog can document expected metadata and naming behavior without
adding domain-specific framework code.

## Current Limitations

- the only supported backend today is TinyDB behind the repository abstraction
- non-file record types are only partially modelled so far; readers, managers, and richer URI
  handling are still future work
- derived metadata extraction is intentionally small and currently focused on optional netCDF summaries
- reader and manager bindings are not implemented yet
- richer readers, managers, and import workflows are future work

## Roadmap

The current direction is:

- today: spec-driven file catalog with metadata, naming, and search
- next: generalise from managed files to catalogued artefacts with clearer record typing and locator handling
- later: reader hooks, manager bindings, and scan or import workflows

See [docs/architecture.md](docs/architecture.md),
[docs/design-note-artifact-locators.md](docs/design-note-artifact-locators.md),
[docs/design-note-hooks-plugins.md](docs/design-note-hooks-plugins.md),
and [docs/roadmap.md](docs/roadmap.md) for more detail.
