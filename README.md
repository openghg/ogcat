# ogcat

`ogcat` stands for OpenGHG Catalog.

`ogcat` is a lightweight artifact catalog for local and shared files. It provides a
self-describing on-disk catalog layout, a small Python API, and a CLI for creating catalogs,
adding files by copy or move, recording existing artifact references and collections, listing
metadata field descriptions, and locating stored paths.

## Scope

- local catalogs centred on managed file ingest
- a self-describing catalog layout with `catalog.json`, `db.json`, and `data/`
- path-based managed ingest using `copy` or `move`
- reference records for existing local paths, URIs, and explicit URI/urlpath locators
- collection records for local or remote roots, with local member listing
- flexible JSON-serialisable user metadata
- simple derived metadata extraction for supported file types
- template-based storage naming
- exact, contains, and regex search from Python and CLI
- shell-friendly CLI outputs for ids, paths, locators, and JSON where appropriate

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
- Naming and templates: managed files default to UUID primary paths under `data/objects/`.
  Directory and filename templates produce human-readable symlink replicas or regenerated views.
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
  data/
    files/
    objects/
```

- `catalog.json`: catalog specification, default schema, and optional named record schemas
- `db.json`: TinyDB-backed record store
- `data/files/`: human-readable template replicas and template-primary artifacts
- `data/objects/`: UUID primary objects for default managed ingest

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

`search()` returns a `CatalogRecordSet` by default, so results can be indexed,
iterated, previewed, or narrowed to selected fields. Pass `as_record_set=False`
when you explicitly need a plain list.

For selection-heavy workflows, use the small helpers around search:

```python
record = catalog.get_one(
    where={
        "product": "GridFED",
        "sector": "TOTAL",
        "species": "co2",
    }
)

# get_one raises on zero or multiple matches. Include enough metadata to
# distinguish variants such as site, domain, and model.

# For local paths, record.path() is often the easiest value to open.
# For URI/urlpath records, use the locator value.
# ds = xr.open_dataset(record.locator.value)

results = catalog.search(
    where={"provenance": "derived", "species": "co2"},
    contains={"keywords": "paris_verification_games"},
)
ids = results.ids
summary = results.select("id", "product", "species")
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
record_id="$(uv run ogcat search --catalog example-catalog species=CO2 product=CTE-HR year=2024 month=1 --one --ids)"
uv run ogcat show "$record_id" --catalog example-catalog
uv run ogcat path "$record_id" --catalog example-catalog
uv run ogcat fields --catalog example-catalog --stored
uv run ogcat fields --catalog example-catalog --values species
```

For advanced materialisation, see
[hooks and plugins](docs/design-note-hooks-plugins.md) for lifecycle, rollback,
and transaction examples.

Use `add_file()` when ogcat should manage a local copy or move into the catalog's `data/objects/` tree.
Use `add_reference()` when the artifact already exists and ogcat should only record a local path,
URI, URL path, or explicit `ArtifactLocator`. Use `add_collection()` when one logical dataset is
represented by several members under a directory, URI, or URL-path root, such as a monthly NetCDF
series opened with `xarray.open_mfdataset`. Use `add_artifact()` with an `OperationSource` and
artifact writer when a plugin or helper should materialise new data before the record is written.
See `ogcat.writers` for small helper wrappers around in-memory data, path-backed transforms, and zip
extraction examples.

For a local collection, `catalog.member_paths(record.id)` returns sorted paths matching its stored
relative glob at call time. It does not index or filter members by their dates or contents; select
the required files by their names or after opening their time coordinates. Remote collections do
not support `member_paths()`.

For a finished output already on disk, compute outside a catalog transaction, then call
`add_file(..., operation="move", derived_metadata={...})` to move it into managed storage while
preserving source-specific metadata. Use `add_reference()` if the output must stay where it is.
Only one process should write a TinyDB catalog. Workers can open
`Catalog.open(root, read_only=True)` for queries; reopen that view after another process writes.
This matters on BP1's GPFS as well as local filesystems: ogcat does not coordinate concurrent
TinyDB writers.

By default, `add_file()` stores the primary artifact under a UUID path and creates a template-based
symlink replica for human-readable browsing. Pass `primary_location="template"` when the template
path should be the primary storage location.

Schema naming templates are intended for human-readable paths. They can use user metadata that
does not collide with generated names, plus fields such as `date_added`, `year_added`,
`original_stem`, `original_suffix`, and `title_slug`. When ogcat builds a storage or
template-link naming context, those generated names are reserved metadata keys. Internal
identifiers such as `id`, `uuid`, `operation_id`, and `artifact_uuid` are also reserved in
template contexts; explicit-locator records that do not render schema naming templates are not
subject to this template-context restriction. Use explicit metadata names such as `dataset_id`
for domain identifiers that should appear in paths.

Use `catalog.plan_artifact_storage(...)` to dry-run a planned target before writing. The returned
`StoragePlan` contains the locator, write intent, and resolved naming outputs; pass record metadata
explicitly when calling `add_artifact(storage_plan=...)`. The plan is available to hooks and artifact
writers as `context.storage_plan`; for older `add_artifact(locator=..., artifact_writer=...)` flows,
ogcat derives a plan from the writer's declared `target_kind` and `write_mode` when available.
Domain logic can create directory-like artifacts such as NetCDF collections or `.zarr` stores while
ogcat core records only generic locators and metadata. Artifact writers remain the place where
filesystem work and rollback registration happen.

Use `catalog.plan_view(root, template, mode="symlink", ...)` to dry-run a generated symlink view
from current catalog metadata. The returned plan reports collisions, unsupported locators, and
missing primary paths before `plan.apply()` creates any links.

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
uv run ogcat search --catalog ./example-catalog species=CO2 product=CTE-HR --one --locators
```

Delete records with trash-style semantics. ``delete`` tombstones a record and
hides it from normal search; ``restore`` makes it active again; ``purge``
permanently removes a tombstoned record after removing managed catalog-local
artifacts. Purge is best-effort across artifacts: if cleanup is incomplete,
the tombstone is retained with purge metadata instead of reporting success.
``--include-deleted`` and ``--only-deleted`` are mutually exclusive search
options.

```bash
uv run ogcat delete 1 --catalog ./example-catalog --reason superseded
uv run ogcat search --catalog ./example-catalog --only-deleted --ids
uv run ogcat restore 1 --catalog ./example-catalog
uv run ogcat purge 1 --catalog ./example-catalog --yes
```

``status`` and ``lifecycle_metadata`` are reserved top-level lifecycle fields in
search. If your domain metadata uses the same names, query it explicitly with a
path such as ``user_metadata.status``.

Show a record or print its stored path:

```bash
uv run ogcat show 1 --catalog ./example-catalog
uv run ogcat path 1 --catalog ./example-catalog
uv run ogcat path 1 --catalog ./example-catalog --readable
uv run ogcat locator 1 --catalog ./example-catalog
```

Use `ogcat reference PATH --catalog ROOT` or `ogcat reference --uri URI --catalog ROOT`
to register an existing artifact without moving it. Use
`ogcat collection DIRECTORY --pattern '*.nc' --catalog ROOT` for one logical
dataset spread across local files; `ogcat members ID --catalog ROOT` lists its
current matching paths. `search --one` fails on ambiguous or missing records;
`--locators` prints raw local or remote locator values. See the
[CLI reference](docs/cli.md) for options and remote collection limits.

Inspect catalog info and declared metadata fields:

```bash
uv run ogcat info --catalog ./example-catalog
uv run ogcat fields --catalog ./example-catalog
uv run ogcat fields --catalog ./example-catalog --json
```

`ogcat search` supports compact positional filters: `field=value` for equality, `field:value` for contains/list membership, `field~pattern` for glob or substring matching, `field?` for exists, and `!field?` for missing. Compatibility flags remain available: `--where`, `--contains`, `--match`, `--regex`, `--exists`, and `--missing`. Human-readable search output is capped by default; use `--limit N` to choose a cap or `--all` to show every match. Use `--fields a,b,c` to choose displayed fields, and `--format table|plain|csv|tsv|pipe` to choose the display format. For automation and shell use, `--json`, `--ids`, `--paths`, and `--locators` provide stable machine-friendly outputs; `--json` prints full matching records and ignores `--fields`, `--format`, and the default display cap.

## Search Semantics

Unqualified field names are resolved in this order:

1. top-level record fields
2. `user_metadata`
3. `derived_metadata`

If you need to bypass flattened lookup, use an explicit dotted path such as `user_metadata.species` or `derived_metadata.netcdf.dims.time`. The shorter `user.species` and `derived.netcdf.dims.time` aliases are also accepted.

Newly added records also store cheap artifact classification under
`derived_metadata.classification`. It is inferred from locator/path shape,
suffixes, and safe local archive member names, not from expensive data reads.
The normalized `format` value is intentionally not the same thing as the file
suffix. For example, `.csv`, `.json`, `.md`, `.tsv`, and `.txt` are all
classified as `format="text"`; use `suffixes` when you need the literal suffix
list. Selected classification fields can be searched unqualified:

```python
catalog.search(where={"format": "zip"})
catalog.search(where={"format": "text"})
catalog.search(where={"artifact_kind": "zarr_store"})
catalog.search(where={"derived_metadata.classification.inner_format": "netcdf"})
```

Current search is intentionally small. It does not support numeric range queries or operators
such as `>`, `<`, `>=`, `<=`, or OR/NOT query composition.

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

Storage is centred on path-backed managed ingest. Files added with
`add_file()` are copied or moved into the catalog's `data/objects/` tree by default, and the
resulting primary path is recorded in the catalog database alongside metadata and naming
information. Template-derived paths are linked replicas that can be regenerated after metadata or
template changes. Existing paths, URIs, and explicit URI/urlpath locators can be recorded with
`add_reference()` without copying or moving data.

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
- collection records describe member patterns, but member-date indexing and remote member
  expansion are not implemented
- derived metadata extraction is intentionally small and currently focused on optional netCDF summaries
- reader and manager bindings are not implemented yet
- richer readers, managers, and import workflows are future work
- TinyDB does not provide a coordinated multi-process writer; use one writer, and register
  finished outputs after long-running computation rather than holding an open transaction

## Roadmap

The current direction is to make single-user registration and retrieval reliable,
including managed files, existing references, and local collections. The
[active plan](docs/plans/2026-09-25-single-user-workflows.md) records the
workflow decisions and deferred features.

See [docs/architecture.md](docs/architecture.md),
[docs/design-note-artifact-locators.md](docs/design-note-artifact-locators.md),
[docs/design-note-hooks-plugins.md](docs/design-note-hooks-plugins.md),
and [docs/roadmap.md](docs/roadmap.md) for more detail.
